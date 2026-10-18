"""AstrBot 大模型判题引擎：走已配置的 provider，要求模型输出带置信度的 JSON。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from .base import JudgeEngine, JudgeResult, apply_threshold
from .prompts import (
    AskInput,
    Language,
    ReviewInput,
    SolveInput,
    ask_question,
    ask_state,
    review_question,
    review_state,
    solve_question,
    solve_state,
)
from .protocol import (
    ErrorClass,
    JudgeError,
    choice_map,
    extract_json,
    map_choice,
    parse_confidence,
)


@dataclass
class ProviderSettings:
    provider_id: str = ""
    threshold: float = 0.5
    language: Language = "zh"
    total_deadline_ms: int = 45000
    max_retries: int = 1
    self_consistency: int = 1


_DECISIONS = {"ask": ("choice", "verdict"), "solve": ("choice", "verdict")}


def _system_prompt(
    state: dict[str, Any], question: dict[str, Any], lang: Language
) -> str:
    criteria = "\n".join(
        f"- {name}：{desc}" for name, desc in question["criteria"].items()
    )
    if lang == "en":
        return (
            "You are the host of a situation puzzle. Judge the player against the hidden answer.\n"
            "Context (JSON):\n"
            f"{json.dumps(state, ensure_ascii=False, indent=2)}\n\n"
            f"Rules:\n{question['instructions']}\n{criteria}\n\n"
            "Never reveal the answer. Output JSON only, no markdown, no explanation:\n"
            '{"choice": "<one of the options>", "confidence": <0..1>}\n'
            "confidence is your own certainty; if the context is insufficient, use a low value."
        )
    return (
        "你是海龟汤主持人，只负责判题，绝不向玩家透露汤底。\n"
        "判题上下文（JSON）：\n"
        f"{json.dumps(state, ensure_ascii=False, indent=2)}\n\n"
        f"判定要求：{question['instructions']}\n可选结论：\n{criteria}\n\n"
        "只输出一行 JSON，不要 markdown、不要解释：\n"
        '{"choice": "<上面的某个结论>", "confidence": <0 到 1 之间的小数>}\n'
        "confidence 是你对本次判定的把握；证据不足时请给出较低的值。"
    )


class ProviderEngine(JudgeEngine):
    name = "provider"

    def __init__(self, context: Any, settings: ProviderSettings) -> None:
        self.context = context
        self.settings = settings

    async def ask(self, payload: AskInput, umo: str | None = None) -> JudgeResult:
        if not payload.question.strip():
            raise JudgeError(ErrorClass.PROTOCOL, "提问为空")
        return await self._judge(
            "ask",
            ask_state(payload, self.settings.language),
            ask_question(self.settings.language),
            umo,
        )

    async def solve(self, payload: SolveInput, umo: str | None = None) -> JudgeResult:
        if not payload.solution.strip():
            raise JudgeError(ErrorClass.PROTOCOL, "还原内容为空")
        return await self._judge(
            "solve",
            solve_state(payload, self.settings.language),
            solve_question(self.settings.language),
            umo,
        )

    async def review(self, payload: ReviewInput, umo: str | None = None) -> str:
        mapping = choice_map("review", self.settings.language)
        try:
            result = await self._judge(
                "review",
                review_state(payload, self.settings.language),
                review_question(self.settings.language),
                umo,
            )
        except JudgeError:
            return "review_uncertain"
        return (
            result.verdict if result.verdict in mapping.values() else "review_uncertain"
        )

    async def close(self) -> None:
        """无持久连接。"""

    async def _judge(
        self,
        purpose: str,
        state: dict[str, Any],
        question: dict[str, Any],
        umo: str | None,
    ) -> JudgeResult:
        provider_id = self.settings.provider_id
        if not provider_id and umo:
            provider_id = await self.context.get_current_chat_provider_id(umo=umo)
        if not provider_id:
            raise JudgeError(ErrorClass.AUTH, "没有可用的对话模型，请先在面板配置")

        started = time.monotonic()
        samples = max(1, self.settings.self_consistency)
        votes: list[tuple[str, float]] = []
        raw: dict[str, Any] = {"samples": []}

        for index in range(samples):
            choice, confidence = await self._generate(
                state, question, provider_id, index > 0
            )
            votes.append((choice, confidence))
            raw["samples"].append({"choice": choice, "confidence": confidence})

        mapping = choice_map(purpose, self.settings.language)
        choice, confidence = self._merge(votes)
        verdict = map_choice(mapping, choice)
        latency = int((time.monotonic() - started) * 1000)
        return JudgeResult(
            verdict=apply_threshold(verdict, confidence, self.settings.threshold),
            confidence=confidence,
            engine=self.name,
            latency_ms=latency,
            threshold=self.settings.threshold,
            raw=raw,
        )

    @staticmethod
    def _merge(votes: list[tuple[str, float]]) -> tuple[str, float]:
        """多次采样取多数票，置信度用赞成票均值。"""
        if len(votes) == 1:
            return votes[0]
        tally: dict[str, list[float]] = {}
        for choice, confidence in votes:
            tally.setdefault(choice, []).append(confidence)
        winner = max(tally, key=lambda key: (len(tally[key]), sum(tally[key])))
        group = tally[winner]
        return winner, sum(group) / len(group)

    async def _generate(
        self,
        state: dict[str, Any],
        question: dict[str, Any],
        provider_id: str,
        reinforced: bool,
    ) -> tuple[str, float]:
        system = _system_prompt(state, question, self.settings.language)
        prompt = (
            "请给出判定。只输出 JSON。"
            if self.settings.language != "en"
            else "Judge now. JSON only."
        )
        if reinforced:
            example = next(iter(question["criteria"]))
            prompt += f'\n上一次输出无法解析。本次只输出一行：{{"choice": "{example}", "confidence": 0.0}}（把值替换成你的判定）'

        timeout = max(1.0, self.settings.total_deadline_ms / 1000)
        try:
            response = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    system_prompt=system,
                ),
                timeout=timeout,
            )
        except TimeoutError as error:
            raise JudgeError(ErrorClass.TIMEOUT, "大模型判题超时") from error
        except Exception as error:
            raise JudgeError(ErrorClass.UPSTREAM, f"大模型调用失败：{error}") from error

        text = getattr(response, "completion_text", None)
        if not isinstance(text, str) or not text.strip():
            raise JudgeError(ErrorClass.PROTOCOL, "大模型没有返回内容")

        payload = extract_json(text)
        choice = (
            payload.get("choice") or payload.get("verdict") or payload.get("answer")
        )
        if choice not in question["criteria"]:
            raise JudgeError(ErrorClass.PROTOCOL, f"大模型返回了无效结论：{choice!r}")
        confidence = parse_confidence(
            payload.get("confidence", payload.get("probability"))
        )
        return str(choice), confidence
