# -*- coding: utf-8 -*-
"""AnythingLLM 文档上传网页

把本地文件上传到指定工作区并完成向量化嵌入，界面具备加载、成功、失败三态。
配置优先级：环境变量 > config.ini。
启动：python app.py，然后浏览器打开 http://127.0.0.1:5000
"""
import configparser
import os

import requests
from flask import Flask, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.ini")
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
ALLOWED_EXTS = {".txt", ".md", ".pdf", ".doc", ".docx", ".csv", ".json",
                ".html", ".pptx", ".xlsx"}
TIMEOUT_LIST = 30
TIMEOUT_UPLOAD = 180


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
HEADERS = {"Authorization": "Bearer " + API_KEY}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


class AnythingLLMError(RuntimeError):
    """可读的页面级错误。"""


def request_json(method, path, **kwargs):
    """统一的 API 调用，失败抛出可读错误而不是透出堆栈。"""
    kwargs.setdefault("timeout", TIMEOUT_LIST)
    try:
        resp = requests.request(method, BASE_URL + path, headers=HEADERS,
                                **kwargs)
    except requests.exceptions.Timeout:
        raise AnythingLLMError("AnythingLLM 响应超时，请确认本地服务是否卡住")
    except requests.exceptions.ConnectionError:
        raise AnythingLLMError("无法连接 AnythingLLM，请确认它在 %s 正常运行"
                               % BASE_URL)
    except requests.exceptions.RequestException as e:
        raise AnythingLLMError("请求 AnythingLLM 失败：%s" % e)
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:200]
        raise AnythingLLMError("AnythingLLM 返回 HTTP %d：%s"
                               % (resp.status_code, detail))
    try:
        return resp.json()
    except ValueError:
        raise AnythingLLMError("AnythingLLM 返回的不是 JSON：%s"
                               % (resp.text or "")[:200])


def list_workspaces():
    """工作区列表，失败时返回空列表并给出提示。"""
    data = request_json("GET", "/api/v1/workspaces")
    return data.get("workspaces", []) or []


def first_slug(workspaces):
    return workspaces[0]["slug"] if workspaces else ""


@app.route("/")
def index():
    message, error, workspaces = None, None, []
    try:
        workspaces = list_workspaces()
        if not workspaces:
            error = "AnythingLLM 中还没有工作区，请先创建一个工作区再上传。"
    except AnythingLLMError as e:
        error = str(e)
    return render_template("index.html", workspaces=workspaces,
                           message=message, error=error,
                           base_url=BASE_URL)


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    slug = (request.form.get("workspace") or "").strip()
    # 先校验本地输入，再看上游状态：上游不可用时也能给出准确的格式提示
    if file is None or not file.filename:
        return render_template("index.html", workspaces=[], message=None,
                               error="请先选择要上传的文件",
                               base_url=BASE_URL), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTS:
        return render_template(
            "index.html", workspaces=[], message=None,
            error="暂不支持 %s 格式，可上传：%s"
                  % (ext or "未知", "、".join(sorted(ALLOWED_EXTS))),
            base_url=BASE_URL), 400
    try:
        workspaces = list_workspaces()
    except AnythingLLMError as e:
        return render_template("index.html", workspaces=[], message=None,
                               error=str(e), base_url=BASE_URL), 502
    slug = slug or first_slug(workspaces)
    if not slug:
        return render_template("index.html", workspaces=[], message=None,
                               error="没有可用工作区，请先在 AnythingLLM 创建",
                               base_url=BASE_URL), 400

    try:
        payload = file.read()
        if len(payload) > MAX_UPLOAD_BYTES:
            raise AnythingLLMError("文件超过 30MB 上限，请压缩后再上传")
        uploaded = request_json(
            "POST", "/api/v1/document/upload",
            files={"file": (file.filename, payload)}, timeout=TIMEOUT_UPLOAD)
        documents = uploaded.get("documents") or []
        if not documents:
            raise AnythingLLMError("上传成功但未返回文档信息：%s"
                                   % str(uploaded)[:200])
        location = documents[0].get("location") or documents[0].get("docpath")
        request_json("POST",
                     "/api/v1/workspace/%s/update-embeddings" % slug,
                     json={"adds": [location], "deletes": []},
                     timeout=TIMEOUT_UPLOAD)
    except AnythingLLMError as e:
        return render_template("index.html", workspaces=workspaces,
                               message=None, error=str(e),
                               base_url=BASE_URL), 502

    return render_template("index.html", workspaces=workspaces,
                           message="已上传 %s 到工作区 %s 并完成向量化嵌入。"
                                   % (file.filename, slug),
                           error=None, base_url=BASE_URL)


@app.errorhandler(413)
def too_large(_):
    return render_template("index.html", workspaces=[], message=None,
                           error="文件超过 30MB 上限，请压缩后再上传",
                           base_url=BASE_URL), 413


def main():
    if not API_KEY or API_KEY == "your_api_key_here":
        raise SystemExit("请先在 config.ini 填入 AnythingLLM 的 api_key，"
                         "或设置环境变量 ANYTHINGLLM_API_KEY")
    # 启动前先探一次上游，配置错时直接给出清晰提示
    try:
        list_workspaces()
    except AnythingLLMError as e:
        print("[警告] 上游 AnythingLLM 暂不可用：%s" % e)
    print("网页已启动：http://127.0.0.1:5000  上游：%s" % BASE_URL)
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
