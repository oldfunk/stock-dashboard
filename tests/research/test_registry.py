"""Research OS 注册表回归测试（R0-3）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "reg.db"))
    db_mod.init_database()
    return db_mod


def _feature(**over):
    d = {"feature_id": "F-ROE", "version": 1, "name": "ROE",
         "status": "active", "formula": "net_profit/equity"}
    d.update(over)
    return d


def _hypothesis(**over):
    d = {"id": "H-20261026-001", "version": 1, "prior": "value",
         "statement": "x", "universe": "A", "signal": {"rank": "mos"},
         "horizon_days": 126, "benchmark": "sh000300", "metric": "excess",
         "threshold": ">0", "regimes": ["all"], "costs": {"c": 0.1},
         "kill_criteria": ["k"], "status": "locked"}
    d.update(over)
    return d


def test_feature_register_get_and_duplicate(db):
    from src.research.registry import FeatureRegistry, RegistryError
    reg = FeatureRegistry()
    reg.register(_feature())
    assert reg.get("F-ROE", 1)["name"] == "ROE"
    with pytest.raises(RegistryError):
        reg.register(_feature())
    with pytest.raises(RegistryError):
        reg.register(_feature(feature_id="F-X", name=""))
    with pytest.raises(RegistryError):
        reg.register(_feature(feature_id="F-Y", status="bogus"))


def test_hypothesis_register_get_and_validation(db):
    from src.research.registry import HypothesisRegistry, RegistryError
    reg = HypothesisRegistry()
    reg.register(_hypothesis())
    assert reg.get("H-20261026-001")["prior"] == "value"
    with pytest.raises(RegistryError):
        reg.register(_hypothesis())
    with pytest.raises(RegistryError):
        reg.register(_hypothesis(id="H-20261026-002", statement=""))
    with pytest.raises(RegistryError):
        reg.register(_hypothesis(id="H-20261026-003", status="unknown"))


def test_strategy_and_prompt_registry(db):
    from src.research.registry import (
        PromptRegistry, RegistryError, StrategyRegistry,
    )
    sreg = StrategyRegistry()
    sreg.register({"id": "S-VALUE-v1", "version": 1,
                   "spec": {"topk": 20}, "status": "draft"})
    assert sreg.get("S-VALUE-v1")["spec"] == {"topk": 20}
    with pytest.raises(RegistryError):
        sreg.register({"id": "S-VALUE-v1", "version": 1,
                       "spec": {}, "status": "draft"})
    with pytest.raises(RegistryError):
        sreg.register({"id": "S-X", "version": 1, "spec": {},
                       "status": "live"})

    preg = PromptRegistry()
    preg.register({"prompt_id": "market_summary", "version": 1,
                   "task": "market_summary", "template_hash": "h",
                   "status": "active", "tools": ["get_market_state"],
                   "max_tokens": 2000})
    assert preg.get("market_summary", 1)["tools"] == ["get_market_state"]
    with pytest.raises(RegistryError):
        preg.register({"prompt_id": "market_summary", "version": 1,
                       "task": "market_summary", "template_hash": "h",
                       "status": "active"})
    with pytest.raises(RegistryError):
        preg.register({"prompt_id": "p2", "version": 1, "task": "t",
                       "template_hash": "h", "status": "active",
                       "max_tokens": "2000"})


def test_registries_bundle(db):
    from src.research.registry import ResearchRegistries
    regs = ResearchRegistries()
    regs.feature.register(_feature())
    regs.hypothesis.register(_hypothesis())
    regs.strategy.register({"id": "S-1", "version": 1, "spec": {"topk": 20},
                            "status": "draft"})
    regs.prompt.register({"prompt_id": "p", "version": 1, "task": "t",
                          "template_hash": "h", "status": "active"})
    assert regs.feature.get("F-ROE", 1)
    assert regs.hypothesis.get("H-20261026-001")
    assert regs.strategy.get("S-1")
    assert regs.prompt.get("p", 1)


def test_empty_required_collections_rejected(db):
    """回归 G1：空 signal/regimes/kill_criteria/spec 视为未定义，必须拒绝。"""
    from src.research.registry import (
        HypothesisRegistry, RegistryError, StrategyRegistry,
    )
    hreg = HypothesisRegistry()
    for over in ({"signal": {}}, {"regimes": []}, {"kill_criteria": []}):
        with pytest.raises(RegistryError):
            hreg.register(_hypothesis(id="H-20261026-009", **over))
    # 空 costs 是合理的（无费用假设）
    hreg.register(_hypothesis(id="H-20261026-012", costs={}))
    sreg = StrategyRegistry()
    with pytest.raises(RegistryError):
        sreg.register({"id": "S-EMPTY", "version": 1, "spec": {},
                       "status": "draft"})
