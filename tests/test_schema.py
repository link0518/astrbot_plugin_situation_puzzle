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


def iter_defs(items, prefix=""):
    """递归展开 schema 的 items，嵌套 object 也能取到叶子定义。"""
    for key, definition in items.items():
        path = f"{prefix}.{key}" if prefix else key
        if definition.get("type") == "object" and isinstance(
            definition.get("items"), dict
        ):
            yield from iter_defs(definition["items"], path)
        else:
            yield path, definition


def schema_defaults():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    flat = {path: definition.get("default") for path, definition in iter_defs(schema)}
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
    for path, definition in iter_defs(schema):
        default = definition.get("default")
        declared = definition.get("type")
        if declared == "text":
            assert isinstance(default, str), path
            continue
        assert expected[type(default)] == declared, path


def test_enum_options_cover_defaults():
    schema, _ = schema_defaults()
    for path, definition in iter_defs(schema):
        if "options" in definition:
            assert definition["default"] in definition["options"], path


def test_legacy_model_name_is_migrated():
    """旧版本的模型名加载时归位到新默认值，自定义地址与模型不动。"""
    from astrbot_plugin_situation_puzzle.core.config import PluginConfig

    legacy = PluginConfig({"judge": {"jev_model": "jev-1.13"}})
    assert legacy.get("judge.jev_model") == DEFAULTS["judge"]["jev_model"]

    custom = PluginConfig(
        {
            "judge": {
                "jev_base_url": "https://my-proxy.example/v1/systemone",
                "jev_model": "d1-ultra",
            }
        }
    )
    assert custom.get("judge.jev_base_url") == "https://my-proxy.example/v1/systemone"
    assert custom.get("judge.jev_model") == "d1-ultra"
