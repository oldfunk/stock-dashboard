"""研究助手设置：默认开启、inherit/custom、Key 脱敏。"""
from __future__ import annotations

from src import llm_config


def test_research_default_enabled_inherit(tmp_path):
    st = llm_config.research_status(tmp_path / "local.yaml")
    assert st["enabled"] is True
    assert st["mode"] == "inherit"
    assert st["has_key"] is False


def test_save_custom_and_key_masked(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    local = tmp_path / "local.yaml"
    monkeypatch.delenv(llm_config.RESEARCH_ENV_KEY, raising=False)
    st = llm_config.save_research_config(
        enabled=True, mode="custom", provider="deepseek",
        api_base="https://api.deepseek.com/v1", model="deepseek-chat",
        api_key="sk-secret-123456", temperature=0.2, max_tokens=8000,
        env_path=env, local_path=local)
    assert st["mode"] == "custom" and st["has_key"] is True
    assert st["key_preview"].startswith("sk-s") and "secret" not in st["key_preview"]
    text = env.read_text(encoding="utf-8")
    assert "STOCK_RESEARCH_AI_API_KEY=sk-secret-123456" in text
    again = llm_config.research_status(local)
    assert again["model"] == "deepseek-chat"


def test_disable_keeps_config(tmp_path):
    local = tmp_path / "local.yaml"
    llm_config.save_research_config(enabled=False, mode="inherit",
                                    local_path=local)
    assert llm_config.research_status(local)["enabled"] is False
