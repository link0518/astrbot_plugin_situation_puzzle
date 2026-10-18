"""判题协议：JSON 抽取、置信度校验、选项映射。"""

import pytest
from astrbot_plugin_situation_puzzle.judge.protocol import (
    ASK_CHOICES_ZH,
    ErrorClass,
    JudgeError,
    extract_json,
    map_choice,
    parse_confidence,
    read_answer,
)


def test_extract_plain_json():
    assert extract_json('{"choice": "是", "confidence": 0.9}')["choice"] == "是"


def test_extract_fenced_json():
    text = '判定如下：\n```json\n{"choice": "否", "confidence": 0.7}\n```\n以上。'
    assert extract_json(text)["confidence"] == 0.7


def test_extract_last_balanced_object():
    text = '先说明 {"a": 1} 然后给结论 {"choice": "无关", "confidence": 0.62}'
    assert extract_json(text)["choice"] == "无关"


def test_extract_invalid_raises_protocol():
    with pytest.raises(JudgeError) as error:
        extract_json("我不知道")
    assert error.value.error_class is ErrorClass.PROTOCOL


def test_parse_confidence_rejects_missing_and_out_of_range():
    for bad in (None, "0.8", True, -0.1, 1.5):
        with pytest.raises(JudgeError):
            parse_confidence(bad)
    assert parse_confidence(0.35) == 0.35


def test_map_choice_rejects_unknown():
    assert map_choice(ASK_CHOICES_ZH, "是") == "yes"
    with pytest.raises(JudgeError):
        map_choice(ASK_CHOICES_ZH, "大概是")


def test_read_answer_requires_probabilities():
    payload = {"answers": {"verdict": {"choice": "是"}}}
    with pytest.raises(JudgeError) as error:
        read_answer(payload, "verdict")
    assert "probabilities" in str(error.value)


def test_read_answer_reads_matching_probability():
    payload = {
        "answers": {
            "verdict": {
                "choice": "否",
                "probabilities": {"是": 0.1, "否": 0.8, "无关": 0.1},
            }
        }
    }
    assert read_answer(payload, "verdict") == ("否", 0.8)
