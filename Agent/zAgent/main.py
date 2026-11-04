# -*- coding: utf-8 -*-
"""zAgent 入口：core 业务与 ui 界面解耦，切换界面只需替换 run 的调用。

启动时若项目根目录没有 config.ini，会自动创建一份完整模板
（填写 mock 信息），并在界面中提示用户修改后重新启动。

界面选择：
  python main.py            启动 TUI 终端界面
  python main.py --web      启动 Web 图形界面并自动打开浏览器
"""
import sys

# Windows 老终端默认 GBK 编码，强制 UTF-8 输出，避免中文显示成 unicode 转义
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from core.agent import Agent
import core.config as config_mod


def run():
    use_web = "--web" in sys.argv

    try:
        cfg, created = config_mod.ensure_config_file()
    except (FileNotFoundError, ValueError) as e:
        print("[配置错误] %s" % e)
        sys.exit(1)

    agent = Agent(cfg)

    if use_web:
        run_web(agent, created)
    else:
        run_tui(agent, created)


def run_tui(agent, created):
    """TUI 终端界面。"""
    from tui.app import ChatTUI
    ChatTUI(agent, config_created=created,
            config_path=config_mod.CONFIG_FILE).run()


def run_web(agent, created):
    """Web 图形界面：本地起 Flask 服务并自动打开浏览器。"""
    import logging
    import threading
    import webbrowser

    from web.server import build_app

    # 静默 Flask 开发服务器的警告与请求日志，避免终端窗口出现黄色提示与刷屏
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    app = build_app(agent)
    # MCP 在后台线程异步加载，页面加载后动态刷新
    agent.start_mcp_loading()

    port = 8666
    url = "http://127.0.0.1:%d" % port
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    if created:
        print("[提示] 已自动创建 config.ini 模板：%s" % config_mod.CONFIG_FILE)
        print("[提示] 请修改为真实配置后重新启动，否则 LLM 无法正常响应。")
    print("zAgent Web 界面已启动：%s" % url)
    print("关闭浏览器页面时按 Ctrl+C 退出。")
    try:
        app.run(host="127.0.0.1", port=port, threaded=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
