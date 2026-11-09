# -*- coding: utf-8 -*-
"""web 前端后端：Flask 提供聊天 API 与页面，浏览器图形界面。

与 tui 平级，只调用 core.Agent 的公开接口，业务代码零改动。
启动方式：python main.py --web，自动打开浏览器访问本地页面。
"""
import json
import queue
import threading

from flask import Flask, Response, jsonify, request, send_from_directory

import core.context_manager as cm
from core.interview import InterviewSession, list_interviews
from core.tools import TOOLS, TOOL_CN, TOOL_GROUP

WEB_DIR = __file__ and __import__("os").path.dirname(__file__)

COMMANDS = [
    ("/help", "显示所有命令帮助"),
    ("/new", "新建会话并自动保存当前会话"),
    ("/save", "保存当前会话"),
    ("/resume", "恢复历史会话"),
    ("/compact", "压缩上下文"),
    ("/clear", "清空消息区显示"),
    ("/model", "查看当前模型与配置"),
    ("/quit", "退出程序"),
]


def build_app(agent):
    """创建 Flask 应用，注入共享的 Agent 实例。"""
    app = Flask(__name__)
    app.agent = agent
    app.interview = None      # 当前进行中的面试会话

    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/interview")
    def interview_page():
        """面试模式页面，与通用对话页面互相独立。"""
        return send_from_directory(WEB_DIR, "interview.html")

    @app.post("/api/interview/start")
    def api_interview_start():
        """创建一场新面试：解析岗位、生成开场与第一题。"""
        body = request.json or {}
        job_title = (body.get("job_title") or "").strip()
        if not job_title:
            return jsonify({"ok": False, "text": "请先填写目标岗位名称"})
        session = InterviewSession(
            agent.client, agent.model, job_title,
            jd=body.get("jd", ""),
            difficulty=body.get("difficulty", "中级"),
            style=body.get("style", "中性"))
        try:
            result = session.prepare()
        except Exception as e:
            return jsonify({"ok": False, "text": "面试初始化失败：%s" % e})
        app.interview = session
        return jsonify(result)

    @app.post("/api/interview/answer")
    def api_interview_answer():
        """提交一轮回答，返回评分卡与下一题。"""
        if not app.interview:
            return jsonify({"ok": False, "text": "请先开始一场面试"})
        text = (request.json or {}).get("text", "")
        try:
            result = app.interview.answer(text)
        except Exception as e:
            return jsonify({"ok": False, "text": "评分失败：%s" % e})
        if result.get("finished"):
            try:
                app.interview.save()
            except Exception:
                pass
        return jsonify(result)

    @app.post("/api/interview/finish")
    def api_interview_finish():
        """提前结束面试：生成并保存复盘报告。"""
        if not app.interview:
            return jsonify({"ok": False, "text": "当前没有进行中的面试"})
        session = app.interview
        try:
            if not session.report:
                session.report = session._build_report()
            path = session.save()
        except Exception as e:
            return jsonify({"ok": False, "text": "生成复盘报告失败：%s" % e})
        return jsonify({"ok": True, "report": session.report,
                        "path": path, "state": session.snapshot()})

    @app.get("/api/interview/state")
    def api_interview_state():
        """返回当前面试状态，页面刷新后可恢复。"""
        if not app.interview:
            return jsonify({"ok": False, "text": "当前没有进行中的面试"})
        return jsonify(app.interview.snapshot())

    @app.get("/api/interview/list")
    def api_interview_list():
        """历史面试记录列表。"""
        return jsonify(list_interviews())

    @app.get("/api/init")
    def api_init():
        """返回页面初始化数据：模型、命令、功能清单、MCP 状态、初始消息。"""
        agent = app.agent
        return jsonify({
            "model": agent.model,
            "context_window": agent.context_window,
            "context_defaulted": (agent.context_window ==
                                  cm.DEFAULT_CONTEXT_WINDOW),
            "commands": COMMANDS,
            "tools": TOOLS,
            "tool_cn": TOOL_CN,
            "tool_group": TOOL_GROUP,
            "skills": getattr(agent, "skills", []),
            "mcp_servers": getattr(agent, "mcp_server_status", {}),
            "messages": [m for m in agent.messages
                         if m.get("content") and m["role"] != "system"],
        })

    @app.get("/api/mcp")
    def api_mcp():
        """返回 MCP 服务器状态与可用工具，前端轮询刷新。"""
        agent = app.agent
        mcp_tools = getattr(agent, "mcp_tools", []) or []
        by_server = {}
        for t in mcp_tools:
            full = t["function"]["name"]
            server = full.split("__", 1)[0] if "__" in full else "MCP"
            short = full.split("__", 1)[1] if "__" in full else full
            by_server.setdefault(server, []).append({
                "name": short,
                "description": (t["function"].get("description") or "")[:60],
            })
        return jsonify({
            "status": getattr(agent, "mcp_server_status", {}),
            "tools": by_server,
            "errors": getattr(agent, "mcp_errors", [])[:5],
        })

    @app.get("/api/sessions")
    def api_sessions():
        from core.session_store import list_sessions
        return jsonify(list_sessions(app.agent.session_dir or None))

    @app.post("/api/resume")
    def api_resume():
        ok, msg = app.agent.resume_session(request.json.get("id", ""))
        return jsonify({"ok": ok, "text": msg})

    @app.post("/api/command")
    def api_command():
        """即时命令，返回结果文本；长任务如 /compact 走 /api/run。"""
        agent = app.agent
        cmd = (request.json.get("cmd") or "").strip()
        name = cmd.split()[0].lower()
        if name == "/help":
            lines = ["可用命令："]
            lines += ["%s  %s" % (c, d) for c, d in COMMANDS]
            lines.append("exit / quit  退出程序")
            return jsonify({"ok": True, "text": "\n".join(lines)})
        if name == "/new":
            saved = agent.new_session()
            if saved:
                sid, path = saved
                return jsonify({"ok": True, "text":
                                "已新建会话，旧会话已自动保存：%s（编号 %s）"
                                % (path, sid)})
            return jsonify({"ok": True, "text": "已新建会话。"})
        if name == "/save":
            if len(agent.messages) <= 1:
                return jsonify({"ok": False,
                                "text": "当前会话还没有对话内容，无需保存。"})
            sid, path = agent.save_session()
            return jsonify({"ok": True, "text":
                            "当前会话已保存：%s（编号 %s）" % (path, sid)})
        if name == "/model":
            info = ("模型：%s\n接口：%s\n上下文窗口：%d k（%s）" % (
                agent.model,
                getattr(agent.client, "base_url", "未知"),
                agent.context_window // 1024,
                "默认值" if agent.context_window == cm.DEFAULT_CONTEXT_WINDOW
                else "config.ini 配置"))
            return jsonify({"ok": True, "text": info})
        if name == "/clear":
            return jsonify({"ok": True, "text": "ok"})
        if name == "/quit":
            return jsonify({"ok": True, "text": "ok"})
        return jsonify({"ok": False,
                        "text": "未知命令：%s。输入 /help 查看可用命令。"
                        % cmd.strip()})

    @app.post("/api/run")
    def api_run():
        """SSE 长任务通道：聊天与上下文压缩，事件实时推送到浏览器。"""
        agent = app.agent
        body = request.json or {}
        task = body.get("type", "chat")
        text = (body.get("text") or "").strip()

        if task == "chat" and not text:
            return jsonify({"ok": False, "text": "消息不能为空"})
        if agent.busy:
            return jsonify({"ok": False, "text": "当前对话还在处理中，请稍候"})
        if agent.compressing:
            return jsonify({"ok": False,
                            "text": "正在压缩上下文中，请稍候"})
        if task == "chat" and agent.context_blocked:
            return jsonify({"ok": False,
                            "text": "上下文使用量已达阈值，请先执行 "
                                    "/compact 压缩上下文"})

        q = queue.Queue()

        def emit(name, data):
            q.put((name, data))

        if task == "compact":
            def worker():
                agent.on_event = emit
                try:
                    ok, msg = agent.compact()
                    if not ok:
                        emit("compact_error", {"text": msg})
                except Exception as e:
                    emit("compact_error", {"text": str(e)})
        else:
            def worker():
                agent.on_event = emit
                try:
                    reply = agent.chat(text)
                    emit("final", {"text": reply})
                except Exception as e:
                    emit("error", {"text": str(e)})

        def generate():
            emit("status", {"text": "AI 思考中...", "loading": True})
            threading.Thread(target=worker, daemon=True).start()
            while True:
                try:
                    name, data = q.get(timeout=30)
                except queue.Empty:
                    yield "event: error\ndata: %s\n\n" % json.dumps(
                        {"text": "请求超时，请重试"}, ensure_ascii=False)
                    break
                yield "event: %s\ndata: %s\n\n" % (
                    name, json.dumps(data, ensure_ascii=False))
                if name in ("final", "error", "compact_error"):
                    break
            agent.on_event = None

        return Response(generate(), mimetype="text/event-stream")

    @app.post("/api/exit")
    def api_exit():
        """退出服务器，浏览器页面会自行关闭。"""
        threading.Thread(target=_shutdown, args=(app,),
                         daemon=True).start()
        return jsonify({"ok": True})

    return app


def _shutdown(app):
    """延迟关闭 Flask 开发服务器。"""
    import time
    time.sleep(0.3)
    try:
        from flask import request as _r
        shutdown = _r.environ.get("werkzeug.server.shutdown")
        if shutdown:
            shutdown()
    except Exception:
        pass
    finally:
        import os
        os._exit(0)
