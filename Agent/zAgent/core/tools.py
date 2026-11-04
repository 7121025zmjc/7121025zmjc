# -*- coding: utf-8 -*-
"""工具模块：本地文件操作、日期、闰年、网络搜索工具的注册表与实现，
业务层不依赖 UI。"""
import json
import os
import shutil
import subprocess
import urllib.request
import urllib.error

SEARCH_URL = "https://qianfan.baidubce.com/v2/ai_search"
SEARCH_TIMEOUT = 30

FILE_TYPES = {
    ".txt": "纯文本文件", ".md": "Markdown 文本文件", ".py": "Python 源文件",
    ".json": "JSON 数据文件", ".ini": "配置文件", ".csv": "CSV 表格文件",
    ".html": "HTML 网页文件", ".css": "CSS 样式文件", ".js": "JavaScript 源文件",
    ".png": "PNG 图片", ".jpg": "JPEG 图片", ".exe": "可执行程序",
    ".pdf": "PDF 文档", ".docx": "Word 文档", ".zip": "压缩包",
}

BINARY_EXTS = (".png", ".jpg", ".exe", ".pdf", ".docx", ".zip")


def get_date():
    """调用本地 date 命令获取当前日期和时间。"""
    try:
        date_str = subprocess.run(
            ["cmd", "/c", "date", "/t"], capture_output=True, text=True,
            timeout=10).stdout.strip()
        time_str = subprocess.run(
            ["cmd", "/c", "time", "/t"], capture_output=True, text=True,
            timeout=10).stdout.strip()
        return "当前日期：%s，当前时间：%s" % (date_str, time_str)
    except Exception as e:
        return "获取日期失败：%s" % e


def is_leap_year(year):
    """用 Python 代码判断指定年份是否为闰年。"""
    year = int(year)
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def get_cwd():
    """获取当前工作目录。"""
    return "当前工作目录：%s" % os.getcwd()


def list_directory(path="."):
    """列出指定目录下所有文件和子目录。"""
    if not os.path.isdir(path):
        return "错误：%s 不是有效目录" % path
    entries = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        kind = "目录" if os.path.isdir(full) else "文件"
        entries.append("%s %s" % (kind, name))
    if not entries:
        return "%s 目录为空" % path
    return "%s 目录内容：\n%s" % (path, "\n".join(entries))


def get_file_type(path):
    """获取指定文件的文件类型。"""
    if not os.path.exists(path):
        return "错误：文件不存在 %s" % path
    if os.path.isdir(path):
        return "%s 是目录" % path
    ext = os.path.splitext(path)[1].lower()
    desc = FILE_TYPES.get(ext, "未知类型文件")
    return "%s 的文件类型：%s" % (path, desc)


def get_file_content(path):
    """获取指定文件的内容，仅支持纯文本格式文件。"""
    if not os.path.isfile(path):
        return "错误：%s 不是有效文件" % path
    ext = os.path.splitext(path)[1].lower()
    if ext not in FILE_TYPES or ext in BINARY_EXTS:
        return "错误：%s 不是纯文本文件，无法读取内容" % path
    try:
        with open(path, "r", encoding="utf-8") as f:
            return "文件 %s 的内容：\n%s" % (path, f.read())
    except UnicodeDecodeError:
        return "错误：%s 不是 UTF-8 文本文件，无法读取" % path
    except Exception as e:
        return "读取失败：%s" % e


OFFICE_EXTS = (".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt")
OFFICE_CLI_EXE = r"D:\office\.agents\skills\office-cli\officecli-win-x64.exe"


def _normalize_office_path(arg):
    """把 LLM 常写错的位置路径规范为 officecli 要求的 /slide[N] 形式。

    例如 /slide1 -> /slide[1]，/body/p2 -> /body/p[2]，/shape3 -> /shape[3]。
    Excel 的 /Sheet1 是工作表名，保持原样。
    """
    if not isinstance(arg, str):
        return arg
    if "/" not in arg or "[" in arg:
        return arg
    # 匹配 /slideN /body/pN /trN /tcN /shapeN /tblN /pN 等尾部数字索引
    return _re.sub(r"/(slide|shape|p|tbl|tr|tc|row|col)(\d+)(?=/|$)",
                   r"/\1[\2]", arg)


