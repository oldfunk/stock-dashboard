"""M4b TopK 离线回测脚本

从 SQLite 读取历史数据，模拟 TopK 等权组合回测。
不依赖 QLib，纯 Python 实现，只回流结论不回流代码。

用法：
    python scripts/backtest_topk.py --start 2024-01-01 --end 2024-12-31 --topk 20
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.database import get_db_path


def get_db():
    """获取数据库连接"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_daily_data(conn, start_date: str, end_date: str):
    """获取日 K 线数据"""
    cursor = conn.execute("""
        SELECT code, trade_date, open, close, high, low, volume, amount
        FROM kline_daily
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY code, trade_date
    """, (start_date, end_date))
    return cursor.fetchall()


def fetch_financial_data(conn, date: str):
    """获取财务数据（最近报告期）"""
    cursor = conn.execute("""
        SELECT code, roe_5y_avg, pe, pb, gross_margin, net_margin,
               debt_ratio, revenue_growth, profit_growth, fcf_5y_sum,
               market_cap, score
        FROM financial_summary
        WHERE report_date <= ?
        ORDER BY report_date DESC
    """, (date,))
    # 每只股票取最新一条
    seen = set()
    results = []
    for row in cursor.fetchall():
        if row['code'] not in seen:
            seen.add(row['code'])
            results.append(row)
    return results


def calculate_score(stock: dict) -> float:
    """简化评分：基于 ROE、PE、PB、毛利率、净利率、负债率"""
    score = 0.0
    # ROE 5年均（0-30分）
    roe = stock.get('roe_5y_avg') or 0
    score += min(roe / 30 * 30, 30)
    # PE（0-20分，越低越好）
    pe = stock.get('pe')
    if pe and pe > 0:
        score += max(0, 20 - pe / 2)
    # PB（0-15分，越低越好）
    pb = stock.get('pb')
    if pb and pb > 0:
        score += max(0, 15 - pb * 3)
    # 毛利率（0-15分）
    gm = stock.get('gross_margin') or 0
    score += min(gm / 100 * 15, 15)
    # 净利率（0-10分）
    nm = stock.get('net_margin') or 0
    score += min(nm / 100 * 10, 10)
    # 负债率（0-10分，越低越好）
    dr = stock.get('debt_ratio') or 0
    score += max(0, 10 - dr / 10)
    return score


def run_backtest(conn, start_date: str, end_date: str, topk: int = 20,
                 rebalance_freq: str = 'W'):
    """运行 TopK 回测

    rebalance_freq: 'D' 日, 'W' 周, 'M' 月
    """
    # 获取所有交易日
    cursor = conn.execute("""
        SELECT DISTINCT trade_date FROM kline_daily
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY trade_date
    """, (start_date, end_date))
    trading_dates = [row['trade_date'] for row in cursor.fetchall()]

    if not trading_dates:
        print("无交易日数据")
        return None

    # 按再平衡频率分组
    rebalance_dates = []
    last_rebalance = None
    for date in trading_dates:
        dt = datetime.strptime(date, '%Y-%m-%d')
        if rebalance_freq == 'D':
            key = date
        elif rebalance_freq == 'W':
            key = dt.strftime('%Y-%W')
        elif rebalance_freq == 'M':
            key = dt.strftime('%Y-%m')
        else:
            key = date
        if key != last_rebalance:
            rebalance_dates.append(date)
            last_rebalance = key

    # 初始资金
    initial_cash = 1_000_000.0
    cash = initial_cash
    positions = {}  # code -> {volume, avg_price}
    portfolio_value = initial_cash

    # 回测循环
    for i, date in enumerate(trading_dates):
        # 再平衡日：调仓
        if date in rebalance_dates:
            # 获取当日财务数据
            financials = fetch_financial_data(conn, date)
            # 计算评分并排序
            scored = []
            for f in financials:
                score = calculate_score(f)
                scored.append((score, f['code'], f))
            scored.sort(reverse=True)
            top_stocks = scored[:topk]

            # 卖出不在 TopK 中的持仓
            for code in list(positions.keys()):
                if code not in [s[1] for s in top_stocks]:
                    # 以当日收盘价卖出
                    cursor = conn.execute("""
                        SELECT close FROM kline_daily
                        WHERE code = ? AND trade_date = ?
                    """, (code, date))
                    row = cursor.fetchone()
                    if row:
                        sell_price = row['close']
                        vol = positions[code]['volume']
                        cash += sell_price * vol * (1 - 0.00025 - 0.005)  # 佣金+印花税
                        del positions[code]

            # 买入 TopK 中的股票
            target_value = portfolio_value / topk
            for score, code, f in top_stocks:
                if code in positions:
                    continue
                # 以当日收盘价买入
                cursor = conn.execute("""
                    SELECT close FROM kline_daily
                    WHERE code = ? AND trade_date = ?
                """, (code, date))
                row = cursor.fetchone()
                if not row:
                    continue
                buy_price = row['close']
                if buy_price <= 0:
                    continue
                # 计算可买数量（100股整数倍）
                max_vol = int(target_value / buy_price / 100) * 100
                if max_vol <= 0:
                    continue
                cost = buy_price * max_vol * (1 + 0.00025)  # 佣金
                if cost > cash:
                    max_vol = int(cash / buy_price / 100) * 100
                    if max_vol <= 0:
                        continue
                    cost = buy_price * max_vol * (1 + 0.00025)
                cash -= cost
                positions[code] = {'volume': max_vol, 'avg_price': buy_price}

        # 计算当日组合价值
        portfolio_value = cash
        for code, pos in positions.items():
            cursor = conn.execute("""
                SELECT close FROM kline_daily
                WHERE code = ? AND trade_date = ?
            """, (code, date))
            row = cursor.fetchone()
            if row:
                portfolio_value += row['close'] * pos['volume']

    # 最终收益
    total_return = (portfolio_value - initial_cash) / initial_cash * 100
    return {
        'start_date': start_date,
        'end_date': end_date,
        'topk': topk,
        'rebalance_freq': rebalance_freq,
        'initial_cash': initial_cash,
        'final_value': portfolio_value,
        'total_return_pct': total_return,
        'positions': len(positions),
    }


def main():
    parser = argparse.ArgumentParser(description='TopK 离线回测')
    parser.add_argument('--start', default='2024-01-01', help='开始日期')
    parser.add_argument('--end', default='2024-12-31', help='结束日期')
    parser.add_argument('--topk', type=int, default=20, help='TopK 数量')
    parser.add_argument('--freq', default='W', choices=['D', 'W', 'M'],
                        help='再平衡频率')
    args = parser.parse_args()

    conn = get_db()
    result = run_backtest(conn, args.start, args.end, args.topk, args.freq)
    conn.close()

    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
