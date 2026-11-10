# -*- coding: utf-8 -*-
"""Skill 发现与加载：扫描 .agents/skills/ 目录，解析 SKILL.md。

每个 Skill 是一个子目录，至少包含一个 SKILL.md 文件，
文件开头用 YAML Frontmatter 声明 name 和 description。
初始化时只加载 name/description 注入 system prompt，
完整正文通过 loadSkill 工具按需加载。"""
import os
import re

SKILLS_DIR = ".agents" + os.sep + "skills"
EXTRA_SKILLS_DIRS = [r"D:\office\.agents\skills"]

# 单个技能文件的读取上限，超过后截断，避免一次把超大文件塞进上下文
MAX_SKILL_CHARS = 20000


def _read_limited(path, limit=None):
    """读取文本文件，超过上限时截断并附提示。"""
    limit = limit or MAX_SKILL_CHARS
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if len(text) > limit:
        return (text[:limit]
                + "\n...(内容过长已截断，原文共 %d 字符，"
                  "需要完整内容请用 loadSkill 指定具体 filename)" % len(text))
    return text


def _parse_frontmatter(text):
    """解析 SKILL.md 开头的 YAML Frontmatter，返回 (meta, body)。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


def _scan_skills_root(skills_root):
    """扫描一个 skills 根目录，返回 Skill 列表。"""
    skills = []
    if not os.path.isdir(skills_root):
        return skills
    for entry in os.listdir(skills_root):
        skill_dir = os.path.join(skills_root, entry)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(skill_md):
            # 兼容小写 skill.md
            skill_md_lower = os.path.join(skill_dir, "skill.md")
            if os.path.isfile(skill_md_lower):
                skill_md = skill_md_lower
            else:
                continue
        try:
            with open(skill_md, "r", encoding="utf-8") as f:
                text = f.read()
            meta, _ = _parse_frontmatter(text)
            name = meta.get("name", entry)
            desc = meta.get("description", "")
            skills.append({
                "name": name,
                "description": desc,
                "path": skill_dir,
                "skill_md": skill_md,
            })
        except Exception:
            continue
    return skills


_CACHE = {}


def discover_skills(base_dir=None):
    """扫描项目目录和额外目录下的 skills，返回 Skill 列表。

    初始化阶段只读取 name 和 description，不读正文。
    """
    if base_dir is None:
        base_dir = os.getcwd()
    skills = []
    seen = set()
    # 先扫项目目录下的
    for s in _scan_skills_root(os.path.join(base_dir, SKILLS_DIR)):
        if s["name"] not in seen:
            skills.append(s)
            seen.add(s["name"])
    # 再扫额外目录
    for extra in EXTRA_SKILLS_DIRS:
        for s in _scan_skills_root(extra):
            if s["name"] not in seen:
                skills.append(s)
                seen.add(s["name"])
    _CACHE.clear()
    for s in skills:
        _CACHE[s["name"]] = s["path"]
    return skills


def _find_skill_dir(name, base_dir=None):
    """先按 name 在缓存里找路径，找不到再按文件夹名搜索。"""
    if name in _CACHE:
        return _CACHE[name]
    if base_dir is None:
        base_dir = os.getcwd()
    search_roots = [os.path.join(base_dir, SKILLS_DIR)] + EXTRA_SKILLS_DIRS
    for root in search_roots:
        skill_dir = os.path.join(root, name)
        if os.path.isdir(skill_dir):
            return skill_dir
    return None


def load_skill_body(name, base_dir=None):
    """按名称加载某个 Skill 的 SKILL.md 全文。"""
    skill_dir = _find_skill_dir(name, base_dir)
    if not skill_dir:
        return "未找到 Skill: %s" % name
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        skill_md = os.path.join(skill_dir, "skill.md")
    if not os.path.isfile(skill_md):
        return "未找到 SKILL.md: %s" % name
    return _read_limited(skill_md)


def load_skill_file(name, filename, base_dir=None):
    """在指定 Skill 目录内加载附加文件，不跳出 Skill 目录。"""
    skill_dir = _find_skill_dir(name, base_dir)
    if not skill_dir:
        return "未找到 Skill: %s" % name
    # 防止路径逃逸：用 commonpath 比较，避免同前缀目录绕过
    skill_root = os.path.normpath(skill_dir)
    target = os.path.normpath(os.path.join(skill_root, filename))
    try:
        inside = os.path.commonpath([skill_root, target]) == skill_root
    except ValueError:
        inside = False
    if not inside:
        return "错误：不允许访问 Skill 目录之外的文件"
    if os.path.isfile(target):
        return _read_limited(target)
    # 递归搜索文件名
    candidates = []
    for root, _, files in os.walk(skill_root):
        for fn in files:
            if fn == os.path.basename(filename):
                candidates.append(os.path.join(root, fn))
    if len(candidates) == 1:
        return _read_limited(candidates[0])
    if len(candidates) > 1:
        return "找到多个同名文件，请指定路径：\n" + "\n".join(candidates)
    return "未找到文件: %s" % filename


def build_skills_prompt(skills):
    """把 Skill 列表整理成 system prompt 片段。"""
    if not skills:
        return ""
    lines = ["You have access to the following skills:"]
    for s in skills:
        lines.append("- %s: %s" % (s["name"], s["description"]))
    lines.append("Use the loadSkill tool to load a skill's full instructions when needed.")
    return "\n".join(lines)