def office_cli(args_list):
    """调用本地 officecli-win-x64.exe 操作 Office 文档。

    args_list 是传给 exe 的参数数组，例如：
      ["create", "D:/a.docx"]
      ["add", "D:/a.docx", "/body", "--type", "paragraph",
       "--prop", "text=hello"]
      ["set", "D:/a.xlsx", "/Sheet1/A1", "--prop", "value=5"]
    位置路径中的 /slide1 等写法会被自动规范为 /slide[1]。
    """
    if not args_list:
        return "错误：officecli 参数不能为空。"
    if not os.path.isfile(OFFICE_CLI_EXE):
        return "错误：未找到 officecli 可执行文件 %s" % OFFICE_CLI_EXE
    cmd = [OFFICE_CLI_EXE]
    for a in args_list:
        # 规范化路径参数，但跳过以 -- 开头的选项和 --prop 的键值
        if isinstance(a, str) and not a.startswith("--") and "/" in a:
            a = _normalize_office_path(a)
        cmd.append(str(a))
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60,
                              shell=False)
    except subprocess.TimeoutExpired:
        return "错误：officecli 执行超时（60秒）。"
    except Exception as e:
        return "错误：officecli 执行失败：%s" % e
    out = proc.stdout.decode("utf-8", "ignore")
    err = proc.stderr.decode("utf-8", "ignore")
    # 过滤无害的 locale 提示，避免被误认为错误
    err_lines = [ln for ln in err.splitlines()
                 if not ln.strip().startswith("Note: locale")]
    err = "\n".join(err_lines).strip()
    if len(out) > 3000:
        out = out[:3000] + "\n...(stdout 过长已截断)"
    if len(err) > 1000:
        err = err[:1000] + "\n...(stderr 过长已截断)"
    result = "officecli 退出码：%d" % proc.returncode
    if out:
        result += "\n" + out
    if err:
        result += "\n标准错误：\n" + err
    # create 成功但只是空文档时，提示：仅在用户要求内容时才继续写
    if proc.returncode == 0 and "create" in [str(a).lower() for a in args_list]:
        has_content_cmd = any(str(a).lower() in ("add", "set", "raw-set")
                              for a in args_list)
        if not has_content_cmd:
            result += ("\n提示：空文档已创建。"
                       "仅当用户明确要求文档包含内容时，才继续用 add 或 set "
                       "命令写入内容；用户没有要求内容时，直接告知已创建即可。")
    return result


def create_file(directory, filename, content=""):
    """根据指定的目录和文件名创建新文件。"""
    lower = filename.lower()
    if lower.endswith(OFFICE_EXTS):
        return ("错误：%s 是 Office 二进制格式文件，不能直接写入文本内容。"
                "请改用 officeCli 工具，例如 officeCli "
                "args=[\"create\", \"<完整路径>\"], 再通过 add/set 填入内容" % filename)
    if not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename)
    if os.path.exists(path):
        return "错误：文件已存在 %s，如需修改请使用编辑功能" % path
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return "已创建文件：%s" % path
    except Exception as e:
        return "创建失败：%s" % e


