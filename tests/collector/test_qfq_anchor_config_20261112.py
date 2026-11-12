"""批20-c：`config/config.yaml` 的 `qfq_anchor` 段真的驱动预算（2026-11-12）。

派工单要求"配置改动不需要改代码"。所以这里不只断言"段存在"，而是**改一份临时
yaml → load_config → 入口拿到的预算跟着变**，把整条链路走通；另钉出厂默认值
本身是安全的（限量/限时都 > 0，不会一上来就全市场 5,514 只 × 2 拉）。
"""

import pytest

from src.collector import qfq_anchor as qa


def test_shipped_config_section_is_present_and_bounded():
    """出厂配置必须有这一段，且限量/限时都是正数（否则等于没有预算）。

    只钉"存在 + 有界"，不钉具体数值：派工单要的就是母方能自行调批大小，
    把 60/900 写死进用例会让调配置变成改代码。
    """
    from src.config import load_config
    conf = load_config().get("qfq_anchor")
    assert isinstance(conf, dict), "config.yaml 缺 qfq_anchor 段"
    assert set(conf) >= {"days", "batch", "max_seconds", "sleep",
                         "max_consecutive_failures"}
    assert conf["days"] > 0 and conf["batch"] > 0
    assert conf["max_seconds"] > 0 and conf["sleep"] >= 0
    assert conf["max_consecutive_failures"] > 0


def test_editing_yaml_changes_budget_without_touching_code(tmp_path, monkeypatch):
    """改配置即改行为：临时 yaml 写什么，入口就拿到什么。"""
    import src.config as cmod
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "qfq_anchor:\n  days: 30\n  batch: 5\n  max_seconds: 60\n"
        "  sleep: 0\n  max_consecutive_failures: 2\n", encoding="utf-8")
    monkeypatch.setattr(cmod, "_CONFIG_PATH", yaml_path)
    monkeypatch.setattr(cmod, "_LOCAL_PATH", tmp_path / "absent.yaml")
    monkeypatch.setattr(cmod, "_config_cache", {})
    monkeypatch.setattr(cmod, "_config_mtime", 0)

    seen = {}
    monkeypatch.setattr(qa, "run", lambda **kw: seen.update(kw) or {"done": 0})
    qa.run_weekly_qfq_anchor(cmod.load_config())
    assert seen == {"days": 30, "limit": 5, "max_seconds": 60.0, "sleep": 0.0,
                    "max_consecutive_failures": 2}

    # 再改一次同一份文件（热重载路径），预算必须跟着变，不需要改代码
    yaml_path.write_text(
        "qfq_anchor:\n  days: 120\n  batch: 9\n  max_seconds: 300\n"
        "  sleep: 0.5\n  max_consecutive_failures: 4\n", encoding="utf-8")
    import os
    st = yaml_path.stat()
    os.utime(yaml_path, (st.st_atime, st.st_mtime + 10))
    seen.clear()
    qa.run_weekly_qfq_anchor(cmod.load_config())
    assert seen == {"days": 120, "limit": 9, "max_seconds": 300.0, "sleep": 0.5,
                    "max_consecutive_failures": 4}


def test_batch_zero_in_yaml_really_disables_the_stage(tmp_path, monkeypatch):
    """上游限流时的让路开关：yaml 写 0 就一段都不跑（与 em_batch=0 同约定）。"""
    import src.config as cmod
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("qfq_anchor:\n  batch: 0\n", encoding="utf-8")
    monkeypatch.setattr(cmod, "_CONFIG_PATH", yaml_path)
    monkeypatch.setattr(cmod, "_LOCAL_PATH", tmp_path / "absent.yaml")
    monkeypatch.setattr(cmod, "_config_cache", {})
    monkeypatch.setattr(cmod, "_config_mtime", 0)
    called = []
    monkeypatch.setattr(qa, "run", lambda **kw: called.append(kw) or {})
    out = qa.run_weekly_qfq_anchor(cmod.load_config())
    assert called == [] and out["skipped_by_config"] is True


@pytest.mark.parametrize("key", ["days", "batch", "max_seconds", "sleep",
                                 "max_consecutive_failures"])
def test_each_key_alone_falls_back_to_its_default(monkeypatch, key):
    """只写一个键时，其余键各回自己的默认值（不是整段一起失效）。"""
    seen = {}
    monkeypatch.setattr(qa, "run", lambda **kw: seen.update(kw) or {"done": 0})
    qa.run_weekly_qfq_anchor({"qfq_anchor": {key: 1}})
    defaults = {"days": 90, "limit": 60, "max_seconds": 900.0, "sleep": 1.0,
                "max_consecutive_failures": 5}
    mapped = {"batch": "limit"}.get(key, key)
    for k, v in defaults.items():
        assert seen[k] == (1 if k == mapped else v), f"{k} 的默认值被带坏了"