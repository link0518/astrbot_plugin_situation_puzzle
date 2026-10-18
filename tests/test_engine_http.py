"""引擎的 HTTP 层：配置容错与错误分类。

线上最常见的两种「主持人走神了」都由这里决定：地址尾斜杠跳转、模型名写错。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from astrbot_plugin_situation_puzzle.judge import jev_engine
from astrbot_plugin_situation_puzzle.judge.jev_engine import JevEngine, JevSettings
from astrbot_plugin_situation_puzzle.judge.prompts import AskInput
from astrbot_plugin_situation_puzzle.judge.protocol import ErrorClass, JudgeError

OK_BODY = {
    "answers": {
        "verdict": {
            "choice": "是",
            "probabilities": {"是": 0.92, "否": 0.05, "无关": 0.03},
        }
    },
    "usage": {"input_tokens": 10, "output_tokens": 3},
}


def make_engine(monkeypatch, handler, **overrides) -> JevEngine:
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(jev_engine.httpx, "AsyncClient", factory)
    settings = {
        "base_url": "https://api.example.com/v1/systemone",
        "api_key": "test-key",
        "model": "jev-1.13.0",
        "max_retries": 0,
    }
    settings.update(overrides)
    return JevEngine(JevSettings(**settings))


def ask(engine: JevEngine):
    payload = AskInput(title="t", surface="汤面", answer="真相", question="他活着吗")
    return asyncio.run(engine.ask(payload))


def test_settings_strip_slash_whitespace_and_bearer():
    settings = JevSettings(
        base_url="  https://api.example.com/v1/systemone/  ",
        api_key="  Bearer abc123  \n",
        model=" jev-1.13.0 ",
    )
    assert settings.base_url == "https://api.example.com/v1/systemone"
    assert settings.api_key == "abc123"
    assert settings.model == "jev-1.13.0"


def test_ask_success(monkeypatch):
    engine = make_engine(monkeypatch, lambda request: httpx.Response(200, json=OK_BODY))
    result = ask(engine)
    assert result.verdict == "yes"
    assert result.confidence == pytest.approx(0.92)
    assert result.engine == "jev"


def test_redirect_is_followed(monkeypatch):
    """地址少写/多写斜杠时服务端会 307，跟随跳转后仍要判题成功。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/"):
            return httpx.Response(200, json=OK_BODY)
        return httpx.Response(
            307, headers={"location": "https://api.example.com/v1/systemone/"}
        )

    engine = make_engine(monkeypatch, handler)
    assert ask(engine).verdict == "yes"


def test_unknown_model_is_config_error(monkeypatch):
    body = {
        "detail": {
            "error_type": "api_usage_error",
            "message": "Unknown model: jev-1.13",
        }
    }
    engine = make_engine(monkeypatch, lambda request: httpx.Response(400, json=body))
    with pytest.raises(JudgeError) as caught:
        ask(engine)
    assert caught.value.error_class is ErrorClass.CONFIG
    assert caught.value.retryable is False
    assert "Unknown model" in str(caught.value)


def test_not_found_is_config_error(monkeypatch):
    engine = make_engine(monkeypatch, lambda request: httpx.Response(404))
    with pytest.raises(JudgeError) as caught:
        ask(engine)
    assert caught.value.error_class is ErrorClass.CONFIG


def test_unprocessable_is_config_error(monkeypatch):
    engine = make_engine(
        monkeypatch, lambda request: httpx.Response(422, json={"detail": []})
    )
    with pytest.raises(JudgeError) as caught:
        ask(engine)
    assert caught.value.error_class is ErrorClass.CONFIG


def test_auth_error_keeps_class(monkeypatch):
    engine = make_engine(monkeypatch, lambda request: httpx.Response(401))
    with pytest.raises(JudgeError) as caught:
        ask(engine)
    assert caught.value.error_class is ErrorClass.AUTH
    assert caught.value.retryable is False


def test_server_error_is_retryable(monkeypatch):
    engine = make_engine(monkeypatch, lambda request: httpx.Response(503))
    with pytest.raises(JudgeError) as caught:
        ask(engine)
    assert caught.value.error_class is ErrorClass.UPSTREAM
    assert caught.value.retryable is True


def test_missing_probabilities_is_protocol_error(monkeypatch):
    body = {"answers": {"verdict": {"choice": "是"}}}
    engine = make_engine(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(JudgeError) as caught:
        ask(engine)
    assert caught.value.error_class is ErrorClass.PROTOCOL
