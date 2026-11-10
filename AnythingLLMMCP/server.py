# -*- coding: utf-8 -*-
"""AnythingLLM MCP Server

把 AnythingLLM 的本地 API 封装为标准 MCP 工具，用标准库实现，无第三方依赖。
传输方式是 Streamable HTTP：JSON-RPC 2.0 通过 POST 收发，initialize 时下发
Mcp-Session-Id，客户端后续请求带上该头即可复用会话。

启动：python server.py
端点：http://127.0.0.1:8000/mcp
"""
import configparser
import json
import mimetypes
import os
import uuid
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.ini")
PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "anythingllm-mcp", "version": "1.1.0"}

SESSION_HEADER = "Mcp-Session-Id"
MAX_FILE_BYTES = 30 * 1024 * 1024
DEFAULT_TIMEOUT = 30


class AnythingLLMError(RuntimeError):
    """统一的 API 错误：带 HTTP 状态码与可读说明。"""

    def __init__(self, path, status, detail):
        super().__init__("%s 调用失败（HTTP %s）：%s" % (path, status, detail))
        self.path = path
        self.status = status
        self.detail = detail


def load_config():
    """配置优先级：环境变量 > config.ini > 默认值。"""
    api_key = os.getenv("ANYTHINGLLM_API_KEY", "")
    base_url = os.getenv("ANYTHINGLLM_BASE_URL", "")
    if not (api_key and base_url) and os.path.isfile(CONFIG_PATH):
        cfg = configparser.ConfigParser()
        cfg.read(CONFIG_PATH, encoding="utf-8")
        section = cfg["ANYTHINGLLM"] if "ANYTHINGLLM" in cfg else {}
        api_key = api_key or section.get("api_key", "")
        base_url = base_url or section.get("base_url", "")
    return api_key.strip(), (base_url or "http://localhost:3001").rstrip("/")


API_KEY, BASE_URL = load_config()
PORT = int(os.getenv("MCP_PORT", "8000"))


def _request(path, method="GET", payload=None, raw_body=None,
             content_type=None, timeout=DEFAULT_TIMEOUT):
    """发一个 AnythingLLM API 请求，失败时抛出结构化错误。"""
    headers = {"Authorization": "Bearer " + API_KEY, "Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        body = raw_body
        headers["Content-Type"] = content_type or "application/octet-stream"
    req = urllib.request.Request(BASE_URL + path, data=body, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        raise AnythingLLMError(path, e.code, detail or e.reason) from e
    except urllib.error.URLError as e:
        raise AnythingLLMError(path, 0, "网络错误：%s" % e.reason) from e
    except Exception as e:  # 超时、连接中断等
        raise AnythingLLMError(path, 0, str(e)) from e
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text[:500]}


def _workspace_list():
    return _request("/api/v1/workspaces").get("workspaces", []) or []


def _workspace_detail(slug):
    data = _request("/api/v1/workspace/" + slug)
    workspace = data.get("workspace", {})
    if isinstance(workspace, list):
        workspace = workspace[0] if workspace else {}
    if not isinstance(workspace, dict):
        workspace = {}
    return workspace


def _first_workspace_slug():
    workspaces = _workspace_list()
    if not workspaces:
        raise AnythingLLMError("/api/v1/workspaces", 200, "AnythingLLM 中没有任何工作区")
    return workspaces[0]["slug"]


def _resolve_slug(slug):
    """空 slug 时退回第一个工作区，兼容"默认工作区"用法。"""
    return (slug or "").strip() or _first_workspace_slug()


def _document_summary(doc):
    title = doc.get("filename", "?")
    metadata = doc.get("metadata")
    if isinstance(metadata, str) and metadata:
        try:
            title = json.loads(metadata).get("title", title)
        except Exception:
            pass
    return title


def tool_list_workspaces():
    workspaces = _workspace_list()
    if not workspaces:
        return "AnythingLLM 中没有任何工作区，请先在 AnythingLLM 里创建一个。"
    lines = ["共 %d 个工作区：" % len(workspaces)]
    for i, ws in enumerate(workspaces, 1):
        lines.append("%d. %s（slug：%s，文档 %s 个）" % (
            i, ws.get("name", "未命名"), ws.get("slug", ""),
            len(ws.get("documents") or [])))
    return "\n".join(lines)


def tool_create_workspace(name):
    if not name:
        return "错误：工作区名称不能为空。"
    data = _request("/api/v1/workspace/new", "POST", {"name": name})
    ws = data.get("workspace") or data.get("workspaceName") or {}
    if isinstance(ws, dict) and ws.get("slug"):
        return "已创建工作区 %s，slug：%s" % (ws.get("name", name), ws["slug"])
    return "已创建工作区 %s：%s" % (name, json.dumps(data, ensure_ascii=False)[:300])


def tool_list_documents(slug=""):
    slug = _resolve_slug(slug)
    workspace = _workspace_detail(slug)
    docs = workspace.get("documents") or []
    if not docs:
        return "工作区 %s 暂无文档。" % slug
    lines = ["工作区 %s 共 %d 个文档：" % (slug, len(docs))]
    for i, doc in enumerate(docs, 1):
        lines.append("%d. %s（%s）" % (i, _document_summary(doc),
                                     doc.get("docpath") or doc.get("location") or ""))
    return "\n".join(lines)


def tool_upload_document(file_path, slug="", embed=True):
    if not file_path:
        return "错误：file_path 不能为空。"
    path = os.path.expanduser(file_path)
    if not os.path.isfile(path):
        return "错误：文件不存在 %s" % path
    if os.path.getsize(path) > MAX_FILE_BYTES:
        return "错误：文件超过 30MB 上限，AnythingLLM 无法处理。"
    filename = os.path.basename(path)
    with open(path, "rb") as f:
        content = f.read()
    boundary = "----anythingllm" + uuid.uuid4().hex
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="file"; filename="%s"\r\n'
         % filename).encode("utf-8"),
        ("Content-Type: %s\r\n\r\n" % content_type).encode(),
        content,
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    data = _request("/api/v1/document/upload", "POST", raw_body=body,
                    content_type="multipart/form-data; boundary=%s" % boundary,
                    timeout=180)
    documents = data.get("documents") or []
    if not documents:
        return "上传完成但未返回文档信息：%s" % json.dumps(data, ensure_ascii=False)[:300]
    location = documents[0].get("location") or documents[0].get("docpath")
    message = "已上传 %s，location：%s" % (filename, location)
    if not embed:
        return message + "（未执行向量化，按 embed=False 处理）"
    slug = _resolve_slug(slug)
    _request("/api/v1/workspace/%s/update-embeddings" % slug, "POST",
             {"adds": [location], "deletes": []}, timeout=180)
    return message + "；已加入工作区 %s 并完成向量化。" % slug


