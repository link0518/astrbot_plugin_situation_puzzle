"""防剧透：除了揭晓与破解成功，任何模板都不能出现汤底。"""

from astrbot_plugin_situation_puzzle.core.config import PluginConfig
from astrbot_plugin_situation_puzzle.core.templates import render, split_message

ANSWER = (
    "父亲七年前去世，幽灵车手是他留下的最快圈速记录，我为了不覆盖它而停在终点线前。"
)


def test_non_reveal_templates_never_contain_answer():
    config = PluginConfig()
    names = ["tpl_start", "tpl_verdict", "tpl_uncertain", "tpl_close", "tpl_not_yet"]
    for name in names:
        text = render(
            config.template(name),
            title="终点线",
            story="汤面",
            host="Jev",
            question="他赢了吗？",
            verdict="否",
            answer=ANSWER,
            source=ANSWER,
        )
        assert ANSWER not in text, name


def test_reveal_and_solved_templates_do_contain_answer():
    config = PluginConfig()
    for name in ("tpl_reveal", "tpl_solved"):
        assert ANSWER in render(
            config.template(name), answer=ANSWER, source="", solved_by="小明", rounds=3
        )


def test_unknown_placeholders_do_not_leak():
    assert render("结果：{unknown}", answer=ANSWER) == "结果："


def test_split_message_preserves_content_and_limit():
    text = "第一段。" * 200 + "\n\n" + "第二段。" * 200
    chunks = split_message(text, 500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "第二段。" in "".join(chunks)


def test_split_message_keeps_short_text_whole():
    assert split_message("很短的一句话", 500) == ["很短的一句话"]
