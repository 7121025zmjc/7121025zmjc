# -*- coding: utf-8 -*-
"""配置加载模块：从 config.ini 读取 LLM 接口与运行参数。

所有可调参数都集中在 config.ini 中，代码里只保留默认值；
config.ini 不存在时可用 ensure_config_file 自动创建模板。
"""
import configparser
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.ini")

# 代码内置默认值，config.ini 未配置对应项时使用
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-3.5-turbo"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_MESSAGES = 30
DEFAULT_WARN_RATIO = 0.8
DEFAULT_COMPRESS_TARGET_TOKENS = 5000
DEFAULT_ARCHIVE_DIR = os.path.join(BASE_DIR, ".agents", "context")
DEFAULT_SESSION_DIR = os.path.join(BASE_DIR, ".agents", "sessions")
DEFAULT_SEARCH_TIMEOUT = 30
DEFAULT_MAX_STEPS = 12
DEFAULT_AGENTS_MD = "AGENTS.md"
DEFAULT_AGENTS_MD_MAX_CHARS = 8000
DEFAULT_WORKSPACE_ROOT = ""
DEFAULT_CONFIRM_DELETE = True
DEFAULT_OFFICE_CLI_EXE = r"D:\office\.agents\skills\office-cli\officecli-win-x64.exe"

# config.ini 缺失时自动创建的完整模板，填入 mock 信息，
# 用户修改 api_key / base_url / model 后重新启动才能正常工作
MOCK_CONFIG = """[LLM]
api_key = your_api_key_here
base_url = https://api.openai.com/v1
model = gpt-3.5-turbo
# 可选：模型上下文窗口大小，单位 Token。不配置时默认按 128k 计算并在界面显示「默认」
# context_window = 131072

[Agent]
# LLM 请求超时时间（秒）
timeout = 60
# 消息历史保留的最大条数，超过后自动截断最早的对话（系统提示不受影响）
max_messages = 30
# 单轮对话内最多执行多少次工具调用，防止模型陷入无限循环
max_steps = 12

[Context]
# 上下文使用量达到窗口的该比例时，警告并暂停接收新消息
warn_ratio = 0.8
# 压缩摘要的目标 Token 数，写入压缩 Prompt 提示 LLM
compress_target_tokens = 5000
# 原始上下文归档目录，/compact 压缩时保存完整历史
archive_dir = .agents/context

[Session]
# 历史会话保存目录，/save 与 /new 自动保存
session_dir = .agents/sessions
# 长期规则文件名，默认兼容 AGENTS.md 与 AGENT.md
agents_md = AGENTS.md
# AGENTS.md 注入时的最大字符数，超出部分截断
agents_md_max_chars = 8000

[Tools]
# 联网搜索超时（秒）
search_timeout = 30
# officecli 可执行文件路径
office_cli_exe = D:\\office\\.agents\\skills\\office-cli\\officecli-win-x64.exe

[Safety]
# 允许文件操作的工作根目录；留空表示不限制，建议填项目目录或 D:\\LLM-Chat
workspace_root =
# 删除文件/目录是否要求模型显式传 confirm=true
confirm_delete = true

[search]
# 百度千帆 API Key，用于 searchWeb 联网搜索
baidu_qianfan_api_key = your_qianfan_api_key_here
"""


def _resolve(base, value):
    """相对路径以项目根为基准解析为绝对路径。"""
    value = (value or "").strip()
    if not value:
        return value
    if os.path.isabs(value):
        return value
    return os.path.normpath(os.path.join(base, value))


