# -*- coding: utf-8 -*-
"""tui 前端：Textual 终端聊天界面。

左侧对话流 + 斜杠命令补全面板 + 上下文占用状态条；
右侧功能清单、MCP 服务器区域与工具活动日志；底部状态栏。
未来新增 web、desktop、mobile 前端时，与 tui 平级创建目录即可。
"""
import threading

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Footer, Header, Label, ListItem,
                            ListView, LoadingIndicator, Static, TextArea)

import core.context_manager as cm
from core.agent import Agent
from core.tools import TOOLS
from core.session_store import list_sessions

EXIT_WORDS = ("exit", "quit")

# 斜杠命令注册表：命令名 -> 说明，补全面板与 /help 共用
COMMANDS = [
    ("/help", "显示所有命令帮助"),
    ("/new", "新建会话并自动保存当前会话"),
    ("/save", "保存当前会话"),
    ("/resume", "恢复历史会话"),
    ("/compact", "压缩上下文"),
    ("/clear", "清空消息区显示"),
    ("/model", "查看当前模型与配置"),
    ("/quit", "退出程序"),
]

BUBBLE_CSS = {
    "you": ("#58a6ff", "rgba(31,111,235,0.13)", "#1f6feb"),
    "ai": ("#3fb950", "rgba(35,134,54,0.15)", "#238636"),
    "tool": ("#d29922", "rgba(158,106,3,0.15)", "#9e6a03"),
    "err": ("#f85149", "rgba(218,54,51,0.15)", "#da3633"),
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
    "recallContext": "检索历史上下文",
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
    "recallContext": "上下文管理",
}


class Bubble(Static):
    """消息气泡，按角色着色。禁用 markup，避免 [ ] 等字符被当作格式解析。"""

    def __init__(self, content="", **kwargs):
        super().__init__(content, markup=False, **kwargs)


class ChatInput(TextArea):
    """多行输入框：Enter 发送，Shift+Enter 换行。

    输入以 / 开头时弹出命令补全面板：Tab / ↑↓ 切换选择，
    Enter 执行选中的命令，Esc 关闭面板。
    """

    def on_key(self, event):
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.app._submit()
        elif event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
        elif event.key == "tab":
            if self.app._cmd_panel_visible():
                event.stop()
                event.prevent_default()
                self.app._cycle_cmd(down=True)
        elif event.key in ("up", "down"):
            if self.app._cmd_panel_visible():
                event.stop()
                event.prevent_default()
                self.app._cycle_cmd(down=(event.key == "down"))
        elif event.key == "escape":
            if self.app._cmd_panel_visible():
                event.stop()
                event.prevent_default()
                self.app._hide_cmd_panel()

    def on_text_area_changed(self, event):
        """输入内容变化时刷新命令补全面板。"""
        self.app._refresh_cmd_panel()


class SessionListScreen(ModalScreen):
    """历史会话选择面板：↑↓ 选择，Enter 恢复，Esc 取消。"""

    def __init__(self, sessions):
        super().__init__()
        self.sessions = sessions

    def compose(self) -> ComposeResult:
        with Vertical(id="session_dlg"):
            yield Static("历史会话（↑↓ 选择，Enter 恢复，Esc 取消）",
                         id="session_dlg_title")
            items = [ListItem(Label("%s  %d 条  %s" % (
                s["id"], s["count"], s["title"]))) for s in self.sessions]
            yield ListView(*items, id="session_list")

    def on_mount(self):
        self.query_one("#session_list", ListView).focus()

    def on_list_view_selected(self, event):
        idx = self.query_one("#session_list", ListView).index
        if idx is not None and 0 <= idx < len(self.sessions):
            self.dismiss(self.sessions[idx]["id"])

    def key_escape(self):
        self.dismiss(None)


