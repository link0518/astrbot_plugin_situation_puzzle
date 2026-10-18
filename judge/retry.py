"""重试与超时编排：单次超时、总期限、可重试分类、429 退避。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .protocol import ErrorClass, JudgeError

T = TypeVar("T")


async def run_attempts(
    attempt: Callable[[float], Awaitable[T]],
    *,
    total_deadline_ms: int,
    max_retries: int,
) -> T:
    """在总期限内执行 attempt(timeout_s)，失败按分类决定是否再试。

    attempt 收到的是「本次可用超时秒数」，由总期限倒推，保证不会超时叠加。
    """
    deadline = time.monotonic() + max(total_deadline_ms, 1000) / 1000
    attempts = 1 + max(0, max_retries)
    last: JudgeError | None = None

    for index in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0.5:
            raise last or JudgeError(ErrorClass.TIMEOUT, "判题总期限已过")
        try:
            return await attempt(remaining)
        except JudgeError as error:
            last = error
            if not error.retryable or index >= attempts:
                raise
            wait = 0.3 * index
            if error.error_class is ErrorClass.RATE_LIMITED and error.retry_after_ms:
                wait = error.retry_after_ms / 1000
            await asyncio.sleep(max(0.0, min(wait, deadline - time.monotonic())))

    raise last or JudgeError(ErrorClass.NETWORK, "判题失败")
