"""运行日志与统计辅助。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from typing import Any


class EventLog:
    """环形缓冲 + 订阅队列，供 dashboard 的 SSE 使用。"""

    def __init__(self, limit: int = 200) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=limit)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def add(self, level: str, message: str, **fields: Any) -> dict[str, Any]:
        event = {"ts": int(time.time()), "level": level, "message": message}
        event.update(fields)
        self._items.append(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._subscribers.discard(queue)
        return event

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._items)[-max(1, limit) :]

    def subscribe(self) -> tuple[asyncio.Queue[dict[str, Any]], Callable[[], None]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)

        def unsubscribe() -> None:
            self._subscribers.discard(queue)

        return queue, unsubscribe


def percent(part: int, total: int) -> float:
    """百分比，保留一位小数。"""
    if total <= 0:
        return 0.0
    return round(part * 100.0 / total, 1)
