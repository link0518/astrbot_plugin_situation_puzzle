"""消息路由：把一条消息判定为指令 / 提问 / 自动开局 / 忽略。

纯函数实现，不依赖 AstrBot 对象，便于单测覆盖三通道触发。
指令别名可由配置覆盖，因此解析结果里的 subcommand 是标准名（hint/answer…），
而不是玩家输入的词。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

DEFAULT_COMMAND_ALIASES = ("汤", "海龟汤", "turtle", "sp")
COMMAND_STYLES = ("both", "slash", "text")

# 标准子指令顺序即帮助文案里的展示顺序
SUBCOMMAND_KEYS = (
    "start",
    "hint",
    "answer",
    "reveal",
    "skip",
    "status",
    "rank",
    "library",
    "help",
)

DEFAULT_SUBCOMMANDS: dict[str, tuple[str, ...]] = {
    "start": ("开", "开局", "开始", "新", "新局", "start"),
    "hint": ("提示", "线索", "hint", "h"),
    "answer": ("答", "作答", "解", "solve"),
    "reveal": ("揭晓", "看答案", "答案", "reveal"),
    "skip": ("跳过", "换题", "换", "skip"),
    "status": ("状态", "进度", "status", "info"),
    "rank": ("排行", "排名", "排行榜", "rank"),
    "library": ("题库", "统计", "stats"),
    "help": ("帮助", "玩法", "菜单", "help", "?"),
}

# 子指令与参数之间的分隔符
_SEPARATORS = (" ", "\u3000", "\t", "：", ":", "，", ",", "、")
_LEADING = re.compile(r"^[/／]\s*")
_SPLIT = re.compile(r"[,，、;；\s]+")
# 开局参数：难度词或 #题号；其余词视为拼错的子指令，不走开局
_START_ARG = re.compile(
    r"^(#[^\s]+|简单|中等|普通|困难|难|easy|medium|hard)(?=$|[\s，,])",
    re.IGNORECASE,
)


class Action(str, Enum):
    IGNORE = "ignore"
    COMMAND = "command"
    QUESTION = "question"
    START_QUESTION = "start_question"


@dataclass
class Decision:
    action: Action
    text: str = ""
    command: str = ""
    subcommand: str = ""
    argument: str = ""


def parse_aliases(value: object) -> tuple[str, ...]:
    """把「逗号/顿号/空白分隔的字符串」或列表归一成去重别名元组。"""
    if isinstance(value, str):
        parts: list[str] = _SPLIT.split(value)
    elif isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value]
    else:
        parts = []

    aliases: list[str] = []
    seen: set[str] = set()
    for part in parts:
        alias = part.strip()
        if not alias:
            continue
        key = alias.lower()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(alias)
    return tuple(aliases)


def normalize_subcommands(value: object) -> dict[str, tuple[str, ...]]:
    """把配置里的子指令映射补全成 {标准名: 别名元组}，未知键丢弃。"""
    source = value if isinstance(value, dict) else {}
    result: dict[str, tuple[str, ...]] = {}
    for key in SUBCOMMAND_KEYS:
        if key in source:
            result[key] = parse_aliases(source.get(key))
        else:
            result[key] = DEFAULT_SUBCOMMANDS[key]
    return result


def _match_alias(body: str, aliases: tuple[str, ...]) -> tuple[str, str] | None:
    """在 body 开头匹配别名，别名后必须是分隔符或结束。"""
    lowered = body.lower()
    for alias in sorted(aliases, key=len, reverse=True):
        probe = alias.lower()
        if not probe:
            continue
        if lowered == probe:
            return alias, ""
        if lowered.startswith(probe) and body[len(probe)] in _SEPARATORS:
            return alias, body[len(probe) :].strip(" \u3000\t：:，,、")
    return None


def _match_command(
    text: str,
    style: str,
    names: tuple[str, ...],
    subcommands: dict[str, tuple[str, ...]],
) -> tuple[str, str, str, str] | None:
    """识别主指令与子指令，返回 (命中的主指令, 标准子指令, 子指令别名, 剩余参数)。

    style 决定斜杠要求：both 两种都认，slash 必须带斜杠，text 不允许带斜杠。
    """
    has_slash = bool(_LEADING.match(text))
    if style == "slash" and not has_slash:
        return None
    if style == "text" and has_slash:
        return None

    body = _LEADING.sub("", text).strip()
    head = _match_alias(body, names)
    if head is None:
        return None
    command, rest = head
    if not rest:
        return command, "start", "", ""

    # 跨组取最长别名，避免「答案」被 answer 组的「答」抢先吃掉
    candidates = [
        (alias, key) for key in SUBCOMMAND_KEYS for alias in subcommands.get(key, ())
    ]
    for alias, key in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        hit = _match_alias(rest, (alias,))
        if hit is not None:
            return command, key, hit[0], hit[1]

    # 只有难度词或 #题号才算开局参数，其余当作拼错的子指令交给上层提示
    if _START_ARG.match(rest):
        return command, "start", "", rest
    return command, "", "", rest


def classify(
    text: str,
    *,
    has_room: bool,
    at_bot: bool = False,
    reply_bot: bool = False,
    prefix: str = "海龟",
    command_style: str = "both",
    command_names: tuple[str, ...] | None = None,
    subcommands: dict[str, tuple[str, ...]] | object | None = None,
) -> Decision:
    """按 指令 > 回复bot > @bot > 关键词前缀 的优先级判定。"""
    raw = (text or "").strip()
    if not raw:
        return Decision(Action.IGNORE)

    names = command_names or DEFAULT_COMMAND_ALIASES
    subs = normalize_subcommands(subcommands)

    command = _match_command(raw, command_style, names, subs)
    if command:
        head, subcommand, _matched, argument = command
        return Decision(
            Action.COMMAND,
            text=raw,
            command=head,
            subcommand=subcommand,
            argument=argument,
        )

    if has_room and reply_bot:
        return Decision(Action.QUESTION, text=raw)
    if has_room and at_bot:
        return Decision(Action.QUESTION, text=raw)

    keyword = (prefix or "").strip()
    if keyword:
        for separator in (" ", "\u3000", "\t", "：", ":"):
            lead = keyword + separator
            if raw.startswith(lead):
                question = raw[len(lead) :].strip()
                if not question:
                    return Decision(Action.IGNORE)
                if has_room:
                    return Decision(Action.QUESTION, text=question)
                return Decision(Action.START_QUESTION, text=question)

    return Decision(Action.IGNORE)


def command_hint(
    command_names: tuple[str, ...] | None = None,
    style: str = "both",
) -> str:
    """文案里展示的指令写法：文字模式不带斜杠，斜杠模式只展示斜杠。"""
    names = command_names or DEFAULT_COMMAND_ALIASES
    head = names[0] if names else DEFAULT_COMMAND_ALIASES[0]
    return head if style == "text" else f"/{head}"
