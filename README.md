# Schoolwork

《大语言模型技术与应用》课程项目仓库。四个子项目互相独立、可单独运行：宠物医院 MCP、AnythingLLM MCP、AnythingLLM 网页、定制 Agent zAgent。

## 目录结构与模块关系

| 目录 | 角色 | 说明 |
| --- | --- | --- |
| `PetHospitalMCP/` | MCP 服务端 | 把 Go 宠物医院 REST API 封装成 22 个 MCP 工具 + 3 个资源，支持 stdio 与 streamable-http |
| `AnythingLLMMCP/` | MCP 服务端 | 把 AnythingLLM 本地 API 封装成 5 个 MCP 工具，Streamable HTTP 传输 |
| `AnythingLLMWebPage/` | 独立网页 | Flask 网页，直连 AnythingLLM API 完成"上传 + 向量化"闭环 |
| `Agent/zAgent/` | Agent 客户端 | LLM 智能体，本地工具集 + 技能系统 + MCP 客户端 + AGENTS.md 注入，含 TUI 与 Web 双前端 |
| `Agent/Practice01/` | 课程练习 | 多轮对话、流式输出等基础练习代码 |
| `Agent/Practice02/` | 课程练习 | 单个工具的 function call 练习，含 Mermaid 时序图 |

模块之间的真实关系：

- `PetHospitalMCP` 与 `AnythingLLMMCP` 都是 **MCP 服务端**，本身不含 LLM，需要上游服务在本地跑起来才能返回数据。
- `Agent/zAgent` 是 **MCP 客户端**，通过 `Agent/zAgent/.mcp.json` 把上面两个 server 挂进自己的可调用工具集，工具名会加 `服务器名__` 前缀以标明来源。
- `AnythingLLMWebPage` 与 `AnythingLLMMCP` 共用同一个上游 AnythingLLM，但互不依赖：网页是给人用的，MCP 是给模型用的。

## 环境依赖

| 依赖 | 版本/说明 |
| --- | --- |
| Python | 3.10 及以上，已在 3.13 验证 |
| 上游：宠物医院 API | Go 服务，默认 `http://127.0.0.1:8080`，需先启动 |
| 上游：AnythingLLM | 桌面版或本地服务，默认 `http://localhost:3001`，需先启动并建好工作区 |
| Node.js | 仅 `zAgent` 的 `Bazi-MCP` 需要 `npx`，没有可以删掉该 server |
| curl | `zAgent` 的 `fetchWeb` 用系统 curl 抓网页，Windows 10+ 自带 |
| officecli | 可选，`zAgent` 生成/修改 Office 文档时需要，路径在 `config.ini` 的 `[Tools] office_cli_exe` 配置 |

各子项目的 Python 依赖都在自身的 `requirements.txt` 里：

| 子项目 | 依赖 |
| --- | --- |
| `PetHospitalMCP` | `mcp>=1.2.0,<2`、`httpx>=0.27,<1`、`pydantic>=2.7,<3` |
| `AnythingLLMMCP` | 无第三方依赖，纯标准库 |
| `AnythingLLMWebPage` | `flask>=3.0,<4`、`requests>=2.31,<3` |
| `Agent/zAgent` | `openai`、`textual`、`trafilatura`、`mcp`、`flask` |

## 配置方式

所有密钥都放在各子项目的 `config.ini`，该文件已被 `.gitignore` 排除，**不会入库**。仓库里只提交模板文件：

| 子项目 | 配置文件 | 模板 |
| --- | --- | --- |
| `PetHospitalMCP` | 环境变量 | `.env.example` |
| `AnythingLLMMCP` | `config.ini` | `config.ini.example` |
| `AnythingLLMWebPage` | `config.ini` | `config.ini.example` |
| `Agent/zAgent` | `config.ini` | 首次启动自动生成，字段说明见 `Agent/zAgent/README.md` |

`AnythingLLMMCP` 与 `AnythingLLMWebPage` 也支持环境变量优先：`ANYTHINGLLM_API_KEY`、`ANYTHINGLLM_BASE_URL`。

## 分步启动

### 1. 宠物医院 MCP

```bash
cd PetHospitalMCP
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
# 先在另一个终端启动 Go 宠物医院 API（监听 127.0.0.1:8080）
python -m pet_hospital_mcp.server --transport stdio
# 或走 HTTP：python -m pet_hospital_mcp.server --transport streamable-http --port 8000
```

详细步骤与验证命令见 `PetHospitalMCP/RUN.md`。

### 2. AnythingLLM MCP

```bash
cd AnythingLLMMCP
copy config.ini.example config.ini    # 填入 api_key 与 base_url
python server.py                      # 端点 http://127.0.0.1:8000/mcp
```

### 3. AnythingLLM 网页

```bash
cd AnythingLLMWebPage
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy config.ini.example config.ini     # 同上
python app.py                         # 浏览器打开 http://127.0.0.1:5000
```

### 4. 定制 Agent zAgent

```bash
cd Agent/zAgent
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python main.py           # TUI 终端界面
venv\Scripts\python main.py --web     # Web 界面，自动打开 http://127.0.0.1:8666
```

首次启动若没有 `config.ini`，会自动生成一份带注释的模板，填好 `[LLM]` 段的 `api_key` / `base_url` / `model` 后重启即可。

想启用 MCP 客户端能力：把 `Agent/zAgent/.mcp.json.example` 复制成 `.mcp.json`，改成自己的 server 清单。注意 `PetHospitalMCP` 那条要指向本机 venv 里的 python 可执行文件。

## 验证清单

按顺序做完，四个子项目就都是可用的真实状态：

1. `curl http://127.0.0.1:8080/health` 返回 200，说明宠物医院上游在线。
2. `python PetHospitalMCP/scripts/smoke_test.py` 能打印 `/api/v1/stats` 与宠物列表。
3. `python AnythingLLMMCP/server.py` 后，用任意 MCP 客户端 `tools/list` 能看到 5 个工具。
4. 打开 `http://127.0.0.1:5000`，选文件上传，页面出现"已上传…并完成向量化嵌入"。
5. `cd Agent/zAgent && venv\Scripts\python -m unittest discover -s tests -v` 全部通过。
6. zAgent 里输入"现在几点"，能看到它真的调用了 `getDate` 工具而不是瞎猜。

## 安全说明

- 真实 `api_key` 只在本地 `config.ini` 里，仓库只提交模板。
- 如果某次提交曾经把密钥带进 git 历史，**改代码不能消除历史泄露**，必须到对应平台（AnythingLLM、DeepSeek、硅基流动等）吊销并重新生成。
- `PetHospitalMCP` 的 Go 服务本身无鉴权，只能在本机/内网跑，不要暴露到公网。
