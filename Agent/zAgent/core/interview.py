# -*- coding: utf-8 -*-
"""面试模拟器核心模块：用 LLM 模拟面试官，完成出题、追问、评分、复盘。

设计要点：
1. 一场面试分五个阶段：自我介绍、技术问答、行为问答、结束。
2. 每轮用户回答后调用一次 LLM，同时拿到评分与追问判断，控制调用次数。
3. 每道题最多追问一次，避免无限追问。
4. 面试会话独立存储，不写入通用对话历史，两种模式互不干扰。

存档目录：项目根目录下的 .agents/interviews/，每场面试一个 JSON 文件。
"""
import json
import os
import time

# 存档目录固定在项目根目录，不依赖启动时的当前工作目录
INTERVIEW_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".agents", "interviews")

# 面试阶段常量
STAGE_SELF_INTRO = "self_intro"
STAGE_TECH = "tech"
STAGE_BEHAVIOR = "behavior"
STAGE_FINISHED = "finished"

STAGE_CN = {
    STAGE_SELF_INTRO: "自我介绍",
    STAGE_TECH: "技术问答",
    STAGE_BEHAVIOR: "行为问答",
    STAGE_FINISHED: "面试结束",
}

# 题目数量配置，改这里就能调整面试长度
TECH_QUESTION_COUNT = 3
BEHAVIOR_QUESTION_COUNT = 1
MAX_FOLLOWUPS = 1          # 每道题最多追问次数

# 面试官风格对应的开场语
STYLE_OPENING = {
    "严厉": "时间有限，我们直接开始。",
    "温和": "别紧张，我们轻松聊一聊。",
    "中性": "",
}

# 各阶段的基础分项，总分 = 内容 40% + 结构 30% + 表达 30%
SCORE_WEIGHTS = {"content": 0.4, "structure": 0.3, "expression": 0.3}

# ---------- Prompt 模板 ----------

PROMPT_JOB_PARSE = """你是面试规划助手。请根据目标岗位和岗位描述，提取这场模拟面试要考察的能力维度。

目标岗位：{job_title}
岗位描述：
{jd}

要求：
1. 维度 3 到 5 个，其中至少 2 个是专业技术维度，至少 1 个是通用能力维度。
2. 每个维度写明名称、类型、权重和考察重点。
3. 类型只能是 tech 或 general。
4. 只输出 JSON，不要任何解释文字。

输出格式：
{{"job_title": "岗位名称", "dimensions": [{{"name": "维度名", "type": "tech 或 general", "weight": 0.3, "focus": "考察重点"}}]}}"""

PROMPT_QUESTION = """你是{style}风格的面试官，正在面试「{job_title}」岗位，难度等级：{difficulty}。

当前考察维度：{dimension_name}
考察重点：{focus}

已经问过的问题：
{asked}

请出一道考察上述维度的面试题。

要求：
1. 只输出题目本身，不超过 40 个字。
2. 不要重复已经问过的问题。
3. 难度匹配：初级考基础概念，中级考理解与应用，高级考深度与技术权衡。
4. 题目要贴近该岗位的日常工作场景，避免与岗位无关的通用算法题。"""

PROMPT_BEHAVIOR_QUESTION = """你是{style}风格的面试官，正在面试「{job_title}」岗位。

请出一道行为面试题，考察候选人的实际经历与做事方式。

要求：
1. 按 STAR 结构引导候选人讲述，例如讲一次解决困难的经历、一次团队协作、一次失败复盘。
2. 只输出题目本身，不超过 40 个字。"""

PROMPT_EVALUATE = """你是资深面试评审。请评估候选人对下面这道面试题的回答。

题目：{question}
候选人的回答：{answer}

评分维度，每项 0 到 10 分：
- content 内容准确性：是否答到关键点，有没有明显错误
- structure 结构完整性：是否条理清晰，行为题是否体现情境、任务、行动、结果
- expression 表达清晰度：语言是否简洁清楚，有没有空话套话

追问判断规则，满足任意一条就需要追问：
1. 回答过于笼统，缺少具体细节
2. 提到数据或结果，但没有说明来源或过程
3. 逻辑跳跃，前后对不上

只输出 JSON，不要解释：
{{"scores": {{"content": 数字, "structure": 数字, "expression": 数字}}, "comment": "一句话点评，40 字内", "need_followup": true 或 false, "followup": "一句追问，20 字内；不需要追问时填空字符串", "better_answer": "参考答案要点，80 字内"}}"""

