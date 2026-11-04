# -*- coding: utf-8 -*-
"""zAgent 入口：core 业务与 ui 界面解耦，切换界面只需替换 run 的调用。

启动时若项目根目录没有 config.ini，会自动创建一份完整模板
（填写 mock 信息），并在 TUI 中弹窗提示用户修改后重新启动。
"""
import sys

# Windows 老终端默认 GBK 编码，强制 UTF-8 输出，避免中文显示成 unicode 转义
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from core.agent import Agent
import core.config as config_mod


def run():
    try:
        cfg, created = config_mod.ensure_config_file()
    except (FileNotFoundError, ValueError) as e:
        print("[配置错误] %s" % e)
        sys.exit(1)

    agent = Agent(cfg)

    # 前端入口：需要开发 web / desktop / mobile 版本时，替换此处即可
    from tui.app import ChatTUI
    ChatTUI(agent, config_created=created,
            config_path=config_mod.CONFIG_FILE).run()


if __name__ == "__main__":
    run()
