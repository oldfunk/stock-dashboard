"""补跑 AI 分析失败的股票（通用版）。

默认行为：定位最新 run，补跑其中 ai_analysis 为空的股票。
用法：
  python3 scripts/retry_ai.py                 # 补跑最新 run 的失败股票
  python3 scripts/retry_ai.py --run-id=20260715_153012   # 指定 run
  python3 scripts/retry_ai.py --all-failed    # 扫所有 run 里的失败记录
  python3 scripts/retry_ai.py --dry-run       # 只列出待补跑股票，不实际调用

退出码：
  0 = 至少成功补跑 1 只
  1 = 没有需要补跑的股票
  2 = API 未配置
"""
import os
import sys
import time
import logging

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJ, '.env'), override=True)

from src.logging_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

from src.config import load_config
from src.models.database import (
    ScreeningResultDAO, StockAnalysisHistoryDAO, FinancialSummaryDAO,
    RunLogDAO, PipelineProgressDAO, db_conn, init_database,
)
from src.analyzer.ai_analyzer import AiAnalyzer, _save_analysis, _save_failure
from src.utils import now_cn

init_database()


# ── 解析参数 ──
run_id_arg = None
all_failed = False
dry_run = False
for a in sys.argv[1:]:
    if a.startswith('--run-id='):
        run_id_arg = a.split('=', 1)[1]
    elif a == '--all-failed':
        all_failed = True
    elif a == '--dry-run':
        dry_run = True

# ── 校验 AI 配置 ──
analyzer = AiAnalyzer(load_config().get('ai', {}))
if not analyzer.configured:
    print("[ERROR] AI 未配置（STOCK_AI_API_KEY 未设置且模型非 free）")
    sys.exit(2)
print(f"[AI] model={analyzer.model} api_base={analyzer.api_base}")


def _enrich(stocks):
    """富集 financial_summary 字段到 stock dict（prompt 依赖 5y/10y 字段）。"""
    fs_dao = FinancialSummaryDAO()
    for s in stocks:
        fs = fs_dao.get(s['code'])
        if fs:
            for k, v in fs.items():
                if k not in ('stock_code', 'updated_at') and v is not None:
                    s[k] = v
    return stocks


def _fetch_failed_for_run(rid):
    """返回某个 run 里 ai_analysis 为空/NULL 的股票。"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM screening_result "
            "WHERE run_id = ? AND (ai_analysis IS NULL OR ai_analysis = '' OR ai_analysis = '{}') "
            "ORDER BY score DESC",
            (rid,)
        ).fetchall()
    return [dict(r) for r in rows]


def _fetch_all_failed():
    """扫所有 run 里 ai_analysis 为空的股票（按 run 倒序、score 倒序）。"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM screening_result "
            "WHERE ai_analysis IS NULL OR ai_analysis = '' OR ai_analysis = '{}' "
            "ORDER BY run_id DESC, score DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── 收集待补跑股票 ──
if all_failed:
    pending = _fetch_all_failed()
    target_run_id = '(all runs)'
else:
    target_run_id = run_id_arg or RunLogDAO().get_latest_completed_run_id()
    if not target_run_id:
        print("[SKIP] 没有已完成的 run")
        sys.exit(1)
    pending = _fetch_failed_for_run(target_run_id)

if not pending:
    print(f"[DONE] 没有需要补跑的股票 (run={target_run_id})")
    sys.exit(1)

print(f"[待补跑] run={target_run_id}, 共 {len(pending)} 只:")
for s in pending:
    print(f"  {s['code']} {s.get('name','')} (score={s.get('score')})")

if dry_run:
    print("\n[--dry-run] 不实际调用，退出")
    sys.exit(1)

# ── 富集财务数据 ──
print(f"\n富集财务历史...")
pending = _enrich(pending)

# ── 逐只补跑 ──
analyzed_ok = 0
analyzed_failed = 0
for idx, stock in enumerate(pending):
    code = stock['code']
    rid = stock['run_id']
    print(f"\n[{now_cn().strftime('%H:%M:%S')}] [{idx+1}/{len(pending)}] {code} {stock.get('name','')} (run={rid})...")

    # 进度回写（用股票所属的 run_id，而不是 target_run_id）
    try:
        PipelineProgressDAO().update(rid, ai_done=None, ai_failed=None)
    except Exception:
        pass

    result = analyzer.analyze_stock(stock)
    if result:
        _save_analysis(stock, result, rid)
        analyzed_ok += 1
        print(f"  ✅ 成功 ({analyzed_ok}/{len(pending)})")
    else:
        _save_failure(stock, rid)
        analyzed_failed += 1
        print(f"  ❌ 失败 ({analyzed_failed} 次失败)")

    # 股票之间冷却（避免限流）
    if idx < len(pending) - 1:
        time.sleep(60)

# ── 回写 run_log.analyzed_count（针对单一 run 的情况）──
if not all_failed and analyzed_ok > 0:
    try:
        # 重新统计该 run 的成功分析数
        with db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM screening_result "
                "WHERE run_id = ? AND ai_analysis IS NOT NULL AND ai_analysis != '' AND ai_analysis != '{}'",
                (target_run_id,)
            ).fetchone()
            RunLogDAO().update_analyzed_count(target_run_id, row['cnt'] if row else analyzed_ok)
    except Exception as e:
        logger.warning(f"回写 analyzed_count 失败: {e}")

print(f"\n=== 补跑完成 ===")
print(f"成功: {analyzed_ok}  失败: {analyzed_failed}  共: {len(pending)}")
sys.exit(0 if analyzed_ok > 0 else 1)