def tool_chat_with_workspace(slug, message, mode="chat"):
    if not message:
        return "错误：message 不能为空。"
    slug = _resolve_slug(slug)
    if mode not in ("chat", "query"):
        return "错误：mode 只支持 chat 或 query。"
    data = _request("/api/v1/workspace/%s/chat" % slug, "POST",
                    {"message": message, "mode": mode}, timeout=180)
    text = data.get("text") or data.get("textResponse") or ""
    if text:
        return "工作区 %s 的回答：\n%s" % (slug, text)
    sources = data.get("sources") or []
    if sources:
        return "工作区 %s 未返回文本，命中 %d 条参考片段。" % (slug, len(sources))
    return "工作区 %s 未返回文本：%s" % (
        slug, json.dumps(data, ensure_ascii=False)[:300])


def _params(properties, required):
    return {"type": "object", "properties": properties, "required": required}


TOOLS = [
    {"name": "list_workspaces",
     "description": "列出 AnythingLLM 中的所有工作区，返回名称、slug 与文档数",
     "inputSchema": _params({}, [])},
    {"name": "create_workspace",
     "description": "在 AnythingLLM 中创建一个新的工作区",
     "inputSchema": _params({
         "name": {"type": "string", "description": "工作区名称"}}, ["name"])},
    {"name": "list_workspace_documents",
     "description": "列出指定工作区里的文档，返回标题与存放位置；不传 slug 时使用第一个工作区",
     "inputSchema": _params({
         "slug": {"type": "string", "description": "工作区 slug，可选"}}, [])},
    {"name": "upload_document",
     "description": "上传本地文件到 AnythingLLM 并完成向量化嵌入，默认加入第一个工作区",
     "inputSchema": _params({
         "file_path": {"type": "string", "description": "本地文件绝对路径"},
         "slug": {"type": "string", "description": "目标工作区 slug，可选"},
         "embed": {"type": "boolean",
                   "description": "是否立即向量化，默认 true"}},
         ["file_path"])},
    {"name": "chat_with_workspace",
     "description": "向指定工作区提问并返回基于该工作区文档的回答；query 为仅检索模式，chat 为对话模式",
     "inputSchema": _params({
         "slug": {"type": "string", "description": "工作区 slug，可选"},
         "message": {"type": "string", "description": "要提问的内容"},
         "mode": {"type": "string", "enum": ["chat", "query"],
                  "description": "chat 对话模式，query 仅检索模式"}},
         ["message"])},
]

