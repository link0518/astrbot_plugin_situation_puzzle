"""消息路由：把一条消息判定为指令 / 提问 / 自动开局 / 忽略。

纯函数实现，不依赖 AstrBot 对象，便于单测覆盖三通道触发。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

COMMAND_ALIASES = ("海龟汤", "turtle", "sp", "汤")
_LEADING = re.compile(r"^[/／]\s*")


class Action(str, Enum):
    IGNORE = "ignore"
    COMMAND = "command"
    QUESTION = "question"
    START_QUESTION = "start_question"


@dataclass
class Decision:
    action: Action
    text: str = ""
    subcommand: str = ""
    argument: str = ""


def _match_command(text: str) -> tuple[str, str] | None:
    """识别「汤 / 海龟汤 / turtle / sp」，允许不带斜杠但要求后接空格或结束。"""
    body = _LEADING.sub("", text).strip()
    for alias in COMMAND_ALIASES:
        if body == alias:
            return alias, ""
        if body.startswith(alias) and body[len(alias)] in (" ", "\u3000", "\t"):
            return alias, body[len(alias) :].strip()
    return None


def classify(
    text: str,
    *,
    has_room: bool,
    at_bot: bool = False,
    reply_bot: bool = False,
    prefix: str = "海龟",
) -> Decision:
    """按 指令 > 回复bot > @bot > 关键词前缀 的优先级判定。"""
    raw = (text or "").strip()
    if not raw:
        return Decision(Action.IGNORE)

    command = _match_command(raw)
    if command:
        _, argument = command
        subcommand = argument.split(maxsplit=1)[0] if argument else ""
        return Decision(
            Action.COMMAND, text=raw, subcommand=subcommand, argument=argument
        )

    if has_room and reply_bot:
        return Decision(Action.QUESTION, text=raw)
    if has_room and at_bot:
        return Decision(Action.QUESTION, text=raw)

    keyword = (prefix or "").strip()
    if keyword:
        for separator in (" ", "\u3000", "\t", "：", ":"):
            head = keyword + separator
            if raw.startswith(head):
                question = raw[len(head) :].strip()
                if not question:
                    return Decision(Action.IGNORE)
                if has_room:
                    return Decision(Action.QUESTION, text=question)
                return Decision(Action.START_QUESTION, text=question)

    return Decision(Action.IGNORE)
