import sys
from pathlib import Path

import pytest

# 把项目根目录加入 sys.path，确保 pytest 能找到 src 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _block_async_onboard(request, monkeypatch):
    """测试默认不启动后台联网 onboard。

    为什么：`/stock/{code}` 与 `POST /api/watchlist/{code}` 在缺数据时会 spawn
    守护线程，线程内按**当时**的 db 路径写快照；用例切换 tmp 库后，旧线程可能
    写进下一个用例的库（2026-11-28 gate 实测：钉选卡 pe=12 被后台写入的 21.18
    覆盖）。测试不联网、不跨用例写库，默认拦住；要验证线程行为的用例加
    `@pytest.mark.allow_onboard`。
    """
    if request.node.get_closest_marker("allow_onboard"):
        return
    from src.collector import onboard
    monkeypatch.setattr(onboard, "onboard_stock_async",
                        lambda *a, **k: None)