class ConfigNotice(ModalScreen):
    """config.ini 缺失时提示用户修改后重新启动。"""

    def __init__(self, config_path):
        super().__init__()
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        with Vertical(id="cfg_dlg"):
            yield Static("配置提示", id="cfg_dlg_title")
            yield Static(
                "未找到 config.ini 配置文件，已自动创建模板：\n%s\n\n"
                "请用记事本打开该文件，填写真实的 api_key、base_url 和 "
                "model 后，重新启动程序才能正常工作。" % self.config_path,
                id="cfg_dlg_body")
            yield Button("知道了，退出", id="cfg_dlg_ok", variant="error")

    def on_button_pressed(self, event):
        if event.button.id == "cfg_dlg_ok":
            self.app.exit()

    def key_escape(self):
        self.app.exit()


class ChatTUI(App):
    """zAgent 终端聊天界面：左侧对话流，右侧功能清单与活动日志，底部状态条。"""

    TITLE = "zAgent"

    CSS = """
    Screen {
        background: #0d1117;
    }
    Header {
        background: #1f6feb;
        color: #ffffff;
        border-bottom: solid #1f6feb;
        text-style: bold;
    }
    #chat_col {
        width: 3fr;
        border-right: solid #30363d;
        background: #0d1117;
    }
    #tools_col {
        width: 2fr;
        background: #161b22;
    }
    #messages {
        height: 1fr;
        padding: 1 1;
        background: #0d1117;
    }
    Bubble {
        width: 100%;
        padding: 0 1;
        margin-bottom: 1;
    }
    #tools_title {
        background: #161b22;
        color: #58a6ff;
        padding: 0 1;
        text-style: bold;
        border-bottom: solid #30363d;
    }
    #func_list {
        height: auto;
        max-height: 40%;
        padding: 0 1;
        background: #161b22;
        border-bottom: solid #30363d;
    }
    .func_item {
        width: 100%;
        padding: 0 0;
        color: #8b949e;
    }
    .func_group {
        width: 100%;
        padding: 1 0 0 0;
        color: #e6edf3;
        text-style: bold;
        border-bottom: solid #21262d;
        margin-bottom: 1;
    }
    .func_used {
        color: #3fb950;
        text-style: bold;
    }
    #mcp_title {
        background: #161b22;
        color: #58a6ff;
        padding: 0 1;
        text-style: bold;
        border-bottom: solid #30363d;
    }
    #mcp_panel {
        height: auto;
        max-height: 30%;
        padding: 0 1;
        background: #161b22;
        border-bottom: solid #30363d;
    }
    #log_title {
        background: #161b22;
        color: #58a6ff;
        padding: 0 1;
        text-style: bold;
        border-bottom: solid #30363d;
    }
    #tool_list {
        height: 1fr;
        padding: 0 1;
        background: #161b22;
    }
    #status {
        height: 3;
        background: #161b22;
        border-top: solid #30363d;
        padding: 0 1;
        color: #8b949e;
    }
    #status_text {
        width: 1fr;
    }
    #status_ctx {
        width: auto;
        color: #8b949e;
    }
    #status_loading {
        width: auto;
    }
    #input_row {
        height: auto;
        padding: 0 1;
        background: #161b22;
        border-top: solid #30363d;
    }
    #input_loading {
        width: auto;
        height: 3;
        display: none;
    }
    #input {
        height: 3;
        border: round #30363d;
        background: #0d1117;
        color: #e6edf3;
    }
    #input:focus {
        border: round #58a6ff;
    }
    #ctx_bar {
        height: 1;
        background: #161b22;
        border-top: solid #30363d;
        padding: 0 1;
        color: #8b949e;
    }
    #cmd_panel {
        display: none;
        height: auto;
        max-height: 10;
        background: #21262d;
        border: round #58a6ff;
        padding: 0 1;
        color: #8b949e;
    }
    #session_dlg {
        width: 76;
        height: auto;
        max-height: 24;
        border: round #58a6ff;
        background: #161b22;
        padding: 1 2;
    }
    #session_dlg_title {
        color: #e6edf3;
        text-style: bold;
        padding-bottom: 1;
    }
    #session_list {
        height: auto;
        max-height: 18;
        border: none;
        background: #161b22;
    }
    #session_list > ListItem.list-view--highlight {
        background: #1f6feb;
        color: #ffffff;
    }
    #cfg_dlg {
        width: 76;
        height: auto;
        border: round #f85149;
        background: #161b22;
        padding: 1 2;
    }
    #cfg_dlg_title {
        color: #f85149;
        text-style: bold;
        padding-bottom: 1;
    }
    #cfg_dlg_body {
        color: #e6edf3;
        padding-bottom: 1;
    }
    Scrollbar {
        scrollbar-background: #21262d;
        scrollbar-color: #58a6ff;
    }
    """

    def __init__(self, agent, config_created=False, config_path=None):
        super().__init__()
        self.agent = agent
        self.config_created = config_created
        self.config_path = config_path
        self._func_widgets = {}
        self._cmd_selected = None       # 补全面板当前选中的命令
        self._cmd_index = 0
        self._cmd_matched = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="chat_col"):
                yield VerticalScroll(id="messages")
                yield Static("上下文 --", id="ctx_bar")
                yield Static("", id="cmd_panel")
                with Horizontal(id="input_row"):
                    yield LoadingIndicator(id="input_loading")
                    yield ChatInput(
                        id="input",
                        placeholder="输入消息，/ 查看命令，/compact 压缩上下文")
            with Vertical(id="tools_col"):
                yield Static("功能清单", id="tools_title")
                yield VerticalScroll(id="func_list")
                yield Static("MCP 服务器", id="mcp_title")
                yield VerticalScroll(id="mcp_panel")
                yield Static("活动日志", id="log_title")
                yield VerticalScroll(id="tool_list")
        with Horizontal(id="status"):
            yield LoadingIndicator(id="status_loading")
            yield Static("就绪，输入消息开始对话", id="status_text")
            yield Static("上下文 --", id="status_ctx")
        yield Footer()

    def on_mount(self):
        model = self.agent.model
        self.run_worker(self._build_func_list())
        self.run_worker(self._render_mcp_panel())
        skill_names = [s["name"] for s in getattr(self.agent, "skills", [])]
        hint = "可以问日期、闰年、文件操作"
        if skill_names:
            hint += "，技能：" + "、".join(skill_names)
        hint += "，输入 / 查看命令，/compact 压缩上下文"
        self._refresh_ctx()
        # MCP 在后台线程异步加载，完成后动态刷新边栏
        self.agent.start_mcp_loading()
        self.run_worker(self._add_bubble(
            "欢迎使用 zAgent，当前模型 %s\n%s" % (model, hint), "ai"))
        if self.config_created:
            self.push_screen(ConfigNotice(self.config_path))

    # ---------- 右侧功能清单 ----------
    async def _build_func_list(self):
        """在右侧生成分类功能清单：本地功能按类别细分，再列技能与 MCP 工具。"""
        box = self.query_one("#func_list", VerticalScroll)
        await box.remove_children()
        self._func_widgets = {}
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
                            "命令执行", "Office 文档", "技能管理",
                            "上下文管理", "其他功能"):
            items = by_group.get(group_title)
            if not items:
                continue
            add_group("◆ 功能：" + group_title)
            if group_title == "上下文管理":
                add_item("● /compact  手动压缩上下文", None)
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

        # 第三组：MCP 动态工具，按服务器分组
        mcp_tools = [t for t in getattr(self.agent, "tools", []) or []
                     if t["function"]["name"] in mcp_names]
        if mcp_tools:
            by_server = {}
            for tool in mcp_tools:
                full = tool["function"]["name"]
                server = full.split("__", 1)[0] if "__" in full else "MCP"
                short = full.split("__", 1)[1] if "__" in full else full
                by_server.setdefault(server, []).append((tool, short))
            for server, items in by_server.items():
                add_group("◆ MCP：" + server)
                for tool, short in items:
                    fn = tool["function"]
                    desc = (fn.get("description") or "")[:24]
                    label = ("● %s  %s" % (short, desc)
                             if desc else "● %s" % short)
                    add_item(label, full)

        for w, name in widgets:
            if name:
                self._func_widgets[name] = w
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

    # ---------- MCP 服务器边栏 ----------
    async def _render_mcp_panel(self):
        """渲染右侧 MCP 服务器区域：服务器名、状态与可用工具列表。"""
        panel = self.query_one("#mcp_panel", VerticalScroll)
        await panel.remove_children()
        agent = self.agent
        status = getattr(agent, "mcp_server_status", {})
        if not status:
            w = Static("未配置 MCP 服务器", markup=False)
            w.styles.color = "#8b949e"
            await panel.mount(w)
            return
        for server, st in status.items():
            names = []
            if st == "loading":
                label = "%s  连接中..." % server
                color = "#d29922"
            elif st == "ok":
                names = [t["function"]["name"][len(server) + 2:]
                         for t in agent.mcp_tools
                         if t["function"]["name"].startswith(server + "__")]
                label = "%s  %d 个工具" % (server, len(names))
                color = "#3fb950"
            else:
                label = "%s  连接失败" % server
                color = "#f85149"
            head = Static(label, markup=False)
            head.styles.color = color
            head.styles.text_style = "bold"
            await panel.mount(head)
            for name in names:
                sub = Static("  " + name, markup=False)
                sub.styles.color = "#8b949e"
                await panel.mount(sub)

    # ---------- 命令补全面板 ----------
    def _cmd_panel_visible(self):
        return self.query_one("#cmd_panel", Static).display

    def _hide_cmd_panel(self):
        self.query_one("#cmd_panel", Static).display = False
        self._cmd_selected = None
        self._cmd_matched = []

    def _cycle_cmd(self, down=True):
        """Tab / ↑↓ 循环切换补全面板中的选中命令。"""
        if not self._cmd_matched:
            return
        n = len(self._cmd_matched)
        if down:
            self._cmd_index = (self._cmd_index + 1) % n
        else:
            self._cmd_index = (self._cmd_index - 1) % n
        self._refresh_cmd_panel()

    def _refresh_cmd_panel(self):
        """输入以 / 开头时显示匹配的命令补全面板，否则隐藏。"""
        panel = self.query_one("#cmd_panel", Static)
        text = self.query_one("#input", TextArea).text.strip()
        if not text.startswith("/"):
            panel.display = False
            self._cmd_selected = None
            self._cmd_matched = []
            return
        word = text.split()[0].lower() if text else "/"
        matched = [c for c in COMMANDS if c[0].startswith(word)]
        if not matched:
            matched = COMMANDS
        self._cmd_matched = matched
        if self._cmd_index >= len(matched):
            self._cmd_index = 0
        self._cmd_selected = matched[self._cmd_index][0] if matched else None
        lines = []
        for cmd, desc in matched:
            if cmd == self._cmd_selected:
                lines.append("[bold #58a6ff]> %s  %s[/]" % (cmd, desc))
            else:
                lines.append("  %s  %s" % (cmd, desc))
        panel._render_markup = True
        panel.update("\n".join(lines))
        panel.display = True

    # ---------- 输入提交与命令分发 ----------
    def action_submit(self):
        """Enter 发送，Shift+Enter 由 TextArea 默认行为处理为换行。"""
        self._submit()

    def _submit(self):
        text_area = self.query_one("#input", TextArea)
        text = text_area.text.strip()
        if not text:
            return
        # 补全面板有选中命令时，回车执行选中的命令
        if text.startswith("/") and self._cmd_selected:
            text = self._cmd_selected
        text_area.text = ""
        self._hide_cmd_panel()
        lower = text.lower()
        if lower in EXIT_WORDS or lower == "/quit":
            self.exit()
            return
        if text.startswith("/"):
            self._handle_command(text)
            return
        if self.agent.compressing:
            self.run_worker(self._add_bubble(
                "正在压缩上下文中，暂时无法处理新消息，请稍候。", "err"))
            return
        if self.agent.context_blocked:
            self.run_worker(self._add_bubble(
                "上下文使用量已达阈值，已暂停接收新消息。"
                "请先输入 /compact 压缩上下文。", "err"))
            return
        self.run_worker(self._add_bubble("你  " + text, "you"))
        threading.Thread(target=self._run_chat, args=(text,),
                         daemon=True).start()

    def _handle_command(self, cmd):
        """按命令名分发到对应的处理函数。"""
        name = cmd.strip().split()[0].lower()
        if name == "/compact":
            if self.agent.busy:
                self.run_worker(self._add_bubble(
                    "当前对话还在处理中，请稍候再执行 /compact。", "err"))
                return
            self.run_worker(self._add_bubble("你  /compact", "you"))
            threading.Thread(target=self._run_compact, daemon=True).start()
        elif name == "/new":
            self._handle_new()
        elif name == "/save":
            self._handle_save()
        elif name == "/resume":
            self._handle_resume()
        elif name == "/help":
            lines = ["可用命令："]
            for c, desc in COMMANDS:
                lines.append("%s  %s" % (c, desc))
            lines.append("exit / quit  退出程序")
            self.run_worker(self._add_bubble("AI  " + "\n".join(lines), "ai"))
        elif name == "/clear":
            self.run_worker(self._clear_messages())
        elif name == "/model":
            info = ("模型：%s\n接口：%s\n上下文窗口：%d k（%s）" % (
                self.agent.model,
                getattr(self.agent.client, "base_url", "未知"),
                self.agent.context_window // 1024,
                "默认值" if self.agent.context_window == cm.DEFAULT_CONTEXT_WINDOW
                else "config.ini 配置"))
            self.run_worker(self._add_bubble("AI  " + info, "ai"))
        elif name == "/quit":
            self.exit()
        else:
            self.run_worker(self._add_bubble(
                "未知命令：%s。输入 /help 查看可用命令。" % cmd.strip(), "err"))

    def _handle_new(self):
        """/new：新建会话并自动保存当前会话历史。"""
        if self.agent.busy or self.agent.compressing:
            self.run_worker(self._add_bubble(
                "当前对话还在处理中，请稍候再执行 /new。", "err"))
            return
        saved = self.agent.new_session()
        self.run_worker(self._rebuild_messages_from_history())
        if saved:
            sid, path = saved
            msg = "已新建会话，旧会话已自动保存：%s（编号 %s）。" % (path, sid)
        else:
            msg = "已新建会话。"
        self.run_worker(self._add_bubble("AI  " + msg, "ai"))
        self._refresh_ctx()

    def _handle_save(self):
        """/save：保存当前会话。"""
        if len(self.agent.messages) <= 1:
            self.run_worker(self._add_bubble(
                "当前会话还没有对话内容，无需保存。", "err"))
            return
        sid, path = self.agent.save_session()
        self.run_worker(self._add_bubble(
            "AI  当前会话已保存：%s（编号 %s）。" % (path, sid), "ai"))

    def _handle_resume(self):
        """/resume：弹出历史会话列表面板，选择并恢复。"""
        sessions = list_sessions(self.agent.session_dir or None)
        if not sessions:
            self.run_worker(self._add_bubble(
                "还没有保存过的历史会话。先输入 /save 保存当前会话，"
                "或继续对话。", "err"))
            return
        self.push_screen(SessionListScreen(sessions), self._on_resume_dismiss)

    def _on_resume_dismiss(self, session_id):
        """会话面板关闭后的回调：None 表示取消，否则恢复对应会话。"""
        if not session_id:
            return
        ok, msg = self.agent.resume_session(session_id)
        if not ok:
            self.run_worker(self._add_bubble("错误  " + msg, "err"))
            return
        self.run_worker(self._rebuild_messages_from_history())
        self.run_worker(self._add_bubble("AI  " + msg, "ai"))
        self._refresh_ctx()

    async def _rebuild_messages_from_history(self):
        """清空消息区，把当前上下文的 user/assistant 消息重新渲染为气泡。"""
        box = self.query_one("#messages", VerticalScroll)
        await box.remove_children()
        for m in self.agent.messages:
            role = m.get("role")
            content = m.get("content")
            if not content:
                continue
            if role == "user":
                await self._add_bubble("你  " + content, "you")
            elif role == "assistant":
                await self._add_bubble("AI  " + content, "ai")

    async def _clear_messages(self):
        """/clear：仅清空消息区显示，不改变 Agent 的对话上下文。"""
        await self.query_one("#messages", VerticalScroll).remove_children()

    # ---------- 上下文压缩与对话 ----------
    def _run_compact(self):
        """后台线程执行上下文压缩，事件通过 call_from_thread 回主线程更新 UI。"""
        def emit(event_name, data):
            self.call_from_thread(self._on_event, event_name, data)

        self.agent.on_event = emit
        try:
            ok, msg = self.agent.compact()
            if not ok:
                self.call_from_thread(self._on_event, "compact_error",
                                      {"text": msg})
        except Exception as e:
            self.call_from_thread(self._on_event, "compact_error",
                                  {"text": str(e)})

    def _refresh_ctx(self):
        """从 Agent 读取当前上下文占用并刷新状态条。"""
        self._update_ctx_bar(self.agent.get_context_usage())

    def _update_ctx_bar(self, info):
        """更新上下文占用状态条：已用 k / 窗口 k，已占用百分比。

        同时在消息区下方状态条和底部状态栏各显示一份，
        保证任何终端布局下都能看到占用情况。
        """
        used_k = info["used"] / 1024.0
        window_k = info["window"] / 1024.0
        mark = " 默认" if info["window_defaulted"] else ""
        est = " 估算" if info["estimated"] else ""
        text = "上下文 %.1fk / %.1fk%s  已占用 %.1f%%%s" % (
            used_k, window_k, mark, info["ratio"] * 100, est)
        for wid in ("#ctx_bar", "#status_ctx"):
            w = self.query_one(wid, Static)
            w._render_markup = False
            w.update(text)
            if info["ratio"] >= 0.8:
                w.styles.color = "#f85149"
                w.styles.text_style = "bold"
            else:
                w.styles.color = "#8b949e"
                # Textual 的 text_style 不接受空字符串，清除样式必须用 None
                w.styles.text_style = None

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
        elif event_name == "usage":
            self._update_ctx_bar(data)
        elif event_name == "context_warning":
            self.run_worker(self._add_bubble(
                "上下文使用量已达窗口的 80%，已暂停接收新消息。"
                "请输入 /compact 压缩上下文后继续。", "err"))
            self._refresh_ctx()
        elif event_name == "compacting":
            stage = data.get("stage", "")
            if stage == "archive":
                self._set_status("正在保存原始上下文归档 %s..."
                                 % data.get("archive_id", ""), loading=True)
            else:
                self._set_status("正在压缩上下文...", loading=True)
        elif event_name == "compacted":
            self.run_worker(self._add_bubble(
                "上下文压缩完成，归档编号 %s，对话历史已替换为压缩摘要。"
                % data.get("archive_id", ""), "ai"))
            self._set_status("就绪，输入消息开始对话", loading=False)
            self._refresh_ctx()
        elif event_name == "compact_error":
            self.run_worker(self._add_bubble(
                "错误  " + data.get("text", ""), "err"))
            self._set_status("就绪，输入消息开始对话", loading=False)
        elif event_name == "error":
            self.run_worker(self._add_bubble("错误  " + data.get("text", ""),
                                             "err"))
            self._set_status("就绪，输入消息开始对话", loading=False)
        elif event_name == "mcp_loaded":
            self.run_worker(self._render_mcp_panel())
            self.run_worker(self._build_func_list())
            ok_count = sum(1 for st in data.get("servers", {}).values()
                           if st == "ok")
            if ok_count:
                self._set_status("MCP 工具加载完成，%d 个服务器可用"
                                 % ok_count, loading=False)
            else:
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
        line.styles.color = "#d29922"
        line.styles.background = "rgba(158,106,3,0.15)"
        line.styles.border = ("solid", "#9e6a03")
        await self.query_one("#tool_list", VerticalScroll).mount(line)
        self.query_one("#tool_list", VerticalScroll).scroll_end(
            animate=True)
