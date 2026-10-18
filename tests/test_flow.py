"""端到端流程：用桩件模拟 AstrBot，跑完 开局→提问→提示→作答→揭晓。"""

from __future__ import annotations

import sys
import types

import pytest
from astrbot_plugin_situation_puzzle.core.store import PuzzleStore
from astrbot_plugin_situation_puzzle.judge.base import JudgeResult


def _install_astrbot_stubs() -> None:
    """在导入插件入口前注入最小 astrbot 依赖，避免依赖真实框架。"""
    if "astrbot" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")

    class AstrBotConfig(dict):
        def save_config(self) -> None:
            self.saved = True

    class Logger:
        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def debug(self, *args, **kwargs):
            pass

    api.AstrBotConfig = AstrBotConfig
    api.logger = Logger()

    event_mod = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:
        pass

    class Filter:
        class EventMessageType:
            ALL = "all"
            PRIVATE_MESSAGE = "private"
            GROUP_MESSAGE = "group"

        @staticmethod
        def command(name, alias=None, **kwargs):
            def wrap(func):
                return func

            return wrap

        @staticmethod
        def event_message_type(*args, **kwargs):
            def wrap(func):
                return func

            return wrap

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.filter = Filter
    event_mod.MessageChain = type("MessageChain", (), {})

    star_mod = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context):
            self.context = context

    class Context:
        pass

    star_mod.Star = Star
    star_mod.Context = Context

    web_mod = types.ModuleType("astrbot.api.web")

    class PluginUploadFile:
        pass

    class Query:
        def __init__(self) -> None:
            self.params = {}

        def get(self, key, default=None, type=None):
            value = self.params.get(key, default)
            if type is not None and value is not None:
                try:
                    return type(value)
                except (TypeError, ValueError):
                    return default
            return value

    class FakeRequest:
        def __init__(self) -> None:
            self.query = Query()
            self.username = "admin"
            self.payload = {}
            self.uploaded = {}

        async def json(self, default=None):
            return self.payload if self.payload else (default or {})

        async def files(self):
            return self.uploaded

    web_mod.PluginUploadFile = PluginUploadFile
    web_mod.request = FakeRequest()
    web_mod.json_response = lambda data: data
    web_mod.error_response = lambda message, status_code=400: {"error": message}
    web_mod.file_response = lambda *args, **kwargs: {}
    web_mod.stream_response = lambda gen: gen

    components = types.ModuleType("astrbot.api.message_components")
    components.Plain = type("Plain", (), {})

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.web"] = web_mod
    sys.modules["astrbot.api.message_components"] = components


_install_astrbot_stubs()

from astrbot_plugin_situation_puzzle import main as plugin_main


class StubContext:
    def __init__(self) -> None:
        self.routes = []

    def register_web_api(self, path, handler, methods, description):
        self.routes.append((path, methods))

    async def get_current_chat_provider_id(self, umo=None):
        return "stub-provider"

    async def llm_generate(self, **kwargs):
        return types.SimpleNamespace(
            completion_text='{"choice": "是", "confidence": 0.9}'
        )


class At:
    def __init__(self, qq: str) -> None:
        self.qq = qq


class Reply:
    def __init__(self, sender_id: str) -> None:
        self.sender_id = sender_id


class MessageObj:
    def __init__(self, self_id="bot", message=None, group_id="g1") -> None:
        self.self_id = self_id
        self.message = message or []
        self.group_id = group_id


class FakeEvent:
    def __init__(self, text: str, message=None, sender="u1") -> None:
        self.message_str = text
        self.unified_msg_origin = "test:GroupMessage:1"
        self.message_obj = MessageObj(message=message)
        self.sender = sender
        self.stopped = False

    def get_sender_id(self) -> str:
        return self.sender

    def get_sender_name(self) -> str:
        return "小明"

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        self.stopped = True

    def is_private(self) -> bool:
        return False

    def is_admin(self) -> bool:
        return True


class FakeEngine:
    name = "fake"

    def __init__(self, verdict: str = "yes", solve_verdict: str = "solved") -> None:
        self.verdict = verdict
        self.solve_verdict = solve_verdict
        self.ask_calls = []

    async def ask(self, payload, umo=None):
        self.ask_calls.append(payload)
        return JudgeResult(
            verdict=self.verdict, confidence=0.92, engine=self.name, latency_ms=12
        )

    async def solve(self, payload, umo=None):
        return JudgeResult(
            verdict=self.solve_verdict, confidence=0.9, engine=self.name, latency_ms=20
        )

    async def review(self, payload, umo=None):
        return "review_pass"

    async def close(self):
        pass


async def collect(generator) -> list[str]:
    return [item async for item in generator]


async def build_plugin(tmp_path, config=None):
    context = StubContext()
    plugin = plugin_main.SituationPuzzlePlugin(context, config or {})
    plugin.data_dir = tmp_path
    plugin.store = PuzzleStore(tmp_path)
    await plugin._ensure_ready()
    plugin.engine = FakeEngine()
    return plugin, context


