# zAgent

在 `Agent/Practice02/chat-stream-tool-fileOps.py` 基础上重构的智能体项目，按软件工程规范实现 **core 业务层与前端界面层分离**，当前提供 Textual TUI 终端界面，未来可扩展 web、desktop、mobile 等前端。

## 项目结构

```
zAgent/
├── core/            # 业务逻辑层，不依赖任何界面实现
│   ├── config.py        # 从 config.ini 加载配置，缺省自动创建模板
│   ├── tools.py         # 本地工具注册表与实现，含 recallContext 历史检索
│   ├── mcp_client.py    # MCP 服务器连接与工具发现
│   ├── context_manager.py # 上下文估算、压缩归档、recallContext 检索
│   ├── session_store.py # 历史会话保存/恢复、AGENTS.md 读取、事件日志
│   ├── skill_loader.py  # 技能发现与系统提示组装
│   └── agent.py         # Agent 类：LLM 客户端与 function call 循环
├── tui/             # TUI 前端，Textual 终端聊天界面
│   └── app.py       # ChatTUI 应用，斜杠命令、补全面板、MCP 边栏
├── main.py          # 程序入口，选择使用哪个前端
├── config.ini       # 配置，已被 .gitignore 排除
└── requirements.txt # 依赖：openai、textual
```

新增 web、desktop、mobile 前端时，与 `tui` 平级创建目录，复用 `core` 全部业务代码，入口处替换前端调用即可。

## 快速开始

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python main.py
```

首次启动若没有 `config.ini`，会自动创建一份完整模板并弹窗提示，填写真实配置后重新启动即可。

## 斜杠命令

| 命令 | 功能 |
| --- | --- |
| /help | 显示所有命令帮助 |
| /new | 新建会话，自动保存当前会话历史 |
| /save | 保存当前会话到 .agents/sessions/ |
| /resume | 打开历史会话列表，选择并恢复 |
| /compact | 压缩上下文到约 5000 Tokens 摘要 |
| /clear | 清空消息区显示，不改动上下文 |
| /model | 查看模型、接口与上下文窗口配置 |
| /quit | 退出程序 |

输入以 `/` 开头时自动弹出补全引导面板：`Tab` / `↑↓` 切换选中命令，`Enter` 执行，`Esc` 关闭。`exit` / `quit` 直接退出。

## 配置说明

`config.ini` 分 5 段配置，缺失的键使用代码内默认值：

```ini
[LLM]
api_key = your_api_key_here
base_url = https://api.deepseek.com/v1
model = deepseek-chat
# context_window = 131072  不配置时默认按 128k 计算并在界面显示「默认」

[Agent]
timeout = 60          # LLM 请求超时秒数
max_messages = 30     # 消息历史保留条数上限

[Context]
warn_ratio = 0.8              # 上下文占用阈值，达到后暂停接收新消息
compress_target_tokens = 5000 # 压缩摘要目标 Token 数
archive_dir = .agents/context # 原始上下文归档目录

[Session]
session_dir = .agents/sessions # 历史会话保存目录

[Tools]
search_timeout = 30        # 联网搜索超时秒数
office_cli_exe = D:\office\... # officecli 可执行文件路径
```

## 上下文压缩与历史检索

- `/compact` 将原始上下文完整归档到 `.agents/context/`，调用 LLM 生成约 5000 Tokens 摘要替换历史，保留 System Prompt 与工具定义。
- 上下文使用量达到窗口 80% 时，界面显示警告并暂停接收新消息，压缩成功后自动解除。
- `recallContext` 工具按关键词检索历史归档，LLM 在摘要缺少细节时主动调用，无需向量数据库。

## AGENTS.md

项目根目录存在 `AGENTS.md` 时，会话启动时将其作为一条 system 消息注入聊天历史并写入事件日志 `.agents/log/events.jsonl`，后续每轮请求自动携带，实现一次注入、全程复用。

## MCP 服务器

MCP 服务器在后台线程异步加载：先进入界面，连接与工具发现完成后动态刷新右侧「MCP 服务器」区域，显示每个服务器的连接状态与可用工具。

## 架构说明

- core 层不导入任何 UI 组件，UI 层只调用 core 的公开接口，单向依赖。
- Agent 类内部维护会话上下文，执行 function call 循环：请求模型、执行工具、结果回传、递归至得到最终回答。
- 前端切换只需替换 `main.py` 中的前端入口，业务代码零改动。
