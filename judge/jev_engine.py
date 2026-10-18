"""Jev SystemOne 判题引擎：复刻原项目 packages/jev/src/client.ts 的请求协议。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

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
    map_choice,
    read_answer,
)
from .retry import run_attempts

_QUESTION_NAMES = {"ask": "verdict", "solve": "outcome", "review": "review"}


def _clean_base_url(value: str) -> str:
    """去掉首尾空白与尾部斜杠：带斜杠的地址会被服务端 307 跳转，空响应解析必然失败。"""
    return (value or "").strip().rstrip("/")


def _clean_api_key(value: str) -> str:
    """容忍整段粘贴的 "Bearer xxx"，以及从别处复制带的换行。"""
    key = (value or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    return key


def _clean_proxy(value: str) -> str:
    """代理地址留空表示直连；只去掉首尾空白。"""
    return (value or "").strip()


def _snippet(response: httpx.Response, limit: int = 160) -> str:
    """截一段响应体放进错误信息，便于面板日志定位。"""
    text = (response.text or "").strip().replace("\r", " ").replace("\n", " ")
    return text[:limit]


@dataclass
class JevSettings:
    base_url: str
    api_key: str
    model: str
    threshold: float = 0.5
    language: Language = "zh"
    attempt_timeout_ms: int = 20000
    total_deadline_ms: int = 45000
    max_retries: int = 1
    proxy: str = ""

    def __post_init__(self) -> None:
        self.base_url = _clean_base_url(self.base_url)
        self.api_key = _clean_api_key(self.api_key)
        self.model = (self.model or "").strip()
        self.proxy = _clean_proxy(self.proxy)


class JevEngine(JudgeEngine):
    name = "jev"

    def __init__(self, settings: JevSettings) -> None:
        self.settings = settings

    async def ask(self, payload: AskInput, umo: str | None = None) -> JudgeResult:
        if not payload.question.strip():
            raise JudgeError(ErrorClass.PROTOCOL, "提问为空")
        return await self._judge(
            choice_map("ask", self.settings.language),
            "ask",
            ask_state(payload, self.settings.language),
            ask_question(self.settings.language),
        )

    async def solve(self, payload: SolveInput, umo: str | None = None) -> JudgeResult:
        if not payload.solution.strip():
            raise JudgeError(ErrorClass.PROTOCOL, "还原内容为空")
        return await self._judge(
            choice_map("solve", self.settings.language),
            "solve",
            solve_state(payload, self.settings.language),
            solve_question(self.settings.language),
        )

    async def review(self, payload: ReviewInput, umo: str | None = None) -> str:
        mapping = choice_map("review", self.settings.language)
        try:
            result = await self._judge(
                mapping,
                "review",
                review_state(payload, self.settings.language),
                review_question(self.settings.language),
            )
        except JudgeError:
            return "review_uncertain"
        return result.verdict if result.verdict != "uncertain" else "review_uncertain"

    async def close(self) -> None:
        """每次请求独立连接，无需释放。"""

    async def _judge(
        self,
        mapping: dict[str, str],
        purpose: str,
        state: dict[str, Any],
        question: dict[str, Any],
    ) -> JudgeResult:
        if not self.settings.api_key:
            raise JudgeError(ErrorClass.AUTH, "未配置 Jev API Key")
        if not self.settings.base_url:
            raise JudgeError(ErrorClass.AUTH, "未配置 Jev 接口地址")

        started = time.monotonic()
        name = _QUESTION_NAMES[purpose]
        choice, confidence, raw = await run_attempts(
            lambda timeout: self._request_once(state, name, question, timeout, purpose),
            total_deadline_ms=self.settings.total_deadline_ms,
            max_retries=self.settings.max_retries,
        )
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

    async def _request_once(
        self,
        state: dict[str, Any],
        question_name: str,
        question: dict[str, Any],
        timeout_s: float,
        purpose: str,
    ) -> tuple[str, float, dict[str, Any]]:
        body = {
            "model": self.settings.model,
            "state": state,
            "questions": {question_name: question},
        }
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
        }
        timeout = max(0.5, min(timeout_s, self.settings.attempt_timeout_ms / 1000))
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                trust_env=True,
                follow_redirects=True,
                proxy=self.settings.proxy or None,
            ) as client:
                response = await client.post(
                    self.settings.base_url, json=body, headers=headers
                )
        except httpx.TimeoutException as error:
            raise JudgeError(ErrorClass.TIMEOUT, f"Jev 请求超时：{error}") from error
        except httpx.HTTPError as error:
            raise JudgeError(ErrorClass.NETWORK, f"Jev 网络错误：{error}") from error

        status = response.status_code
        if status in (401, 403):
            raise JudgeError(
                ErrorClass.AUTH, f"Jev 鉴权失败：{status} {_snippet(response)}"
            )
        if status == 429:
            retry_after = response.headers.get("retry-after", "")
            seconds = int(retry_after) if retry_after.isdigit() else 0
            raise JudgeError(
                ErrorClass.RATE_LIMITED,
                "Jev 触发限流：429",
                retry_after_ms=seconds * 1000 or None,
            )
        if status == 451:
            # 区域限制：请求本身没问题，是出口 IP 所在地区被封，重试和改模型都无用
            raise JudgeError(
                ErrorClass.REGION,
                f"Jev 拒绝服务当前地区：451 {_snippet(response)}",
            )
        if status >= 500:
            raise JudgeError(
                ErrorClass.UPSTREAM, f"Jev 服务错误：{status} {_snippet(response)}"
            )
        if status >= 400:
            # 400/404/422 多是地址或模型名填错，重试没有意义，直接指向配置
            error_class = (
                ErrorClass.CONFIG if status in (400, 404, 422) else ErrorClass.PROTOCOL
            )
            raise JudgeError(
                error_class, f"Jev 拒绝请求：{status} {_snippet(response)}"
            )
        if not 200 <= status < 300:
            location = response.headers.get("location", "")
            raise JudgeError(
                ErrorClass.CONFIG,
                f"Jev 返回跳转：{status} → {location}，接口地址可能不对",
            )

        try:
            payload = response.json()
        except ValueError as error:
            raise JudgeError(ErrorClass.PROTOCOL, "Jev 返回不是合法 JSON") from error
        if not isinstance(payload, dict):
            raise JudgeError(ErrorClass.PROTOCOL, "Jev 返回结构不合法")

        try:
            choice, confidence = read_answer(payload, question_name)
        except JudgeError as error:
            # 把响应片段带进日志，否则「返回了无效选项」根本看不出模型说了什么
            snippet = json.dumps(payload, ensure_ascii=False)[:200]
            raise JudgeError(
                error.error_class, f"{error}；响应片段：{snippet}"
            ) from error
        return choice, confidence, {"purpose": purpose, "usage": payload.get("usage")}
