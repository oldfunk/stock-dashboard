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


@pytest.fixture(autouse=True)
def _no_external_news(monkeypatch):
    """新闻抓取一律打桩：单测永不碰真实 akshare/网络。

    `run()` → `llm_context` → `fetch_market_news`，网络受限环境下会 stall
    挂死（母项目 gate 因此卡死，Issue #1）。这里把它换成固定回空的桩。
    `test_news.py` 要测真实实现，它自己的 autouse fixture 会还原真函数。
    """
    from paper_trading.data import news as nw

    def _offline(limit=12, force=False, timeout_sec=15.0):
        return {"ok": False, "available": False, "error": "offline in tests"}

    monkeypatch.setattr(nw, "fetch_market_news", _offline)