# Pet Hospital MCP 运行说明

以下命令都以**仓库根目录**为起点，不写死任何本机绝对路径。

## 1. 启动 Go 宠物医院 API

上游是一个独立编译的 Go 服务，二进制不在本仓库内，按你的实际位置启动即可：

```bat
pethospital.exe -addr 127.0.0.1:8080
```

如果是源码模式：

```bat
go run . -addr 127.0.0.1:8080
```

只要它监听 `127.0.0.1:8080`，后面的步骤都能对上。

## 2. 启动 MCP 服务（HTTP 模式，适合调试）

```bat
cd PetHospitalMCP
python -m pet_hospital_mcp.server --transport streamable-http --host 127.0.0.1 --port 8000
```

MCP 地址：

```text
http://127.0.0.1:8000/mcp
```

如果你同时跑 `AnythingLLMMCP`，它默认也占 8000，请给其中一个换端口。

## 3. 先验证底层 Go API

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/api/v1/stats
curl "http://127.0.0.1:8080/api/v1/pets?page=1&pageSize=3"
```

三条都返回 200 再往下走，否则 MCP 的每个工具都会跟着报错。

## 4. 运行 smoke test

```bat
cd PetHospitalMCP
python scripts\smoke_test.py
```

它直接打上游 REST 接口并打印返回摘要，用来确认"上游通了、数据形状没变"。

## 5. MCP 配置（stdio 模式）

```json
{
  "servers": {
    "pet-hospital": {
      "command": "python",
      "args": [
        "-m",
        "pet_hospital_mcp.server",
        "--transport",
        "stdio"
      ],
      "env": {
        "PET_HOSPITAL_API_BASE_URL": "http://127.0.0.1:8080",
        "MCP_TRANSPORT": "stdio"
      },
      "cwd": "./PetHospitalMCP"
    }
  }
}
```

`cwd` 用相对路径时，取决于启动 MCP host 时的工作目录；用虚拟环境里的 python 更稳妥，例如把 `command` 换成 `PetHospitalMCP/.venv/Scripts/python.exe`。

## 6. 关键验证点

- `GET /health` 返回 200
- `GET /api/v1/pets` 返回宠物列表
- MCP HTTP 地址 `127.0.0.1:8000/mcp` 可访问
- `list_pets` 工具能被 MCP client 调用，返回带 `data` 解包后的列表
- 传一个不存在的 `pet_id` 给 `get_pet`，应返回带 HTTP 404 的结构化错误，而不是裸异常