def edit_file(path, content):
    """编辑指定文件的内容，仅支持纯文本格式文件。"""
    if not os.path.isfile(path):
        return "错误：%s 不是有效文件" % path
    if path.lower().endswith(OFFICE_EXTS):
        return ("错误：%s 是 Office 二进制格式文件，不能直接写入文本内容。"
                "请改用 officeCli 工具，通过 set / add 命令修改内容" % path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return "已更新文件：%s" % path
    except Exception as e:
        return "编辑失败：%s" % e


def create_directory(path):
    """创建新目录。"""
    try:
        os.makedirs(path, exist_ok=True)
        return "已创建目录：%s" % path
    except Exception as e:
        return "创建失败：%s" % e


def delete_directory(path):
    """删除目录，支持非空目录。"""
    if not os.path.isdir(path):
        return "错误：%s 不是有效目录" % path
    try:
        shutil.rmtree(path)
        return "已删除目录：%s" % path
    except Exception as e:
        return "删除失败：%s" % e


def delete_file(path):
    """删除文件。"""
    if not os.path.isfile(path):
        return "错误：%s 不是有效文件" % path
    try:
        os.remove(path)
        return "已删除文件：%s" % path
    except Exception as e:
        return "删除失败：%s" % e


def rename_file_or_dir(old_path, new_path):
    """重命名文件或目录。"""
    if not os.path.exists(old_path):
        return "错误：%s 不存在" % old_path
    if os.path.exists(new_path):
        return "错误：目标 %s 已存在" % new_path
    try:
        os.rename(old_path, new_path)
        return "已将 %s 重命名为 %s" % (old_path, new_path)
    except Exception as e:
        return "重命名失败：%s" % e


def search_web(query, api_key):
    """调用百度千帆 AI 搜索，返回搜索摘要和参考来源。"""
    if not api_key:
        return "错误：未配置百度千帆 API Key，无法联网搜索。"
    if not query:
        return "错误：搜索关键词为空。"
    payload = json.dumps({
        "messages": [{"role": "user", "content": query}],
        "search_source": "baidu_search_v2",
    }).encode("utf-8")
    req = urllib.request.Request(
        SEARCH_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % api_key})
    try:
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:200]
        except Exception:
            pass
        return "搜索请求失败：HTTP %d %s %s" % (e.code, e.reason, detail)
    except urllib.error.URLError as e:
        return "搜索请求失败：网络错误 %s" % e.reason
    except Exception as e:
        return "搜索请求失败：%s" % e

    choices = data.get("choices") or []
    if choices:
        content = choices[0].get("message", {}).get("content", "")
        if content:
            return "网络搜索结果（关键词：%s）：\n%s" % (query, content)

    references = data.get("references") or []
    if not references:
        return "搜索无结果。原始返回：%s" % str(data)[:300]
    lines = []
    for i, ref in enumerate(references[:5], 1):
        title = ref.get("title", "无标题")
        url = ref.get("url", "")
        date = ref.get("date", "")
        content = ref.get("content", "")[:200]
        lines.append("%d. %s\n   时间：%s\n   来源：%s\n   摘要：%s"
                     % (i, title, date, url, content))
    return "网络搜索结果（关键词：%s）：\n%s" % (query, "\n\n".join(lines))


def fetch_web(url):
    """用 curl 抓取网页，trafilatura 提取正文转 Markdown。"""
    if not url:
        return "错误：URL 为空。"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        proc = subprocess.run(
            ["curl", "-L", "--max-time", "20", "-s", "-A",
             "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", url],
            capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "错误：网页访问超时（30秒）。"
    except FileNotFoundError:
        return "错误：系统未找到 curl 程序。"
    except Exception as e:
        return "错误：curl 调用失败：%s" % e
    if proc.returncode != 0:
        return "错误：网页访问失败，curl 返回码 %d：%s" % (
            proc.returncode, proc.stderr.decode("utf-8", "ignore")[:200])
    html = proc.stdout.decode("utf-8", "ignore")
    if not html.strip():
        return "错误：网页内容为空。"
    try:
        import trafilatura
        downloaded = trafilatura.extract(
            html, output_format="markdown", include_links=True,
            include_images=False, include_tables=True, favor_recall=True)
    except Exception as e:
        return "错误：网页正文解析失败：%s" % e
    if not downloaded:
        return "错误：无法从该网页提取正文，可能是图片页、登录页或反爬页面。"
    if len(downloaded) > 4000:
        downloaded = downloaded[:4000] + "\n...(内容过长已截断)"
    return "网页 %s 的正文内容：\n%s" % (url, downloaded)


def run_cli(command, args):
    """以当前用户权限执行系统命令，返回 stdout/stderr/exit code。"""
    if not command:
        return "错误：命令名为空。"
    cmd = [command] + list(args or [])
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30,
                              shell=False)
    except subprocess.TimeoutExpired:
        return "错误：命令执行超时（30秒）。"
    except FileNotFoundError:
        return "错误：未找到命令 %s。" % command
    except Exception as e:
        return "错误：命令执行失败：%s" % e
    out = proc.stdout.decode("utf-8", "ignore")
    err = proc.stderr.decode("utf-8", "ignore")
    truncated = False
    if len(out) > 3000:
        out = out[:3000] + "\n...(stdout 过长已截断)"
        truncated = True
    if len(err) > 1000:
        err = err[:1000] + "\n...(stderr 过长已截断)"
    result = "命令：%s\n退出码：%d" % (" ".join(cmd), proc.returncode)
    if out:
        result += "\n标准输出：\n" + out
    if err:
        result += "\n标准错误：\n" + err
    if truncated:
        result += "\n注意：输出过长已截断。"
    return result


