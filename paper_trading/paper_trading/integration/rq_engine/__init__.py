"""rqalpha 适配层（M2 交易执行底座）。

边界（AGENTS 引擎教条 + `refactor-plan` §5.4）：**撮合/账本/T+1/涨跌停判定全在
rqalpha 内**，本包只做三件事——供数据（原始价 + 涨跌停带）、供状态持久化
（rqalpha 开源发行里缺的那块）、把一天的模拟会话用固定门禁跑起来。

导入纪律：产品 venv（NEC）**不装 rqalpha**。所以除 `data_source.py` / `runner.py`
之外，本包所有模块都**不 import rqalpha**（store 与 provider 走鸭子类型——
rqalpha 实际只要求 `get_bars/get_date_range` 与 `store/load` 存在）。
`rq_alpha_available()` 供上层判断能否真跑。
"""
from __future__ import annotations

from .bands import band_for, round_tick
from .provider import FilePersistProvider, SessionGate, config_fingerprint
from .store import BandMissingError, RawDayBarStore, to_order_book_id

__all__ = ["rq_alpha_available", "band_for", "round_tick", "RawDayBarStore",
           "BandMissingError", "to_order_book_id", "FilePersistProvider",
           "SessionGate", "config_fingerprint"]


def rq_alpha_available() -> bool:
    try:
        import rqalpha  # noqa: F401
    except Exception:               # noqa: BLE001 - 没装就是没装
        return False
    return True
