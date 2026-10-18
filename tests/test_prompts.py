"""提示词与 state：键名、语言、历史问答、类别注记。"""

from astrbot_plugin_situation_puzzle.judge.prompts import (
    AskInput,
    RecentQa,
    ReviewInput,
    SolveInput,
    ask_question,
    ask_state,
    review_state,
    solve_question,
    solve_state,
)


def test_ask_state_zh_keys():
    payload = AskInput(
        title="终点线",
        surface="汤面",
        answer="汤底",
        question="她是自杀吗？",
        core_facts=["父亲已去世"],
        category="本格",
        recent_qas=[RecentQa(question="他死了吗？", verdict="yes")],
    )
    state = ask_state(payload, "zh")
    assert state["汤面"] == "汤面"
    assert state["真相"] == "汤底"
    assert state["玩家提问"] == "她是自杀吗？"
    assert state["核心事实"] == ["父亲已去世"]
    assert "本格" in state["类别"]
    assert state["历史问答"] == ["「他死了吗？」→ 是"]


def test_ask_state_en_keys():
    payload = AskInput(
        title="t", surface="story", answer="answer", question="is he dead?"
    )
    state = ask_state(payload, "en")
    assert set(state) == {"story", "answer", "playerQuestion"}


def test_recent_qas_omitted_when_empty():
    payload = AskInput(title="t", surface="s", answer="a", question="q")
    assert "历史问答" not in ask_state(payload, "zh")


def test_solve_state_uses_answer_key_per_language():
    payload = SolveInput(title="t", surface="s", answer="a", solution="我的推理")
    assert solve_state(payload, "zh")["汤底"] == "a"
    assert solve_state(payload, "en")["answer"] == "a"
    assert solve_state(payload, "zh")["玩家还原"] == "我的推理"


def test_review_state_carries_hints():
    state = review_state(
        ReviewInput(title="t", surface="s", answer="a", hints=["h1", "h2"]), "zh"
    )
    assert state["提示"] == ["h1", "h2"]


def test_question_criteria_match_choice_maps():
    for lang, expected in (
        ("zh", {"是", "否", "无关"}),
        ("en", {"Yes", "No", "Irrelevant"}),
    ):
        assert set(ask_question(lang)["criteria"]) == expected
    for lang, expected in (
        ("zh", {"破解成功", "接近真相", "还没猜对"}),
        ("en", {"Solved", "Close", "Not yet"}),
    ):
        assert set(solve_question(lang)["criteria"]) == expected
