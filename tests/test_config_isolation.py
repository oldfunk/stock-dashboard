"""`load_config()` 返回值与进程级缓存的隔离性（审计 N15，2026-10-29 员工批5）。

旧实现 `return dict(_config_cache)` 只复制顶层，嵌套段仍与缓存同一对象，
而注释声称「返回副本，防止外部修改」——浅拷贝做不到这件事。
调度器 / AI 队列 / Web 三条线程都走 load_config()，任一处就地改嵌套值
即串改全进程配置。
"""

import os

import pytest

import src.config as cfgmod


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    conf = tmp_path / "config.yaml"
    conf.write_text(
        "ai:\n  temperature: 0.3\n  workers: 2\n"
        "screening:\n  max_candidates: 20\n", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_CONFIG_PATH", conf)
    monkeypatch.setattr(cfgmod, "_LOCAL_PATH", tmp_path / "local.yaml")
    monkeypatch.setattr(cfgmod, "_config_cache", {})
    monkeypatch.setattr(cfgmod, "_config_mtime", 0.0)
    return conf


def test_nested_mutation_does_not_leak_into_cache(tmp_config):
    first = cfgmod.load_config()
    first["ai"]["temperature"] = 999.0          # 调用方就地改嵌套段
    first["screening"]["max_candidates"] = 1
    second = cfgmod.load_config()
    assert second["ai"]["temperature"] == 0.3
    assert second["screening"]["max_candidates"] == 20
    assert cfgmod._config_cache["ai"]["temperature"] == 0.3, "外部改动穿到了进程级缓存"


def test_two_callers_do_not_share_nested_dict(tmp_config):
    a = cfgmod.load_config()
    b = cfgmod.load_config()
    assert a["ai"] is not b["ai"], "两次 load 拿到同一个嵌套对象"
    a["ai"]["workers"] = 50
    assert b["ai"]["workers"] == 2


def test_top_level_key_addition_isolated(tmp_config):
    first = cfgmod.load_config()
    first["injected"] = True
    assert "injected" not in cfgmod.load_config()


def test_hot_reload_still_applies(tmp_config):
    assert cfgmod.load_config()["ai"]["temperature"] == 0.3
    tmp_config.write_text("ai:\n  temperature: 0.9\n", encoding="utf-8")
    # 显式推 mtime，避免同秒写入导致重载判据不触发（flaky）
    st = tmp_config.stat()
    os.utime(tmp_config, ns=(st.st_atime_ns + 10 ** 9, st.st_mtime_ns + 10 ** 9))
    assert cfgmod.load_config()["ai"]["temperature"] == 0.9
