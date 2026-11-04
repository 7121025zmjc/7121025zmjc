# -*- coding: utf-8 -*-
"""MCP Client 模块：读取 .mcp.json 配置，连接外部 MCP Server，
动态发现并注册工具，支持通过 Function Calling 调用。

支持两种协议：
  - streamable_http：MCP Server 以 HTTP 端点提供（如魔搭社区 MCP 广场）
  - stdio：MCP Server 为本地可执行程序，通过标准输入输出通信
"""
import asyncio
import json
import logging
import os
import sys

# 静默 MCP SDK 的日志噪音：部分服务器会把启动横幅写到 stdout，
# 触发 SDK 打印 "Failed to parse JSONRPC message from server" 的完整堆栈。
# 该横幅不影响工具发现与调用，本模块已有独立的错误收集与提示。
logging.getLogger("mcp").setLevel(logging.CRITICAL)

# 丢弃 MCP 子进程的 stderr 输出，避免服务器自己的日志直接刷到终端窗口
_DEVNULL = open(os.devnull, "w", encoding="utf-8")

# ---- 配置查找 ----
MCP_CONFIG_NAME = ".mcp.json"
# 配置文件查找顺序：当前项目目录 -> 本代码所在项目根目录
_CWD = os.getcwd()
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_mcp_config():
    """按顺序查找 .mcp.json：当前项目目录优先，再退回代码项目目录。"""
    for base in (_CWD, _PROJECT_DIR):
        candidate = os.path.join(base, MCP_CONFIG_NAME)
        if os.path.isfile(candidate):
            return candidate
    return None


def load_mcp_config(path=None):
    """读取 .mcp.json，返回 {"server名": {"type": ..., "url": ...}}。

    path 可以是 .mcp.json 文件路径，也可以直接是已解析的配置字典。
    """
    if isinstance(path, dict):
        data = path
        # 兼容两种写法：{"mcpServers": {...}} 或直接 {"server名": {...}}
        servers = data.get("mcpServers")
        if servers is None:
            servers = data
        if not isinstance(servers, dict):
            return {"__error__": "配置中缺少 mcpServers 对象"}
        return servers
    path = path or find_mcp_config()
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"__error__": "读取 %s 失败：%s" % (path, e)}
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return {"__error__": ".mcp.json 中缺少 mcpServers 对象"}
    return servers


def _tool_to_openai(tool):
    """把 MCP 的 Tool 对象转为 OpenAI Function Calling 格式。"""
    name = tool.name or ""
    description = (tool.description or "")[:200] or "由 MCP Server 提供的工具"
    schema = getattr(tool, "input_schema", None) or getattr(
        tool, "inputSchema", None) or {}
    if not isinstance(schema, dict):
        schema = {}
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict):
        properties = {}
    if not isinstance(required, list):
        required = []
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _json_text(value):
    """把 MCP 工具返回的内容序列化为可读文本。"""
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        text = str(value)
    if len(text) > 3000:
        text = text[:3000] + "\n...(内容过长已截断)"
    return text