def _get_int(config, section, key, default):
    """读取整数配置，缺失或非法时返回默认值。"""
    raw = config.get(section, key, fallback="").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(config, section, key, default):
    """读取浮点配置，缺失或非法时返回默认值。"""
    raw = config.get(section, key, fallback="").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_bool(config, section, key, default):
    """读取布尔配置，缺失或非法时返回默认值。"""
    raw = config.get(section, key, fallback="").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def load_config(path=CONFIG_FILE):
    """读取配置文件，返回完整配置字典。

    各 section 未配置的项使用代码内置默认值，
    相对路径统一解析为项目根下的绝对路径。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            "未找到配置文件 %s，请先参照以下格式创建：\n"
            "[LLM]\napi_key = your_api_key_here\n"
            "base_url = https://api.openai.com/v1\nmodel = gpt-3.5-turbo" % path)

    config = configparser.ConfigParser()
    try:
        config.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError) as e:
        raise ValueError("读取配置文件失败：%s" % e)

    if not config.has_section("LLM"):
        raise ValueError("配置文件中缺少 [LLM] 段，请检查 config.ini 格式。")

    api_key = config.get("LLM", "api_key", fallback="").strip()
    if not api_key:
        raise ValueError("配置文件中 api_key 为空，请填写有效的 API Key。")

    # 可选：模型上下文窗口大小，单位 Token。
    # 未配置或配置无效时返回 None，Agent 端默认按 128k 处理
    context_window_raw = config.get("LLM", "context_window",
                                    fallback="").strip()
    try:
        context_window = int(context_window_raw) if context_window_raw else None
    except ValueError:
        context_window = None

    return {
        "api_key": api_key,
        "base_url": config.get("LLM", "base_url",
                               fallback=DEFAULT_BASE_URL).strip(),
        "model": config.get("LLM", "model", fallback=DEFAULT_MODEL).strip(),
        "context_window": context_window,
        "baidu_qianfan_api_key": config.get("search", "baidu_qianfan_api_key",
                                            fallback="").strip(),
        "timeout": _get_int(config, "Agent", "timeout", DEFAULT_TIMEOUT),
        "max_messages": _get_int(config, "Agent", "max_messages",
                                 DEFAULT_MAX_MESSAGES),
        "warn_ratio": _get_float(config, "Context", "warn_ratio",
                                 DEFAULT_WARN_RATIO),
        "compress_target_tokens": _get_int(
            config, "Context", "compress_target_tokens",
            DEFAULT_COMPRESS_TARGET_TOKENS),
        "archive_dir": _resolve(BASE_DIR, config.get(
            "Context", "archive_dir", fallback=".agents/context")),
        "session_dir": _resolve(BASE_DIR, config.get(
            "Session", "session_dir", fallback=".agents/sessions")),
        "search_timeout": _get_int(config, "Tools", "search_timeout",
                                   DEFAULT_SEARCH_TIMEOUT),
        "office_cli_exe": config.get("Tools", "office_cli_exe",
                                     fallback=DEFAULT_OFFICE_CLI_EXE).strip(),
        "max_steps": _get_int(config, "Agent", "max_steps",
                              DEFAULT_MAX_STEPS),
        "agents_md_name": config.get("Session", "agents_md",
                                     fallback=DEFAULT_AGENTS_MD).strip()
        or DEFAULT_AGENTS_MD,
        "agents_md_max_chars": _get_int(
            config, "Session", "agents_md_max_chars",
            DEFAULT_AGENTS_MD_MAX_CHARS),
        "workspace_root": _resolve(BASE_DIR, config.get(
            "Safety", "workspace_root", fallback=DEFAULT_WORKSPACE_ROOT)),
        "confirm_delete": _get_bool(config, "Safety", "confirm_delete",
                                    DEFAULT_CONFIRM_DELETE),
    }


def ensure_config_file(path=CONFIG_FILE):
    """确保 config.ini 存在：不存在时创建完整模板并返回 mock 配置。

    返回 (cfg, created)：created 为 True 表示本次自动创建了模板，
    调用方应提示用户修改后重新启动。
    """
    if os.path.exists(path):
        return load_config(path), False
    with open(path, "w", encoding="utf-8") as f:
        f.write(MOCK_CONFIG)
    return load_config(path), True
