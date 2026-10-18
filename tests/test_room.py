"""房间状态：冷却、排队上限、提示解锁、闲置回收。"""

import time

from astrbot_plugin_situation_puzzle.core.models import Puzzle
from astrbot_plugin_situation_puzzle.core.room import RoomManager


def make_puzzle(puzzle_id: str = "p1") -> Puzzle:
    return Puzzle(
        id=puzzle_id,
        title="终点线",
        story="汤面",
        answer="汤底",
        hints=["提示一", "提示二", "提示三"],
        category="本格",
        difficulty="medium",
    )


def test_start_and_get():
    manager = RoomManager()
    room = manager.start("umo", make_puzzle(), "u1", "小明")
    assert manager.get("umo") is room
    assert room.participants["u1"] == "小明"
    assert room.active


def test_finished_room_is_not_active():
    manager = RoomManager()
    room = manager.start("umo", make_puzzle(), "u1")
    room.state = "solved"
    assert manager.get("umo") is None


def test_cooldown_blocks_second_question():
    manager = RoomManager()
    assert manager.allow("u1", 60) is True
    assert manager.allow("u1", 60) is False
    assert manager.allow("u1", 0) is True


def test_reserve_enforces_queue_limit():
    manager = RoomManager()
    room = manager.start("umo", make_puzzle(), "u1")
    assert manager.reserve(room, 2) is True
    assert manager.reserve(room, 2) is True
    assert manager.reserve(room, 2) is False
    manager.release(room)
    assert manager.reserve(room, 2) is True


def test_hint_unlock_stops_at_max():
    manager = RoomManager()
    room = manager.start("umo", make_puzzle(), "u1")
    assert manager.hint_next(room, 2) == "提示一"
    assert manager.hint_next(room, 2) == "提示二"
    assert manager.hint_next(room, 2) is None
    assert room.hints_used == 2


def test_daily_limit():
    manager = RoomManager()
    assert manager.daily_allow(2) is True
    assert manager.daily_allow(2) is True
    assert manager.daily_allow(2) is False
    assert manager.daily_used() == 2


def test_expired_rooms():
    manager = RoomManager()
    room = manager.start("old", make_puzzle(), "u1")
    room.updated_at = time.time() - 3600
    manager.start("new", make_puzzle("p2"), "u2")
    assert manager.expired(30) == ["old"]
    assert manager.expired(0) == []


def test_snapshot_fields():
    manager = RoomManager()
    manager.start("umo", make_puzzle(), "u1")
    snapshot = manager.snapshot()[0]
    assert snapshot["title"] == "终点线"
    assert snapshot["hint_total"] == 3
    assert snapshot["state"] == "playing"
