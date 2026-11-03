# -*- coding: utf-8 -*-
"""tui 前端：Textual 终端聊天界面，气泡消息流 + 功能清单 + 工具活动日志。
未来新增 web、desktop、mobile 前端时，与 tui 平级创建目录即可。"""
import threading

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (Footer, Header, LoadingIndicator, Static,
                            TextArea)

from core.agent import Agent
from core.tools import TOOLS

EXIT_WORDS = ("exit", "quit")

BUBBLE_CSS = {
    "you": ("#1f6feb", "#e8f1ff", "#d5e6ff"),
    "ai": ("#1a7f37", "#e9f6ec", "#d3f0da"),
    "tool": ("#9a6700", "#fff8e5", "#f5e6b8"),
    "err": ("#d1242f", "#fdebec", "#f6cdd0"),
}

# 工具中文说明，与 core/tools.py 的注册表对应
TOOL_CN = {
    "getCWD": "查看当前目录",
    "listDirectory": "列出目录内容",
    "getFileType": "查看文件类型",
    "getFileContent": "读取文件内容",
    "createFile": "新建文件",
    "editFile": "编辑文件",
    "createDirectory": "新建文件夹",
    "deleteDirectory": "删除文件夹",
    "deleteFile": "删除文件",
    "renameFileOrDir": "重命名/移动",
    "getDate": "获取当前日期",
    "isLeapYear": "判断闰年",
    "searchWeb": "联网搜索",
    "fetchWeb": "抓取网页正文",
    "RunCli": "执行系统命令",
    "loadSkill": "加载技能",
    "createSkill": "创建技能",
    "officeCli": "创建/修改Office文档",
}

# 本地功能的分组标题
TOOL_GROUP = {
    "getCWD": "文件与目录",
    "listDirectory": "文件与目录",
    "getFileType": "文件与目录",
    "getFileContent": "文件与目录",
    "createFile": "文件与目录",
    "editFile": "文件与目录",
    "createDirectory": "文件与目录",
    "deleteDirectory": "文件与目录",
    "deleteFile": "文件与目录",
    "renameFileOrDir": "文件与目录",
    "getDate": "日期与时间",
    "isLeapYear": "日期与时间",
    "searchWeb": "网络与搜索",
    "fetchWeb": "网络与搜索",
    "RunCli": "命令执行",
    "loadSkill": "技能管理",
    "createSkill": "技能管理",
    "officeCli": "Office 文档",
}


class Bubble(Static):
    """消息气泡，按角色着色。禁用 markup，避免 [ ] 等字符被当作格式解析。"""

    def __init__(self, content="", **kwargs):
        super().__init__(content, markup=False, **kwargs)


class ChatInput(TextArea):
    """多行输入框：Enter 发送，Shift+Enter 换行（需 VS Code/Windows Terminal）。"""

    def on_key(self, event):
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.app._submit()
        elif event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")


