# AnythingLLM MCP

把本地 AnythingLLM 的 API 封装成标准 MCP 工具，让 AI Agent 能直接查工作区、上传文档、基于知识库问答。

- 传输方式：Streamable HTTP，`POST /mcp` 收发 JSON-RPC 2.0，`initialize` 时下发 `Mcp-Session-Id`，客户端后续请求带上该头即复用会话
- 依赖：纯 Python 标准库，无需 `pip install` 任何包
- 默认端点：`http://127.0.0.1:8000/mcp`

## 工具清单

| 工具 | 能力类别 | 参数 | 说明 |
| --- | --- | --- | --- |
| `list_workspaces` | 工作区管理 | 无 | 列出所有工作区，返回名称、slug、文档数 |
| `create_workspace` | 工作区管理 | `name` 必填 | 新建工作区 |
| `list_workspace_documents` | 文档管理 | `slug` 可选 | 列出工作区文档标题与存放位置，不传 slug 用第一个工作区 |
| `upload_document` | 文档上传 + 向量化 | `file_path` 必填，`slug`、`embed` 可选 | 上传本地文件并调用 `update-embeddings` 完成嵌入 |
| `chat_with_workspace` | 对话 / 检索问答 | `message` 必填，`slug`、`mode` 可选 | `mode=chat` 对话，`mode=query` 仅检索；回答基于该工作区文档 |

对应的 AnythingLLM 上游端点：`/api/v1/workspaces`、`/api/v1/workspace/new`、`/api/v1/workspace/{slug}`、`/api/v1/document/upload`、`/api/v1/workspace/{slug}/update-embeddings`、`/api/v1/workspace/{slug}/chat`。

## 配置

复制模板后填真实值：

```bash
copy config.ini.example config.ini
```

```ini
[ANYTHINGLLM]
api_key = your_api_key_here
base_url = http://localhost:3001
```

也可以用环境变量覆盖，优先级更高：

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `ANYTHINGLLM_API_KEY` | API Key | 无，缺了会拒绝启动 |
| `ANYTHINGLLM_BASE_URL` | AnythingLLM 地址 | `http://localhost:3001` |
| `MCP_PORT` | 本服务监听端口 | `8000` |

`config.ini` 已被 `.gitignore` 排除，不会进仓库；仓库只提交 `config.ini.example`。

## 启动

```bash
python server.py
```

正常输出：

```text
AnythingLLM MCP 已启动：http://127.0.0.1:8000/mcp
上游 AnythingLLM：http://localhost:3001
```

如果想同时跑 `PetHospitalMCP` 的 HTTP 传输，两边都占 8000 会冲突，给这个服务换端口：

```bash
set MCP_PORT=8001 && python server.py
```

## 接入客户端

在任意 MCP 客户端里登记（以 `Agent/zAgent/.mcp.json` 的格式为例）：

```json
{
  "mcpServers": {
    "AnythingLLM": {
      "type": "streamable_http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

zAgent 会把工具名加上 `AnythingLLM__` 前缀，所以最终可调用名形如 `AnythingLLM__list_workspaces`。

## 验证

不接客户端也能直接验证协议层：

```bash
curl -s -X POST http://127.0.0.1:8000/mcp -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}"
```

应返回 5 个工具定义。再试一次工具调用：

```bash
curl -s -X POST http://127.0.0.1:8000/mcp -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"list_workspaces\",\"arguments\":{}}}"
```

## 失败场景

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 启动直接退出，提示填入 api_key | 没配置密钥 | 填 `config.ini` 或设环境变量 |
| 工具返回 `HTTP 0 网络错误：目标计算机积极拒绝` | AnythingLLM 没启动或端口不对 | 先打开 AnythingLLM，确认 `base_url` |
| 工具返回 `HTTP 401` | API Key 无效 | 到 AnythingLLM 的 Settings → API Keys 重新生成 |
| 工具返回 `HTTP 404` | 工作区 slug 不存在 | 先调 `list_workspaces` 取真实 slug |
| `upload_document` 返回超时 | 文件大或本地模型慢 | 换小文件重试，超时阈值 180 秒 |
| 返回 `isError: true` | 上游错误已被捕获 | 结果里带状态码与可读原因，可直接展示给用户 |

设计上刻意做了两件事：所有上游异常都收敛成 `isError` 结果而不是让请求 500；未知工具名返回明确错误而不是静默成功。
