"""判题包：协议、结果结构、提示词。

引擎实现按需导入，避免仅引用协议时把 httpx 等依赖拉起来。
"""

from .base import JudgeEngine, JudgeResult, apply_threshold, failure_result
from .protocol import ErrorClass, JudgeError

__all__ = [
    "ErrorClass",
    "JudgeEngine",
    "JudgeError",
    "JudgeResult",
    "apply_threshold",
    "failure_result",
]
