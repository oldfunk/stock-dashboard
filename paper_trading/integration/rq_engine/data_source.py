"""rqalpha 数据源装配（**需要安装 rqalpha**，产品 venv 默认不装）。

两件事：
1. 热替换 CS 日线 store（`BaseDataSource.register_day_bar_store`，
   `data_source.py:143-144` 官方扩展点）——静态元数据（交易日历/合约/分红/拆股/
   复权因子/停牌/ST）继续来自 bundle，日线原始价来自我们的 `daily_bars_raw`；
2. 覆盖 `available_data_range`：基类只问 INDX store 的 `000001.XSHG`
   （`data_source.py:406-414`），bundle 落后时"今日"会被 `main.py:52-64` 夹掉——
   实测报错文案把人往"换回测区间"上带，真实原因是指数日线缺失（report-t13 §4.6）。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from rqalpha.const import INSTRUMENT_TYPE, MARKET
from rqalpha.data.base_data_source.data_source import BaseDataSource

from .store import RawDayBarStore


class PaperDataSource(BaseDataSource):
    def __init__(self, base_config, store: RawDayBarStore,
                 override_available_range: bool = True,
                 minute: bool = False) -> None:
        super().__init__(base_config, market=MARKET.CN)
        self._pt_store = store
        self._override = override_available_range
        self._minute = minute
        self.register_day_bar_store(INSTRUMENT_TYPE.CS, store)

    def get_bar(self, instrument, dt, frequency):
        """M3b：开源 BaseDataSource 对 `frequency != '1d'` 直接
        NotImplementedError（`base_data_source/data_source.py:232`，分钟查询
        只在商业版 rqdatac 数据源里）——子类覆盖为从我们的分钟 store 查询
        （`datetime <= dt` 的最后一根，分钟 bar 的标准 ≤ 语义，返回 numpy 行
        与基类一致，由 data_proxy 包 BarObject）。日线走基类。"""
        if frequency == "1d" or not self._minute:
            return super().get_bar(instrument, dt, frequency)
        import numpy as np
        from rqalpha.utils.datetime_func import convert_dt_to_int
        bars = self._pt_store.get_bars(instrument.order_book_id)
        if bars is None or len(bars) == 0:
            return None
        dt_int = convert_dt_to_int(dt)
        i = int(np.searchsorted(bars["datetime"], dt_int, side="right")) - 1
        if i < 0:
            return None
        return bars[i]

    def available_data_range(self, frequency):
        if not self._override:
            return super().available_data_range(frequency)
        first = self._pt_store.first_date() if hasattr(self._pt_store, "first_date") else None
        return (first or self._pt_store.today, self._pt_store.today)


def bundle_price_last(bundle_path: str | Path, order_book_id: str) -> Optional[date]:
    """bundle 里该标的最后一根日线日期（行情覆盖末日）。"""
    import h5py

    p = Path(bundle_path) / "stocks.h5"
    if not p.exists():
        return None
    with h5py.File(p, "r") as h5:
        if order_book_id not in h5:
            return None
        dt = h5[order_book_id]["datetime"]
        if len(dt) == 0:
            return None
        return _d_from_int(int(dt[-1]))


def bundle_factor_last(bundle_path: str | Path, order_book_id: str) -> Optional[date]:
    """bundle 里该标的最后一个**复权因子生效日**（G-T13-2 回测线要的那个数）。"""
    import h5py

    p = Path(bundle_path) / "ex_cum_factor.h5"
    if not p.exists():
        return None
    with h5py.File(p, "r") as h5:
        if order_book_id not in h5:
            return None
        sd = h5[order_book_id]["start_date"]
        if len(sd) == 0:
            return None
        return _d_from_int(int(sd[-1]))


def _d_from_int(v: int) -> date:
    s = str(int(v))
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
