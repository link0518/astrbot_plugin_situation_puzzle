"""消息模板渲染与分段发送。"""

from __future__ import annotations

import re

_VAR = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_SENTENCE = re.compile(r"(?<=[。！？!?；;\n])")


def render(template: str, **values: object) -> str:
    """替换 {var} 占位符；未知变量替换为空，避免把占位符发给玩家。"""
    if not template:
        return ""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return "" if key not in values else str(values[key])

    return _VAR.sub(replace, template).strip()


def split_message(text: str, max_chars: int = 1500) -> list[str]:
    """按段落 → 句子 → 硬切，切成平台能接受的长度。"""
    text = (text or "").strip()
    if not text:
        return []
    limit = max(200, int(max_chars))
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    buffer = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) <= limit:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
            buffer = ""
        if len(paragraph) <= limit:
            buffer = paragraph
            continue
        for sentence in _SENTENCE.split(paragraph):
            if not sentence:
                continue
            if len(buffer) + len(sentence) <= limit:
                buffer += sentence
                continue
            if buffer:
                chunks.append(buffer)
            while len(sentence) > limit:
                chunks.append(sentence[:limit])
                sentence = sentence[limit:]
            buffer = sentence
    if buffer:
        chunks.append(buffer)
    return [chunk.strip() for chunk in chunks if chunk.strip()]
