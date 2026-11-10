# -*- coding: utf-8 -*-
"""zAgent 核心逻辑单测：纯本地、不需要网络与 API Key。

运行：python -m unittest discover -s tests -v
"""
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config as config_mod
from core import mcp_client
from core import session_store
from core import skill_loader
from core import tools


class DateToolsTest(unittest.TestCase):
    """日期与闰年：不依赖系统 date 命令，跨平台一致。"""

    def test_get_date_has_date_and_weekday(self):
        text = tools.get_date()
        self.assertIn("当前日期", text)
        self.assertTrue(any(w in text for w in tools.WEEKDAYS))

    def test_leap_year_boundaries(self):
        # 世纪年整除 400 才是闰年，这是最容易写错的边界
        self.assertFalse(tools.is_leap_year(1900))
        self.assertTrue(tools.is_leap_year(2000))
        self.assertTrue(tools.is_leap_year(2024))
        self.assertFalse(tools.is_leap_year(2023))
        self.assertTrue(tools.is_leap_year(1600))

    def test_call_tool_is_leap_year(self):
        self.assertIn("不是闰年", tools.call_tool("isLeapYear", {"year": 1900}))
        self.assertIn("是闰年", tools.call_tool("isLeapYear", {"year": 2000}))


class PathGuardTest(unittest.TestCase):
    """工作根目录边界：越界路径必须被拦下，不能真的读到文件。"""

    def setUp(self):
        self._old_root = tools.WORKSPACE_ROOT
        self.tmp = tempfile.mkdtemp(prefix="zagent_guard_")
        tools.WORKSPACE_ROOT = self.tmp

    def tearDown(self):
        tools.WORKSPACE_ROOT = self._old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_inside_path_allowed(self):
        ok, resolved = tools._guard_path(os.path.join(self.tmp, "a.txt"))
        self.assertTrue(ok)
        self.assertTrue(os.path.isabs(resolved))

    def test_parent_dir_blocked(self):
        ok, message = tools._guard_path(
            os.path.join(self.tmp, "..", "outside.txt"))
        self.assertFalse(ok)
        self.assertIn("超出允许的工作根目录", message)

    def test_absolute_outside_blocked(self):
        ok, message = tools._guard_path(os.path.dirname(self.tmp))
        self.assertFalse(ok)
        self.assertIn("超出允许的工作根目录", message)

    def test_list_directory_outside_root_blocked(self):
        result = tools.call_tool(
            "listDirectory", {"path": os.path.join(self.tmp, "..")})
        self.assertIn("超出允许的工作根目录", result)

    def test_create_file_outside_root_blocked(self):
        result = tools.call_tool("createFile", {
            "directory": os.path.join(self.tmp, ".."),
            "filename": "evil.txt",
            "content": "boom",
        })
        self.assertIn("超出允许的工作根目录", result)


class DeleteConfirmTest(unittest.TestCase):
    """删除是危险操作：没有确认时拒绝执行，确认后才真的删除。"""

    def setUp(self):
        self._old_root = tools.WORKSPACE_ROOT
        self._old_confirm = tools.CONFIRM_DELETE
        self.tmp = tempfile.mkdtemp(prefix="zagent_del_")
        tools.WORKSPACE_ROOT = self.tmp
        tools.CONFIRM_DELETE = True
        self.file_path = os.path.join(self.tmp, "target.txt")
        with open(self.file_path, "w", encoding="utf-8") as f:
            f.write("delete me")

    def tearDown(self):
        tools.WORKSPACE_ROOT = self._old_root
        tools.CONFIRM_DELETE = self._old_confirm
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_delete_without_confirm_is_refused(self):
        result = tools.call_tool("deleteFile", {"path": self.file_path})
        self.assertIn("未收到确认", result)
        self.assertTrue(os.path.isfile(self.file_path))

    def test_delete_with_confirm_works(self):
        result = tools.call_tool(
            "deleteFile", {"path": self.file_path, "confirm": True})
        self.assertIn("已删除", result)
        self.assertFalse(os.path.exists(self.file_path))

    def test_delete_directory_without_confirm_is_refused(self):
        sub = os.path.join(self.tmp, "sub")
        os.makedirs(sub)
        result = tools.call_tool("deleteDirectory", {"path": sub})
        self.assertIn("未收到确认", result)
        self.assertTrue(os.path.isdir(sub))


