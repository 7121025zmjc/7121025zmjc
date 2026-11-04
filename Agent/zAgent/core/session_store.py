# -*- coding: utf-8 -*-
"""会话存储模块：历史会话保存/列出/恢复、事件日志、AGENTS.md 注入。

- /save 与 /new 命令把当前会话完整保存到 .agents/sessions/ 目录
- /resume 命令列出历史会话并恢复
- AGENTS.md 在会话首次加载时注入聊天历史，并写入事件日志留痕
- 事件日志统一追加到 .agents/log/events.jsonl
"""
import json
import os
import re
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_DIR = os.path.join(BASE_DIR, ".agents", "sessions")
LOG_DIR = os.path.join(BASE_DIR, ".agents", "log")
AGENTS_MD_NAME = "AGENTS.md"

# 会话文件名格式：20261104-153000.jsonl，冲突时追加 -1 -2 序号
SESSION_RE = re.compile(r"^\d{8}-\d{6}(?:-\d+)?\.jsonl$")


def new_session_id(session_dir=None):
    """生成唯一会话编号，格式 20261104-153000，同秒冲突时追加序号。"""
    session_dir = session_dir or SESSION_DIR
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_id = base
    n = 1
    while os.path.exists(os.path.join(session_dir, session_id + ".jsonl")):
        session_id = "%s-%d" % (base, n)
        n += 1
    return session_id


def _session_title(messages):
    """取第一条用户消息作为会话标题，方便 /resume 列表识别。"""
    for m in messages:
        if m.get("role") == "user":
            text = (m.get("content") or "").strip()
            if text:
                return text[:30]
    return "空会话"


def save_session(messages, session_id=None, session_dir=None):
    """把完整消息列表保存为 JSONL 会话文件，返回 (编号, 路径)。

    保留每条消息的原始角色、内容、工具调用参数和工具返回结果，
    与压缩归档格式一致，供 /resume 原样恢复。
    """
    session_dir = session_dir or SESSION_DIR
    os.makedirs(session_dir, exist_ok=True)
    session_id = session_id or new_session_id(session_dir)
    path = os.path.join(session_dir, session_id + ".jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return session_id, path


def list_sessions(session_dir=None):
    """返回历史会话摘要列表，按文件名（即保存时间）排序。

    每项包含：id 编号、count 消息条数、title 首条用户消息。
    """
    session_dir = session_dir or SESSION_DIR
    if not os.path.isdir(session_dir):
        return []
    result = []
    for name in sorted(os.listdir(session_dir)):
        if not SESSION_RE.match(name):
            continue
        path = os.path.join(session_dir, name)
        msgs = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [l for l in f if l.strip()]
            msgs = [json.loads(l) for l in lines]
        except Exception:
            msgs = []
        result.append({
            "id": name[:-len(".jsonl")],
            "count": len(msgs),
            "title": _session_title(msgs),
        })
    return result


def load_session(session_id, session_dir=None):
    """读取历史会话，返回完整消息列表，不存在时返回 None。"""
    session_dir = session_dir or SESSION_DIR
    session_id = str(session_id or "").strip()
    if session_id.endswith(".jsonl"):
        session_id = session_id[:-len(".jsonl")]
    if not re.match(r"^\d{8}-\d{6}(?:-\d+)?$", session_id):
        return None
    path = os.path.join(session_dir, session_id + ".jsonl")
    if not os.path.isfile(path):
        return None
    msgs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return msgs or None


def read_agents_md(base_dir=None):
    """读取项目根目录的 AGENTS.md，存在且有内容时返回正文，否则返回 None。

    默认固定读取项目根目录，与启动时所在目录无关，
    保证 AGENTS.md 在任何位置启动都能被注入。
    """
    base_dir = base_dir or BASE_DIR
    path = os.path.join(base_dir, AGENTS_MD_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except Exception:
        return None
    return text or None


def log_event(event, data=None, log_dir=None):
    """向事件日志追加一条 JSON 记录，返回日志文件路径。

    事件日志持久化到 .agents/log/events.jsonl，用于留痕关键操作，
    例如 AGENTS.md 注入、会话保存、上下文压缩等。
    """
    log_dir = log_dir or LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "events.jsonl")
    record = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        "data": data or {},
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path
