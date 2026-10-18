"""插件配置：默认值、类型收敛、面板读写。"""

from __future__ import annotations

import copy
from typing import Any

from .router import (
    DEFAULT_COMMAND_ALIASES,
    DEFAULT_SUBCOMMANDS,
    normalize_subcommands,
    parse_aliases,
)

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "basic": {
        "host_name": "Jev",
        "language": "zh",
        "room_scope": "group",
        "keyword_prefix": "海龟",
        "command_style": "both",
        "command_names": ",".join(DEFAULT_COMMAND_ALIASES),
        "sub_commands": {
            key: ",".join(aliases) for key, aliases in DEFAULT_SUBCOMMANDS.items()
        },
        "default_difficulty": "random",
        "allow_group": True,
        "allow_private": True,
    },
    "judge": {
        "engine": "jev",
        "jev_api_key": "",
        "jev_base_url": "https://ai-proxy.xiaobaozi.cn/opencode.ai/zen/v1/systemone",
        "jev_model": "jev-1.13",
        "provider_id": "",
        "threshold": 0.5,
        "attempt_timeout_ms": 20000,
        "total_deadline_ms": 45000,
        "max_retries": 1,
        "self_consistency": 1,
        "recent_qa_window": 5,
    },
    "game": {
        "hint_max": 3,
        "idle_timeout_min": 30,
        "reveal_policy": "all",
        "reveal_lock_s": 30,
        "allow_replay": False,
    },
    "limit": {
        "user_cooldown_s": 3,
        "room_queue_limit": 8,
        "daily_limit": 0,
    },
    "message": {
        "max_chars": 1500,
        "tpl_start": (
            "【{title}】\n{story}\n\n我是{host}，这碗汤的主持人。@我提问，我只回答 是 / 否 / 无关。\n"
            "{quick}"
        ),
        "tpl_verdict": "「{question}」→ {verdict}",
        "tpl_uncertain": "这个问题我得再看看（无法确定）",
        "tpl_solved": "破解成功！\n\n{answer}",
        "tpl_reveal": "汤底：\n{answer}{source}",
        "tpl_close": "接近真相了，但还缺一环。",
        "tpl_not_yet": "还没猜对，再想想那些反常的细节。",
    },
}

# 字段约束：取值枚举、数值区间
ENUMS = {
    "basic.language": ("zh", "en"),
    "basic.room_scope": ("group", "user"),
    "basic.command_style": ("both", "slash", "text"),
    "basic.default_difficulty": ("random", "easy", "medium", "hard"),
    "judge.engine": ("jev", "provider"),
    "game.reveal_policy": ("all", "admin"),
}
RANGES = {
    "judge.threshold": (0.0, 1.0),
    "judge.attempt_timeout_ms": (1000, 120000),
    "judge.total_deadline_ms": (2000, 300000),
    "judge.max_retries": (0, 3),
    "judge.self_consistency": (1, 5),
    "judge.recent_qa_window": (0, 20),
    "game.hint_max": (1, 3),
    "game.idle_timeout_min": (1, 1440),
    "game.reveal_lock_s": (0, 600),
    "limit.user_cooldown_s": (0, 60),
    "limit.room_queue_limit": (1, 50),
    "limit.daily_limit": (0, 100000),
    "message.max_chars": (200, 5000),
}
SECRETS = {"judge.jev_api_key"}


