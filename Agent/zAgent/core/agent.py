# -*- coding: utf-8 -*-
"""Agent 核心模块：封装 LLM 客户端与 function call 循环，
支持事件回调和流式输出，供各 UI 层复用。"""
import json

from openai import OpenAI

from core.tools import TOOLS, call_tool
from core.skill_loader import discover_skills, build_skills_prompt

DEFAULT_TIMEOUT = 60
MAX_MESSAGES = 30
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
                 "只创建空文件不算完成任务：PPT 必须用 add 命令添加幻灯片"
                 "和文字内容，Word 必须用 add 命令添加段落，Excel 必须用 "
                 "set 命令填写单元格，确保用户要求的文档包含实际内容。"
                 "文档路径使用完整路径，例如 "
                 "D:/LLM-Chat/Agent/zAgent/xxx.pptx，不要使用相对路径。")


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
                             timeout=DEFAULT_TIMEOUT)
        self.model = cfg["model"]
        self.search_api_key = cfg.get("baidu_qianfan_api_key", "")
        self.skills = discover_skills()
        skills_text = build_skills_prompt(self.skills)
        full_prompt = system_prompt
        if skills_text:
            full_prompt = system_prompt + "\n\n" + skills_text
        self.messages = [{"role": "system", "content": full_prompt}]
        self.on_event = on_event

    def _emit(self, event, **data):
        """触发事件回调，UI 层借此感知对话过程。"""
        if self.on_event:
            self.on_event(event, data)

    def trim_messages(self):
        """消息条数超过上限时，截断最早的对话，保留系统提示。"""
        if len(self.messages) <= MAX_MESSAGES:
            return
        excess = len(self.messages) - MAX_MESSAGES
        del self.messages[1:1 + excess]

    def chat(self, user_input):
        """发送用户消息，自动执行工具调用，返回最终文本回复。"""
        self.messages.append({"role": "user", "content": user_input})
        self.trim_messages()
        self._emit("thinking")
        reply = self._chat_stream()
        self.messages.append({"role": "assistant", "content": reply})
        self.trim_messages()
        self._emit("done")
        return reply

    def _chat_stream(self):
        """流式 function call 循环：增量输出内容，必要时执行工具并递归。"""
        stream = self.client.chat.completions.create(
            model=self.model, messages=self.messages, tools=TOOLS,
            stream=True)
        content = []
        tool_calls = {}
        finish = None
        for chunk in stream:
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
                result = call_tool(tc["name"], args,
                                   search_api_key=self.search_api_key)
                self.messages.append({"role": "tool",
                                      "tool_call_id": tc["id"],
                                      "content": result})
                self._emit("tool", name=tc["name"], args=args,
                           result=result)
            self._emit("thinking")
            return self._chat_stream()
        return "".join(content)
