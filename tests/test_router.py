"""三通道消息路由：指令优先、不算闲聊、无房时关键词自动开局。"""

from astrbot_plugin_situation_puzzle.core.router import Action, classify


def test_command_variants():
    for text in (
        "/汤",
        "汤",
        "/海龟汤 提示",
        "/turtle 状态",
        "sp 排行",
        "/汤 答 他是自杀",
    ):
        decision = classify(text, has_room=False)
        assert decision.action is Action.COMMAND, text


def test_command_wins_over_keyword():
    decision = classify("/汤 提示", has_room=True, at_bot=True)
    assert decision.action is Action.COMMAND
    assert decision.subcommand == "提示"


def test_at_bot_requires_active_room():
    assert (
        classify("她是自杀吗？", has_room=True, at_bot=True).action is Action.QUESTION
    )
    assert classify("她是自杀吗？", has_room=False, at_bot=True).action is Action.IGNORE


def test_reply_bot_requires_active_room():
    assert classify("她还好吗", has_room=True, reply_bot=True).action is Action.QUESTION
    assert classify("她还好吗", has_room=False, reply_bot=True).action is Action.IGNORE


def test_keyword_prefix_starts_game_without_room():
    decision = classify("海龟 她真的死在船上吗？", has_room=False, prefix="海龟")
    assert decision.action is Action.START_QUESTION
    assert decision.text == "她真的死在船上吗？"


def test_keyword_prefix_asks_inside_room():
    decision = classify("海龟：凶手是熟人吗", has_room=True, prefix="海龟")
    assert decision.action is Action.QUESTION
    assert decision.text == "凶手是熟人吗"


def test_keyword_without_space_is_ignored():
    assert (
        classify("海龟汤真好玩", has_room=True, prefix="海龟").action is Action.IGNORE
    )


def test_plain_chat_is_never_eaten():
    for text in ("今天天气不错", "在吗", "哈哈哈哈"):
        assert classify(text, has_room=True, prefix="海龟").action is Action.IGNORE


def test_empty_message_ignored():
    assert classify("   ", has_room=True, at_bot=True).action is Action.IGNORE


def test_custom_prefix():
    decision = classify("汤面 他是好人吗", has_room=True, prefix="汤面")
    assert decision.action is Action.QUESTION
    assert decision.text == "他是好人吗"