PROMPT_REPORT = """你是面试复盘顾问。请根据下面的面试记录，输出一份复盘报告。

岗位：{job_title}
难度：{difficulty}
每道题的评分记录：
{records}

请输出 Markdown 格式报告，包含五个部分：
1. 总评：总分、等级、一句话总结
2. 维度得分：每个考察维度的平均分
3. 亮点：答得好的地方，2 条
4. 薄弱点：需要改进的地方，2 条
5. 改进建议：可执行的练习方向，3 条

格式要求：不要使用表格，每个部分用三级标题加列表呈现；不要加代码块围栏；不要输出解释性文字。

总分计算规则：每道题得分 = content 乘 0.4 加 structure 乘 0.3 加 expression 乘 0.3，再取所有题的平均。
等级规则：8 分以上优秀，6 到 8 分良好，4 到 6 分合格，4 分以下需加强。
如果有题目缺少评分，跳过该题。"""


def _parse_json(text):
    """宽容解析模型返回的 JSON，兼容代码块包裹、多余文字等情况。"""
    text = (text or "").strip()
    if text.startswith("```"):
        # 去掉 Markdown 代码块围栏
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def _score_total(scores):
    """按权重把三项分数折算成单题总分。"""
    if not scores:
        return 0.0
    total = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        total += float(scores.get(key, 0) or 0) * weight
    return round(total, 1)


