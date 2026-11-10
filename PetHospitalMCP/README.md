# Pet Hospital MCP

这是一个独立的 Python MCP 服务，用于把现有 Go 宠物医院 REST API 暴露给 AI Agent / MCP Client。

它基于官方 Python SDK 提供的 `FastMCP` 封装（`mcp.server.fastmcp`），协议版本由 SDK 决定，不自行实现 JSON-RPC 底层。

## 目标

- 让 AI Agent 能通过标准 MCP 调用现有宠物医院系统
- 以“工具 + 资源”的方式封装 Go API 能力
- 支持本地/内网运行，并默认连接本机 `http://127.0.0.1:8080`
- 允许以 `stdio` 或 `streamable-http` 方式启动，适配不同 host / client

## 现有 Go API 能力映射

该服务包装了仓库根目录的 REST API，核心覆盖：

- 健康检查：`/health`
- 接口目录：`/api/v1/endpoints`
- 枚举元数据：`/api/v1/meta`
- 经营统计：`/api/v1/stats`
- 宠物 CRUD：列表、查询、新增、更新、删除
- 高级查询：搜索、按主人、按医生、按种类、按疾病、按状态
- 历史病历与消费明细：读取与追加
- 导出：JSON / CSV

## 运行方式

### 1) 安装依赖

```bash
cd PetHospitalMCP
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2) 启动 Go 宠物医院 API

确保根目录的 Go 服务正在本地运行：

```bash
# 示例：在仓库根目录启动
pethospital.exe -addr 127.0.0.1:8080
# 或根据实际安装方式运行 Go 服务
```

### 3) 启动 MCP 服务

默认以 stdio 方式运行，适合 Claude Desktop、VS Code Copilot 等 MCP host：

```bash
python -m pet_hospital_mcp.server
```

也可以显式选择 transport：

```bash
python -m pet_hospital_mcp.server --transport stdio
python -m pet_hospital_mcp.server --transport streamable-http --host 127.0.0.1 --port 8000
```

## 环境变量

```bash
PET_HOSPITAL_API_BASE_URL=http://127.0.0.1:8080
```

## 主要工具

服务共注册 **22 个 MCP 工具**，与 `src/pet_hospital_mcp/server.py` 一一对应：

| 分组 | 工具 |
| --- | --- |
| 服务自检 | `health_check`、`list_endpoints`、`get_meta`、`get_stats` |
| 宠物档案 CRUD | `list_pets`、`get_pet`、`create_pet`、`update_pet`、`patch_pet`、`delete_pet`、`search_pets`、`export_pets` |
| 条件查询 | `find_pets_by_owner`、`find_pets_by_doctor`、`find_pets_by_species`、`find_pets_by_disease`、`find_pets_by_status` |
| 病历与消费 | `get_pet_records`、`add_pet_record`、`get_pet_charges`、`add_pet_charge`、`get_pet_summary` |

## 资源

- `pet-hospital://meta`
- `pet-hospital://endpoints`
- `pet-hospital://pets/{pet_id}`

## 设计说明

这个服务本质上是一个“Go REST API 的 MCP 适配层”。它不改写、复制或迁移 Go 应用本身，而是让 AI Agent 以标准 MCP 方式访问它。

因此，所有真实业务逻辑仍然保留在 Go 服务中，Python 侧负责：

1. 生成标准 MCP 工具签名
2. 统一参数校验与错误处理
3. 调用 Go REST 接口
4. 把结果转换成 MCP 友好的 JSON 结构

## 安全提醒

该 Go 服务本身无鉴权，默认仅适合本地/内网使用。MCP 服务也不应直接暴露到公网。

## 规范依据

- 官方 Python SDK：`mcp`（`>=1.2.0,<2`），工具与资源由 `mcp.server.fastmcp.FastMCP` 生成
- 传输方式：stdio（默认）与 streamable-http，均由 SDK 提供，不在本仓库手写协议层
- 参数校验与错误语义：`httpx` 调用上游，异常统一收敛为 `PetHospitalAPIError`（含 HTTP 状态码与可读原因）