def _params(properties, required):
    return {"type": "object", "properties": properties, "required": required}


TOOLS = [
    {"type": "function", "function": {"name": "getCWD",
        "description": "获取当前工作目录",
        "parameters": _params({}, [])}},
    {"type": "function", "function": {"name": "listDirectory",
        "description": "列出指定目录下所有文件和子目录",
        "parameters": _params({"path": {"type": "string",
            "description": "要列出的目录路径，默认当前目录"}}, [])}},
    {"type": "function", "function": {"name": "getFileType",
        "description": "获取指定文件的文件类型",
        "parameters": _params({"path": {"type": "string",
            "description": "文件路径"}}, ["path"])}},
    {"type": "function", "function": {"name": "getFileContent",
        "description": "获取指定文件的内容，仅支持纯文本格式文件",
        "parameters": _params({"path": {"type": "string",
            "description": "文件路径"}}, ["path"])}},
    {"type": "function", "function": {"name": "createFile",
        "description": "根据指定的目录和文件名创建新文件",
        "parameters": _params({
            "directory": {"type": "string", "description": "目标目录"},
            "filename": {"type": "string", "description": "文件名"},
            "content": {"type": "string", "description": "文件内容，可选"}},
            ["directory", "filename"])}},
    {"type": "function", "function": {"name": "editFile",
        "description": "编辑指定文件的内容，仅支持纯文本格式文件",
        "parameters": _params({
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "新的文件内容"}},
            ["path", "content"])}},
    {"type": "function", "function": {"name": "createDirectory",
        "description": "创建新目录",
        "parameters": _params({"path": {"type": "string",
            "description": "要创建的目录路径"}}, ["path"])}},
    {"type": "function", "function": {"name": "deleteDirectory",
        "description": "删除目录，支持非空目录",
        "parameters": _params({"path": {"type": "string",
            "description": "要删除的目录路径"}}, ["path"])}},
    {"type": "function", "function": {"name": "deleteFile",
        "description": "删除文件",
        "parameters": _params({"path": {"type": "string",
            "description": "要删除的文件路径"}}, ["path"])}},
    {"type": "function", "function": {"name": "renameFileOrDir",
        "description": "重命名文件或目录",
        "parameters": _params({
            "old_path": {"type": "string", "description": "原路径"},
            "new_path": {"type": "string", "description": "新路径"}},
            ["old_path", "new_path"])}},
    {"type": "function", "function": {"name": "getDate",
        "description": "获取当前电脑的日期和时间",
        "parameters": _params({}, [])}},
    {"type": "function", "function": {"name": "isLeapYear",
        "description": "判断指定年份是否为闰年",
        "parameters": _params({"year": {"type": "integer",
            "description": "要判断的年份"}}, ["year"])}},
    {"type": "function", "function": {"name": "searchWeb",
        "description": "通过百度联网搜索获取互联网上的最新信息，"
                       "适用于实时新闻、天气、日期、最新事件等本地无法回答的问题",
        "parameters": _params({"query": {"type": "string",
            "description": "搜索关键词或问题"}}, ["query"])}},
    {"type": "function", "function": {"name": "fetchWeb",
        "description": "获取指定网页 URL 的正文内容，自动去除导航、广告、页脚等无关内容，"
                       "返回 Markdown 格式正文。适用于已知具体网页链接需要读取内容时",
        "parameters": _params({"url": {"type": "string",
            "description": "要抓取的网页完整 URL"}}, ["url"])}},
    {"type": "function", "function": {"name": "RunCli",
        "description": "执行本地命令行程序，返回标准输出、标准错误和退出码。"
                       "用于运行系统命令、查看系统信息、运行脚本等",
        "parameters": _params({
            "command": {"type": "string", "description": "要执行的命令名，如 dir、python、git"},
            "args": {"type": "array", "items": {"type": "string"},
                     "description": "命令参数列表，可选"}},
            ["command"])}},
    {"type": "function", "function": {"name": "loadSkill",
        "description": "加载某个 Skill 的完整说明文档。只给 name 时返回该 Skill 的 "
                       "SKILL.md 全文；同时给 filename 时返回该 Skill 目录下的附加文件。"
                       "当你需要了解某个技能的详细用法时调用此工具",
        "parameters": _params({
            "name": {"type": "string", "description": "Skill 的名称"},
            "filename": {"type": "string", "description": "要加载的附加文件名，可选"}},
            ["name"])}},
    {"type": "function", "function": {"name": "createSkill",
        "description": "根据用户的自然语言要求创建新的 Skill。在当前项目的 "
                       ".agents/skills/ 目录下创建与 name 对应的英文子目录，"
                       "并生成 SKILL.md 写入 skillContext。Skill 名称统一采用"
                       "小写字母、数字和连字符格式。如果目标 Skill 已存在，"
                       "返回提示避免覆盖",
        "parameters": _params({
            "name": {"type": "string", "description": "Skill 名称，小写字母数字连字符"},
            "skillContext": {"type": "string",
                             "description": "要写入 SKILL.md 的完整内容，"
                                            "包含 YAML Frontmatter 和正文"}},
            ["name", "skillContext"])}},
    {"type": "function", "function": {"name": "officeCli",
        "description": "调用本地 officecli 工具创建、读取、修改 Office 文档。"
                       "args 是传给 officecli-win-x64.exe 的参数数组。"
                       "生成带内容的 Word："
                       "[\"create\", \"<路径.docx>\"] 创建，再 "
                       "[\"add\", \"<路径>\", \"/body\", \"--type\", \"paragraph\", "
                       "\"--prop\", \"text=段落内容\"] 逐段添加。"
                       "生成带内容的 PPT："
                       "[\"create\", \"<路径.pptx>\"] 创建，再 "
                       "[\"add\", \"<路径>\", \"/\", \"--type\", \"slide\", "
                       "\"--prop\", \"title=幻灯片标题\"] 添加幻灯片，然后 "
                       "[\"add\", \"<路径>\", \"/slide[N]\", \"--type\", \"shape\", "
                       "\"--prop\", \"text=正文内容\", \"--prop\", \"x=2cm\", "
                       "\"--prop\", \"y=4cm\", \"--prop\", \"size=24\"] 逐页加文字"
                       "（N 从 1 开始）。"
                       "生成带内容的 Excel："
                       "[\"create\", \"<路径.xlsx>\"] 创建，再 "
                       "[\"set\", \"<路径>\", \"/Sheet1/A1\", \"--prop\", "
                       "\"value=内容\"] 填单元格。"
                       "不确定命令语法时可先用 [\"help\"] 或 "
                       "[\"help\", \"<格式>\"] 查看帮助",
        "parameters": _params({
            "args": {"type": "array", "items": {"type": "string"},
                     "description": "传给 officecli 的参数数组"}},
            ["args"])}},
    {"type": "function", "function": {"name": "recallContext",
        "description": "检索之前压缩保存的历史上下文。当压缩摘要缺少某些历史"
                       "细节时，用此工具从 .agents/context/ 目录下的归档中"
                       "查找原始记录。提供 id 时只检索该归档编号对应的文件，"
                       "不提供 id 时检索全部归档。返回匹配的原始内容片段、"
                       "归档编号和所在位置",
        "parameters": _params({
            "id": {"type": "string",
                   "description": "归档编号，例如 20261104-153000，可选"},
            "text": {"type": "string",
                     "description": "要查找的关键词或文本内容，多个词用空格"
                                    "分隔时要求全部出现"}},
            ["text"])}},
]