def _clean_markdown(text):
    """去掉模型可能包上的代码块围栏，避免前端原样显示。"""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class InterviewSession:
    """一场模拟面试的完整状态。

    对外只暴露 prepare、answer、finish 三个动作，
    状态推进、LLM 调用、评分计算都在内部完成。
    """

    def __init__(self, client, model, job_title, jd="",
                 difficulty="中级", style="中性"):
        self.client = client
        self.model = model
        self.job_title = job_title or "目标岗位"
        self.jd = (jd or "").strip()
        self.difficulty = difficulty or "中级"
        self.style = style or "中性"

        self.dimensions = []       # 考察维度列表
        self.stage = STAGE_SELF_INTRO
        self.questions = []        # 已生成的题目记录
        self.current = None        # 当前正在回答的题目
        self.tech_index = 0        # 技术维度轮转下标
        self.tech_asked = 0        # 已出的技术题数量
        self.behavior_asked = 0    # 已出的行为题数量
        self.asked_texts = []      # 已问过的题干，防止重复
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.interview_id = time.strftime("%Y%m%d-%H%M%S")
        self.report = ""
        self.random = __import__("random").Random()

    # ---------- 底层调用 ----------

    def _call(self, prompt, temperature=0.3):
        """调用模型并返回文本，非流式，保证评分结果稳定。"""
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            timeout=60,
        )
        return (resp.choices[0].message.content or "").strip()

    # ---------- 题目生成 ----------

    def _tech_dimension(self):
        """按轮转取一个技术维度，没有技术维度时退回通用维度。"""
        tech = [d for d in self.dimensions if d.get("type") == "tech"]
        general = [d for d in self.dimensions if d.get("type") != "tech"]
        pool = tech or general
        if not pool:
            return {"name": "通用能力", "focus": "基础素质与表达"}
        dim = pool[self.tech_index % len(pool)]
        self.tech_index += 1
        return dim

    def _new_tech_question(self):
        """生成一道技术面试题。"""
        dim = self._tech_dimension()
        asked = "\n".join("- " + t for t in self.asked_texts) or "暂无"
        prompt = PROMPT_QUESTION.format(
            style=self.style, job_title=self.job_title,
            difficulty=self.difficulty,
            dimension_name=dim.get("name", "通用能力"),
            focus=dim.get("focus", ""),
            asked=asked)
        try:
            text = self._call(prompt, temperature=0.5)
        except Exception as e:
            text = "请介绍一下你在 %s 方面的经验。" % dim.get("name", "专业")
            text = text + "（提示：模型调用失败，已使用备用题目：%s）" % e
        return {"text": text, "dimension": dim.get("name", "通用能力"),
                "type": "tech"}

    def _new_behavior_question(self):
        """生成一道行为面试题。"""
        prompt = PROMPT_BEHAVIOR_QUESTION.format(
            style=self.style, job_title=self.job_title)
        try:
            text = self._call(prompt, temperature=0.5)
        except Exception:
            text = "请讲一次你克服困难的经历，说明情境、你的做法和最终结果。"
        return {"text": text, "dimension": "行为与协作", "type": "behavior"}

    def _self_intro_question(self):
        """自我介绍环节的固定开题，不走模型，节省一次调用。"""
        return {"text": ("第一个问题：请先做一个一分钟的自我介绍，"
                         "重点说说你与这个岗位相关的经历。"),
                "dimension": "自我认知与表达", "type": "self_intro"}

    def _push_question(self, question):
        """把新题目放入当前状态并记录。"""
        question["answers"] = []
        question["scores"] = {}
        question["comment"] = ""
        question["better_answer"] = ""
        question["followups"] = 0
        question["followup_text"] = ""
        self.current = question
        self.questions.append(question)
        self.asked_texts.append(question["text"])

    # ---------- 对外动作 ----------

    def prepare(self):
        """解析岗位并给出开场与第一题，返回起始状态。"""
        if self.jd:
            # 岗位描述过长时截断，避免 prompt 过大
            jd_text = self.jd[:1500]
            prompt = PROMPT_JOB_PARSE.format(job_title=self.job_title,
                                             jd=jd_text)
            try:
                parsed = _parse_json(self._call(prompt, temperature=0.2))
                dims = parsed.get("dimensions") or []
                if dims:
                    self.dimensions = dims
            except Exception:
                pass
        if not self.dimensions:
            # 模型解析失败时用兜底维度，面试仍可继续
            self.dimensions = [
                {"name": "专业知识", "type": "tech", "weight": 0.5,
                 "focus": "岗位相关的基础与实操"},
                {"name": "问题解决", "type": "general", "weight": 0.3,
                 "focus": "分析与应变"},
                {"name": "沟通表达", "type": "general", "weight": 0.2,
                 "focus": "表达清晰与条理"},
            ]
        # 自我介绍是必然第一阶段，直接放进题目列表
        self._push_question(self._self_intro_question())
        return {
            "ok": True,
            "interview_id": self.interview_id,
            "job_title": self.job_title,
            "difficulty": self.difficulty,
            "style": self.style,
            "dimensions": self.dimensions,
            "stage": self.stage,
            "stage_cn": STAGE_CN.get(self.stage, self.stage),
            "opening": (STYLE_OPENING.get(self.style, "") +
                        "我是今天的面试官，我们模拟一场「%s」岗位的面试，"
                        "难度%s。流程是自我介绍、技术问答、行为问答，"
                        "结束后给你一份复盘报告。" % (self.job_title,
                                                  self.difficulty)),
            "question": self.current["text"],
        }

    def answer(self, text):
        """提交一轮回答：评分、判断追问、推进到下一题或结束。"""
        if self.stage == STAGE_FINISHED:
            return {"ok": False, "text": "这场面试已经结束，请点击查看复盘报告。"}
        if not self.current:
            return {"ok": False, "text": "当前没有待回答的问题。"}

        text = (text or "").strip()
        if not text:
            return {"ok": False, "text": "回答不能为空。"}

        self.current["answers"].append(text)

        # 如果上一轮已经发过追问，本轮回答针对的是追问，评分的题目文本应换成追问内容
        in_followup = bool(self.current.get("followups") and
                           self.current.get("followup_text"))
        question_text = (self.current["followup_text"] if in_followup
                         else self.current["text"])

        # 一次调用同时拿到评分和追问判断
        prompt = PROMPT_EVALUATE.format(
            question=question_text, answer=text)
        try:
            evaluation = _parse_json(self._call(prompt, temperature=0.2))
        except Exception as e:
            return {"ok": False, "text": "评分调用失败：%s" % e}

        scores = evaluation.get("scores") or {}
        need_followup = bool(evaluation.get("need_followup"))
        followup = (evaluation.get("followup") or "").strip()

        # 未满足追问条件、追问已达上限、或追问为空时，推进到下一题
        followup_ok = (need_followup and followup and
                       self.current["followups"] < MAX_FOLLOWUPS)

        if followup_ok:
            self.current["followups"] += 1
            self.current["followup_text"] = followup
            # 追问轮暂不记最终分数，等该题结束后再记
            result = {
                "ok": True,
                "stage": self.stage,
                "stage_cn": STAGE_CN.get(self.stage, self.stage),
                "question": followup,
                "is_followup": True,
                "comment": evaluation.get("comment", ""),
                "finished": False,
                "progress": self._progress(),
            }
            return result

        # 该题到此结束，记录最终分数
        self.current["scores"] = {"content": scores.get("content", 0),
                                  "structure": scores.get("structure", 0),
                                  "expression": scores.get("expression", 0)}
        self.current["comment"] = evaluation.get("comment", "")
        self.current["better_answer"] = evaluation.get("better_answer", "")
        self.current["total"] = _score_total(self.current["scores"])

        score_card = {
            "question": self.current["text"],
            "dimension": self.current["dimension"],
            "scores": self.current["scores"],
            "total": self.current["total"],
            "comment": self.current["comment"],
            "better_answer": self.current["better_answer"],
        }

        next_question = self._advance()
        if next_question is None:
            # 所有题目完成，生成复盘报告
            self.report = self._build_report()
            return {
                "ok": True,
                "stage": STAGE_FINISHED,
                "stage_cn": STAGE_CN[STAGE_FINISHED],
                "score_card": score_card,
                "question": "",
                "finished": True,
                "report": self.report,
                "progress": self._progress(),
            }
        return {
            "ok": True,
            "stage": self.stage,
            "stage_cn": STAGE_CN.get(self.stage, self.stage),
            "score_card": score_card,
            "question": next_question["text"],
            "is_followup": False,
            "finished": False,
            "progress": self._progress(),
        }

    def _advance(self):
        """按阶段和题量推进，返回下一题；全部完成时返回 None。"""
        if self.stage == STAGE_SELF_INTRO:
            self.stage = STAGE_TECH
            self.tech_asked += 1
            q = self._new_tech_question()
            self._push_question(q)
            return q

        if self.stage == STAGE_TECH:
            if self.tech_asked < TECH_QUESTION_COUNT:
                self.tech_asked += 1
                q = self._new_tech_question()
                self._push_question(q)
                return q
            self.stage = STAGE_BEHAVIOR
            self.behavior_asked += 1
            q = self._new_behavior_question()
            self._push_question(q)
            return q

        if self.stage == STAGE_BEHAVIOR:
            if self.behavior_asked < BEHAVIOR_QUESTION_COUNT:
                self.behavior_asked += 1
                q = self._new_behavior_question()
                self._push_question(q)
                return q
            self.stage = STAGE_FINISHED
            return None

        return None

    def _progress(self):
        """返回进度信息：已完成题数、总题数、当前阶段。"""
        total = 1 + TECH_QUESTION_COUNT + BEHAVIOR_QUESTION_COUNT
        done = len([q for q in self.questions if q.get("scores")])
        return {"done": done, "total": total,
                "stage_cn": STAGE_CN.get(self.stage, self.stage)}

    def _build_report(self):
        """调用模型生成复盘报告，失败时用本地统计兜底。"""
        records = []
        for q in self.questions:
            if not q.get("scores"):
                continue
            extra = ""
            if q.get("followup_text"):
                extra = "\n  追问：%s" % q["followup_text"]
            records.append("- 题目：%s%s\n  维度：%s\n  评分：内容 %s，"
                           "结构 %s，表达 %s，总分 %s\n  点评：%s" % (
                               q["text"], extra, q["dimension"],
                               q["scores"].get("content", 0),
                               q["scores"].get("structure", 0),
                               q["scores"].get("expression", 0),
                               q.get("total", 0), q.get("comment", "")))
        record_text = "\n".join(records) or "暂无评分记录"
        prompt = PROMPT_REPORT.format(
            job_title=self.job_title, difficulty=self.difficulty,
            records=record_text)
        try:
            report = self._call(prompt, temperature=0.3)
            return _clean_markdown(report)
        except Exception:
            totals = [q.get("total", 0) for q in self.questions
                      if q.get("scores")]
            avg = round(sum(totals) / len(totals), 1) if totals else 0
            return ("## 总评\n\n平均得分 %s 分。\n\n"
                    "## 说明\n\n复盘报告生成时模型调用失败，"
                    "以上仅为本地统计结果。" % avg)

    # ---------- 状态与存档 ----------

    def snapshot(self):
        """返回当前会话的简要状态，供前端轮询或刷新恢复。"""
        return {
            "ok": True,
            "interview_id": self.interview_id,
            "job_title": self.job_title,
            "difficulty": self.difficulty,
            "style": self.style,
            "stage": self.stage,
            "stage_cn": STAGE_CN.get(self.stage, self.stage),
            "question": self.current["text"] if self.current else "",
            "progress": self._progress(),
            "questions": self.questions,
            "report": self.report,
            "finished": self.stage == STAGE_FINISHED,
        }

    def to_dict(self):
        """导出为可存档的字典。"""
        return {
            "interview_id": self.interview_id,
            "job_title": self.job_title,
            "jd": self.jd,
            "difficulty": self.difficulty,
            "style": self.style,
            "dimensions": self.dimensions,
            "stage": self.stage,
            "questions": self.questions,
            "report": self.report,
            "started_at": self.started_at,
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def save(self):
        """把面试记录写入 .agents/interviews/，返回文件路径。"""
        os.makedirs(INTERVIEW_DIR, exist_ok=True)
        path = os.path.join(INTERVIEW_DIR, self.interview_id + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path


def list_interviews():
    """列出历史面试记录，按编号倒序返回简要信息。"""
    if not os.path.isdir(INTERVIEW_DIR):
        return []
    items = []
    for name in os.listdir(INTERVIEW_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(INTERVIEW_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        questions = data.get("questions") or []
        totals = [q.get("total", 0) for q in questions if q.get("scores")]
        avg = round(sum(totals) / len(totals), 1) if totals else 0
        items.append({
            "id": data.get("interview_id", name[:-5]),
            "job_title": data.get("job_title", ""),
            "difficulty": data.get("difficulty", ""),
            "started_at": data.get("started_at", ""),
            "questions": len(questions),
            "avg_score": avg,
        })
    items.sort(key=lambda x: x["id"], reverse=True)
    return items
