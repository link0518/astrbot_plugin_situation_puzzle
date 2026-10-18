"""判题协议：选项映射、置信度校验、错误分类、JSON 抽取。

选项在插件内部一律用稳定枚举（yes/no/...），中文英文只在展示层映射。
"""

from __future__ import annotations

import json
import math
import re
from enum import Enum
from typing import Any


class ErrorClass(str, Enum):
    """错误分类决定是否重试、是否提示用户检查配置。"""

    NETWORK = "network"
    UPSTREAM = "upstream"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    PROTOCOL = "protocol"
    TIMEOUT = "timeout"


RETRYABLE = {
    ErrorClass.NETWORK,
    ErrorClass.UPSTREAM,
    ErrorClass.TIMEOUT,
    ErrorClass.RATE_LIMITED,
}


class JudgeError(Exception):
    """带分类的判题异常；调用方按分类决定重试与话术。"""

    def __init__(
        self,
        error_class: ErrorClass,
        message: str,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_class = ErrorClass(error_class)
        self.retry_after_ms = retry_after_ms

    @property
    def retryable(self) -> bool:
        return self.error_class in RETRYABLE


ASK_VERDICTS = ("yes", "no", "irrelevant", "uncertain")
SOLVE_VERDICTS = ("solved", "close", "not_yet", "uncertain")
REVIEW_VERDICTS = ("review_pass", "review_reject", "review_uncertain")

ASK_CHOICES_ZH = {"是": "yes", "否": "no", "无关": "irrelevant"}
ASK_CHOICES_EN = {"Yes": "yes", "No": "no", "Irrelevant": "irrelevant"}
SOLVE_CHOICES_ZH = {"破解成功": "solved", "接近真相": "close", "还没猜对": "not_yet"}
SOLVE_CHOICES_EN = {"Solved": "solved", "Close": "close", "Not yet": "not_yet"}
REVIEW_CHOICES_ZH = {"通过": "review_pass", "不通过": "review_reject"}
REVIEW_CHOICES_EN = {"Approved": "review_pass", "Rejected": "review_reject"}

VERDICT_LABELS = {
    "zh": {
        "yes": "是",
        "no": "否",
        "irrelevant": "无关",
        "uncertain": "无法确定",
        "solved": "破解成功",
        "close": "接近真相",
        "not_yet": "还没猜对",
        "review_pass": "通过",
        "review_reject": "不通过",
    },
    "en": {
        "yes": "Yes",
        "no": "No",
        "irrelevant": "Irrelevant",
        "uncertain": "Uncertain",
        "solved": "Solved",
        "close": "Close",
        "not_yet": "Not yet",
        "review_pass": "Approved",
        "review_reject": "Rejected",
    },
}


def label(verdict: str, lang: str) -> str:
    """枚举转展示文案。"""
    return VERDICT_LABELS.get(lang, VERDICT_LABELS["zh"]).get(verdict, verdict)


def choice_map(purpose: str, lang: str) -> dict[str, str]:
    """按用途与语言取「模型选项 → 枚举」映射。"""
    if purpose == "ask":
        return ASK_CHOICES_EN if lang == "en" else ASK_CHOICES_ZH
    if purpose == "solve":
        return SOLVE_CHOICES_EN if lang == "en" else SOLVE_CHOICES_ZH
    return REVIEW_CHOICES_EN if lang == "en" else REVIEW_CHOICES_ZH


def map_choice(mapping: dict[str, str], choice: Any) -> str:
    """把模型返回的选项映射为枚举，未知选项按协议错误处理。"""
    if not isinstance(choice, str) or choice not in mapping:
        raise JudgeError(ErrorClass.PROTOCOL, f"返回了无效选项：{choice!r}")
    return mapping[choice]


def parse_confidence(value: Any) -> float:
    """置信度必须是 0~1 的有限数，缺失或越界都是协议错误。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JudgeError(ErrorClass.PROTOCOL, f"置信度类型不合法：{value!r}")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 1:
        raise JudgeError(ErrorClass.PROTOCOL, f"置信度越界：{value!r}")
    return number


def _balanced_objects(text: str) -> list[str]:
    """从文本中截取所有顶层平衡的 {...}，用于兼容多余说明文字。"""
    found: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                found.append(text[start : index + 1])
                start = -1
    return found


def extract_json(text: str) -> dict[str, Any]:
    """从模型输出里抽 JSON 对象：优先代码块，其次最后一个平衡对象。"""
    if not isinstance(text, str) or not text.strip():
        raise JudgeError(ErrorClass.PROTOCOL, "模型返回为空")

    candidates: list[str] = []
    for block in re.findall(
        r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE
    ):
        candidates.extend(_balanced_objects(block))
    candidates.extend(_balanced_objects(text))

    for candidate in reversed(candidates):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise JudgeError(ErrorClass.PROTOCOL, "模型未返回合法 JSON")


def read_answer(payload: dict[str, Any], question_name: str) -> tuple[str, float]:
    """读取 SystemOne 风格响应里的 choice 与 probabilities。"""
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise JudgeError(ErrorClass.PROTOCOL, "响应缺少 answers")
    answer = answers.get(question_name)
    if not isinstance(answer, dict):
        raise JudgeError(ErrorClass.PROTOCOL, f"响应缺少 answers.{question_name}")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict):
        raise JudgeError(ErrorClass.PROTOCOL, "响应缺少 probabilities")
    choice = answer.get("choice")
    if not isinstance(choice, str):
        raise JudgeError(ErrorClass.PROTOCOL, "响应缺少 choice")
    return choice, parse_confidence(probabilities.get(choice))