def test_routes_registered(tmp_path):
    import asyncio

    async def scenario():
        plugin, context = await build_plugin(tmp_path)
        try:
            paths = {path for path, _ in context.routes}
            assert "/astrbot_plugin_situation_puzzle/settings" in paths
            assert "/astrbot_plugin_situation_puzzle/library/list" in paths
            assert "/astrbot_plugin_situation_puzzle/events" in paths
            assert len(paths) == 15
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_full_game_flow(tmp_path):
    import asyncio

    async def scenario():
        plugin, _ = await build_plugin(tmp_path)
        try:
            start = FakeEvent("/汤 开 easy")
            texts = await collect(plugin.tang_command(start))
            assert any("主持人" in text for text in texts)

            room = plugin.rooms.get(start.unified_msg_origin)
            assert room is not None
            answer = room.puzzle.answer

            ask = FakeEvent("海龟 主角最后赢了吗？")
            replies = await collect(plugin.on_message(ask))
            assert replies == ["「主角最后赢了吗？」→ 是"]
            assert ask.stopped is True
            assert all(answer not in text for text in replies)

            hint = await collect(plugin.tang_command(FakeEvent("/汤 提示")))
            assert hint[0].startswith("提示 1/")

            solved = await collect(
                plugin.tang_command(
                    FakeEvent("/汤 答 他为了不覆盖父亲的记录而停在终点线前")
                )
            )
            assert any(answer in text for text in solved)

            assert plugin.rooms.get(start.unified_msg_origin) is None
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_plain_chat_is_not_answered(tmp_path):
    import asyncio

    async def scenario():
        plugin, _ = await build_plugin(tmp_path)
        try:
            await collect(plugin.tang_command(FakeEvent("/汤")))
            chat = FakeEvent("今天天气不错")
            assert await collect(plugin.on_message(chat)) == []
            assert chat.stopped is False
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_uncertain_is_not_guessed(tmp_path):
    import asyncio

    async def scenario():
        plugin, _ = await build_plugin(tmp_path)
        plugin.engine = FakeEngine(verdict="uncertain")
        try:
            await collect(plugin.tang_command(FakeEvent("/汤")))
            replies = await collect(plugin.on_message(FakeEvent("海龟 他是凶手吗")))
            assert replies == ["这个问题我得再看看（无法确定）"]
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_reveal_is_locked_at_start(tmp_path):
    import asyncio

    async def scenario():
        plugin, _ = await build_plugin(tmp_path)
        try:
            event = FakeEvent("/汤")
            await collect(plugin.tang_command(event))
            room = plugin.rooms.get(event.unified_msg_origin)
            blocked = await collect(plugin.tang_command(FakeEvent("/汤 揭晓")))
            assert "不能揭晓" in blocked[0]

            room.started_at -= plugin.config.reveal_lock_s + 5
            revealed = await collect(plugin.tang_command(FakeEvent("/汤 揭晓")))
            assert room.puzzle.answer in revealed[0]
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_config_update_rebuilds_engine(tmp_path):
    import asyncio

    async def scenario():
        plugin, _ = await build_plugin(tmp_path)
        try:
            errors = plugin.update_config(
                {"judge": {"engine": "provider", "threshold": 1.5}}
            )
            assert errors
            assert plugin.config.engine == "jev"

            errors = plugin.update_config(
                {"judge": {"engine": "provider", "threshold": 0.62}}
            )
            assert errors == []
            assert plugin.config.engine == "provider"
            assert plugin.config.threshold == pytest.approx(0.62)
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def fake_request():
    return sys.modules["astrbot.api.web"].request


def test_settings_and_library_api(tmp_path):
    import asyncio

    async def scenario():
        plugin, _ = await build_plugin(tmp_path)
        request = fake_request()
        try:
            settings = await plugin.api.get_settings()
            assert "judge" in settings["schema"]
            assert settings["meta"]["puzzle_total"] >= 350

            request.payload = {"judge": {"threshold": 0.66}}
            saved = await plugin.api.save_settings()
            assert saved["saved"] is True
            assert plugin.config.threshold == pytest.approx(0.66)

            request.payload = {"judge": {"threshold": 9}}
            rejected = await plugin.api.save_settings()
            assert rejected["saved"] is False
            assert plugin.config.threshold == pytest.approx(0.66)

            request.payload = {}
            request.query.params = {"page": "1", "size": "5"}
            listed = await plugin.api.list_library()
            assert listed["size"] == 5
            assert len(listed["items"]) == 5

            request.query.params = {}
            stats = await plugin.api.get_stats()
            assert stats["puzzle_total"] >= 350
            assert stats["active_rooms"] == 0
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_engine_test_reports_auth_failure_without_key(tmp_path):
    import asyncio

    async def scenario():
        plugin, _ = await build_plugin(tmp_path)
        request = fake_request()
        try:
            request.payload = {"question": "他是自杀吗？"}
            result = await plugin.api.test_engine()
            assert result["ok"] is False
            assert result["error_class"] == "auth"
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_upsert_and_lint_api(tmp_path):
    import asyncio

    async def scenario():
        plugin, _ = await build_plugin(tmp_path)
        request = fake_request()
        try:
            request.payload = {
                "id": "custom-api",
                "title": "面板新增",
                "story": "这是一段足够长的汤面，用来通过最短长度检查。",
                "answer": "这是一段足够长的汤底，用来通过最短长度检查。",
                "hints": ["一", "二", "三"],
                "category": "本格",
                "difficulty": "medium",
            }
            saved = await plugin.api.upsert_puzzle()
            assert saved["saved"] is True
            assert await plugin.store.get_puzzle("custom-api") is not None

            request.payload = {}
            lint = await plugin.api.lint_library()
            assert lint["total"] >= 350
            assert isinstance(lint["issues"], list)
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_keyword_starts_game_and_asks_first_question(tmp_path):
    import asyncio

    async def scenario():
        plugin, _ = await build_plugin(tmp_path)
        try:
            event = FakeEvent("海龟 主角最后赢了吗？")
            texts = await collect(plugin.on_message(event))
            assert any("主持人" in text for text in texts)
            assert texts[-1] == "「主角最后赢了吗？」→ 是"
            assert event.stopped is True
            assert plugin.rooms.get(event.unified_msg_origin) is not None
        finally:
            await plugin.terminate()

    asyncio.run(scenario())