class ChatTUI(App):
    """zAgent 终端聊天界面：左侧对话流，右侧功能清单与活动日志，底部状态条。"""

    TITLE = "zAgent"

    CSS = """
    Screen {
        background: #f6f8fa;
    }
    Header {
        background: #ffffff;
        color: #24292f;
        border-bottom: solid #d0d7de;
    }
    #chat_col {
        width: 3fr;
        border-right: solid #d0d7de;
        background: #f6f8fa;
    }
    #tools_col {
        width: 2fr;
        background: #ffffff;
    }
    #messages {
        height: 1fr;
        padding: 1 1;
        background: #f6f8fa;
    }
    Bubble {
        width: 100%;
        padding: 0 1;
        margin-bottom: 1;
    }
    #tools_title {
        background: #f6f8fa;
        color: #57606a;
        padding: 0 1;
        text-style: bold;
        border-bottom: solid #d0d7de;
    }
    #func_list {
        height: auto;
        max-height: 45%;
        padding: 0 1;
        background: #ffffff;
        border-bottom: solid #d0d7de;
    }
    .func_item {
        width: 100%;
        padding: 0 0;
        color: #57606a;
    }
    .func_group {
        width: 100%;
        padding: 1 0 0 0;
        color: #24292f;
        text-style: bold;
        border-bottom: solid #d0d7de;
        margin-bottom: 1;
    }
    .func_used {
        color: #1a7f37;
        text-style: bold;
    }
    #log_title {
        background: #f6f8fa;
        color: #57606a;
        padding: 0 1;
        text-style: bold;
        border-bottom: solid #d0d7de;
    }
    #tool_list {
        height: 1fr;
        padding: 0 1;
        background: #ffffff;
    }
    #status {
        height: 3;
        background: #ffffff;
        border-top: solid #d0d7de;
        padding: 0 1;
        color: #57606a;
    }
    #status_text {
        width: 1fr;
    }
    #status_loading {
        width: auto;
    }
    #input_row {
        height: auto;
        padding: 0 1;
        background: #ffffff;
        border-top: solid #d0d7de;
    }
    #input_loading {
        width: auto;
        height: 3;
        display: none;
    }
    #input {
        height: 3;
        border: solid #d0d7de;
        background: #ffffff;
        color: #24292f;
    }
    #input:focus {
        border: solid #0969da;
    }
    """

    def __init__(self, agent):
        super().__init__()
        self.agent = agent
        self._func_widgets = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="chat_col"):
                yield VerticalScroll(id="messages")
                with Horizontal(id="input_row"):
                    yield LoadingIndicator(id="input_loading")
                    yield ChatInput(
                        id="input",
                        placeholder="输入消息，Shift+Enter 换行，Enter 发送，exit 退出")
            with Vertical(id="tools_col"):
                yield Static("功能清单", id="tools_title")
                yield VerticalScroll(id="func_list")
                yield Static("活动日志", id="log_title")
                yield VerticalScroll(id="tool_list")
        with Horizontal(id="status"):
            yield LoadingIndicator(id="status_loading")
            yield Static("就绪，输入消息开始对话", id="status_text")
        yield Footer()

    def on_mount(self):
        model = self.agent.model
        self._build_func_list()
        skill_names = [s["name"] for s in getattr(self.agent, "skills", [])]
        hint = "可以问日期、闰年、文件操作"
        if skill_names:
            hint += "，技能：" + "、".join(skill_names)
        self.run_worker(self._add_bubble(
            "欢迎使用 zAgent，当前模型 %s\n%s" % (model, hint), "ai"))

    def _build_func_list(self):
        """在右侧生成分类功能清单：本地功能按类别细分，再列技能与 MCP 工具，
        未用时为灰色小圆点。"""
        widgets = []          # 按展示顺序的 (widget, name)
        mcp_names = set(t["function"]["name"]
                        for t in getattr(self.agent, "mcp_tools", []) or [])

        def add_group(title):
            g = Static(title, markup=False)
            g.set_classes("func_group")
            widgets.append((g, None))

        def add_item(label, name):
            w = Static(label, id="func_%d" % len(widgets), markup=False)
            w.set_classes("func_item")
            widgets.append((w, name))

        # 第一组：本地功能，按类别细分
        by_group = {}
        for tool in TOOLS:
            fn = tool["function"]
            name = fn["name"]
            by_group.setdefault(TOOL_GROUP.get(name, "其他功能"), []).append(
                (name, TOOL_CN.get(name, "")))
        for group_title in ("文件与目录", "日期与时间", "网络与搜索",
                            "命令执行", "Office 文档", "技能管理", "其他功能"):
            items = by_group.get(group_title)
            if not items:
                continue
            add_group("◆ 功能：" + group_title)
            for name, cn in items:
                label = "● %s  %s" % (name, cn) if cn else "● %s" % name
                add_item(label, name)

        # 第二组：技能
        skills = getattr(self.agent, "skills", []) or []
        if skills:
            add_group("◆ 技能")
            for s in skills:
                name = s["name"]
                desc = (s.get("description") or "")[:24]
                label = "● %s  %s" % (name, desc) if desc else "● %s" % name
                add_item(label, name)

        # 第三组：MCP 动态工具
        mcp_tools = [t for t in getattr(self.agent, "tools", []) or []
                     if t["function"]["name"] in mcp_names]
        if mcp_tools:
            add_group("◆ MCP 工具")
            for tool in mcp_tools:
                fn = tool["function"]
                name = fn["name"]
                desc = (fn.get("description") or "")[:24]
                label = "● %s  %s" % (name, desc) if desc else "● %s" % name
                add_item(label, name)

        for w, name in widgets:
            if name:
                self._func_widgets[name] = w
        self.run_worker(self._mount_funcs([w for w, _ in widgets]))

    async def _mount_funcs(self, ws):
        box = self.query_one("#func_list", VerticalScroll)
        for w in ws:
            await box.mount(w)

    def _mark_used(self, name):
        """调用过某功能后，把清单里对应项打勾并变绿。"""
        w = self._func_widgets.get(name)
        if w is None:
            return
        text = w.render().plain if hasattr(w.render(), "plain") else str(w.render())
        text = text.replace("●", "✓", 1)
        w._render_markup = False
        w.update(text)
        w.set_classes("func_item func_used")

    def action_submit(self):
        """Enter 发送，Shift+Enter 由 TextArea 默认行为处理为换行。"""
        self._submit()

    def _submit(self):
        text_area = self.query_one("#input", TextArea)
        text = text_area.text.strip()
        if not text:
            return
        text_area.text = ""
        if text.lower() in EXIT_WORDS:
            self.exit()
            return
        self.run_worker(self._add_bubble("你  " + text, "you"))
        threading.Thread(target=self._run_chat, args=(text,),
                         daemon=True).start()

    def _run_chat(self, text):
        """后台线程执行对话，事件通过 call_from_thread 回主线程更新 UI。"""
        def emit(event_name, data):
            self.call_from_thread(self._on_event, event_name, data)

        self.agent.on_event = emit
        try:
            reply = self.agent.chat(text)
            self.call_from_thread(self._on_event, "final", {"text": reply})
        except Exception as e:
            self.call_from_thread(self._on_event, "error", {"text": str(e)})

    def _on_event(self, event_name, data):
        if event_name == "thinking":
            self._set_status("AI 思考中...", loading=True)
        elif event_name == "tool":
            name = data.get("name", "")
            args = data.get("args") or {}
            result = str(data.get("result", ""))
            arg_str = "，".join("%s=%s" % (k, v) for k, v in args.items())
            short_result = result[:150]
            if len(result) > 150:
                short_result += "..."
            self._mark_used(name)
            self.run_worker(self._add_bubble("工具  " + name + "  " + arg_str,
                                             "tool"))
            self.run_worker(self._add_tool_line(
                "%s\n参数 %s\n结果 %s" % (name, arg_str, short_result)))
            self._set_status("正在执行工具 %s..." % name, loading=True)
        elif event_name == "stream":
            self._set_status("AI 生成中..." + data.get("text", ""),
                             loading=True)
        elif event_name == "final":
            self.run_worker(self._add_bubble("AI  " + data.get("text", ""),
                                             "ai"))
            self._set_status("就绪，输入消息开始对话", loading=False)
        elif event_name == "error":
            self.run_worker(self._add_bubble("错误  " + data.get("text", ""),
                                             "err"))
            self._set_status("就绪，输入消息开始对话", loading=False)

    def _set_status(self, text, loading):
        status = self.query_one("#status_text", Static)
        status._render_markup = False
        status.update(text)
        self.query_one("#status_loading",
                       LoadingIndicator).display = loading
        self.query_one("#input_loading",
                       LoadingIndicator).display = loading

    async def _add_bubble(self, text, role):
        color, bg, border = BUBBLE_CSS.get(role, BUBBLE_CSS["ai"])
        bubble = Bubble(text)
        bubble.styles.border = ("solid", border)
        bubble.styles.color = color
        bubble.styles.background = bg
        await self.query_one("#messages", VerticalScroll).mount(bubble)
        self.query_one("#messages", VerticalScroll).scroll_end(
            animate=True)

    async def _add_tool_line(self, text):
        line = Static(text, markup=False)
        line.styles.color = "#9a6700"
        line.styles.background = "#fff8e5"
        line.styles.border = ("solid", "#f5e6b8")
        await self.query_one("#tool_list", VerticalScroll).mount(line)
        self.query_one("#tool_list", VerticalScroll).scroll_end(
            animate=True)
