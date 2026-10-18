"""配置面板 schema 与代码默认值必须逐项一致，避免面板显示与实际行为漂移。"""

import json
from pathlib import Path

from astrbot_plugin_situation_puzzle.core.config import DEFAULTS

ROOT = Path(__file__).resolve().parents[1]


def flatten(node, prefix=""):
    flat = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, path))
        else:
            flat[path] = value
    return flat


def schema_defaults():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    flat = {}
    for key, node in schema.items():
        if node.get("type") == "object":
            for sub, definition in node["items"].items():
                flat[f"{key}.{sub}"] = definition.get("default")
        else:
            flat[key] = node.get("default")
    return schema, flat


def test_schema_keys_match_defaults():
    _, flat = schema_defaults()
    assert set(flat) == set(flatten(DEFAULTS))


def test_schema_default_values_match_defaults():
    _, flat = schema_defaults()
    for path, value in flatten(DEFAULTS).items():
        assert flat[path] == value, path


def test_schema_types_match_python_types():
    schema, _ = schema_defaults()
    expected = {bool: "bool", int: "int", float: "float", str: "string"}
    for key, node in schema.items():
        if node.get("type") != "object":
            continue
        for sub, definition in node["items"].items():
            default = definition.get("default")
            declared = definition.get("type")
            if declared == "text":
                assert isinstance(default, str), f"{key}.{sub}"
                continue
            assert expected[type(default)] == declared, f"{key}.{sub}"


def test_enum_options_cover_defaults():
    schema, _ = schema_defaults()
    for key, node in schema.items():
        if node.get("type") != "object":
            continue
        for sub, definition in node["items"].items():
            if "options" in definition:
                assert definition["default"] in definition["options"], f"{key}.{sub}"
