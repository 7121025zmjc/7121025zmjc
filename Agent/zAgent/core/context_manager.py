# -*- coding: utf-8 -*-
"""上下文管理模块：Token 估算、历史归档、压缩摘要与历史检索。

上下文压缩流程：
  1. 把当前会话的原始消息完整保存到 .agents/context/ 目录下的归档文件
  2. 用专门设计的压缩 Prompt 调用 LLM 生成约 5000 Token 的工作摘要
  3. 用摘要替换原有对话历史，保留 System Prompt 和工具定义
  4. 之后可通过 recallContext 工具检索归档中的原始记录

本模块不依赖向量数据库、Embedding 或其他检索框架，
检索使用 Python 本地全文关键词匹配，方便学生理解和维护。
"""
import json
import os
import re
from datetime import datetime

# 项目根目录：本文件位于 core/ 下，上一级即项目根
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXT_DIR = os.path.join(BASE_DIR, ".agents", "context")

# 模型上下文窗口默认值：无法从配置或模型 API 获得时按 128k 处理
DEFAULT_CONTEXT_WINDOW = 128 * 1024

# 上下文使用量达到窗口比例时，警告用户并暂停接收新消息
WARN_RATIO = 0.8

# 压缩摘要的目标 Token 数，写入压缩 Prompt 提示 LLM
COMPRESS_TARGET_TOKENS = 5000

# 归档文件名格式：20261104-153000.jsonl，允许冲突时追加 -1 -2 序号
ARCHIVE_RE = re.compile(r"^\d{8}-\d{6}(?:-\d+)?\.jsonl$")


def estimate_tokens(text):
    """粗略估算文本的 Token 数，无需调用模型 API。

    中文字符按 1 个 Token 计算，其他字符按每 4 个计 1 个 Token，
    非空文本至少计 1 个 Token。这是估算值，模型 API 返回的 usage
    信息优先使用。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return max(1, cjk + other // 4)


def estimate_messages_tokens(messages):
    """估算整组消息的 Token 数，覆盖 System Prompt、对话历史和工具消息。"""
    return sum(estimate_tokens(json.dumps(m, ensure_ascii=False))
               for m in messages)


def new_archive_id(context_dir=None):
    """生成唯一归档编号，格式 20261104-153000，同秒冲突时追加序号。"""
    context_dir = context_dir or CONTEXT_DIR
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_id = base
    n = 1
    while os.path.exists(os.path.join(context_dir, archive_id + ".jsonl")):
        archive_id = "%s-%d" % (base, n)
        n += 1
    return archive_id


def save_archive(messages, archive_id=None, context_dir=None):
    """把消息列表完整保存为 JSONL 归档，返回 (编号, 文件路径)。

    每条消息保留角色、内容、工具调用参数和工具返回结果，
    文件行顺序即消息顺序，同时用 index 字段记录序号。
    必须先成功保存归档，才能继续执行压缩和替换操作。
    """
    context_dir = context_dir or CONTEXT_DIR
    os.makedirs(context_dir, exist_ok=True)
    archive_id = archive_id or new_archive_id(context_dir)
    path = os.path.join(context_dir, archive_id + ".jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for i, msg in enumerate(messages, 1):
            record = {
                "index": i,
                "role": msg.get("role", ""),
                "content": msg.get("content"),
            }
            if msg.get("tool_calls"):
                record["tool_calls"] = msg["tool_calls"]
            if msg.get("tool_call_id"):
                record["tool_call_id"] = msg["tool_call_id"]
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return archive_id, path


def list_archives(context_dir=None):
    """返回 .agents/context/ 下按名称排序的归档编号列表。"""
    context_dir = context_dir or CONTEXT_DIR
    if not os.path.isdir(context_dir):
        return []
    return [name[:-len(".jsonl")] for name in sorted(os.listdir(context_dir))
            if ARCHIVE_RE.match(name)]


def _load_archive(archive_id, context_dir=None):
    """读取单个归档，返回 (编号, 消息记录列表)，不存在时返回 (None, [])。"""
    context_dir = context_dir or CONTEXT_DIR
    archive_id = str(archive_id or "").strip()
    if archive_id.endswith(".jsonl"):
        archive_id = archive_id[:-len(".jsonl")]
    if not re.match(r"^\d{8}-\d{6}(?:-\d+)?$", archive_id):
        return None, []
    path = os.path.join(context_dir, archive_id + ".jsonl")
    if not os.path.isfile(path):
        return None, []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return archive_id, records


def _record_text(record):
    """把一条消息记录拼成带角色标注的可检索文本。"""
    parts = []
    role = record.get("role", "")
    if role == "tool":
        parts.append("[工具返回]")
    elif role == "assistant":
        parts.append("[assistant]")
    else:
        parts.append("[%s]" % role)
    parts.append(record.get("content") or "")
    for tc in record.get("tool_calls") or []:
        fn = tc.get("function") or {}
        parts.append("[调用工具 %s 参数 %s]"
                     % (fn.get("name", ""), fn.get("arguments", "")))
    if record.get("tool_call_id"):
        parts.append("[工具调用编号 %s]" % record["tool_call_id"])
    return "\n".join(parts)


def _snippet(text, terms, width=120):
    """截取首个关键词附近的一段文本作为片段，过长部分用省略号标记。"""
    low = text.lower()
    idx = -1
    for t in terms:
        i = low.find(t)
        if i >= 0:
            idx = i
            break
    if idx < 0:
        idx = 0
    start = max(0, idx - width // 2)
    end = min(len(text), idx + width // 2)
    snippet = text[start:end].replace("\n", " ")
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def recall_context(text, archive_id=None, context_dir=None,
                   max_matches=8, max_total=3000):
    """在历史归档中检索关键词，返回匹配片段、归档编号和所在位置。

    text 是必需的关键词或短语，多个词用空格分隔时要求全部出现；
    archive_id 指定编号时只检索该归档，不指定时检索全部归档。
    纯本地关键词匹配，结果数量与总长度均受限，避免再次占满上下文。
    """
    text = (text or "").strip()
    if not text:
        return "错误：检索关键词 text 不能为空。"
    context_dir = context_dir or CONTEXT_DIR
    if not os.path.isdir(context_dir):
        return ("当前没有历史上下文归档，说明本会话从未执行过上下文压缩，"
                "现有对话历史完整可用，无需检索历史。"
                "如需压缩上下文，请输入 /compact 命令。"
                "归档目录：%s" % context_dir)

    targets = []
    if archive_id:
        aid, records = _load_archive(archive_id, context_dir)
        if not records:
            return ("错误：未找到归档 %s。可用归档：%s"
                    % (archive_id, "、".join(list_archives(context_dir)) or "无"))
        targets = [(aid, records)]
    else:
        for aid in list_archives(context_dir):
            _, records = _load_archive(aid, context_dir)
            if records:
                targets.append((aid, records))

    terms = [t.lower() for t in text.split() if t.strip()]
    if not terms:
        return "错误：检索关键词 text 不能为空。"

    results = []          # (归档编号, 行号, 角色, 片段)
    total_len = 0
    for aid, records in targets:
        for record in records:
            haystack = _record_text(record)
            if not all(t in haystack.lower() for t in terms):
                continue
            snippet = _snippet(haystack, terms)
            results.append((aid, record.get("index"), record.get("role"),
                            snippet))
            total_len += len(snippet)
            if len(results) >= max_matches or total_len >= max_total:
                break
        if len(results) >= max_matches or total_len >= max_total:
            break

    if not results:
        return "在 %d 个归档中未找到包含“%s”的内容。" % (len(targets), text)

    lines = ["在归档中找到 %d 条匹配，关键词：%s" % (len(results), text)]
    for aid, idx, role, snippet in results:
        lines.append("--- 归档 %s 第 %s 条 %s ---" % (aid, idx, role))
        lines.append(snippet)
    out = "\n".join(lines)
    if len(out) > max_total:
        out = out[:max_total] + "\n...(结果过长已截断，请缩小关键词后重试)"
    return out


COMPRESS_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 recallContext 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。"""


