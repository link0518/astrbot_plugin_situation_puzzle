"""指令映射：主指令与子指令都能改词，改完立刻按新词解析。"""

from astrbot_plugin_situation_puzzle.core.config import PluginConfig
from astrbot_plugin_situation_puzzle.core.router import (
    DEFAULT_COMMAND_ALIASES,
    SUBCOMMAND_KEYS,
    Action,
    classify,
    command_hint,
    normalize_subcommands,
    parse_aliases,
)


def test_parse_aliases_accepts_mixed_separators():
    assert parse_aliases("提示, 线索、给点提示") == ("提示", "线索", "给点提示")
    assert parse_aliases(["汤", "汤", " turtle "]) == ("汤", "turtle")
    assert parse_aliases(None) == ()
    assert parse_aliases("   ") == ()


def test_normalize_fills_missing_groups_from_defaults():
    subs = normalize_subcommands({"hint": "线索"})
    assert subs["hint"] == ("线索",)
    assert subs["reveal"] == ("揭晓", "看答案", "答案", "reveal")
    assert set(subs) == set(SUBCOMMAND_KEYS)


def test_custom_command_name():
    decision = classify("/线索 提示", has_room=True, command_names=("线索", "js"))
    assert decision.action is Action.COMMAND
    assert decision.command == "线索"
    assert decision.subcommand == "hint"
    # 旧写法不再命中
    assert (
        classify("/汤 提示", has_room=True, command_names=("线索",)).action
        is Action.IGNORE
    )


def test_custom_subcommand_words():
    subs = {"hint": ("给点线索",), "reveal": ("看底牌",)}
    decision = classify("/汤 给点线索", has_room=True, subcommands=subs)
    assert decision.subcommand == "hint"
    assert (
        classify("/汤 看底牌", has_room=True, subcommands=subs).subcommand == "reveal"
    )
    # 改词后旧词不再是子指令，也不会被当成开局参数
    unknown = classify("/汤 提示", has_room=True, subcommands=subs)
    assert unknown.action is Action.COMMAND
    assert unknown.subcommand == ""


def test_longest_alias_wins():
    subs = {"answer": ("答", "答案"), "reveal": ("揭晓",)}
    decision = classify("/汤 答案", has_room=True, subcommands=subs)
    assert decision.subcommand == "answer"
    assert decision.argument == ""


def test_subcommand_argument_after_colon():
    decision = classify("/汤 答：她是自杀", has_room=True)
    assert decision.subcommand == "answer"
    assert decision.argument == "她是自杀"


def test_empty_group_disables_subcommand():
    subs = {"skip": ""}
    decision = classify("/汤 跳过", has_room=True, subcommands=subs)
    assert decision.action is Action.COMMAND
    assert decision.subcommand == ""


def test_start_accepts_difficulty_and_id_only():
    assert classify("/汤 easy", has_room=False).subcommand == "start"
    assert classify("/汤 #p-12", has_room=False).argument == "#p-12"
    assert classify("/汤", has_room=False).subcommand == "start"
    assert classify("/汤 随便聊聊", has_room=False).subcommand == ""


def test_command_hint_follows_style():
    names = ("海龟汤",)
    assert command_hint(names, "both") == "/海龟汤"
    assert command_hint(names, "slash") == "/海龟汤"
    assert command_hint(names, "text") == "海龟汤"
    assert command_hint((), "both") == f"/{DEFAULT_COMMAND_ALIASES[0]}"


def test_config_apply_writes_nested_subcommands():
    config = PluginConfig({})
    assert config.apply({"basic": {"command_names": "海龟汤,ts"}}) == []
    assert config.command_names == ("海龟汤", "ts")

    assert config.apply({"basic": {"sub_commands": {"hint": "线索,给点提示"}}}) == []
    assert config.subcommands["hint"] == ("线索", "给点提示")
    # 落盘是嵌套 dict，不能出现 "sub_commands.hint" 这种字面键
    assert isinstance(config.raw["basic"]["sub_commands"], dict)
    assert "sub_commands.hint" not in config.raw["basic"]


def test_config_rejects_empty_command_names():
    config = PluginConfig({})
    errors = config.apply({"basic": {"command_names": " , "}})
    assert errors
    assert config.command_names == DEFAULT_COMMAND_ALIASES


def test_config_rejects_conflicting_alias():
    config = PluginConfig({})
    errors = config.apply(
        {"basic": {"sub_commands": {"hint": "提示", "reveal": "提示"}}}
    )
    assert errors and "同时映射" in errors[0]
    # 整批不生效
    assert config.subcommands["hint"] == ("提示", "线索", "hint", "h")


def test_config_rejects_slash_in_alias():
    config = PluginConfig({})
    assert config.apply({"basic": {"command_names": "/汤"}})
    assert config.apply({"basic": {"sub_commands": {"hint": "/提示"}}})


def test_config_accepts_cleared_group():
    config = PluginConfig({})
    assert config.apply({"basic": {"sub_commands": {"skip": ""}}}) == []
    assert config.subcommands["skip"] == ()
