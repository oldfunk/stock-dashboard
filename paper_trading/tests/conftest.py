"""全量单测的环境隔离（hermetic 铁律）。

每个用例切到独立临时目录：避免读到仓库根的真实配置/本地状态
（如 `strategy.local.yaml` 的定时队列、`config.yaml`），使单测在
任何机器、任何本地配置下结果一致。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    """切到本用例的临时目录：仓库根的本地配置不会泄漏进单测。"""
    workdir = tmp_path / "cwd"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workdir)