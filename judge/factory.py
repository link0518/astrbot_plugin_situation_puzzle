"""判题引擎工厂：按配置装配具体引擎。"""

from __future__ import annotations

from typing import Any

from ..core.config import PluginConfig
from .base import JudgeEngine
from .jev_engine import JevEngine, JevSettings
from .provider_engine import ProviderEngine, ProviderSettings


def build_engine(
    config: PluginConfig, context: Any, provider_id: str | None = None
) -> JudgeEngine:
    """引擎无状态，配置变更后重建即可生效。"""
    language = config.language
    if config.engine == "provider":
        return ProviderEngine(
            context,
            ProviderSettings(
                provider_id=str(
                    provider_id
                    if provider_id is not None
                    else config.get("judge.provider_id", "") or ""
                ),
                threshold=config.threshold,
                language=language,
                total_deadline_ms=config.total_deadline_ms,
                max_retries=config.max_retries,
                self_consistency=config.self_consistency,
            ),
        )
    return JevEngine(
        JevSettings(
            base_url=str(config.get("judge.jev_base_url", "") or ""),
            api_key=str(config.get("judge.jev_api_key", "") or ""),
            model=str(config.get("judge.jev_model", "jev-1.13") or "jev-1.13"),
            threshold=config.threshold,
            language=language,
            attempt_timeout_ms=config.attempt_timeout_ms,
            total_deadline_ms=config.total_deadline_ms,
            max_retries=config.max_retries,
        )
    )
