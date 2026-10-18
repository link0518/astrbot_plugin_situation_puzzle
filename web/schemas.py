"""Web API 输入校验：不信任 iframe 传来的任何字段。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

MAX_TEXT = 20000


class ValidationError(ValueError):
    pass


def as_dict(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError("请求体需要是 JSON 对象")
    return payload


def text(
    payload: dict[str, Any], key: str, *, required: bool = False, limit: int = MAX_TEXT
) -> str:
    value = payload.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{key} 需要字符串")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"{key} 不能为空")
    if len(value) > limit:
        raise ValidationError(f"{key} 超长（上限 {limit}）")
    return value


def one_of(
    payload: dict[str, Any], key: str, options: Iterable[str], default: str = ""
) -> str:
    value = text(payload, key) or default
    if value and value not in set(options):
        raise ValidationError(f"{key} 只能是 {list(options)} 之一")
    return value


def number(
    payload: dict[str, Any],
    key: str,
    *,
    default: int = 0,
    low: int = 0,
    high: int = 1000,
) -> int:
    value = payload.get(key, default)
    try:
        parsed = int(float(value))
    except (TypeError, ValueError) as error:
        raise ValidationError(f"{key} 需要整数") from error
    return max(low, min(high, parsed))


def boolean(payload: dict[str, Any], key: str, default: bool = True) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return default


def hints(payload: dict[str, Any]) -> list[str]:
    value = payload.get("hints", [])
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()][:5]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()][:5]
    return []
