"""房间状态：一个会话一局，串行判题、冷却、闲置回收。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .models import Puzzle


@dataclass
class Room:
    umo: str
    puzzle: Puzzle
    owner_id: str = ""
    room_id: str = "default"
    state: str = "playing"  # playing / solved / revealed / skipped
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    hints_used: int = 0
    rounds: int = 0
    participants: dict[str, str] = field(default_factory=dict)
    pending: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def active(self) -> bool:
        return self.state == "playing"

    def touch(self) -> None:
        self.updated_at = time.time()

    def join(self, user_id: str, name: str) -> None:
        self.participants[user_id] = name or user_id

    def snapshot(self) -> dict[str, Any]:
        return {
            "umo": self.umo,
            "room_id": self.room_id,
            "puzzle_id": self.puzzle.id,
            "title": self.puzzle.title,
            "state": self.state,
            "rounds": self.rounds,
            "hints_used": self.hints_used,
            "hint_total": len(self.puzzle.hints),
            "participants": len(self.participants),
            "owner_id": self.owner_id,
            "started_at": int(self.started_at),
            "updated_at": int(self.updated_at),
            "idle_seconds": int(time.time() - self.updated_at),
        }


class RoomManager:
    """内存中的房间表；对局记录另由 store 落库。"""

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}
        self._cooldown: dict[str, float] = {}
        self._daily_key: str = ""
        self._daily_count: int = 0

    def get(self, umo: str) -> Room | None:
        room = self.rooms.get(umo)
        if room and not room.active:
            return None
        return room

    def start(
        self, umo: str, puzzle: Puzzle, owner_id: str, owner_name: str = ""
    ) -> Room:
        room = Room(umo=umo, puzzle=puzzle, owner_id=owner_id)
        room.join(owner_id, owner_name)
        self.rooms[umo] = room
        return room

    def drop(self, umo: str) -> Room | None:
        return self.rooms.pop(umo, None)

    def expired(self, idle_timeout_min: int) -> list[str]:
        if idle_timeout_min <= 0:
            return []
        deadline = time.time() - idle_timeout_min * 60
        return [umo for umo, room in self.rooms.items() if room.updated_at < deadline]

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            room.snapshot()
            for room in sorted(
                self.rooms.values(), key=lambda item: item.updated_at, reverse=True
            )
        ]

    # ---- 限流 ----
    def allow(self, user_id: str, cooldown_s: int) -> bool:
        """用户级冷却；0 表示不限。"""
        if cooldown_s <= 0:
            return True
        now = time.time()
        last = self._cooldown.get(user_id, 0.0)
        if now - last < cooldown_s:
            return False
        self._cooldown[user_id] = now
        return True

    def reserve(self, room: Room, limit: int) -> bool:
        """占一个判题名额，超过排队上限直接拒绝。"""
        if room.pending >= max(1, limit):
            return False
        room.pending += 1
        return True

    @staticmethod
    def release(room: Room) -> None:
        room.pending = max(0, room.pending - 1)

    def daily_allow(self, limit: int) -> bool:
        """每日开局上限；0 表示不限。"""
        today = date.today().isoformat()
        if self._daily_key != today:
            self._daily_key = today
            self._daily_count = 0
        if limit <= 0:
            return True
        if self._daily_count >= limit:
            return False
        self._daily_count += 1
        return True

    def daily_used(self) -> int:
        return self._daily_count

    def hint_next(self, room: Room, hint_max: int) -> str | None:
        """解锁下一条提示；到上限返回 None。"""
        total = min(len(room.puzzle.hints), hint_max)
        if room.hints_used >= total:
            return None
        hint = room.puzzle.hints[room.hints_used]
        room.hints_used += 1
        return hint
