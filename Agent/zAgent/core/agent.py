# -*- coding: utf-8 -*-
"""Agent 核心模块：封装 LLM 客户端与 function call 循环，
支持事件回调和流式输出，供各 UI 层复用。"""
import json
import threading

from openai import OpenAI

import core.context_manager as cm
import core.tools as tools
from core.tools import TOOLS, call_tool
from core.skill_loader import discover_skills, build_skills_prompt
from core.mcp_client import (discover_mcp_tools, call_mcp_tool,
                             load_mcp_config)
from core.context_manager import (
    estimate_messages_tokens, save_archive, build_compress_messages,
    build_archive_note, DEFAULT_CONTEXT_WINDOW,
)
from core.session_store import (save_session, load_session,
                                read_agents_md, log_event)

DEFAULT_TIMEOUT = 60
MAX_MESSAGES = 30
MAX_STEPS = 12
SYSTEM_PROMPT = ("你是一个乐于助人的 AI 助手。涉及本地文件操作时，"
                 "必须调用对应的文件操作工具，不要编造结果。"
                 "涉及日期、年份、星期或时间时，必须先调用 getDate 工具"
                 "获取真实的当前日期，严禁自己猜测或编造年份。"
                 "撰写通知、公文等涉及落款日期或正文日期的内容时，"
                 "同样必须先调用 getDate 取得真实日期再落笔。"
                 "创建或修改 Word(.docx)、Excel(.xlsx)、PowerPoint(.pptx)"
                 "等 Office 文档时，必须调用 officeCli 工具完成，"
                 "严禁用 createFile 或 editFile 向 Office 文件写入文本内容。"
                 "当用户要求生成或创建 PPT、Word、Excel 文档时，"
                 "立即调用 officeCli 工具动手创建，不要先向用户询问主题、"
                 "风格、页数等细节；信息不足时先按用户的字面要求创建一个"
                 "初步版本，生成后再询问是否需要修改。"
                 "用户明确要求写入内容时，必须用 add / set 命令写入；"
                 "用户没有要求内容时，只创建空文档即可，不要擅自添加"
                 "任何文字，如实告知用户文档已创建。"
                 "文档路径使用完整路径，例如 "
                 "D:/LLM-Chat/Agent/zAgent/xxx.pptx，不要使用相对路径。"
                 "当历史上下文被 /compact 压缩后，如果发现缺少完成任务所需的"
                 "历史细节，必须调用 recallContext 工具从原始归档中检索，"
                 "不要凭空编造历史信息。")


def _safe_json(text):
    """宽容解析工具参数 JSON：兼容拼接杂质、前导/尾部非法字符等。"""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


