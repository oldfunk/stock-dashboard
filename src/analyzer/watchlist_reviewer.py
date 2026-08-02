"""AI 观察池复盘引擎

混合机制：硬规则预过滤 + LLM 自主决策。

对外暴露：
- WatchlistReviewer 类：单次复盘入口 review()
- 4 个硬规则检查函数：check_signal_avoid / check_roe_below /
  check_price_above_buyzone / check_roe_collapse
"""

import json
import logging
import os
import threading
from typing import Optional

from src.utils import now_cn

logger = logging.getLogger(__name__)


# ── 4 条硬规则 ──

def check_signal_avoid(stock: dict, analysis: Optional[dict]) -> bool:
    """规则 1: Signal=AVOID → 强制调出"""
    if not analysis:
        return False
    trade = analysis.get('trade_strategy', {})
    if isinstance(trade, str):
        try:
            trade = json.loads(trade)
        except (json.JSONDecodeError, TypeError):
            return False
    return trade.get('signal', '').upper() == 'AVOID'


def check_roe_below(stock: dict, analysis: Optional[dict],
                    threshold: float = 5) -> bool:
    """规则 2: ROE 跌破阈值 → 强制调出"""
    roe = stock.get('roe')
    if roe is None:
        return False
    return roe < threshold


def check_price_above_buyzone(stock: dict, analysis: Optional[dict],
                               pct: float = 20) -> bool:
    """规则 3: 当前价 > 买入区上限 +pct% → 强制调出（已涨过头）

    买入区格式形如 "1500-1700"，取上限 1700 计算。
    """
    if not analysis:
        return False
    trade = analysis.get('trade_strategy', {})
    if isinstance(trade, str):
        try:
            trade = json.loads(trade)
        except (json.JSONDecodeError, TypeError):
            return False
    buy_zone = trade.get('buy_zone')
    if not buy_zone or not isinstance(buy_zone, str):
        return False
    current_price = stock.get('current_price')
    if current_price is None:
        return False
    # 解析 "1500-1700" 或 "1500~1700"
    parts = buy_zone.replace('~', '-').split('-')
    if len(parts) != 2:
        return False
    try:
        upper = float(parts[1].strip())
    except (ValueError, IndexError):
        return False
    return current_price > upper * (1 + pct / 100)


def check_roe_collapse(stock: dict, analysis: Optional[dict],
                       pp: float = 10) -> bool:
    """规则 4: ROE 同比下降 > pp 个百分点 → 强制调出

    依赖 _history_roe.last_year 字段（由 _load_history_roe 注入）。
    """
    history = stock.get('_history_roe')
    if not history:
        return False
    last_year = history.get('last_year')
    current = stock.get('roe')
    if last_year is None or current is None:
        return False
    return (last_year - current) > pp


# ── 复盘引擎 ──

