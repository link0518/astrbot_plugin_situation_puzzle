"""判题提示词与 state 构造，移植自 Situation-Puzzle packages/jev/src/prompts.ts。

state 的键名与原项目逐字段一致：中文题用中文键，英文题用英文键，
保证换语言只影响文案、不改变判题语义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Language = Literal["zh", "en"]
PuzzleCategory = Literal["honkaku", "henkaku"]


@dataclass
class RecentQa:
    """一轮已判定的问答，用于代词消解。"""

    question: str
    verdict: str


@dataclass
class AskInput:
    title: str
    surface: str
    answer: str
    question: str
    core_facts: list[str] = field(default_factory=list)
    category: str | None = None
    recent_qas: list[RecentQa] = field(default_factory=list)


@dataclass
class SolveInput:
    title: str
    surface: str
    answer: str
    solution: str
    core_facts: list[str] = field(default_factory=list)
    category: str | None = None


@dataclass
class ReviewInput:
    title: str
    surface: str
    answer: str
    hints: list[str] = field(default_factory=list)


def _category_note(category: str | None, lang: Language) -> dict[str, Any]:
    """把本格/变格的现实性边界写进判题上下文；未分类不写入。"""
    if not category:
        return {}
    henkaku = category in ("变格", "henkaku")
    if lang == "en":
        note = (
            "Henkaku: the answer may include supernatural or unrealistic elements."
            if henkaku
            else "Honkaku: the answer is a realistic explanation with no supernatural forces."
        )
        return {"category": note}
    note = (
        "变格：汤底可以包含超自然或非现实设定。"
        if henkaku
        else "本格：汤底是现实合理的解释，不存在超自然力量。"
    )
    return {"类别": note}


_VERDICT_LABELS = {
    "yes": {"zh": "是", "en": "yes"},
    "no": {"zh": "否", "en": "no"},
    "irrelevant": {"zh": "无关", "en": "irrelevant"},
    "uncertain": {"zh": "无法确定", "en": "uncertain"},
}


def _recent_entry(recent: list[RecentQa], lang: Language) -> dict[str, Any]:
    """历史问答（旧在前）；空列表不写入任何键。"""
    if not recent:
        return {}
    items = [
        f'"{item.question}" -> {_VERDICT_LABELS.get(item.verdict, {}).get(lang, item.verdict)}'
        if lang == "en"
        else f"「{item.question}」→ {_VERDICT_LABELS.get(item.verdict, {}).get(lang, item.verdict)}"
        for item in recent
    ]
    return {"recentQAs": items} if lang == "en" else {"历史问答": items}


def ask_question(lang: Language) -> dict[str, Any]:
    if lang == "en":
        return {
            "type": "choice",
            "instructions": "Judge the player question against the answer. Choose only Yes, No or Irrelevant.",
            "criteria": {
                "Yes": "The answer supports the claim or a reasonable inference in the question.",
                "No": "The answer contradicts the claim or a reasonable inference in the question.",
                "Irrelevant": "The question has no material connection to the answer.",
            },
        }
    return {
        "type": "choice",
        "instructions": "根据真相判断玩家提问，只能选择是、否、无关。",
        "criteria": {
            "是": "提问的事实或合理推论被真相支持。",
            "否": "提问的事实或合理推论被真相否定。",
            "无关": "提问和真相没有实质关系。",
        },
    }


def solve_question(lang: Language) -> dict[str, Any]:
    if lang == "en":
        return {
            "type": "choice",
            "instructions": (
                "Compare the player's reconstruction with the answer. Focus on the core event, key reasons and "
                "causal chain that explain the mystery. Accept equivalent wording, different narrative order and "
                "omission of details that do not affect the core truth. Names, exact times and locations are required "
                "only when essential to the explanation. A complete core causal chain counts as solved even if "
                "incidental details are omitted."
            ),
            "criteria": {
                "Solved": "The player covers the core event, key reasons and causal connections; incidental detail omissions and differences are allowed.",
                "Close": "The player has the main idea but misses a fact, reason or causal step essential to explaining the mystery.",
                "Not yet": "The explanation conflicts with central facts. Incidental detail differences alone do not qualify.",
            },
        }
    return {
        "type": "choice",
        "instructions": (
            "根据汤底判断玩家是否还原了解释谜团所需的核心事件、关键原因和因果链。允许同义表达、不同叙述顺序，"
            "以及省略不影响核心真相的细节。姓名、时间、地点等仅在影响核心解释时才需覆盖。"
            "核心因果链完整时，即使省略装饰细节也应判成功。"
        ),
        "criteria": {
            "破解成功": "玩家覆盖了汤底的核心事件、关键原因与因果关系；允许省略不影响真相的细节或存在装饰细节差异。",
            "接近真相": "玩家抓住主要方向，但遗漏了解释谜团必需的关键事实、原因或因果环节。",
            "还没猜对": "玩家的解释与汤底核心事实冲突。仅有装饰细节差异不足以判为还没猜对。",
        },
    }


def review_question(lang: Language) -> dict[str, Any]:
    if lang == "en":
        return {
            "type": "choice",
            "instructions": (
                "Review this entire situation puzzle for sexual or political content. Consider every field. "
                "Ordinary suspense, death, crime and police investigations are allowed."
            ),
            "criteria": {
                "Approved": "Contains no sexual content or political figures, events, advocacy or policy disputes.",
                "Rejected": "Contains sexual descriptions or acts, or political figures, events, advocacy or policy disputes.",
            },
        }
    return {
        "type": "choice",
        "instructions": (
            "审核整道海龟汤是否适合公开。只检查色情或政治内容；标题、汤面、汤底和提示任一处涉及这两类内容，"
            "就选不通过。普通的悬疑、死亡、犯罪和警察办案不因此被判为政治内容。"
        ),
        "criteria": {
            "通过": "没有色情内容，也没有政治人物、政治事件、政治宣传或政策争议等政治内容。",
            "不通过": "包含色情描写或性行为内容，或者包含政治人物、政治事件、政治宣传或政策争议等政治内容。",
        },
    }


def ask_state(payload: AskInput, lang: Language) -> dict[str, Any]:
    common = _category_note(payload.category, lang)
    facts = payload.core_facts or []
    if lang == "en":
        state: dict[str, Any] = {"story": payload.surface, "answer": payload.answer}
        state.update(common)
        if facts:
            state["coreFacts"] = facts
        state.update(_recent_entry(payload.recent_qas, lang))
        state["playerQuestion"] = payload.question
        return state
    state = {"汤面": payload.surface, "真相": payload.answer}
    state.update(common)
    if facts:
        state["核心事实"] = facts
    state.update(_recent_entry(payload.recent_qas, lang))
    state["玩家提问"] = payload.question
    return state


def solve_state(payload: SolveInput, lang: Language) -> dict[str, Any]:
    common = _category_note(payload.category, lang)
    facts = payload.core_facts or []
    if lang == "en":
        state: dict[str, Any] = {"story": payload.surface, "answer": payload.answer}
        state.update(common)
        if facts:
            state["coreFacts"] = facts
        state["playerSolution"] = payload.solution
        return state
    state = {"汤面": payload.surface, "汤底": payload.answer}
    state.update(common)
    if facts:
        state["核心事实"] = facts
    state["玩家还原"] = payload.solution
    return state


def review_state(payload: ReviewInput, lang: Language) -> dict[str, Any]:
    if lang == "en":
        return {
            "title": payload.title,
            "story": payload.surface,
            "answer": payload.answer,
            "hints": payload.hints,
        }
    return {
        "标题": payload.title,
        "汤面": payload.surface,
        "汤底": payload.answer,
        "提示": payload.hints,
    }
