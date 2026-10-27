"""实验计划 CLI 回归测试（R2-2 预注册）。"""

import json

import pytest


def _create(**overrides):
    from src.research.experiment import main
    args = {
        "hypothesis": "H-20261026-001",
        "snapshot": "DS-FIXTURE",
        "dataset": "kline_daily",
        "metric": "mean_excess",
        "threshold": ">0",
        "horizon-days": 5,
    }
    args.update(overrides)
    argv = ["create"]
    for key, value in args.items():
        argv += [f"--{key}", str(value)]
    return main(argv)


def _last_line(capsys) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_create_binds_and_registers_planned(experiment_fixture, capsys):
    """create 落 planned，绑定假设/快照/指标/分域/费用/参数（预注册）。"""
    from src.research.dao import ResearchDAO
    rc = _create(**{
        "regimes": "risk_on,neutral",
        "costs": '{"slippage": 0.001}',
        "params": '{"start_date": "2026-01-05", "end_date": "2026-01-30"}',
        "seed": 7,
    })
    assert rc == 0
    eid = _last_line(capsys)
    row = ResearchDAO().get_experiment(eid)
    assert row["status"] == "planned"
    assert row["hypothesis_id"] == "H-20261026-001"
    assert row["data_snapshot_id"] == "DS-FIXTURE"
    assert row["random_seed"] == 7
    plan = row["plan"]
    assert plan["metric"] == "mean_excess"
    assert plan["threshold"] == ">0"
    assert plan["horizon_days"] == 5
    assert plan["regimes"] == ["risk_on", "neutral"]
    assert plan["costs"] == {"slippage": 0.001}
    assert plan["params"]["start_date"] == "2026-01-05"
    assert plan["dataset"] == "kline_daily"


def test_create_rejects_missing_hypothesis(experiment_fixture):
    from src.research.experiment import ExperimentError
    with pytest.raises(ExperimentError, match="假设不存在"):
        _create(**{"hypothesis": "H-NOPE"})


def test_create_rejects_missing_snapshot(experiment_fixture):
    """快照缺失/漂移：注册即拒绝（fail-fast，不留到运行时）。"""
    from src.research.snapshot import SnapshotError
    with pytest.raises(SnapshotError):
        _create(**{"snapshot": "DS-NOPE"})


def test_create_rejects_duplicate_id(experiment_fixture):
    """预注册不可覆盖：同 ID 拒绝（事后不可篡改）。"""
    from src.research.experiment import ExperimentError
    _create(**{"id": "E-20990101-001"})
    with pytest.raises(ExperimentError, match="已存在"):
        _create(**{"id": "E-20990101-001"})


def test_create_rejects_bad_threshold_and_horizon(experiment_fixture):
    from src.research.experiment import ExperimentError
    with pytest.raises(ExperimentError, match="threshold"):
        _create(**{"threshold": "高"})          # 无操作符 -> 解析失败
    with pytest.raises(ExperimentError, match="horizon_days"):
        _create(**{"horizon-days": 0})


def test_view_and_list(experiment_fixture, capsys):
    from src.research.experiment import main
    _create()
    eid = capsys.readouterr().out.strip().splitlines()[-1]
    assert main(["view", eid]) == 0
    out = capsys.readouterr().out          # 多行 JSON，整段解析
    assert json.loads(out)["id"] == eid
    assert main(["list", "--status", "planned"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert any(line.startswith(eid) for line in lines)
    # view 不存在 -> 报错
    from src.research.experiment import ExperimentError
    with pytest.raises(ExperimentError, match="不存在"):
        main(["view", "E-NOPE-001"])
