"""判题引擎抽象：统一返回结构与阈值降级。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .prompts import AskInput, ReviewInput, SolveInput
from .protocol import JudgeError, label


@dataclass
class JudgeResult:
    """一次判题的最终结果（已应用阈值）。"""

    verdict: str
    confidence: float
    engine: str
    latency_ms: int = 0
    threshold: float = 0.5
    raw: dict[str, Any] | None = field(default=None)
    error_class: str | None = None

    @property
    def degraded(self) -> bool:
        """是否因置信度不足被降级为「无法确定」。"""
        return self.verdict == "uncertain"


class JudgeEngine(Protocol):
    name: str

    async def ask(self, payload: AskInput, umo: str | None = None) -> JudgeResult: ...

    async def solve(
        self, payload: SolveInput, umo: str | None = None
    ) -> JudgeResult: ...

    async def review(self, payload: ReviewInput, umo: str | None = None) -> str: ...

    async def close(self) -> None: ...


def apply_threshold(verdict: str, confidence: float, threshold: float) -> str:
    """低于阈值统一降级为 uncertain，宁可让玩家重问也不硬猜。"""
    return verdict if confidence >= threshold else "uncertain"


def failure_result(error: JudgeError, engine: str, lang: str = "zh") -> JudgeResult:
    """把异常收敛成可展示的结果，供调用方决定话术。"""
    return JudgeResult(
        verdict="uncertain",
        confidence=0.0,
        engine=engine,
        error_class=error.error_class.value,
        raw={"message": str(error), "label": label("uncertain", lang)},
    )
