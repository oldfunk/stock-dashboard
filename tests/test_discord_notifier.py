"""Discord 通知器初始化不吞中断信号（批8 P1-3，2026-10-29）。

`get_discord_notifier()` 里读 `.env` 那段原本是裸 `except: pass`。本模块被
`scheduler.py:651` 在调度线程中调用，裸 except 会把 `KeyboardInterrupt` /
`SystemExit` 一起吞掉——服务停不干净且日志里毫无线索。
"""

import configparser

import pytest

from src.notifications.discord_notifier import get_discord_notifier


@pytest.fixture(autouse=True)
def no_webhook_env(monkeypatch):
    """确保走"读配置文件"分支，而不是直接吃环境变量。"""
    monkeypatch.delenv("STOCK_DISCORD_WEBHOOK_URL", raising=False)


def _boom(exc):
    def read(self, *a, **k):
        raise exc
    return read


def test_systemexit_is_not_swallowed(monkeypatch):
    """回归 P1-3：SystemExit 必须穿出，而不是被当成"读配置失败"返回 None。"""
    monkeypatch.setattr(configparser.ConfigParser, "read",
                        _boom(SystemExit(3)), raising=True)
    with pytest.raises(SystemExit):
        get_discord_notifier()


def test_keyboardinterrupt_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(configparser.ConfigParser, "read",
                        _boom(KeyboardInterrupt()), raising=True)
    with pytest.raises(KeyboardInterrupt):
        get_discord_notifier()


def test_ordinary_config_error_still_degrades_to_none(monkeypatch):
    """真实失败（坏配置）仍按原行为降级返回 None，本件不改这个语义。"""
    monkeypatch.setattr(configparser.ConfigParser, "read",
                        _boom(configparser.Error("bad .env")), raising=True)
    assert get_discord_notifier() is None