TOOL_HANDLERS = {
    "list_workspaces": lambda args: tool_list_workspaces(),
    "create_workspace": lambda args: tool_create_workspace(
        args.get("name", "")),
    "list_workspace_documents": lambda args: tool_list_documents(
        args.get("slug", "")),
    "upload_document": lambda args: tool_upload_document(
        args.get("file_path", ""), args.get("slug", ""),
        bool(args.get("embed", True))),
    "chat_with_workspace": lambda args: tool_chat_with_workspace(
        args.get("slug", ""), args.get("message", ""),
        args.get("mode", "chat")),
}


def call_tool(name, arguments):
    """执行工具，任何异常都转成 isError 结果返回给客户端。"""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return "未知工具：%s" % name, True
    try:
        return handler(arguments or {}), False
    except AnythingLLMError as e:
        return str(e), True
    except Exception as e:
        return "调用失败：%s: %s" % (type(e).__name__, e), True


def _jsonrpc_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _jsonrpc_error(rid, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": error}


class Handler(BaseHTTPRequestHandler):
    server_version = "anythingllm-mcp/1.1.0"
    session_ids = set()

    def log_message(self, *args):
        """保持静默，避免把请求日志刷进终端。"""
        pass

    def _send(self, code, obj, extra_headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", SESSION_HEADER)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, " + SESSION_HEADER)
        self.end_headers()

    def do_DELETE(self):
        """会话结束：把会话从登记表移除即可。"""
        sid = self.headers.get(SESSION_HEADER, "")
        Handler.session_ids.discard(sid)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/"):
            self._send(200, {"name": SERVER_INFO["name"],
                             "version": SERVER_INFO["version"],
                             "endpoint": "/mcp",
                             "transport": "streamable-http"})
            return
        self._send(405, _jsonrpc_error(None, -32601,
                                       "该服务只接受 POST 的 JSON-RPC 请求"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "ignore") if length else "{}"
        try:
            request_body = json.loads(raw)
        except ValueError:
            self._send(400, _jsonrpc_error(None, -32700, "请求体不是合法 JSON"))
            return
        if isinstance(request_body, list):
            replies = []
            for item in request_body:
                response, is_notification = self._handle_one(item)
                if not is_notification and response is not None:
                    replies.append(response)
            self._send(200, replies, self._session_headers())
            return

        response, is_notification = self._handle_one(request_body, notify_only=True)
        if is_notification:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, response, self._session_headers())

    def _session_headers(self):
        sid = getattr(self, "_issued_session", "")
        return {SESSION_HEADER: sid} if sid else {}

    def _handle_one(self, request_body, notify_only=False):
        method = request_body.get("method", "")
        rid = request_body.get("id")
        params = request_body.get("params") or {}
        is_notification = "id" not in request_body

        if method == "initialize":
            sid = self.headers.get(SESSION_HEADER, "")
            if not sid:
                sid = uuid.uuid4().hex
                self._issued_session = sid
            Handler.session_ids.add(sid)
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            }
            return _jsonrpc_result(rid, result), is_notification

        if method.startswith("notifications/"):
            return None, True

        if method == "ping":
            return _jsonrpc_result(rid, {}), is_notification

        if method == "tools/list":
            return _jsonrpc_result(rid, {"tools": TOOLS}), is_notification

        if method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _jsonrpc_error(rid, -32602,
                                      "arguments 必须是对象"), is_notification
            text, is_error = call_tool(name, arguments)
            result = {"content": [{"type": "text", "text": text}]}
            if is_error:
                result["isError"] = True
            return _jsonrpc_result(rid, result), is_notification

        return _jsonrpc_error(rid, -32601,
                              "不支持的方法：%s" % method), is_notification


def main():
    if not API_KEY or API_KEY == "your_api_key_here":
        raise SystemExit("请先在 config.ini 填入 AnythingLLM 的 api_key，"
                         "或设置环境变量 ANYTHINGLLM_API_KEY")
    if not BASE_URL.startswith(("http://", "https://")):
        raise SystemExit("AnythingLLM 地址不合法：%s" % BASE_URL)
    print("AnythingLLM MCP 已启动：http://127.0.0.1:%d/mcp" % PORT)
    print("上游 AnythingLLM：%s" % BASE_URL)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