def _truncate(text, limit):
    """截断过长的文本，并提示完整内容已保存在归档中。"""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...(过长已截断，完整内容见上下文归档)"


def serialize_history(messages, max_result_chars=800):
    """把对话消息序列化成可读文本，供压缩 Prompt 使用。

    保留角色、内容、工具调用参数和工具返回结果；
    过长的工具返回结果在压缩输入中截断，完整内容保存在归档里。
    """
    lines = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""
        if msg.get("tool_calls"):
            lines.append("[assistant 调用工具]")
            for tc in msg["tool_calls"]:
                fn = tc.get("function") or {}
                lines.append("工具名：%s" % fn.get("name", ""))
                lines.append("参数：%s" % fn.get("arguments", ""))
            if content:
                lines.append("assistant 说明：%s" % content)
        elif role == "tool":
            lines.append("[工具返回]")
            lines.append(_truncate(content, max_result_chars))
        else:
            lines.append("[%s] %s" % (role, content))
    return "\n\n".join(lines)


def build_compress_messages(messages):
    """构造压缩请求的消息：专门压缩 Prompt + 序列化后的历史上下文。

    压缩目标 Token 数取自 COMPRESS_TARGET_TOKENS，可在 config.ini
    的 [Context] 段调整，模块默认值为 5000。
    """
    history = serialize_history(messages)
    prompt = COMPRESS_PROMPT.replace(
        "约 5000 Tokens", "约 %d Tokens" % COMPRESS_TARGET_TOKENS)
    prompt += ("\n\n下面是需要压缩的历史上下文：\n\n"
               "<历史上下文开始>\n%s\n<历史上下文结束>" % history)
    return [{"role": "user", "content": prompt}]


def build_archive_note(archive_id):
    """生成追加在压缩摘要末尾的归档说明。"""
    ts = str(archive_id or "")[:15]
    try:
        dt = datetime.strptime(ts, "%Y%m%d-%H%M%S")
    except ValueError:
        dt = datetime.now()
    return ("\n\n> 此前上下文已于 %s 完成压缩，原始上下文保存在编号为 %s 的"
            "归档文件中。如需查找历史信息，请使用 recallContext 工具。"
            % (dt.strftime("%Y-%m-%d %H:%M:%S"), archive_id))
