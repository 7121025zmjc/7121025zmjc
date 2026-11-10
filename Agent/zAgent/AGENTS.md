# AGENTS.md

本文件是给 AI 编程助手的长期规则。zAgent 启动时会把它作为一条 system 消息注入会话，后续每轮自动携带。
按 `[Session] agents_md_max_chars`（默认 8000 字符）截断，所以写最关键的约定，不要贴长文档。

## 项目背景

这是《大语言模型技术与应用》的课程作业仓库，四个子项目：PetHospitalMCP、AnythingLLMMCP、AnythingLLMWebPage、Agent/zAgent。

## 动手前先确认

- 只改用户明确指定范围内的文件；顺手重构、顺手改格式都算越界。
- 不确定目标文件是哪一个时，先读再改，不要按文件名猜。
- 改完一个文件立刻做语法/导入验证，不要把一堆改动攒到最后一起试。

## 代码约定

- 注释与回复一律用中文。
- `core/` 是纯逻辑层，不允许 import `tui/` 或 `web/`；前端依赖 core，反向依赖会让 TUI 与 Web 互相拖累。
- 新增工具必须三件套齐全：`TOOLS` 里的 schema、`call_tool` 里的分支、本文件或 README 里的说明。
- 工具的 `description` 要写清"什么时候该用"，模型靠这句话决定是否调用。
- 文件操作一律走 `_guard_path`，不要绕过 `[Safety] workspace_root` 直接 `open()`。
- 删除类工具保留 `confirm` 参数，不要为了方便把确认逻辑删掉。

## 配置约定

- 真实密钥只写本地 `config.ini`，仓库里只提交 `config.ini.example`。提交前 `git status` 检查一遍有没有把 `config.ini` 加进暂存区。
- 新增配置项时同步三处：`MOCK_CONFIG` 模板、`load_config()` 的返回字典、README 的配置说明。
- 配置读取必须有默认值，缺段缺键不能抛异常。

## 验证约定

- 改完 `core/` 下的代码，跑 `python -m unittest discover -s tests -v`。
- 改完 MCP 相关代码，至少手动跑一遍 `initialize → tools/list → tools/call`，确认协议层没退化。
- 报告"已修复"之前，给出实际命令输出，不要只说"应该可以了"。

## 提交约定

- 一次提交只做一件事，提交信息写清"改了什么、为什么"。
- 不提交 `venv/`、`*.db`、`*.egg-info/`、`config.ini`、`.mcp.json`。
- 不 force push，不改写已推送的历史，除非用户明确授权。