class Agent:
    """LLM 智能体：管理会话上下文并执行 function call 循环。

    UI 层创建 Agent 后调用 chat 方法即可，过程事件通过 on_event 回调通知：
    thinking 开始请求模型；tool 工具调用完成；stream 流式内容增量；
    done 本轮对话结束。
    """

    def __init__(self, cfg, system_prompt=SYSTEM_PROMPT, on_event=None):
        self.client = OpenAI(api_key=cfg["api_key"],
                             base_url=cfg["base_url"],
                             timeout=cfg.get("timeout", DEFAULT_TIMEOUT))
        self.model = cfg["model"]
        self.search_api_key = cfg.get("baidu_qianfan_api_key", "")
        self.skills = discover_skills()
        skills_text = build_skills_prompt(self.skills)
        full_prompt = system_prompt
        if skills_text:
            full_prompt = system_prompt + "\n\n" + skills_text

        # 把 config.ini 中的运行参数同步到各业务模块，
        # 模块内部保留默认值，未配置的项不改变原有行为
        cm.WARN_RATIO = cfg.get("warn_ratio", cm.WARN_RATIO)
        cm.COMPRESS_TARGET_TOKENS = cfg.get(
            "compress_target_tokens", cm.COMPRESS_TARGET_TOKENS)
        cm.CONTEXT_DIR = cfg.get("archive_dir", cm.CONTEXT_DIR)
        tools.SEARCH_TIMEOUT = cfg.get("search_timeout", tools.SEARCH_TIMEOUT)
        tools.OFFICE_CLI_EXE = cfg.get("office_cli_exe", tools.OFFICE_CLI_EXE)
        # 安全开关：路径边界与删除确认，由 config.ini 的 [Safety] 段驱动
        tools.WORKSPACE_ROOT = cfg.get("workspace_root", tools.WORKSPACE_ROOT)
        tools.CONFIRM_DELETE = cfg.get("confirm_delete", tools.CONFIRM_DELETE)
        self.max_steps = cfg.get("max_steps", MAX_STEPS)
        self.agents_md_name = cfg.get("agents_md_name", "AGENTS.md")
        self.agents_md_max_chars = cfg.get("agents_md_max_chars", 8000)

        # MCP 动态工具：先读取 .mcp.json 的服务器清单，
        # 实际连接放到后台线程异步完成，TUI 先进入界面再动态刷新，
        # 加载完成后通过 mcp_loaded 事件通知 UI
        self.mcp_servers = load_mcp_config()
        if not isinstance(self.mcp_servers, dict):
            self.mcp_servers = {}
        self.mcp_server_status = {s: "loading" for s in self.mcp_servers}
        self.mcp_tools = []
        self.mcp_mapping = {}
        self.mcp_errors = []
        self.mcp_loaded = False
        self._mcp_loading = False
        self.tools = TOOLS + self.mcp_tools

        self.messages = [{"role": "system", "content": full_prompt}]
        self.on_event = on_event

        # 上下文管理状态
        # 模型上下文窗口大小：优先用 config.ini 的 context_window 配置，
        # 未配置时默认按 128k 处理，TUI 会显示「默认」提示
        self.context_window = cfg.get("context_window") or DEFAULT_CONTEXT_WINDOW
        self.context_blocked = False    # 达到阈值后暂停接收新消息
        self._warned = False            # 是否已弹出过阈值警告
        self.compressing = False        # 正在执行上下文压缩
        self.busy = False               # 正在处理一轮对话
        self._last_prompt_tokens = None  # 最近一次响应 API 返回的 prompt tokens
        self.max_messages = cfg.get("max_messages", MAX_MESSAGES)
        self.session_dir = cfg.get("session_dir", "")

        # AGENTS.md 一次注入：会话首次加载时把项目说明追加为一条
        # system 消息，后续每轮请求都会携带，实现全程复用；
        # 同时写入事件日志留痕
        agents_md = read_agents_md(name=self.agents_md_name,
                                   max_chars=self.agents_md_max_chars)
        if agents_md:
            self.messages.append({"role": "system",
                                  "content": "[AGENTS.md 项目说明]\n"
                                             + agents_md})
            try:
                log_event("agents_md_injected", {
                    "chars": len(agents_md),
                    "messages": len(self.messages),
                })
            except Exception:
                pass

    def _emit(self, event, **data):
        """触发事件回调，UI 层借此感知对话过程。"""
        if self.on_event:
            self.on_event(event, data)

    def trim_messages(self):
        """消息条数超过上限时，截断最早的对话，保留全部 system 提示。

        系统提示可能有多条（主 System Prompt + AGENTS.md 注入），
        截断只作用于其后的对话消息，避免项目说明被误删。
        """
        keep = 1
        while keep < len(self.messages) and \
                self.messages[keep]["role"] == "system":
            keep += 1
        if len(self.messages) <= self.max_messages:
            return
        excess = len(self.messages) - self.max_messages
        excess = min(excess, max(0, len(self.messages) - keep))
        if excess > 0:
            del self.messages[keep:keep + excess]

    def get_context_usage(self):
        """返回当前上下文占用情况字典。

        used：已用 Token 数；window：上下文窗口大小；ratio：占用比例；
        estimated：已用 Token 是否为本地估算值；window_defaulted：
        窗口大小是否为默认 128k；blocked：是否已暂停接收新消息。
        """
        if self._last_prompt_tokens:
            used = self._last_prompt_tokens
            estimated = False
        else:
            used = estimate_messages_tokens(self.messages)
            estimated = True
        return {
            "used": used,
            "window": self.context_window,
            "ratio": used / self.context_window,
            "estimated": estimated,
            "window_defaulted": self.context_window == DEFAULT_CONTEXT_WINDOW,
            "blocked": self.context_blocked,
        }

    def _check_context(self):
        """LLM 响应完成后检查上下文占用，达到阈值时暂停接收新消息。"""
        info = self.get_context_usage()
        if info["ratio"] >= cm.WARN_RATIO and not self.context_blocked:
            self.context_blocked = True
            if not self._warned:
                self._warned = True
                self._emit("context_warning", **info)
        self._emit("usage", **info)

    def start_mcp_loading(self):
        """后台线程异步加载 MCP 工具，完成后发 mcp_loaded 事件。

        TUI 先进入界面，MCP Server 连接与工具发现不阻塞启动，
        加载完成后 UI 动态刷新边栏中的 MCP 区域。
        """
        if self.mcp_loaded or self._mcp_loading:
            return
        self._mcp_loading = True
        threading.Thread(target=self._load_mcp_worker, daemon=True).start()

    def _load_mcp_worker(self):
        """后台执行 MCP 工具发现，完成后更新工具集合并通知 UI。"""
        try:
            mcp_tools, mcp_mapping, mcp_errors = discover_mcp_tools()
        except Exception as e:
            mcp_tools, mcp_mapping, mcp_errors = [], {}, [str(e)]
        self.mcp_tools = mcp_tools
        self.mcp_mapping = mcp_mapping
        self.mcp_errors = mcp_errors
        self.tools = TOOLS + mcp_tools
        self.mcp_loaded = True
        self._mcp_loading = False

        # 服务器状态：映射表中出现的服务器视为连接成功
        ok_servers = {entry["server"] for entry in mcp_mapping.values()}
        for server in self.mcp_servers:
            if server in ok_servers:
                self.mcp_server_status[server] = "ok"
            else:
                self.mcp_server_status[server] = "error"

        # 把 MCP 工具说明动态追加到 System Prompt，保持能力说明完整
        content = self.messages[0]["content"]
        if mcp_errors:
            err_text = "\n".join("  - %s" % e for e in mcp_errors)
            content += ("\n\n[MCP 工具] 部分 MCP Server 连接失败，"
                        "相关 MCP 工具不可用：\n%s" % err_text)
        elif mcp_tools:
            names = ", ".join(t["function"]["name"] for t in mcp_tools)
            content += ("\n\n[MCP 工具] 以下工具由外部 MCP Server "
                        "动态提供，可像本地工具一样调用：\n%s" % names)
        self.messages[0]["content"] = content
        self._emit("mcp_loaded", servers=dict(self.mcp_server_status),
                   errors=list(mcp_errors))

    def save_session(self):
        """把当前会话完整保存到 .agents/sessions/，返回 (编号, 路径)。"""
        session_id, path = save_session(self.messages,
                                        session_dir=self.session_dir)
        try:
            log_event("session_saved", {
                "session_id": session_id,
                "messages": len(self.messages),
            })
        except Exception:
            pass
        return session_id, path

    def new_session(self):
        """新建会话：自动保存当前会话历史，然后重置对话上下文。

        保留 System Prompt，重置上下文占用统计与暂停状态；
        没有对话内容时不保存，直接返回 None。
        """
        saved = None
        if len(self.messages) > 1:
            saved = self.save_session()
        self.messages = [self.messages[0]]
        self._last_prompt_tokens = None
        self.context_blocked = False
        self._warned = False
        self.compressing = False
        self.busy = False
        return saved

    def resume_session(self, session_id):
        """恢复历史会话：替换当前对话上下文，返回 (成功, 说明)。"""
        msgs = load_session(session_id, session_dir=self.session_dir)
        if not msgs:
            return False, "未找到历史会话 %s。" % session_id
        self.messages = msgs
        self._last_prompt_tokens = None
        self.context_blocked = False
        self._warned = False
        self.compressing = False
        self.busy = False
        return True, "已恢复会话 %s，共 %d 条消息。" % (session_id, len(msgs))

    def chat(self, user_input):
        """发送用户消息，自动执行工具调用，返回最终文本回复。"""
        if self.context_blocked:
            return ("[上下文已满] 上下文使用量已达窗口的 %d%%，"
                    "请先输入 /compact 压缩上下文后再继续。"
                    % int(cm.WARN_RATIO * 100))
        if self.compressing:
            return "[压缩中] 正在压缩上下文，请稍候再发送消息。"
        self.busy = True
        try:
            self.messages.append({"role": "user", "content": user_input})
            self.trim_messages()
            self._emit("thinking")
            reply = self._chat_stream(1)
            self.messages.append({"role": "assistant", "content": reply})
            self.trim_messages()
            self._check_context()
            self._emit("done")
            return reply
        finally:
            self.busy = False

    def _chat_stream(self, step=1):
        """流式 function call 循环：增量输出内容，必要时执行工具并递归。

        step 是本轮对话内的工具调用轮次，超过 max_steps 后停止继续调用，
        防止模型陷入无限工具循环；工具执行异常已由 call_tool 兜成文本，
        不会中断对话。
        """
        if step > self.max_steps:
            notice = ("[已达到单轮工具调用上限 %d 步] 为安全起见停止继续调用工具。"
                      "如需继续，请把任务拆成更小的步骤后重新提问。" % self.max_steps)
            self._emit("step_limit", text=notice)
            return notice
        kwargs = {"model": self.model, "messages": self.messages,
                  "tools": self.tools, "stream": True}
        try:
            # 请求携带 usage，优先使用模型 API 提供的 Token 使用信息
            stream = self.client.chat.completions.create(
                **kwargs, stream_options={"include_usage": True})
        except Exception:
            # 部分接口不支持 stream_options 时退回普通流式请求，
            # 此时上下文占用改用本地估算
            stream = self.client.chat.completions.create(**kwargs)
        content = []
        tool_calls = {}
        finish = None
        for chunk in stream:
            # 流式响应的最后一个 chunk 会携带 usage 信息
            if getattr(chunk, "usage", None):
                self._last_prompt_tokens = chunk.usage.prompt_tokens
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta and delta.content:
                content.append(delta.content)
                self._emit("stream", text=delta.content)
            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    entry = tool_calls.setdefault(
                        tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["name"] += tc.function.name
                        if tc.function.arguments:
                            entry["arguments"] += tc.function.arguments
            finish = choice.finish_reason

        if finish == "tool_calls":
            assistant_msg = {
                "role": "assistant",
                "content": "".join(content) or None,
                "tool_calls": [{
                    "id": entry["id"], "type": "function",
                    "function": {"name": entry["name"],
                                 "arguments": entry["arguments"]},
                } for _, entry in sorted(tool_calls.items())],
            }
            self.messages.append(assistant_msg)
            for _, tc in sorted(tool_calls.items()):
                args = _safe_json(tc["arguments"])
                name = tc["name"]
                if name in self.mcp_mapping:
                    result = call_mcp_tool(self.mcp_mapping[name], args)
                else:
                    result = call_tool(name, args,
                                       search_api_key=self.search_api_key)
                self.messages.append({"role": "tool",
                                      "tool_call_id": tc["id"],
                                      "content": result})
                self._emit("tool", name=name, args=args, result=result)
            self._emit("thinking")
            return self._chat_stream(step + 1)
        return "".join(content)

    def compact(self):
        """执行手动上下文压缩，由 /compact 命令触发。

        流程：保存完整原始归档 → 调用 LLM 生成压缩摘要 → 用摘要替换历史。
        压缩期间禁止执行其他工具调用，也不处理新的对话消息。
        成功返回 (True, 说明)；失败保留原上下文，返回 (False, 原因)，
        可重新执行 /compact。
        """
        if self.compressing:
            return False, "正在压缩上下文中，请勿重复触发。"
        if self.busy:
            return False, "当前还有对话正在处理，请等对话结束后再执行 /compact。"
        if len(self.messages) <= 1:
            return False, "当前没有可压缩的对话内容。"
        self.compressing = True
        try:
            # 1. 先完整保存原始上下文，保存成功后才能继续压缩和替换
            archive_id, archive_path = save_archive(self.messages[1:])
            self._emit("compacting", stage="archive",
                       archive_id=archive_id, path=archive_path)

            # 2. 用专门压缩 Prompt 调用 LLM 生成摘要，不携带任何工具定义，
            #    压缩期间不会执行其他 Function Calling 工具
            compress_msgs = build_compress_messages(self.messages[1:])
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=compress_msgs, stream=False,
                    timeout=120)
                summary = (resp.choices[0].message.content or "").strip()
            except Exception as e:
                return False, ("上下文压缩失败：调用模型生成摘要出错：%s。"
                               "原上下文已保留，可重新执行 /compact。" % e)
            if not summary:
                return False, ("上下文压缩失败：模型返回的摘要为空。"
                               "原上下文已保留，可重新执行 /compact。")

            # 3. 摘要末尾追加归档说明，替换原对话历史，保留 System Prompt
            note = build_archive_note(archive_id)
            self.messages = [self.messages[0],
                             {"role": "user",
                              "content": summary + note}]

            # 4. 重置占用统计并解除输入限制
            self._last_prompt_tokens = None
            self.context_blocked = False
            self._warned = False
            self._emit("compacted", archive_id=archive_id,
                       path=archive_path, summary=summary)
            return True, ("上下文压缩完成。原始上下文已归档到 %s，"
                          "对话历史已替换为压缩摘要。" % archive_path)
        except Exception as e:
            return False, ("上下文压缩失败：%s。原上下文已保留，"
                           "可重新执行 /compact。" % e)
        finally:
            self.compressing = False