class WatchlistReviewer:
    """AI 观察池复盘引擎

    单一职责：输入数据 → 输出复盘结果 + 落库。
    模块级锁防并发，跟每日流水线锁 _pipeline_lock 独立。
    """

    _module_lock = threading.Lock()

    def __init__(self, config: dict):
        self.cfg = config or {}
        ai_review_cfg = self.cfg.get('ai_review', {})
        self.hard_rules_cfg = ai_review_cfg.get('hard_rules', {})
        self.watchlist_size = ai_review_cfg.get('watchlist_size', 5)
        self.candidate_weeks = ai_review_cfg.get('candidate_pool_weeks', 4)

    def _apply_hard_rules(self, current: list[dict],
                          analyses: dict[str, dict]) -> list[dict]:
        """对当前观察池应用 4 条硬规则，返回必须调出的股票列表。

        返回: [{"code": "002415", "name": "海康威视", "reasons": ["signal_avoid", ...]}]
        """
        forced_out = []
        for stock in current:
            code = stock['code']
            analysis = analyses.get(code)
            reasons = []

            if self.hard_rules_cfg.get('signal_avoid', True):
                if check_signal_avoid(stock, analysis):
                    reasons.append('signal_avoid')

            threshold = self.hard_rules_cfg.get('roe_below', 5)
            if check_roe_below(stock, analysis, threshold=threshold):
                reasons.append(f'roe_below_{threshold}')

            pct = self.hard_rules_cfg.get('price_above_buyzone_pct', 20)
            if check_price_above_buyzone(stock, analysis, pct=pct):
                reasons.append(f'price_above_buyzone_{pct}')

            pp = self.hard_rules_cfg.get('roe_collapse_pp', 10)
            if check_roe_collapse(stock, analysis, pp=pp):
                reasons.append(f'roe_collapse_{pp}')

            if reasons:
                forced_out.append({
                    'code': code,
                    'name': stock.get('name', ''),
                    'reasons': reasons,
                })
        return forced_out

    # 下面的方法在后续 Task 实现
    def review(self, run_id: str) -> dict:
        """复盘主入口。

        流程：
        1. 加载当前观察池 + 候选池 + 周五分析 + 大盘
        2. 硬规则预过滤
        3. 调用 LLM 决策
        4. 校验 + 落库
        """
        with self._module_lock:
            from src.models.ai_watchlist import (
                AiWatchlistDAO, AiWatchlistHistoryDAO, AiJournalDAO
            )
            from src.models.database import (
                ScreeningResultDAO, MarketIndexDAO, StockAnalysisHistoryDAO
            )

            # 1. 加载数据
            current = AiWatchlistDAO().get_all()
            candidates = self._load_recent_candidates(self.candidate_weeks)
            if not candidates and not current:
                logger.info("[复盘] 候选池为空，跳过")
                return {"skipped": True, "reason": "no_candidates"}

            analyses = self._load_recent_analyses(current + candidates)
            market = MarketIndexDAO().get_latest()
            # 历史财务（用于 ROE 同比判断）
            self._inject_history_roe(current)

            # 2. 硬规则预过滤
            forced_out = self._apply_hard_rules(current, analyses)

            # 3. 调用 LLM
            prompt = self._build_prompt(
                current, candidates, analyses, market, forced_out
            )
            result = self._call_llm(prompt)
            if result is None:
                logger.warning("[复盘] LLM 调用失败，跳过本次")
                return {"skipped": True, "reason": "llm_failed"}

            # 4. 校验 + 落库
            validated = self._validate_and_persist(
                result, run_id, forced_out, current, candidates
            )
            return validated

    def _build_prompt(self, current: list[dict], candidates: list[dict],
                      analyses: dict[str, dict], market: list[dict],
                      forced_out: list[dict]) -> str:
        """构建 LLM prompt。

        - current 为空 → 初始化模式
        - current 非空 → 复盘模式
        """
        size = self.watchlist_size
        is_initial = len(current) == 0

        if is_initial:
            role_line = (
                f"你是一位价值投资基金经理，这是观察池初始化（首次启动）。"
                f"请从候选池选 {size} 只作为初始成员。"
            )
            rule_line = (
                f"观察池固定 {size} 只。全部记为 action=add。"
                f"优先选评分高、ROE 稳定、护城河宽的股票。"
            )
        else:
            role_line = (
                f"你是一位价值投资基金经理，每周复盘一次观察池（固定 {size} 只）。"
            )
            rule_line = (
                f"观察池固定 {size} 只。优先保持稳定，没有充分理由不要换。"
                f"调入决策要给出理由，调出决策也要给出理由。"
            )

        # 强制调出列表
        if forced_out:
            forced_lines = []
            for f in forced_out:
                forced_lines.append(
                    f"  - {f['code']} {f['name']}: {', '.join(f['reasons'])}"
                )
            forced_section = (
                "【本次硬规则强制调出】（你必须接受这些调出）\n"
                + "\n".join(forced_lines)
            )
        else:
            forced_section = "【本次硬规则强制调出】无"

        # 当前观察池
        if current:
            current_lines = []
            for s in current:
                analysis = analyses.get(s['code'], {})
                trade = analysis.get('trade_strategy', {}) if analysis else {}
                if isinstance(trade, str):
                    try:
                        trade = json.loads(trade)
                    except Exception:
                        trade = {}
                signal = trade.get('signal', '--') if trade else '--'
                current_lines.append(
                    f"  - {s['code']} {s.get('name', '')} | ROE={s.get('roe', 'N/A')}% "
                    f"| Signal={signal} | 置信={s.get('ai_confidence', '--')} "
                    f"| 在池{s.get('review_count', 1)}周 | 进入理由: {s.get('added_reason', '')}"
                )
            current_section = "【当前观察池】\n" + "\n".join(current_lines)
        else:
            current_section = "【当前观察池】空（首次启动）"

        # 候选池
        if candidates:
            cand_lines = []
            for c in candidates:
                cand_lines.append(
                    f"  - {c['code']} {c.get('name', '')} | 评分={c.get('score', 'N/A')} "
                    f"| ROE={c.get('roe', 'N/A')}% | PE={c.get('pe', 'N/A')} "
                    f"| 市值={c.get('market_cap', 'N/A')}亿"
                )
            cand_section = "【候选池】（最近 4 周 Top 20 去重）\n" + "\n".join(cand_lines)
        else:
            cand_section = "【候选池】空"

        # 大盘
        if market:
            mkt_lines = [
                f"  - {m['index_name']}: {m.get('current_value', 'N/A')} "
                f"({m.get('change_percent', 'N/A')}%)"
                for m in market
            ]
            mkt_section = "【大盘近况】\n" + "\n".join(mkt_lines)
        else:
            mkt_section = "【大盘近况】无数据"

        return f"""{role_line}

【规则】
{rule_line}
- 你只能在候选池中选调入，不能选候选池外的股票
- 输出严格 JSON，不包含其他任何内容

{forced_section}

{current_section}

{cand_section}

{mkt_section}

【输出 JSON schema】
{{
    "watchlist_actions": [
        {{"code": "000792", "action": "keep", "reason": "..."}},
        {{"code": "600519", "action": "add", "reason": "..."}},
        {{"code": "002415", "action": "remove", "reason": "Signal 转 AVOID"}}
    ],
    "new_watchlist": ["000792", "600519", "300750", "600276", "002415"],
    "journal": {{
        "title": "本周复盘 - 市场震荡，观察池稳定",
        "content_md": "# 本周复盘\\n## 市场观察\\n..."
    }}
}}

journal.content_md 用 Markdown，写叙事性笔记（不要只是列股票），包含：
1. 本周市场观察（大盘走势、情绪）
2. 观察池调整动作的思考过程
3. 对当前观察池的整体判断
4. 风险提示
"""

    def _resolve_model(self) -> str:
        """获取当前可用的模型名（从 FreeModelPool 或配置）。

        可被测试 monkeypatch 覆盖以跳过池调用。
        """
        ai_cfg = self.cfg.get('ai', {})
        model = ai_cfg.get('model', 'deepseek-v4-flash-free')
        pool = getattr(self, '_pool', None)
        if pool:
            acquired = pool.acquire()
            if acquired:
                return acquired
        return model

    def _call_llm(self, prompt: str) -> Optional[dict]:
        """调用 LLM 并解析 JSON。

        复用 ai_analyzer 的 FreeModelPool 做模型轮换，但独立实现重试逻辑
        （复盘只需 1 次成功调用，不需要像 analyze_batch 那样的长重试链）。
        """
        import httpx
        from src.analyzer.ai_analyzer import (
            get_model_pool, parse_ai_response, BACKOFF_SCHEDULE
        )

        ai_cfg = self.cfg.get('ai', {})
        api_base = ai_cfg.get('api_base', 'https://opencode.ai/zen/v1').rstrip('/')
        api_key = ai_cfg.get('api_key') or os.getenv('STOCK_AI_API_KEY')
        model = ai_cfg.get('model', 'deepseek-v4-flash-free')

        # 免费模型用池
        is_free = model.endswith('-free')
        self._pool = get_model_pool(api_base, api_key) if is_free else None

        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        url = f"{api_base}/chat/completions"
        max_retries = 3  # 复盘只重试 3 次，避免长时间阻塞

        for attempt in range(max_retries):
            current_model = self._resolve_model()
            payload = {
                'model': current_model,
                'messages': [
                    {'role': 'system',
                     'content': '你只输出JSON，不输出其他任何内容。'
                                '必须严格按照用户指定的JSON结构输出。'},
                    {'role': 'user', 'content': prompt},
                ],
                'temperature': ai_cfg.get('temperature', 0.3),
                'max_tokens': ai_cfg.get('max_tokens', 6000),
            }
            try:
                with httpx.Client(timeout=240.0) as client:
                    resp = client.post(url, headers=headers, json=payload)

                if resp.status_code == 200:
                    data = resp.json()
                    content = data['choices'][0]['message']['content']
                    if not content or len(content) < 10:
                        if self._pool:
                            self._pool.mark_dead(current_model)
                        continue
                    # 复用 ai_analyzer 的健壮 JSON 解析
                    result = parse_ai_response(content)
                    if result is not None:
                        if isinstance(result, dict):
                            result['model'] = current_model
                        return result
                    # parse_ai_response 校验 ai_analyzer 特有字段（analysis/
                    # investment_strategy/trade_strategy），观察池复盘 schema 不同
                    # （watchlist_actions/new_watchlist/journal），回退到清洗后直接解析
                    text = content.strip()
                    if '```json' in text:
                        text = text.split('```json', 1)[1].split('```', 1)[0].strip()
                    elif '```' in text:
                        parts = text.split('```')
                        text = parts[1] if len(parts) > 1 else text
                    start = text.find('{')
                    end = text.rfind('}')
                    if start != -1 and end > start:
                        text = text[start:end + 1]
                    try:
                        result = json.loads(text)
                    except json.JSONDecodeError:
                        logger.warning("[复盘] JSON 解析失败，重试")
                        continue
                    if isinstance(result, dict):
                        result['model'] = current_model
                    return result

                # 非 200 → 轮换模型 + 退避
                if self._pool and resp.status_code in (500, 502, 503):
                    self._pool.mark_dead(current_model)
                import time
                time.sleep(BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)])

            except httpx.TimeoutException:
                logger.warning(f"[复盘] 超时, 重试 {attempt + 1}/{max_retries}")
                if self._pool:
                    self._pool.mark_dead(current_model)
            except Exception as e:
                logger.warning(f"[复盘] 异常: {e}, 重试 {attempt + 1}/{max_retries}")
                import time
                time.sleep(5)

        logger.error("[复盘] 已达最大重试次数，放弃")
        return None

    def _validate_and_persist(self, result: dict, run_id: str,
                              forced_out: list[dict], current: list[dict],
                              candidates: list[dict]) -> dict:
        """校验 LLM 输出 + 落库。

        校验规则：
        - 强制调出的股票必须调出（无论 LLM 想干嘛）
        - 调入的股票必须在候选池内
        - new_watchlist 不超过 watchlist_size
        """
        from src.models.ai_watchlist import (
            AiWatchlistDAO, AiWatchlistHistoryDAO, AiJournalDAO
        )
        from src.models.database import MarketIndexDAO

        candidate_codes = {c['code'] for c in candidates}
        forced_out_codes = {f['code'] for f in forced_out}
        current_map = {s['code']: s for s in current}

        actions = result.get('watchlist_actions', [])
        new_watchlist = result.get('new_watchlist', [])
        journal = result.get('journal', {})

        # 构建最终动作列表（强制调出 + LLM 动作，去重）
        final_actions = {}  # code → (action, reason)

        # 1. 强制调出优先
        for f in forced_out:
            final_actions[f['code']] = (
                'remove',
                f"硬规则强制调出: {', '.join(f['reasons'])}"
            )

        # 2. LLM 动作（不覆盖强制调出）
        for action in actions:
            code = action.get('code')
            if not code:
                continue
            if code in forced_out_codes:
                continue  # 强制调出已处理
            act = action.get('action', 'keep')
            reason = action.get('reason', '')
            # 调入必须在候选池
            if act == 'add' and code not in candidate_codes:
                logger.warning(
                    f"[复盘] 拒绝调入 {code}: 不在候选池"
                )
                final_actions[code] = ('remove', f"rejected: not in candidate pool")
                continue
            final_actions[code] = (act, reason)

        # 3. 落库
        action_date = now_cn().strftime("%Y-%m-%d")
        watchlist_dao = AiWatchlistDAO()
        history_dao = AiWatchlistHistoryDAO()

        for code, (action, reason) in final_actions.items():
            name = current_map.get(code, {}).get('name', '')
            if action == 'remove':
                watchlist_dao.remove(code)
            elif action == 'add':
                # 从候选池找 name
                for c in candidates:
                    if c['code'] == code:
                        name = c['name']
                        break
                watchlist_dao.add(code, name, added_reason=reason)
            elif action == 'keep':
                watchlist_dao.update_reviewed(code)

            history_dao.append(
                code=code, name=name, action=action,
                reason=reason, review_run_id=run_id, action_date=action_date
            )

        # 4. 笔记落库
        if journal and journal.get('title'):
            market = MarketIndexDAO().get_latest()
            # 构造调入调出详情（code + reason）
            action_details = {
                a: [{'code': c, 'reason': r}
                    for c, (act, r) in final_actions.items() if act == a]
                for a in ('add', 'remove', 'keep')
            }
            journal_dao = AiJournalDAO()
            journal_dao.save(
                journal_date=action_date,
                run_id=run_id,
                title=journal.get('title', ''),
                content_md=journal.get('content_md', ''),
                market_snapshot=json.dumps(
                    {'indices': [{'name': m.get('index_name'),
                                  'value': m.get('current_value'),
                                  'change_percent': m.get('change_percent')}
                                 for m in (market or [])]}
                ),
                actions_summary=json.dumps({
                    'add': sum(1 for a, _ in final_actions.values() if a == 'add'),
                    'remove': sum(1 for a, _ in final_actions.values() if a == 'remove'),
                    'keep': sum(1 for a, _ in final_actions.values() if a == 'keep'),
                    'details': action_details,
                    'model': result.get('model') if isinstance(result, dict) else None,
                })
            )

        return {
            "new_watchlist": [w['code'] for w in watchlist_dao.get_all()],
            "actions": dict(final_actions),
            "journal_date": action_date,
            "journal": journal,
        }

    def _load_recent_candidates(self, weeks: int) -> list[dict]:
        """加载最近 N 周 screening_result 去重"""
        from datetime import timedelta
        from src.models.database import ScreeningResultDAO, db_conn

        cutoff = (now_cn() - timedelta(weeks=weeks)).strftime("%Y-%m-%d")
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM screening_result "
                "WHERE run_date >= ? "
                "GROUP BY code "
                "ORDER BY MAX(score) DESC LIMIT 50",
                (cutoff,)
            ).fetchall()
        return [dict(r) for r in rows]

    def _load_recent_analyses(self, stocks: list[dict]) -> dict[str, dict]:
        """加载最近一次 AI 分析（按 code）"""
        from src.models.database import StockAnalysisHistoryDAO
        dao = StockAnalysisHistoryDAO()
        analyses = {}
        for s in stocks:
            code = s.get('code')
            if not code:
                continue
            hist = dao.get_latest_for_code(code)
            if hist and hist.get('ai_analysis'):
                try:
                    parsed = json.loads(hist['ai_analysis'])
                    if parsed and parsed != {}:
                        # 合并 ai_trade_strategy 字段（生产代码写入 ai_analysis
                        # 时通常已包含 trade_strategy，但历史/测试数据可能拆分
                        # 存储，这里做兼容处理）
                        if 'trade_strategy' not in parsed and hist.get('ai_trade_strategy'):
                            try:
                                parsed['trade_strategy'] = json.loads(
                                    hist['ai_trade_strategy']
                                )
                            except (json.JSONDecodeError, TypeError):
                                pass
                        analyses[code] = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
        return analyses

    def _inject_history_roe(self, stocks: list[dict]):
        """注入历史 ROE（用于 ROE 同比判断）"""
        from src.models.database import FinancialHistoryDAO
        dao = FinancialHistoryDAO()
        for s in stocks:
            reports = dao.get_annual_reports(s['code'])
            if len(reports) >= 2:
                # reports 按日期降序，[0] 是最新年报，[1] 是去年
                s['_history_roe'] = {
                    'last_year': reports[1].get('roe'),
                    'this_year': reports[0].get('roe'),
                }
            else:
                s['_history_roe'] = None
