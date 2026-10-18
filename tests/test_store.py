"""SQLite 持久化：入库、抽取、检索、问答上下文、统计。"""

import asyncio
import json
import time

from astrbot_plugin_situation_puzzle.core.store import PuzzleStore

SEED = [
    {
        "id": "p-easy",
        "title": "终点线",
        "category": "本格",
        "difficulty": "easy",
        "story": "一个关于赛车游戏的离奇故事。",
        "answer": "父亲留下的最快圈速记录被我一直追赶。",
        "hints": ["提示一", "提示二", "提示三"],
    },
    {
        "id": "p-hard",
        "title": "变格样本",
        "category": "变格",
        "difficulty": "hard",
        "story": "一个可以包含超自然设定的故事。",
        "answer": "答案在这里。",
        "hints": ["甲", "乙", "丙"],
    },
]


def build_store(tmp_path, seed_items=None):
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(seed_items if seed_items is not None else SEED, ensure_ascii=False),
        encoding="utf-8",
    )
    store = PuzzleStore(tmp_path / "data")
    asyncio.run(store.init())
    asyncio.run(store.import_seed(seed, "v1"))
    return store


def test_seed_import_is_idempotent(tmp_path):
    store = build_store(tmp_path)
    assert asyncio.run(store.count()) == 2
    seed = tmp_path / "seed.json"
    assert asyncio.run(store.import_seed(seed, "v1")) == 0


def test_seed_file_has_full_library():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "resources" / "library.seed.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) >= 350
    assert len({item["id"] for item in data}) == len(data)
    assert all(item.get("category") in {"本格", "变格"} for item in data)
    assert all(item.get("difficulty") in {"easy", "medium", "hard"} for item in data)
    assert all(
        item.get("story") and item.get("answer") and len(item.get("hints") or []) >= 3
        for item in data
    )


def test_random_puzzle_respects_difficulty(tmp_path):
    store = build_store(tmp_path)
    for _ in range(5):
        puzzle = asyncio.run(store.random_puzzle("hard"))
        assert puzzle is not None and puzzle.id == "p-hard"
    assert asyncio.run(store.random_puzzle("easy")).id == "p-easy"


def test_list_and_search(tmp_path):
    store = build_store(tmp_path)
    page = asyncio.run(store.list_puzzles(keyword="赛车"))
    assert page["total"] == 1 and page["items"][0]["id"] == "p-easy"
    filtered = asyncio.run(store.list_puzzles(category="变格"))
    assert filtered["total"] == 1


def test_upsert_and_soft_delete(tmp_path):
    store = build_store(tmp_path)
    asyncio.run(
        store.upsert_puzzle(
            {
                "id": "custom",
                "title": "自制",
                "story": "汤面",
                "answer": "汤底",
                "hints": ["a"],
            }
        )
    )
    assert asyncio.run(store.count()) == 3
    asyncio.run(store.delete_puzzle("custom"))
    assert asyncio.run(store.count(enabled_only=True)) == 2
    assert (asyncio.run(store.list_puzzles(enabled="0")))["total"] == 1


def test_recent_qas_filters_and_orders(tmp_path):
    store = build_store(tmp_path)
    asyncio.run(
        store.record_round(
            "umo", "p-easy", "u1", "问题一", "ask", "yes", 0.9, "jev", 100
        )
    )
    asyncio.run(
        store.record_round(
            "umo", "p-easy", "u1", "问题二", "ask", "no", 0.8, "jev", 120
        )
    )
    asyncio.run(
        store.record_round(
            "umo", "p-easy", "u1", "失败的一次", "ask", "error", 0, "jev", 0, "timeout"
        )
    )
    asyncio.run(
        store.record_round(
            "umo", "p-easy", "u1", "问题三", "ask", "irrelevant", 0.7, "jev", 90
        )
    )

    recent = asyncio.run(store.recent_qas("umo", "p-easy", 2))
    assert recent == [("问题二", "no"), ("问题三", "irrelevant")]


def test_stats_and_leaderboard(tmp_path):
    store = build_store(tmp_path)
    now = int(time.time())
    asyncio.run(store.start_room("umo", "default", "p-easy", "u1"))
    asyncio.run(
        store.record_round("umo", "p-easy", "u1", "问题", "ask", "yes", 0.9, "jev", 100)
    )
    asyncio.run(
        store.record_round(
            "umo", "p-easy", "u1", "推理", "solve", "solved", 0.95, "jev", 200
        )
    )
    asyncio.run(store.add_score("umo", "u1", "solved", 1, "小明"))
    asyncio.run(store.add_score("umo", "u1", "asked", 3, "小明"))

    stats = asyncio.run(store.stats(now - 60))
    assert stats["asks"] == 1
    assert stats["solves"] == 1
    assert stats["solved"] == 1
    assert stats["rooms"] == 1

    board = asyncio.run(store.leaderboard("umo"))
    assert board[0]["user_name"] == "小明"
    assert board[0]["solved"] == 1
