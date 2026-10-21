"""SQLite 持久化：题库、对局记录、计分。

sqlite3 是同步库，所有读写都放到线程里执行，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import Puzzle

SCHEMA = """
CREATE TABLE IF NOT EXISTS puzzles (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT DEFAULT '',
    difficulty TEXT DEFAULT 'medium',
    story TEXT NOT NULL,
    answer TEXT NOT NULL,
    hints_json TEXT DEFAULT '[]',
    core_facts_json TEXT DEFAULT '[]',
    source TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    play_count INTEGER DEFAULT 0,
    solved_count INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_puzzles_diff ON puzzles(difficulty, enabled);

CREATE TABLE IF NOT EXISTS rooms (
    umo TEXT NOT NULL,
    room_id TEXT NOT NULL,
    puzzle_id TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT 0,
    hints_used INTEGER DEFAULT 0,
    rounds INTEGER DEFAULT 0,
    participants_json TEXT DEFAULT '[]',
    owner_id TEXT DEFAULT '',
    PRIMARY KEY (umo, room_id)
);

CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umo TEXT DEFAULT '',
    puzzle_id TEXT DEFAULT '',
    user_id TEXT DEFAULT '',
    question TEXT DEFAULT '',
    kind TEXT DEFAULT 'ask',
    verdict TEXT DEFAULT '',
    confidence REAL DEFAULT 0,
    engine TEXT DEFAULT '',
    latency_ms INTEGER DEFAULT 0,
    error_class TEXT DEFAULT '',
    created_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rounds_umo ON rounds(umo, created_at);

CREATE TABLE IF NOT EXISTS scores (
    umo TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT DEFAULT '',
    solved INTEGER DEFAULT 0,
    close INTEGER DEFAULT 0,
    asked INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT 0,
    PRIMARY KEY (umo, user_id)
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


class PuzzleStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "situation_puzzle.db"
        self._init_lock = asyncio.Lock()

    # ---- 基础设施 ----
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def init(self) -> None:
        async with self._init_lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    async def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._query_sync, sql, tuple(params))

    def _query_sync(self, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(sql, params).fetchall()

    async def _write(self, sql: str, params: Iterable[Any] = ()) -> int:
        return await asyncio.to_thread(self._write_sync, sql, tuple(params))

    def _write_sync(self, sql: str, params: tuple[Any, ...]) -> int:
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount

    # ---- 题库 ----
    async def import_seed(self, seed_path: Path, version: str) -> int:
        """按 seed 版本幂等导入题库，返回本次写入条数。"""
        stored = await self.kv_get("seed_version")
        total = await self.count()
        if stored == version and total > 0:
            return 0
        payload = await asyncio.to_thread(self._read_seed, Path(seed_path))
        if not payload:
            return 0
        written = await asyncio.to_thread(self._import_sync, payload)
        await self.kv_set("seed_version", version)
        return written

    @staticmethod
    def _read_seed(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []

    def _import_sync(self, items: list[dict[str, Any]]) -> int:
        now = int(time.time())
        rows = [
            (
                str(item.get("id") or ""),
                str(item.get("title") or "").strip(),
                str(item.get("category") or ""),
                str(item.get("difficulty") or "medium"),
                str(item.get("story") or "").strip(),
                str(item.get("answer") or "").strip(),
                json.dumps(item.get("hints") or [], ensure_ascii=False),
                json.dumps(
                    item.get("coreFacts") or item.get("core_facts") or [],
                    ensure_ascii=False,
                ),
                str(item.get("source") or ""),
                str(item.get("sourceUrl") or item.get("source_url") or ""),
                now,
                now,
            )
            for item in items
            if item.get("id") and item.get("story") and item.get("answer")
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO puzzles (id, title, category, difficulty, story, answer, hints_json,
                                     core_facts_json, source, source_url, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                rows,
            )
            conn.commit()
        return len(rows)

    async def count(self, enabled_only: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM puzzles" + (
            " WHERE enabled=1" if enabled_only else ""
        )
        rows = await self._query(sql)
        return int(rows[0]["n"]) if rows else 0

    async def get_puzzle(self, puzzle_id: str) -> Puzzle | None:
        rows = await self._query("SELECT * FROM puzzles WHERE id=?", (puzzle_id,))
        return self._row_to_puzzle(rows[0]) if rows else None

    @staticmethod
    def _row_to_puzzle(row: sqlite3.Row) -> Puzzle:
        data = dict(row)
        data["hints"] = json.loads(data.get("hints_json") or "[]")
        data["core_facts"] = json.loads(data.get("core_facts_json") or "[]")
        return Puzzle.from_row(data)

    async def random_puzzle(
        self,
        difficulty: str = "random",
        exclude: list[str] | None = None,
    ) -> Puzzle | None:
        sql = "SELECT * FROM puzzles WHERE enabled=1"
        params: list[Any] = []
        if difficulty in ("easy", "medium", "hard"):
            sql += " AND difficulty=?"
            params.append(difficulty)
        excluded = [item for item in (exclude or []) if item][-50:]
        if excluded:
            sql += f" AND id NOT IN ({','.join('?' * len(excluded))})"
            params.extend(excluded)
        sql += " ORDER BY RANDOM() LIMIT 1"
        rows = await self._query(sql, params)
        return self._row_to_puzzle(rows[0]) if rows else None

    async def pick_puzzle(
        self,
        umo: str,
        difficulty: str = "random",
        fresh_first: bool = True,
    ) -> Puzzle | None:
        """按会话挑题；fresh_first 时优先本会话没开过的题。"""
        if not fresh_first:
            return await self.random_puzzle(difficulty)

        sql = """
            SELECT p.* FROM puzzles p
            LEFT JOIN (
                SELECT puzzle_id, COUNT(*) AS times FROM rooms
                WHERE umo=? GROUP BY puzzle_id
            ) c ON c.puzzle_id = p.id
            WHERE p.enabled=1
        """
        params: list[Any] = [umo]
        if difficulty in ("easy", "medium", "hard"):
            sql += " AND p.difficulty=?"
            params.append(difficulty)
        # 没开过的题次数为 0，排在最前；全部玩过后按次数最少的一批轮转
        sql += " ORDER BY COALESCE(c.times, 0) ASC, RANDOM() LIMIT 1"
        rows = await self._query(sql, params)
        return self._row_to_puzzle(rows[0]) if rows else None

    async def list_puzzles(
        self,
        page: int = 1,
        size: int = 20,
        keyword: str = "",
        difficulty: str = "",
        category: str = "",
        enabled: str = "",
    ) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if keyword:
            like = f"%{keyword}%"
            where.append("(title LIKE ? OR story LIKE ? OR answer LIKE ? OR id LIKE ?)")
            params.extend([like, like, like, like])
        if difficulty in ("easy", "medium", "hard"):
            where.append("difficulty=?")
            params.append(difficulty)
        if category in ("本格", "变格"):
            where.append("category=?")
            params.append(category)
        if enabled in ("0", "1"):
            where.append("enabled=?")
            params.append(int(enabled))
        clause = (" WHERE " + " AND ".join(where)) if where else ""

        total_rows = await self._query(
            f"SELECT COUNT(*) AS n FROM puzzles{clause}", params
        )
        total = int(total_rows[0]["n"]) if total_rows else 0
        page = max(1, page)
        size = min(max(1, size), 200)
        rows = await self._query(
            f"SELECT * FROM puzzles{clause} ORDER BY rowid LIMIT ? OFFSET ?",
            [*params, size, (page - 1) * size],
        )
        items = []
        for row in rows:
            puzzle = self._row_to_puzzle(row)
            item = puzzle.to_dict()
            item["enabled"] = bool(dict(row)["enabled"])
            item["play_count"] = int(dict(row)["play_count"])
            item["solved_count"] = int(dict(row)["solved_count"])
            items.append(item)
        return {"total": total, "page": page, "size": size, "items": items}

    async def upsert_puzzle(self, item: dict[str, Any]) -> str:
        now = int(time.time())
        puzzle_id = (
            str(item.get("id") or "").strip() or f"custom-{int(time.time() * 1000)}"
        )
        hints = item.get("hints") or []
        if isinstance(hints, str):
            hints = [line.strip() for line in hints.splitlines() if line.strip()]
        await self._write(
            """
            INSERT INTO puzzles (id, title, category, difficulty, story, answer, hints_json,
                                 core_facts_json, source, source_url, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, category=excluded.category, difficulty=excluded.difficulty,
                story=excluded.story, answer=excluded.answer, hints_json=excluded.hints_json,
                core_facts_json=excluded.core_facts_json, source=excluded.source,
                source_url=excluded.source_url, enabled=excluded.enabled, updated_at=excluded.updated_at
            """,
            (
                puzzle_id,
                str(item.get("title") or ""),
                str(item.get("category") or ""),
                str(item.get("difficulty") or "medium"),
                str(item.get("story") or ""),
                str(item.get("answer") or ""),
                json.dumps(hints, ensure_ascii=False),
                json.dumps(item.get("core_facts") or [], ensure_ascii=False),
                str(item.get("source") or ""),
                str(item.get("source_url") or ""),
                1 if item.get("enabled", True) else 0,
                now,
                now,
            ),
        )
        return puzzle_id

    async def set_enabled(self, puzzle_id: str, enabled: bool) -> None:
        await self._write(
            "UPDATE puzzles SET enabled=?, updated_at=? WHERE id=?",
            (1 if enabled else 0, int(time.time()), puzzle_id),
        )

    async def delete_puzzle(self, puzzle_id: str) -> None:
        """软删：保留历史对局引用，只是不再抽到。"""
        await self.set_enabled(puzzle_id, False)

    async def bump_puzzle(self, puzzle_id: str, field: str) -> None:
        if field not in ("play_count", "solved_count"):
            return
        await self._write(
            f"UPDATE puzzles SET {field}={field}+1 WHERE id=?", (puzzle_id,)
        )

    # ---- 对局与判题 ----
    async def record_round(
        self,
        umo: str,
        puzzle_id: str,
        user_id: str,
        question: str,
        kind: str,
        verdict: str,
        confidence: float,
        engine: str,
        latency_ms: int,
        error_class: str = "",
    ) -> None:
        await self._write(
            """
            INSERT INTO rounds (umo, puzzle_id, user_id, question, kind, verdict, confidence,
                                engine, latency_ms, error_class, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                umo,
                puzzle_id,
                user_id,
                question[:500],
                kind,
                verdict,
                float(confidence),
                engine,
                int(latency_ms),
                error_class,
                int(time.time()),
            ),
        )

    async def recent_qas(
        self, umo: str, puzzle_id: str, limit: int
    ) -> list[tuple[str, str]]:
        """取最近若干轮已判定的提问，旧的在前，用于代词消解。"""
        if limit <= 0:
            return []
        rows = await self._query(
            """
            SELECT question, verdict FROM rounds
            WHERE umo=? AND puzzle_id=? AND kind='ask' AND verdict IN ('yes','no','irrelevant')
            ORDER BY id DESC LIMIT ?
            """,
            (umo, puzzle_id, limit),
        )
        return [(row["question"], row["verdict"]) for row in reversed(rows)]

    async def start_room(
        self, umo: str, room_id: str, puzzle_id: str, owner_id: str
    ) -> None:
        now = int(time.time())
        await self._write(
            """
            INSERT INTO rooms (umo, room_id, puzzle_id, state, started_at, updated_at, owner_id)
            VALUES (?, ?, ?, 'playing', ?, ?, ?)
            ON CONFLICT(umo, room_id) DO UPDATE SET
                puzzle_id=excluded.puzzle_id, state='playing', started_at=excluded.started_at,
                updated_at=excluded.updated_at, hints_used=0, rounds=0,
                participants_json='[]', owner_id=excluded.owner_id
            """,
            (umo, room_id, puzzle_id, now, now, owner_id),
        )
        await self.bump_puzzle(puzzle_id, "play_count")

    async def touch_room(
        self,
        umo: str,
        room_id: str,
        hints_used: int,
        rounds: int,
        participants: list[str],
        state: str,
    ) -> None:
        await self._write(
            "UPDATE rooms SET hints_used=?, rounds=?, participants_json=?, state=?, updated_at=? WHERE umo=? AND room_id=?",
            (
                hints_used,
                rounds,
                json.dumps(participants, ensure_ascii=False),
                state,
                int(time.time()),
                umo,
                room_id,
            ),
        )

    async def finish_room(
        self, umo: str, room_id: str, state: str, solved: bool, puzzle_id: str
    ) -> None:
        await self._write(
            "UPDATE rooms SET state=?, updated_at=? WHERE umo=? AND room_id=?",
            (state, int(time.time()), umo, room_id),
        )
        if solved:
            await self.bump_puzzle(puzzle_id, "solved_count")

    async def add_score(
        self, umo: str, user_id: str, field: str, amount: int = 1, name: str = ""
    ) -> None:
        if field not in ("solved", "close", "asked"):
            return
        now = int(time.time())
        await self._write(
            """
            INSERT INTO scores (umo, user_id, user_name, solved, close, asked, updated_at)
            VALUES (?, ?, ?, 0, 0, 0, ?)
            ON CONFLICT(umo, user_id) DO NOTHING
            """,
            (umo, user_id, name, now),
        )
        await self._write(
            f"UPDATE scores SET {field}={field}+?, user_name=?, updated_at=? WHERE umo=? AND user_id=?",
            (amount, name or user_id, now, umo, user_id),
        )

    async def leaderboard(self, umo: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = await self._query(
            """
            SELECT user_id, user_name, solved, close, asked FROM scores
            WHERE umo=? ORDER BY solved DESC, close DESC, asked ASC LIMIT ?
            """,
            (umo, limit),
        )
        return [dict(row) for row in rows]

    # ---- 统计 ----
    async def stats(self, since: int) -> dict[str, Any]:
        totals = await self._query(
            """
            SELECT COUNT(*) AS rounds,
                   SUM(CASE WHEN kind='ask' THEN 1 ELSE 0 END) AS asks,
                   SUM(CASE WHEN kind='solve' THEN 1 ELSE 0 END) AS solves,
                   SUM(CASE WHEN verdict='solved' THEN 1 ELSE 0 END) AS solved,
                   SUM(CASE WHEN verdict='close' THEN 1 ELSE 0 END) AS close,
                   SUM(CASE WHEN verdict='uncertain' THEN 1 ELSE 0 END) AS uncertain,
                   AVG(CASE WHEN latency_ms>0 THEN latency_ms END) AS avg_latency
            FROM rounds WHERE created_at>=?
            """,
            (since,),
        )
        row = dict(totals[0]) if totals else {}
        latencies = await self._query(
            "SELECT latency_ms FROM rounds WHERE created_at>=? AND latency_ms>0 ORDER BY latency_ms",
            (since,),
        )
        values = [int(item["latency_ms"]) for item in latencies]
        p95 = values[int(len(values) * 0.95) - 1] if values else 0

        errors = await self._query(
            "SELECT error_class, COUNT(*) AS n FROM rounds WHERE created_at>=? AND error_class<>'' GROUP BY error_class",
            (since,),
        )
        verdicts = await self._query(
            "SELECT verdict, COUNT(*) AS n FROM rounds WHERE created_at>=? AND kind='ask' GROUP BY verdict",
            (since,),
        )
        rooms = await self._query(
            "SELECT COUNT(*) AS n FROM rooms WHERE started_at>=?", (since,)
        )
        return {
            "asks": int(row.get("asks") or 0),
            "solves": int(row.get("solves") or 0),
            "solved": int(row.get("solved") or 0),
            "close": int(row.get("close") or 0),
            "uncertain": int(row.get("uncertain") or 0),
            "rooms": int(dict(rooms[0])["n"]) if rooms else 0,
            "avg_latency_ms": int(row.get("avg_latency") or 0),
            "p95_latency_ms": p95,
            "errors": {item["error_class"]: int(item["n"]) for item in errors},
            "verdicts": {item["verdict"]: int(item["n"]) for item in verdicts},
        }

    async def difficulty_breakdown(self) -> dict[str, int]:
        rows = await self._query(
            "SELECT difficulty, COUNT(*) AS n FROM puzzles WHERE enabled=1 GROUP BY difficulty"
        )
        return {row["difficulty"] or "unknown": int(row["n"]) for row in rows}

    # ---- kv ----
    async def kv_get(self, key: str) -> str | None:
        rows = await self._query("SELECT value FROM kv WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    async def kv_set(self, key: str, value: str) -> None:
        await self._write(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
