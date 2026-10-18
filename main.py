"""Jev 海龟汤：AstrBot 插件入口。

一个会话一局；提问三通道（@bot / 回复 bot / “海龟 ”前缀）；
判题默认走 Jev SystemOne 协议，也可切 AstrBot 已配的大模型。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.config import PluginConfig
from .core.room import Room, RoomManager
from .core.router import Action, Decision, classify
from .core.stats import EventLog, percent
from .core.store import PuzzleStore
from .core.templates import render, split_message, strip_command_slash
from .judge import JudgeError
from .judge.factory import build_engine
from .judge.prompts import AskInput, RecentQa, SolveInput
from .judge.protocol import label
from .web.api import PLUGIN_NAME, PluginAPI

SEED_VERSION = "2026-10-19.1"

HELP_TEXT = """【Jev 海龟汤】
/汤 开局 · /汤 提示 · /汤 答 <推理> · /汤 揭晓
/汤 状态 · /汤 跳过 · /汤 排行 · /汤 题库
提问方式：@我 或 回复我的消息 或 发送「海龟 你的问题」
我只回答 是 / 否 / 无关，拿不准会说无法确定。"""


def _plugin_data_dir() -> Path:
    """持久化目录固定在 data 下，插件更新不会丢数据。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        base = Path(get_astrbot_plugin_data_path())
    except Exception:  # noqa: BLE001 - 老版本没有该工具函数
        base = Path("data") / "plugin_data"
    return base / PLUGIN_NAME


class SituationPuzzlePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.context = context
        self.raw_config = config if config is not None else {}
        self.config = PluginConfig(dict(self.raw_config) if self.raw_config else {})
        self.data_dir = _plugin_data_dir()
        self.store = PuzzleStore(self.data_dir)
        self.rooms = RoomManager()
        self.events = EventLog()
        self.engine: Any = None
        self.api = PluginAPI(self)
        self._ready = False
        self._init_lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._register_web_api()

    # ---- 生命周期 ----
    def _register_web_api(self) -> None:
        try:
            for path, handler, methods, description in self.api.routes():
                self.context.register_web_api(path, handler, methods, description)
        except AttributeError:
            logger.warning(
                "[海龟汤] 当前 AstrBot 版本不支持插件页面，仅保留原生配置面板"
            )
        except Exception as error:  # noqa: BLE001 - 面板注册失败不能拖垮插件
            logger.warning(f"[海龟汤] 注册插件页面失败：{error}")

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return
            await self.store.init()
            seed = Path(__file__).parent / "resources" / "library.seed.json"
            imported = await self.store.import_seed(seed, SEED_VERSION)
            self.engine = self.build_engine()
            self._ready = True
            total = await self.store.count()
            logger.info(f"[海龟汤] 就绪：题库 {total} 题，本次导入 {imported} 题")
            if self._cleanup_task is None:
                self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(120)
            try:
                for umo in self.rooms.expired(self.config.idle_timeout_min):
                    room = self.rooms.drop(umo)
                    if room is None:
                        continue
                    await self.store.finish_room(
                        umo, room.room_id, "expired", False, room.puzzle.id
                    )
                    self.events.add(
                        "info", "闲置结束对局", umo=umo, puzzle=room.puzzle.id
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - 清理失败不终止循环
                logger.warning(f"[海龟汤] 清理闲置房间失败：{error}")

    async def terminate(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
        if self.engine is not None:
            with contextlib.suppress(Exception):
                await self.engine.close()
        logger.info("[海龟汤] 已卸载")

    # ---- 面板与配置 ----
    def load_schema(self) -> dict[str, Any]:
        path = Path(__file__).parent / "_conf_schema.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logger.warning(f"[海龟汤] 读取配置 schema 失败：{error}")
            return {}

    def build_engine(self, provider_id: str | None = None) -> Any:
        return build_engine(self.config, self.context, provider_id)

    def default_provider_id(self) -> str:
        configured = str(self.config.get("judge.provider_id", "") or "")
        if configured:
            return configured
        try:
            manager = getattr(self.context, "provider_manager", None)
            for provider in getattr(manager, "providers", None) or []:
                config = getattr(provider, "provider_config", None) or {}
                if config.get("id"):
                    return str(config["id"])
        except Exception:  # noqa: BLE001, S110 - 取不到就交给上层报错
            pass
        return ""

    def update_config(self, payload: dict[str, Any]) -> list[str]:
        errors = self.config.apply(payload)
        if errors:
            return errors
        try:
            if hasattr(self.raw_config, "save_config"):
                self.raw_config.clear()
                self.raw_config.update(self.config.raw)
                self.raw_config.save_config()
            elif isinstance(self.raw_config, dict):
                self.raw_config.clear()
                self.raw_config.update(self.config.raw)
        except Exception as error:  # noqa: BLE001 - 保存失败要让人看见
            logger.warning(f"[海龟汤] 保存配置失败：{error}")
            return [f"保存失败：{error}"]
        self.engine = self.build_engine()
        self.events.add("info", "配置已更新", engine=self.config.engine)
        return []

    # ---- 指令 ----
    @filter.command("汤", alias={"海龟汤", "turtle", "sp"})
    async def tang_command(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        await self._ensure_ready()
        # 文字指令模式下不接管斜杠写法，交给下面的消息 handler 判断
        if not self.config.enabled or self.config.command_style == "text":
            return

        decision = classify(
            event.message_str,
            has_room=self.rooms.get(event.unified_msg_origin) is not None,
            prefix=self.config.keyword_prefix,
            command_style=self.config.command_style,
        )
        if decision.action is not Action.COMMAND:
            return
        event.stop_event()
        async for item in self._run_command(event, decision):
            yield item

    async def _run_command(
        self, event: AstrMessageEvent, decision: Decision
    ) -> AsyncGenerator[Any, None]:
        argument = decision.argument or ""
        subcommand = decision.subcommand.lower()
        rest = self._split_argument(argument, decision.subcommand)

        if subcommand in ("", "开", "start", "新", "新局"):
            texts = await self._start_game(
                event,
                difficulty=self._difficulty(rest),
                puzzle_id=self._puzzle_id(rest),
            )
            async for item in self._emit(event, texts):
                yield item
            return

        room = self.rooms.get(event.unified_msg_origin)
        if room is None and subcommand not in ("帮助", "help", "?", "题库", "stats"):
            async for item in self._emit(
                event, ["现在没有进行中的对局。发送 /汤 开一局。"]
            ):
                yield item
            return

        if subcommand in ("提示", "hint", "h"):
            texts = self._hint(room)
        elif subcommand in ("答", "solve", "解", "作答"):
            texts = await self._solve(event, room, rest)
        elif subcommand in ("揭晓", "reveal", "答案"):
            texts = await self._reveal(event, room)
        elif subcommand in ("跳过", "skip", "换", "换题"):
            texts = await self._skip(event, room)
        elif subcommand in ("状态", "status", "info"):
            texts = [self._status(room)]
        elif subcommand in ("排行", "rank", "榜", "排行榜"):
            texts = [await self._rank(room.umo)]
        elif subcommand in ("题库", "stats", "统计"):
            texts = [await self._overview()]
        elif subcommand in ("帮助", "help", "?", "菜单"):
            texts = [HELP_TEXT]
        else:
            texts = [HELP_TEXT]

        async for item in self._emit(event, texts):
            yield item

    # ---- 三通道消息 ----
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        await self._ensure_ready()
        if not self.config.enabled:
            return

        sender = event.get_sender_id()
        self_id = str(getattr(getattr(event, "message_obj", None), "self_id", "") or "")
        if self_id and sender == self_id:
            return

        umo = event.unified_msg_origin
        room = self.rooms.get(umo)
        at_bot, reply_bot = self._flags(event)
        decision = classify(
            event.message_str,
            has_room=room is not None,
            at_bot=at_bot,
            reply_bot=reply_bot,
            prefix=self.config.keyword_prefix,
            command_style=self.config.command_style,
        )
        if decision.action is Action.IGNORE:
            return

        event.stop_event()
        if decision.action is Action.COMMAND:
            async for item in self._run_command(event, decision):
                yield item
            return
        if decision.action is Action.START_QUESTION:
            texts = await self._start_game(event, first_question=decision.text)
            async for item in self._emit(event, texts):
                yield item
            return

        if room is None:
            return
        async for item in self._emit(
            event, await self._ask_question(event, room, decision.text)
        ):
            yield item

    # ---- 开局与判题 ----
    async def _start_game(
        self,
        event: AstrMessageEvent,
        difficulty: str = "random",
        puzzle_id: str = "",
        first_question: str = "",
    ) -> list[str]:
        if difficulty == "random" and self.config.default_difficulty != "random":
            difficulty = self.config.default_difficulty

        if self._is_private(event):
            if not bool(self.config.get("basic.allow_private", True)):
                return ["私聊暂不允许开局。"]
        elif not bool(self.config.get("basic.allow_group", True)):
            return ["群聊暂不允许开局。"]

        if not self.rooms.daily_allow(self.config.daily_limit):
            return [f"今天的开局次数已用完（上限 {self.config.daily_limit}）。"]

        puzzle = await self.store.get_puzzle(puzzle_id) if puzzle_id else None
        if puzzle is None:
            puzzle = await self.store.random_puzzle(difficulty)
        if puzzle is None:
            if difficulty != "random":
                puzzle = await self.store.random_puzzle("random")
            if puzzle is None:
                return ["题库里没有可用的题目。"]

        umo = event.unified_msg_origin
        existing = self.rooms.get(umo)
        if existing is not None:
            await self.store.finish_room(
                umo, existing.room_id, "skipped", False, existing.puzzle.id
            )

        room = self.rooms.start(
            umo, puzzle, event.get_sender_id(), event.get_sender_name()
        )
        await self.store.start_room(umo, room.room_id, puzzle.id, room.owner_id)
        self.events.add(
            "info",
            f"开局：{puzzle.title}",
            umo=umo,
            puzzle=puzzle.id,
            user=event.get_sender_id(),
        )

        texts = [
            render(
                self.config.template("tpl_start"),
                title=puzzle.title,
                story=puzzle.story,
                host=self.config.host_name,
            )
        ]
        if first_question:
            texts.extend(await self._ask_question(event, room, first_question))
        return texts

    async def _ask_question(
        self, event: AstrMessageEvent, room: Room, question: str
    ) -> list[str]:
        question = (question or "").strip()[:500]
        if not question:
            return []
        user_id = event.get_sender_id()
        name = event.get_sender_name()

        if not self.rooms.allow(user_id, self.config.user_cooldown_s):
            return ["慢一点，我还没想完上一句。"]
        if not self.rooms.reserve(room, self.config.room_queue_limit):
            return ["问得太快了，排队已满，稍等一下再问。"]

        try:
            async with room.lock:
                room.join(user_id, name)
                room.touch()
                recent = await self.store.recent_qas(
                    room.umo, room.puzzle.id, self.config.recent_qa_window
                )
                payload = AskInput(
                    title=room.puzzle.title,
                    surface=room.puzzle.story,
                    answer=room.puzzle.answer,
                    question=question,
                    core_facts=room.puzzle.core_facts,
                    category=room.puzzle.category,
                    recent_qas=[
                        RecentQa(question=item[0], verdict=item[1]) for item in recent
                    ],
                )
                try:
                    result = await self.engine.ask(payload, umo=room.umo)
                except JudgeError as error:
                    await self.store.record_round(
                        room.umo,
                        room.puzzle.id,
                        user_id,
                        question,
                        "ask",
                        "error",
                        0,
                        self.config.engine,
                        0,
                        error.error_class.value,
                    )
                    self.events.add(
                        "error",
                        f"判题失败：{error.error_class.value}",
                        detail=str(error),
                    )
                    return [self._error_text(error)]

                room.rounds += 1
                room.touch()
                await self.store.record_round(
                    room.umo,
                    room.puzzle.id,
                    user_id,
                    question,
                    "ask",
                    result.verdict,
                    result.confidence,
                    result.engine,
                    result.latency_ms,
                    result.error_class or "",
                )
                await self.store.add_score(room.umo, user_id, "asked", 1, name)
                await self.store.touch_room(
                    room.umo,
                    room.room_id,
                    room.hints_used,
                    room.rounds,
                    list(room.participants),
                    room.state,
                )
                self.events.add(
                    "judge",
                    f"{question[:30]} → {label(result.verdict, self.config.language)}",
                    umo=room.umo,
                    verdict=result.verdict,
                    confidence=round(result.confidence, 3),
                    latency_ms=result.latency_ms,
                )

                if result.verdict == "uncertain":
                    return [self.config.template("tpl_uncertain")]
                return [
                    render(
                        self.config.template("tpl_verdict"),
                        question=question,
                        verdict=label(result.verdict, self.config.language),
                        confidence=f"{result.confidence:.2f}",
                        host=self.config.host_name,
                    )
                ]
        finally:
            self.rooms.release(room)

    async def _solve(
        self, event: AstrMessageEvent, room: Room, solution: str
    ) -> list[str]:
        solution = (solution or "").strip()
        if len(solution) < 8:
            return ["把推理写完整一点再提交：/汤 答 <你的推理>"]
        user_id = event.get_sender_id()
        name = event.get_sender_name()

        async with room.lock:
            room.join(user_id, name)
            room.touch()
            payload = SolveInput(
                title=room.puzzle.title,
                surface=room.puzzle.story,
                answer=room.puzzle.answer,
                solution=solution[:2000],
                core_facts=room.puzzle.core_facts,
                category=room.puzzle.category,
            )
            try:
                result = await self.engine.solve(payload, umo=room.umo)
            except JudgeError as error:
                await self.store.record_round(
                    room.umo,
                    room.puzzle.id,
                    user_id,
                    solution,
                    "solve",
                    "error",
                    0,
                    self.config.engine,
                    0,
                    error.error_class.value,
                )
                self.events.add(
                    "error", f"判题失败：{error.error_class.value}", detail=str(error)
                )
                return [self._error_text(error)]

            await self.store.record_round(
                room.umo,
                room.puzzle.id,
                user_id,
                solution,
                "solve",
                result.verdict,
                result.confidence,
                result.engine,
                result.latency_ms,
                result.error_class or "",
            )

            if result.verdict == "solved":
                room.state = "solved"
                await self.store.finish_room(
                    room.umo, room.room_id, "solved", True, room.puzzle.id
                )
                await self.store.add_score(room.umo, user_id, "solved", 1, name)
                text = render(
                    self.config.template("tpl_solved"),
                    answer=room.puzzle.answer,
                    solved_by=name or user_id,
                    rounds=room.rounds,
                    score=percent(room.hints_used, max(1, len(room.puzzle.hints))),
                    host=self.config.host_name,
                    source=self._source_line(room),
                )
                self.events.add(
                    "solve",
                    f"破解成功：{room.puzzle.title}",
                    umo=room.umo,
                    user=user_id,
                )
                return [text]

            await self.store.touch_room(
                room.umo,
                room.room_id,
                room.hints_used,
                room.rounds,
                list(room.participants),
                room.state,
            )
            if result.verdict == "uncertain":
                return [self.config.template("tpl_uncertain")]
            if result.verdict == "close":
                await self.store.add_score(room.umo, user_id, "close", 1, name)
                return [self.config.template("tpl_close")]
            return [self.config.template("tpl_not_yet")]

    async def _reveal(self, event: AstrMessageEvent, room: Room) -> list[str]:
        elapsed = int(time.time() - room.started_at)
        if self.config.reveal_lock_s > 0 and elapsed < self.config.reveal_lock_s:
            return [
                f"刚开局 {self.config.reveal_lock_s} 秒内不能揭晓，再想想（还剩 {self.config.reveal_lock_s - elapsed} 秒）。"
            ]
        if self.config.reveal_policy == "admin" and not self._is_admin(event):
            return ["揭晓需要管理员操作。"]

        room.state = "revealed"
        await self.store.finish_room(
            room.umo, room.room_id, "revealed", False, room.puzzle.id
        )
        self.events.add("reveal", f"揭晓：{room.puzzle.title}", umo=room.umo)
        text = render(
            self.config.template("tpl_reveal"),
            answer=room.puzzle.answer,
            title=room.puzzle.title,
            rounds=room.rounds,
            hints=room.hints_used,
            source=self._source_line(room),
        )
        return [text]

    async def _skip(self, event: AstrMessageEvent, room: Room) -> list[str]:
        room.state = "skipped"
        await self.store.finish_room(
            room.umo, room.room_id, "skipped", False, room.puzzle.id
        )
        self.events.add("info", f"跳过：{room.puzzle.title}", umo=room.umo)
        return await self._start_game(event)

    def _hint(self, room: Room) -> list[str]:
        hint = self.rooms.hint_next(room, self.config.hint_max)
        total = min(len(room.puzzle.hints), self.config.hint_max)
        if hint is None:
            return [f"提示已经全部解锁（{total}/{total}）。"]
        room.touch()
        return [f"提示 {room.hints_used}/{total}：{hint}"]

    def _status(self, room: Room) -> str:
        total = min(len(room.puzzle.hints), self.config.hint_max)
        return (
            f"【{room.puzzle.title}】进行中\n"
            f"已提问 {room.rounds} 轮 · 提示 {room.hints_used}/{total} · "
            f"参与者 {len(room.participants)} 人 · 难度 {room.puzzle.difficulty}\n"
            "/汤 提示 · /汤 答 <推理> · /汤 揭晓"
        )

    async def _rank(self, umo: str) -> str:
        rows = await self.store.leaderboard(umo, limit=10)
        if not rows:
            return "本群还没有破解记录。"
        lines = ["【本群解谜榜】"]
        for index, row in enumerate(rows, start=1):
            name = row.get("user_name") or row.get("user_id") or "?"
            lines.append(
                f"{index}. {name} · 破解 {row['solved']} · 接近 {row['close']} · 提问 {row['asked']}"
            )
        return "\n".join(lines)

    async def _overview(self) -> str:
        total = await self.store.count()
        enabled = await self.store.count(enabled_only=True)
        breakdown = await self.store.difficulty_breakdown()
        detail = " · ".join(
            f"{key} {value}" for key, value in sorted(breakdown.items())
        )
        return (
            f"题库：{enabled}/{total} 题启用\n{detail}\n"
            f"活跃房间：{len(self.rooms.rooms)} · 判题引擎：{self.config.engine}"
        )

    # ---- 工具 ----
    async def _emit(
        self, event: AstrMessageEvent, texts: list[str]
    ) -> AsyncGenerator[Any, None]:
        text_mode = self.config.command_style == "text"
        for text in texts:
            if text_mode:
                text = strip_command_slash(text)
            for chunk in split_message(text, self.config.max_chars):
                yield event.plain_result(chunk)

    @staticmethod
    def _split_argument(argument: str, subcommand: str) -> str:
        if subcommand and argument.startswith(subcommand):
            return argument[len(subcommand) :].strip(" ：:，,、")
        return argument.strip()

    @staticmethod
    def _difficulty(value: str) -> str:
        mapping = {
            "简单": "easy",
            "easy": "easy",
            "中等": "medium",
            "普通": "medium",
            "medium": "medium",
            "困难": "hard",
            "难": "hard",
            "hard": "hard",
        }
        return mapping.get(value.strip().lower(), "random")

    @staticmethod
    def _puzzle_id(value: str) -> str:
        token = value.strip()
        if not token.startswith("#"):
            return ""
        token = token[1:].strip()
        return token if token and " " not in token else ""

    def _flags(self, event: AstrMessageEvent) -> tuple[bool, bool]:
        """解析消息链，判断是否 @bot 或 回复 bot。"""
        at_bot = False
        reply_bot = False
        message_obj = getattr(event, "message_obj", None)
        self_id = str(getattr(message_obj, "self_id", "") or "")
        for component in getattr(message_obj, "message", None) or []:
            name = type(component).__name__
            if name == "At":
                target = str(getattr(component, "qq", "") or "")
                if not target or target.lower() == "all":
                    continue
                if not self_id or target == self_id:
                    at_bot = True
            elif name == "Reply":
                sender = str(
                    getattr(component, "sender_id", "")
                    or getattr(component, "qq", "")
                    or ""
                )
                if not sender or not self_id or sender == self_id:
                    reply_bot = True
        return at_bot, reply_bot

    def _is_private(self, event: AstrMessageEvent) -> bool:
        value = getattr(event, "is_private", None)
        if isinstance(value, bool):
            return value
        if callable(value):
            try:
                return bool(value())
            except Exception:  # noqa: BLE001, S110
                pass
        group_id = getattr(getattr(event, "message_obj", None), "group_id", None)
        return not bool(group_id)

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        value = getattr(event, "is_admin", None)
        if isinstance(value, bool):
            return value
        if callable(value):
            try:
                return bool(value())
            except Exception:  # noqa: BLE001, S110
                pass
        return False

    @staticmethod
    def _source_line(room: Room) -> str:
        if not room.puzzle.source:
            return ""
        link = f"（{room.puzzle.source_url}）" if room.puzzle.source_url else ""
        return f"\n出处：{room.puzzle.source}{link}"

    @staticmethod
    def _error_text(error: JudgeError) -> str:
        """按错误分类给玩家可读提示，不暴露内部细节。"""
        messages = {
            "auth": "主持人的判题服务没配好（鉴权失败），请管理员到插件面板检查 Key。",
            "rate_limited": "主持人被限流了，喘口气再问一次。",
            "timeout": "这题我想太久了，换个问法试试？",
            "protocol": "主持人走神了，刚才那句没听明白，再问一次。",
            "network": "判题服务暂时连不上，稍后再试。",
            "upstream": "判题服务打了个盹，稍后再试。",
        }
        return messages.get(error.error_class.value, "主持人这边出了点状况，稍后再试。")
