"""插件 Web API：面板的读写、试判、题库管理、统计与 SSE。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from astrbot.api.web import (
    error_response,
    file_response,
    json_response,
    request,
    stream_response,
)

from ..judge.prompts import AskInput
from ..judge.protocol import JudgeError, label
from . import schemas

PLUGIN_NAME = "astrbot_plugin_situation_puzzle"
SEED_URL = "https://raw.githubusercontent.com/ZeroTang05/Situation-Puzzle/main/data/library.json"

# 校准样本：基于内置题「终点线」的事实，expect 为空表示只观察不评分
CALIBRATION_SAMPLES: list[dict[str, str]] = [
    {"question": "爸爸已经去世了吗？", "expect": "是"},
    {"question": "那个东西是一台游戏机吗？", "expect": "是"},
    {"question": "幽灵车手是爸爸留下的记录吗？", "expect": "是"},
    {"question": "他最后赢了比赛吗？", "expect": "否"},
    {"question": "故事发生在船上吗？", "expect": "否"},
    {"question": "他喜欢吃苹果吗？", "expect": "无关"},
]


class PluginAPI:
    """所有 handler 都是插件实例的方法，通过 register_web_api 注册。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    def routes(self) -> list[tuple[str, Callable[..., Any], list[str], str]]:
        prefix = f"/{PLUGIN_NAME}"
        return [
            (f"{prefix}/settings", self.get_settings, ["GET"], "读取插件配置"),
            (f"{prefix}/settings/save", self.save_settings, ["POST"], "保存插件配置"),
            (f"{prefix}/engine/test", self.test_engine, ["POST"], "试判一道问题"),
            (f"{prefix}/engine/calibrate", self.calibrate, ["POST"], "阈值校准"),
            (f"{prefix}/library/list", self.list_library, ["GET"], "题库列表"),
            (
                f"{prefix}/library/upsert",
                self.upsert_puzzle,
                ["POST"],
                "新增或编辑题目",
            ),
            (
                f"{prefix}/library/<puzzle_id>",
                self.delete_puzzle,
                ["DELETE"],
                "停用题目",
            ),
            (f"{prefix}/library/import", self.import_library, ["POST"], "导入题库"),
            (f"{prefix}/library/export", self.export_library, ["GET"], "导出题库"),
            (f"{prefix}/library/sync", self.sync_library, ["POST"], "同步官方题库"),
            (f"{prefix}/library/lint", self.lint_library, ["POST"], "题目体检"),
            (f"{prefix}/stats", self.get_stats, ["GET"], "运行统计"),
            (f"{prefix}/rooms", self.get_rooms, ["GET"], "活跃房间"),
            (f"{prefix}/logs", self.get_logs, ["GET"], "最近日志"),
            (f"{prefix}/events", self.events, ["GET"], "实时事件流"),
        ]

    # ---- 设置 ----
    async def get_settings(self) -> Any:
        plugin = self.plugin
        counts = await plugin.store.count()
        breakdown = await plugin.store.difficulty_breakdown()
        return json_response(
            {
                "schema": plugin.load_schema(),
                "values": plugin.config.snapshot(),
                "meta": {
                    "plugin_name": PLUGIN_NAME,
                    "puzzle_total": counts,
                    "difficulty": breakdown,
                    "providers": self._providers(),
                    "default_provider": plugin.default_provider_id(),
                    "seed_version": await plugin.store.kv_get("seed_version"),
                    "engine": plugin.config.engine,
                },
            }
        )

    async def save_settings(self) -> Any:
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体需要是 JSON 对象", status_code=400)
        errors = self.plugin.update_config(payload)
        return json_response(
            {
                "saved": not errors,
                "errors": errors,
                "values": self.plugin.config.snapshot(),
                "meta": {"engine": self.plugin.config.engine},
            }
        )

    async def test_engine(self) -> Any:
        payload = schemas.as_dict(await request.json(default={}))
        try:
            question = schemas.text(payload, "question", required=True, limit=500)
            puzzle_id = schemas.text(payload, "puzzle_id", limit=100)
        except schemas.ValidationError as error:
            return error_response(str(error), status_code=400)

        puzzle = await self.plugin.store.get_puzzle(puzzle_id) if puzzle_id else None
        if puzzle is None:
            puzzle = (
                await self.plugin.store.get_puzzle("real-finish-line")
                or await self.plugin.store.random_puzzle()
            )
        if puzzle is None:
            return error_response("题库为空，无法试判", status_code=400)

        return await self._judge_once(puzzle, question)

    async def calibrate(self) -> Any:
        payload = schemas.as_dict(await request.json(default={}))
        raw_samples = payload.get("samples")
        samples: list[dict[str, str]] = []
        if isinstance(raw_samples, list):
            for item in raw_samples[:12]:
                if not isinstance(item, dict):
                    continue
                question = str(item.get("question") or "").strip()
                if not question:
                    continue
                samples.append(
                    {
                        "question": question[:500],
                        "expect": str(item.get("expect") or "").strip(),
                    }
                )
        if not samples:
            samples = list(CALIBRATION_SAMPLES)

        puzzle_id = schemas.text(payload, "puzzle_id", limit=100)
        puzzle = await self.plugin.store.get_puzzle(puzzle_id) if puzzle_id else None
        if puzzle is None:
            puzzle = (
                await self.plugin.store.get_puzzle("real-finish-line")
                or await self.plugin.store.random_puzzle()
            )
        if puzzle is None:
            return error_response("题库为空，无法校准", status_code=400)

        results: list[dict[str, Any]] = []
        hits = 0
        scored = 0
        for sample in samples:
            outcome = await self._judge_value(puzzle, sample["question"])
            verdict_label = outcome.get("label", "")
            hit = bool(sample["expect"]) and verdict_label == sample["expect"]
            if sample["expect"]:
                scored += 1
                hits += 1 if hit else 0
            results.append(
                {
                    "question": sample["question"],
                    "expect": sample["expect"],
                    "verdict": verdict_label,
                    "confidence": outcome.get("confidence", 0),
                    "latency_ms": outcome.get("latency_ms", 0),
                    "ok": outcome.get("ok", False),
                    "hit": hit,
                }
            )

        return json_response(
            {
                "ok": True,
                "puzzle": {"id": puzzle.id, "title": puzzle.title},
                "engine": self.plugin.config.engine,
                "threshold": self.plugin.config.threshold,
                "scored": scored,
                "hits": hits,
                "accuracy": round(hits * 100.0 / scored, 1) if scored else 0.0,
                "items": results,
            }
        )

    async def _judge_once(self, puzzle: Any, question: str) -> Any:
        outcome = await self._judge_value(puzzle, question)
        if not outcome.get("ok"):
            return json_response(outcome)
        return json_response(outcome)

    async def _judge_value(self, puzzle: Any, question: str) -> dict[str, Any]:
        engine = self.plugin.build_engine(provider_id=self.plugin.default_provider_id())
        payload = AskInput(
            title=puzzle.title,
            surface=puzzle.story,
            answer=puzzle.answer,
            question=question,
            core_facts=puzzle.core_facts,
            category=puzzle.category,
        )
        started = time.monotonic()
        try:
            result = await engine.ask(payload)
        except JudgeError as error:
            return {
                "ok": False,
                "error_class": error.error_class.value,
                "message": str(error),
                "latency_ms": int((time.monotonic() - started) * 1000),
                "engine": self.plugin.config.engine,
            }
        except Exception as error:  # noqa: BLE001 - 面板需要看到原因而不是 500
            return {
                "ok": False,
                "error_class": "unknown",
                "message": f"{type(error).__name__}: {error}",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "engine": self.plugin.config.engine,
            }

        return {
            "ok": True,
            "puzzle": {"id": puzzle.id, "title": puzzle.title},
            "verdict": result.verdict,
            "label": label(result.verdict, self.plugin.config.language),
            "confidence": round(result.confidence, 4),
            "threshold": result.threshold,
            "latency_ms": result.latency_ms,
            "engine": result.engine,
            "degraded": result.degraded,
        }

    # ---- 题库 ----
    async def list_library(self) -> Any:
        page = request.query.get("page", 1, type=int)
        size = request.query.get("size", 20, type=int)
        keyword = request.query.get("q", "", type=str) or ""
        difficulty = request.query.get("difficulty", "", type=str) or ""
        category = request.query.get("category", "", type=str) or ""
        enabled = request.query.get("enabled", "", type=str) or ""
        data = await self.plugin.store.list_puzzles(
            page=page or 1,
            size=size or 20,
            keyword=keyword.strip(),
            difficulty=difficulty,
            category=category,
            enabled=enabled,
        )
        return json_response(data)

    async def upsert_puzzle(self) -> Any:
        try:
            payload = schemas.as_dict(await request.json(default={}))
            puzzle_id = schemas.text(payload, "id", limit=100)
            title = schemas.text(payload, "title", required=True, limit=120)
            story = schemas.text(payload, "story", required=True, limit=4000)
            answer = schemas.text(payload, "answer", required=True, limit=8000)
            category = schemas.one_of(
                payload, "category", ("本格", "变格"), default="本格"
            )
            difficulty = schemas.one_of(
                payload, "difficulty", ("easy", "medium", "hard"), default="medium"
            )
            item_hints = schemas.hints(payload)
            source = schemas.text(payload, "source", limit=200)
            source_url = schemas.text(payload, "source_url", limit=500)
        except schemas.ValidationError as error:
            return error_response(str(error), status_code=400)

        saved_id = await self.plugin.store.upsert_puzzle(
            {
                "id": puzzle_id,
                "title": title,
                "story": story,
                "answer": answer,
                "category": category,
                "difficulty": difficulty,
                "hints": item_hints,
                "source": source,
                "source_url": source_url,
                "enabled": schemas.boolean(payload, "enabled", True),
            }
        )
        return json_response({"saved": True, "id": saved_id})

    async def delete_puzzle(self, puzzle_id: str) -> Any:
        if not puzzle_id or len(puzzle_id) > 100:
            return error_response("题目 id 不合法", status_code=400)
        await self.plugin.store.delete_puzzle(puzzle_id)
        return json_response({"deleted": True, "id": puzzle_id})

    async def import_library(self) -> Any:
        files = await request.files()
        upload = files.get("file")
        if upload is None:
            return error_response("缺少上传文件（字段名 file）", status_code=400)
        filename = Path(getattr(upload, "filename", "") or "import.json").name
        if not filename.lower().endswith(".json"):
            return error_response("只支持 JSON 文件", status_code=400)

        target_dir = self.plugin.data_dir / "imports"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{int(time.time())}-{filename}"
        await upload.save(target)

        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return error_response(f"文件解析失败：{error}", status_code=400)

        items = (
            raw
            if isinstance(raw, list)
            else raw.get("items") or raw.get("puzzles") or raw.get("library")
        )
        if not isinstance(items, list):
            return error_response(
                "JSON 结构需要是数组，或包含 items 数组", status_code=400
            )
        if len(items) > 3000:
            return error_response("单次导入上限 3000 题", status_code=400)

        imported = 0
        skipped = 0
        for item in items:
            if (
                not isinstance(item, dict)
                or not item.get("story")
                or not item.get("answer")
            ):
                skipped += 1
                continue
            await self.plugin.store.upsert_puzzle(item)
            imported += 1

        return json_response(
            {"imported": imported, "skipped": skipped, "total": len(items)}
        )

    async def export_library(self) -> Any:
        fmt = (request.query.get("format", "json", type=str) or "json").lower()
        items: list[dict[str, Any]] = []
        page = 1
        while page <= 30:
            data = await self.plugin.store.list_puzzles(page=page, size=200)
            items.extend(data["items"])
            if len(items) >= data["total"]:
                break
            page += 1

        export_dir = self.plugin.data_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")

        if fmt == "csv":
            target = export_dir / f"library-{stamp}.csv"
            lines = ["id,title,category,difficulty,hints"]
            for item in items:
                hints_text = " | ".join(item.get("hints") or [])
                row = [
                    item.get("id", ""),
                    item.get("title", ""),
                    item.get("category", ""),
                    item.get("difficulty", ""),
                    hints_text,
                ]
                lines.append(
                    ",".join(
                        f'"{str(cell).replace(chr(34), chr(34) * 2)}"' for cell in row
                    )
                )
            target.write_text("\n".join(lines), encoding="utf-8-sig")
            return file_response(target, filename=target.name, content_type="text/csv")

        target = export_dir / f"library-{stamp}.json"
        target.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return file_response(
            target, filename=target.name, content_type="application/json"
        )

    async def sync_library(self) -> Any:
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=True) as client:
                response = await client.get(SEED_URL)
                response.raise_for_status()
                items = response.json()
        except (httpx.HTTPError, ValueError) as error:
            return json_response({"ok": False, "message": f"拉取失败：{error}"})
        if not isinstance(items, list):
            return json_response({"ok": False, "message": "远端题库结构不合法"})

        imported = 0
        for item in items:
            if isinstance(item, dict) and item.get("story") and item.get("answer"):
                await self.plugin.store.upsert_puzzle(item)
                imported += 1
        await self.plugin.store.kv_set("seed_version", f"remote-{int(time.time())}")
        self.plugin.events.add("info", f"同步官方题库：{imported} 题")
        return json_response({"ok": True, "imported": imported, "total": len(items)})

    async def lint_library(self) -> Any:
        issues: list[dict[str, Any]] = []
        seen: dict[str, str] = {}
        page = 1
        total = 0
        while page <= 30:
            data = await self.plugin.store.list_puzzles(page=page, size=200)
            total = data["total"]
            for item in data["items"]:
                if len(item.get("story") or "") < 20:
                    issues.append(
                        {"id": item["id"], "level": "warn", "message": "汤面过短"}
                    )
                if len(item.get("answer") or "") < 20:
                    issues.append(
                        {"id": item["id"], "level": "warn", "message": "汤底过短"}
                    )
                if len(item.get("hints") or []) < 3:
                    issues.append(
                        {"id": item["id"], "level": "info", "message": "提示不足 3 条"}
                    )
                fingerprint = (item.get("story") or "").strip()[:120]
                if fingerprint and fingerprint in seen:
                    issues.append(
                        {
                            "id": item["id"],
                            "level": "warn",
                            "message": f"与 {seen[fingerprint]} 汤面重复",
                        }
                    )
                elif fingerprint:
                    seen[fingerprint] = item["id"]
                if len(issues) >= 200:
                    break
            if len(issues) >= 200 or page * 200 >= total:
                break
            page += 1

        return json_response({"total": total, "issues": issues, "count": len(issues)})

    # ---- 运行状态 ----
    async def get_stats(self) -> Any:
        days = request.query.get("days", 1, type=int) or 1
        since = int(time.time()) - max(1, min(30, days)) * 86400
        stats = await self.plugin.store.stats(since)
        stats.update(
            {
                "puzzle_total": await self.plugin.store.count(),
                "puzzle_enabled": await self.plugin.store.count(enabled_only=True),
                "difficulty": await self.plugin.store.difficulty_breakdown(),
                "active_rooms": len(self.plugin.rooms.rooms),
                "daily_used": self.plugin.rooms.daily_used(),
                "engine": self.plugin.config.engine,
                "threshold": self.plugin.config.threshold,
                "days": days,
            }
        )
        return json_response(stats)

    async def get_rooms(self) -> Any:
        return json_response({"rooms": self.plugin.rooms.snapshot()})

    async def get_logs(self) -> Any:
        limit = request.query.get("limit", 50, type=int) or 50
        return json_response({"logs": self.plugin.events.tail(max(1, min(200, limit)))})

    async def events(self) -> Any:
        log = self.plugin.events

        async def stream():
            queue, unsubscribe = log.subscribe()
            try:
                for item in log.tail(10):
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                unsubscribe()

        return stream_response(stream())

    # ---- 辅助 ----
    def _providers(self) -> list[dict[str, str]]:
        context = self.plugin.context
        options: list[dict[str, str]] = []
        try:
            manager = getattr(context, "provider_manager", None)
            providers = getattr(manager, "providers", None) or []
            for provider in providers:
                config = getattr(provider, "provider_config", None) or {}
                provider_id = str(
                    config.get("id") or getattr(provider, "provider_id", "") or ""
                )
                model = str(config.get("model") or getattr(provider, "model", "") or "")
                if provider_id:
                    options.append(
                        {
                            "id": provider_id,
                            "label": f"{provider_id} · {model}"
                            if model
                            else provider_id,
                        }
                    )
        except Exception:  # noqa: BLE001 - 面板元信息失败不影响主流程
            return []
        return options