from core.skill_loader import load_skill_body, load_skill_file, SKILLS_DIR
from core.context_manager import recall_context
import re as _re


def create_skill(name, skill_context):
    """根据用户自然语言要求创建新的 Skill。

    在当前项目的 .agents/skills/ 目录下创建与 name 对应的子目录，
    并在其中生成 SKILL.md，将 skillContext 写入该文件。
    """
    name = (name or "").strip().lower()
    if not name:
        return "错误：Skill 名称不能为空。"
    if not _re.fullmatch(r"[a-z0-9-]+", name):
        return ("错误：Skill 名称只能包含小写字母、数字和连字符，"
                "例如 mini-encryption。")
    skill_context = skill_context or ""
    skill_dir = os.path.join(os.getcwd(), SKILLS_DIR, name)
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if os.path.exists(skill_md):
        return "提示：Skill %s 已存在，未覆盖原文件。路径：%s" % (name, skill_md)
    try:
        os.makedirs(skill_dir, exist_ok=True)
        with open(skill_md, "w", encoding="utf-8") as f:
            f.write(skill_context)
    except Exception as e:
        return "错误：创建 Skill 失败：%s" % e
    return "已创建 Skill %s，路径：%s" % (name, skill_md)


def call_tool(name, args, search_api_key=""):
    """根据工具名分发到对应实现函数。"""
    if name == "getCWD":
        return get_cwd()
    if name == "listDirectory":
        return list_directory(args.get("path", "."))
    if name == "getFileType":
        return get_file_type(args["path"])
    if name == "getFileContent":
        return get_file_content(args["path"])
    if name == "createFile":
        return create_file(args["directory"], args["filename"],
                           args.get("content", ""))
    if name == "editFile":
        return edit_file(args["path"], args["content"])
    if name == "createDirectory":
        return create_directory(args["path"])
    if name == "deleteDirectory":
        return delete_directory(args["path"])
    if name == "deleteFile":
        return delete_file(args["path"])
    if name == "renameFileOrDir":
        return rename_file_or_dir(args["old_path"], args["new_path"])
    if name == "getDate":
        return get_date()
    if name == "isLeapYear":
        is_leap = is_leap_year(args.get("year"))
        return "%s 年是闰年" % args["year"] if is_leap else "%s 年不是闰年" % args["year"]
    if name == "searchWeb":
        return search_web(args.get("query", ""), search_api_key)
    if name == "fetchWeb":
        return fetch_web(args.get("url", ""))
    if name == "RunCli":
        return run_cli(args.get("command", ""), args.get("args", []))
    if name == "loadSkill":
        skill_name = args.get("name", "")
        filename = args.get("filename", "")
        if filename:
            return load_skill_file(skill_name, filename)
        return load_skill_body(skill_name)
    if name == "createSkill":
        return create_skill(args.get("name", ""),
                            args.get("skillContext", ""))
    if name == "officeCli":
        return office_cli(args.get("args", []))
    if name == "recallContext":
        return recall_context(args.get("text", ""), args.get("id", ""))
    return "未知工具：%s" % name