def _flatten(node: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif key in merged:
            merged[key] = value
    return merged


def _set_path(node: dict[str, Any], path: str, value: Any) -> None:
    """按点分路径写入，sub_commands.<key> 这类两级键不会被压成字面键名。"""
    parts = path.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


_BAD_ALIAS = ("/", "／", "{", "}", "\n", "\r")


def _validate_commands(raw: dict[str, Any], pending: dict[str, Any]) -> list[str]:
    """校验主指令与子指令映射：不能为空、不能有非法字符、不能跨组重名。"""
    errors: list[str] = []
    flat = {**_flatten(raw), **pending}

    names = parse_aliases(flat.get("basic.command_names", ""))
    if not names:
        errors.append("basic.command_names 至少要保留一个主指令写法")
    for alias in names:
        if len(alias) > 20:
            errors.append(f"主指令「{alias}」过长（最多 20 个字符）")
        if any(bad in alias for bad in _BAD_ALIAS):
            errors.append(f"主指令「{alias}」不能包含斜杠、花括号或换行")

    owners: dict[str, str] = {}
    for key in DEFAULT_SUBCOMMANDS:
        aliases = parse_aliases(flat.get(f"basic.sub_commands.{key}", ""))
        for alias in aliases:
            if len(alias) > 20:
                errors.append(f"子指令「{alias}」过长（最多 20 个字符）")
            if any(bad in alias for bad in _BAD_ALIAS):
                errors.append(f"子指令「{alias}」不能包含斜杠、花括号或换行")
            owner = owners.get(alias.lower())
            if owner is not None and owner != key:
                errors.append(f"「{alias}」同时映射到 {owner} 和 {key}，请只保留一处")
            owners[alias.lower()] = key
    return errors


class PluginConfig:
    """AstrBotConfig 的只读视图 + 面板写入校验。"""

    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        self.raw = _deep_merge(DEFAULTS, raw or {})

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # ---- 常用访问器 ----
    @property
    def enabled(self) -> bool:
        return bool(self.get("enabled", True))

    @property
    def host_name(self) -> str:
        return str(self.get("basic.host_name", "Jev") or "Jev")

    @property
    def language(self) -> str:
        return "en" if self.get("basic.language") == "en" else "zh"

    @property
    def room_scope(self) -> str:
        return str(self.get("basic.room_scope", "group"))

    @property
    def keyword_prefix(self) -> str:
        return str(self.get("basic.keyword_prefix", "海龟") or "海龟").strip()

    @property
    def default_difficulty(self) -> str:
        return str(self.get("basic.default_difficulty", "random"))

    @property
    def command_style(self) -> str:
        style = str(self.get("basic.command_style", "both"))
        return style if style in ("both", "slash", "text") else "both"

    @property
    def command_names(self) -> tuple[str, ...]:
        names = parse_aliases(self.get("basic.command_names", ""))
        return names or DEFAULT_COMMAND_ALIASES

    @property
    def subcommands(self) -> dict[str, tuple[str, ...]]:
        return normalize_subcommands(self.get("basic.sub_commands", {}))

    @property
    def engine(self) -> str:
        return "provider" if self.get("judge.engine") == "provider" else "jev"

    @property
    def threshold(self) -> float:
        return float(self.get("judge.threshold", 0.5))

    @property
    def attempt_timeout_ms(self) -> int:
        return int(self.get("judge.attempt_timeout_ms", 20000))

    @property
    def total_deadline_ms(self) -> int:
        return int(self.get("judge.total_deadline_ms", 45000))

    @property
    def max_retries(self) -> int:
        return int(self.get("judge.max_retries", 1))

    @property
    def self_consistency(self) -> int:
        return int(self.get("judge.self_consistency", 1))

    @property
    def recent_qa_window(self) -> int:
        return int(self.get("judge.recent_qa_window", 5))

    @property
    def hint_max(self) -> int:
        return int(self.get("game.hint_max", 3))

    @property
    def idle_timeout_min(self) -> int:
        return int(self.get("game.idle_timeout_min", 30))

    @property
    def reveal_policy(self) -> str:
        return str(self.get("game.reveal_policy", "all"))

    @property
    def reveal_lock_s(self) -> int:
        return int(self.get("game.reveal_lock_s", 30))

    @property
    def allow_replay(self) -> bool:
        return bool(self.get("game.allow_replay", False))

    @property
    def user_cooldown_s(self) -> int:
        return int(self.get("limit.user_cooldown_s", 3))

    @property
    def room_queue_limit(self) -> int:
        return int(self.get("limit.room_queue_limit", 8))

    @property
    def daily_limit(self) -> int:
        return int(self.get("limit.daily_limit", 0))

    @property
    def max_chars(self) -> int:
        return int(self.get("message.max_chars", 1500))

    def template(self, name: str) -> str:
        return str(self.get(f"message.{name}", ""))

    # ---- 面板读写 ----
    def snapshot(self, mask_secrets: bool = True) -> dict[str, Any]:
        """返回可下发到前端的配置；密钥字段只回传是否已配置。"""
        data = copy.deepcopy(self.raw)
        if mask_secrets:
            present: dict[str, bool] = {}
            for path in SECRETS:
                section, key = path.split(".", 1)
                present[path] = bool(data.get(section, {}).get(key))
                data.setdefault(section, {})[key] = ""
            data["_secrets"] = present
        return data

    def apply(self, patch: dict[str, Any]) -> list[str]:
        """校验并写入面板提交的配置，返回被拒绝的字段说明。

        校验全部通过才落盘：只要有一项非法，整批改动都不生效，避免半套配置。
        """
        errors: list[str] = []
        flat_defaults = _flatten(DEFAULTS)
        submitted = _flatten(patch or {})
        pending: dict[str, Any] = {}
        for path, value in submitted.items():
            if path not in flat_defaults or path.startswith("_"):
                errors.append(f"未知配置项：{path}")
                continue
            if path in SECRETS and value == "":
                continue
            expected = flat_defaults[path]
            coerced, ok = _coerce(value, expected, path, errors)
            if not ok:
                continue
            if path in ENUMS and coerced not in ENUMS[path]:
                errors.append(f"{path} 只能是 {ENUMS[path]} 之一")
                continue
            if path in RANGES:
                low, high = RANGES[path]
                if not (low <= float(coerced) <= high):
                    errors.append(f"{path} 需在 {low}~{high} 之间")
                    continue
            pending[path] = coerced

        if not errors:
            errors.extend(_validate_commands(self.raw, pending))
        if errors:
            return errors

        for path, value in pending.items():
            _set_path(self.raw, path, value)
        return []


def _coerce(
    value: Any, expected: Any, path: str, errors: list[str]
) -> tuple[Any, bool]:
    if isinstance(expected, bool):
        if isinstance(value, bool):
            return value, True
        if isinstance(value, (int, float)):
            return bool(value), True
        if isinstance(value, str) and value.lower() in ("true", "false", "1", "0"):
            return value.lower() in ("true", "1"), True
        errors.append(f"{path} 需要布尔值")
        return None, False
    if isinstance(expected, int):
        try:
            return int(float(value)), True
        except (TypeError, ValueError):
            errors.append(f"{path} 需要整数")
            return None, False
    if isinstance(expected, float):
        try:
            return float(value), True
        except (TypeError, ValueError):
            errors.append(f"{path} 需要数字")
            return None, False
    if isinstance(expected, str):
        if not isinstance(value, str):
            errors.append(f"{path} 需要字符串")
            return None, False
        return value, True
    return value, True