async def _list_tools_http(url, server_name, prefix):
    """通过 streamable_http 协议连接并列出工具。"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    tools = []
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            for tool in result.tools:
                openai_tool = _tool_to_openai(tool)
                openai_tool["function"]["name"] = prefix + openai_tool["function"]["name"]
                tools.append(openai_tool)
    return tools


async def _list_tools_stdio(params, server_name, prefix):
    """通过 stdio 协议连接并列出工具。"""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    tools = []
    async with stdio_client(params, errlog=_DEVNULL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            for tool in result.tools:
                openai_tool = _tool_to_openai(tool)
                openai_tool["function"]["name"] = prefix + openai_tool["function"]["name"]
                tools.append(openai_tool)
    return tools


def list_server_tools(server_name, config):
    """连接单个 MCP Server 并返回其工具列表（OpenAI 格式）。

    返回 (tools, error)：tools 为空列表时 error 为错误说明。
    """
    prefix = server_name + "__"
    server_type = (config.get("type") or "streamable_http").lower()
    try:
        if server_type in ("streamable_http", "http", "https", "sse"):
            url = config.get("url") or ""
            if not url:
                return [], "MCP Server %s 缺少 url 配置" % server_name
            return (asyncio.run(_list_tools_http(url, server_name, prefix)), None)
        if server_type == "stdio":
            from mcp.client.stdio import StdioServerParameters
            command = config.get("command") or ""
            if not command:
                return [], "MCP Server %s 缺少 command 配置" % server_name
            args = config.get("args", []) or []
            env = config.get("env")
            cwd = config.get("cwd")
            params = StdioServerParameters(command=command, args=args,
                                           env=env, cwd=cwd)
            return (asyncio.run(_list_tools_stdio(params, server_name, prefix)), None)
        return [], "MCP Server %s 的 type 不支持：%s（仅支持 streamable_http / stdio）" % (
            server_name, server_type)
    except Exception as e:
        detail = str(e)
        # TaskGroup 包装的异常，展开子异常便于定位
        if isinstance(e, BaseExceptionGroup):
            sub = [str(x) for x in e.exceptions]
            detail += " | 子异常: " + "; ".join(sub[:5])
        return [], "连接 MCP Server %s 失败：%s" % (server_name, detail)


def discover_mcp_tools(config_path=None):
    """读取 .mcp.json，连接所有 Server，返回动态工具列表和映射表。

    返回 (tools, mapping, errors)：
      tools   - OpenAI Function Calling 格式的工具定义列表
      mapping - {"带前缀的工具名": {"server": ..., "tool": 原名}}，用于调用时反查
      errors  - 各 Server 连接失败的说明列表
    """
    servers = load_mcp_config(config_path)
    if "__error__" in servers:
        return [], {}, [servers["__error__"]]
    if not servers:
        return [], {}, []

    tools = []
    mapping = {}
    errors = []
    for server_name, config in servers.items():
        if not isinstance(config, dict):
            errors.append("MCP Server %s 配置不是对象，已跳过" % server_name)
            continue
        server_tools, err = list_server_tools(server_name, config)
        if err:
            errors.append(err)
            continue
        for t in server_tools:
            full_name = t["function"]["name"]
            tools.append(t)
            mapping[full_name] = {
                "server": server_name,
                "tool": full_name[len(server_name) + 2:],
            }
    return tools, mapping, errors


async def _call_tool_http(url, tool_name, arguments):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments or {})
            return result


async def _call_tool_stdio(params, tool_name, arguments):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(params, errlog=_DEVNULL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments or {})
            return result


def call_mcp_tool(mapping_entry, arguments, config=None):
    """根据映射条目调用 MCP 工具，返回文本结果或错误。"""
    if not mapping_entry:
        return "错误：未找到对应的 MCP 工具映射。"
    server_name = mapping_entry["server"]
    tool_name = mapping_entry["tool"]
    servers = load_mcp_config(config)
    if "__error__" in servers:
        return servers["__error__"]
    config_obj = servers.get(server_name)
    if not config_obj:
        return "错误：未找到 MCP Server %s 的配置" % server_name
    server_type = (config_obj.get("type") or "streamable_http").lower()
    try:
        if server_type in ("streamable_http", "http", "https", "sse"):
            url = config_obj.get("url") or ""
            if not url:
                return "错误：MCP Server %s 缺少 url" % server_name
            result = asyncio.run(_call_tool_http(url, tool_name, arguments))
        elif server_type == "stdio":
            from mcp.client.stdio import StdioServerParameters
            command = config_obj.get("command") or ""
            if not command:
                return "错误：MCP Server %s 缺少 command" % server_name
            params = StdioServerParameters(
                command=command,
                args=config_obj.get("args", []) or [],
                env=config_obj.get("env"),
                cwd=config_obj.get("cwd"))
            result = asyncio.run(_call_tool_stdio(params, tool_name, arguments))
        else:
            return "错误：MCP Server %s 的 type 不支持：%s" % (server_name, server_type)
    except Exception as e:
        return "错误：调用 MCP 工具 %s 失败：%s" % (tool_name, e)

    parts = []
    if result.content:
        for item in result.content:
            if hasattr(item, "text") and item.text:
                parts.append(item.text)
            elif hasattr(item, "type") and item.type == "image":
                parts.append("[图片结果]")
            elif hasattr(item, "type") and item.type == "audio":
                parts.append("[音频结果]")
    if getattr(result, "is_error", False) and not parts:
        parts.append("MCP 工具返回错误（无内容）")
    structured = getattr(result, "structured_content", None)
    if structured:
        parts.append("结构化数据：\n" + _json_text(structured))
    return "MCP 工具 %s 调用结果：\n%s" % (tool_name, "\n".join(parts) if parts else "(空结果)")