class CallToolRobustnessTest(unittest.TestCase):
    """工具层出错不能把整轮对话打断，必须收敛成可读文本。"""

    def test_missing_argument_returns_text(self):
        result = tools.call_tool("getFileType", {})
        self.assertIn("工具执行出错", result)
        self.assertIn("KeyError", result)

    def test_unknown_tool_returns_text(self):
        self.assertIn("未知工具", tools.call_tool("noSuchTool", {}))

    def test_nonexistent_file_returns_text(self):
        result = tools.call_tool(
            "getFileContent", {"path": r"C:\no\such\file.txt"})
        self.assertIn("错误", result)

    def test_long_file_content_truncated(self):
        tmp = tempfile.mkdtemp(prefix="zagent_big_")
        try:
            path = os.path.join(tmp, "big.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("A" * 6000)
            result = tools.call_tool("getFileContent", {"path": path})
            self.assertIn("已截断", result)
            self.assertLess(len(result), 6000)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SkillLoaderTest(unittest.TestCase):
    """技能路径不能跳出技能目录，超长技能文件要截断。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zagent_skill_")
        self.skill_dir = os.path.join(
            self.tmp, skill_loader.SKILLS_DIR, "demo")
        os.makedirs(self.skill_dir)
        with open(os.path.join(self.skill_dir, "SKILL.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nname: demo\ndescription: 演示技能\n---\n正文")
        # 技能外的机密文件，用来验证逃逸拦截
        self.secret = os.path.join(self.tmp, "secret.txt")
        with open(self.secret, "w", encoding="utf-8") as f:
            f.write("SECRET")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_escape_is_blocked(self):
        result = skill_loader.load_skill_file(
            "demo", "../secret.txt", base_dir=self.tmp)
        self.assertIn("不允许访问 Skill 目录", result)

    def test_inside_file_can_be_read(self):
        target = os.path.join(self.skill_dir, "notes.md")
        with open(target, "w", encoding="utf-8") as f:
            f.write("笔记")
        result = skill_loader.load_skill_file(
            "demo", "notes.md", base_dir=self.tmp)
        self.assertEqual(result, "笔记")

    def test_long_skill_is_truncated(self):
        target = os.path.join(self.skill_dir, "long.md")
        with open(target, "w", encoding="utf-8") as f:
            f.write("B" * 30000)
        result = skill_loader.load_skill_file(
            "demo", "long.md", base_dir=self.tmp)
        self.assertIn("已截断", result)
        self.assertLess(len(result), 30000)


class SessionStoreTest(unittest.TestCase):
    """AGENTS.md 注入：兼容两种命名，超长截断，缺失不报错。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zagent_md_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_returns_none(self):
        self.assertIsNone(session_store.read_agents_md(base_dir=self.tmp))

    def test_agent_md_fallback(self):
        with open(os.path.join(self.tmp, "AGENT.md"), "w",
                  encoding="utf-8") as f:
            f.write("规则")
        self.assertEqual(
            session_store.read_agents_md(base_dir=self.tmp), "规则")

    def test_truncated_by_max_chars(self):
        with open(os.path.join(self.tmp, "AGENTS.md"), "w",
                  encoding="utf-8") as f:
            f.write("C" * 100)
        result = session_store.read_agents_md(
            base_dir=self.tmp, max_chars=10)
        self.assertIn("已截断", result)
        self.assertTrue(result.startswith("C" * 10))

    def test_custom_name(self):
        with open(os.path.join(self.tmp, "MY_RULES.md"), "w",
                  encoding="utf-8") as f:
            f.write("自定义")
        self.assertEqual(
            session_store.read_agents_md(base_dir=self.tmp,
                                         name="MY_RULES.md"), "自定义")


class McpSchemaTest(unittest.TestCase):
    """MCP 工具定义转 OpenAI Function Calling 格式。"""

    class FakeTool:
        name = "list_pets"
        description = "按条件查询宠物" * 40  # 超过 200 字符，验证截断
        inputSchema = {
            "type": "object",
            "properties": {"page": {"type": "integer"}},
            "required": ["page"],
        }

    def test_convert_to_openai(self):
        converted = mcp_client._tool_to_openai(self.FakeTool())
        self.assertEqual(converted["type"], "function")
        func = converted["function"]
        self.assertEqual(func["name"], "list_pets")
        self.assertLessEqual(len(func["description"]), 200)
        self.assertEqual(func["parameters"]["properties"]["page"]["type"],
                         "integer")
        self.assertEqual(func["parameters"]["required"], ["page"])

    def test_broken_schema_does_not_raise(self):
        class BadTool:
            name = "bad"
            description = None
            inputSchema = None

        converted = mcp_client._tool_to_openai(BadTool())
        self.assertEqual(converted["function"]["name"], "bad")
        self.assertEqual(converted["function"]["parameters"]["properties"], {})


class ConfigDefaultsTest(unittest.TestCase):
    """配置缺段缺键时全部走默认值，不能抛异常。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zagent_cfg_")
        self.path = os.path.join(self.tmp, "config.ini")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("[LLM]\napi_key = test-key\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_applied(self):
        cfg = config_mod.load_config(self.path)
        self.assertEqual(cfg["max_steps"], config_mod.DEFAULT_MAX_STEPS)
        self.assertEqual(cfg["max_messages"],
                         config_mod.DEFAULT_MAX_MESSAGES)
        self.assertEqual(cfg["agents_md_name"], "AGENTS.md")
        self.assertTrue(cfg["confirm_delete"])
        self.assertEqual(cfg["workspace_root"], "")

    def test_safety_section_read(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("[LLM]\napi_key = test-key\n\n"
                    "[Safety]\nconfirm_delete = false\n"
                    "workspace_root = .\n")
        cfg = config_mod.load_config(self.path)
        self.assertFalse(cfg["confirm_delete"])
        self.assertTrue(os.path.isabs(cfg["workspace_root"]))

    def test_invalid_number_falls_back(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("[LLM]\napi_key = test-key\n\n[Agent]\nmax_steps = abc\n")
        cfg = config_mod.load_config(self.path)
        self.assertEqual(cfg["max_steps"], config_mod.DEFAULT_MAX_STEPS)

    def test_missing_llm_section_raises(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("[Other]\nx = 1\n")
        with self.assertRaises(ValueError):
            config_mod.load_config(self.path)


class TrimMessagesTest(unittest.TestCase):
    """历史截断必须保留所有 system 消息。"""

    def test_system_messages_kept(self):
        try:
            from core.agent import Agent
        except Exception as e:  # 依赖缺失时跳过，而不是让整套测试失败
            self.skipTest("无法导入 core.agent：%s" % e)

        fake = types.SimpleNamespace(
            messages=[
                {"role": "system", "content": "主提示"},
                {"role": "system", "content": "AGENTS.md"},
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
                {"role": "assistant", "content": "a2"},
            ],
            max_messages=4,
        )
        Agent.trim_messages(fake)
        self.assertEqual(len(fake.messages), 4)
        self.assertEqual(fake.messages[0]["content"], "主提示")
        self.assertEqual(fake.messages[1]["content"], "AGENTS.md")
        self.assertEqual(fake.messages[-1]["content"], "a2")


if __name__ == "__main__":
    unittest.main()
