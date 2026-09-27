"""AI 分析任务队列 + 并发批量（OpenAI-compatible 自带 Key 模式）。

- analyze_batch_parallel：股票级并发（每只独立 AiAnalyzer，线程隔离），interval 为下发间隔。
- AnalysisQueue：内存队列（重启丢失，见文档），批次串行、批次内 N 并发；get_queue() 单例。
- polish_requirement：用户口语需求 → 结构化分析指令（ask_raw 复用）。
- explain_market：大盘解盘（指数快照 + 用户要求 → ask_raw，不落库）。
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


def analyze_batch_parallel(stocks, run_id, interval_seconds=60, max_workers=2,
                           extra_instruction=None, on_progress=None):
    """并发版批量分析。

    每只股票独立 AiAnalyzer 实例（线程隔离 _last_usage 等状态）；
    主线程按 interval 下发任务；_save_* 走独立 DB 连接（db_conn 每次新建）。
    返回 (成功数, 失败数)。
    """
    from src.config import load_config
    from src.analyzer.ai_analyzer import (
        AiAnalyzer, _save_analysis, _save_failure, _attach_batch_context)
    from src.models.database import AiAnalysisLogDAO
    total = len(stocks)
    if total == 0:
        return 0, 0
    cfg = load_config().get('ai', {})
    if not AiAnalyzer(cfg).configured:
        logger.error("[AI分析] 无 API Key，跳过全部")
        for s in stocks:
            _save_failure(s, run_id, '未配置 API Key')
        return 0, total
    workers = max(1, int(max_workers or 1))
    interval = min(600, max(0, int(interval_seconds or 0)))
    logger.info(f"[AI分析] 并发分析 {total} 只股票（{workers} 并发）...")
    _attach_batch_context(stocks, run_id)
    log_dao = AiAnalysisLogDAO()
    lock = threading.Lock()
    counts = {'ok': 0, 'failed': 0}

    def _one(s):
        try:
            analyzer = AiAnalyzer(cfg)
            result = analyzer.analyze_stock(s, extra_instruction)
            with lock:
                if result:
                    _save_analysis(s, result, run_id)
                    try:
                        usage = getattr(analyzer, '_last_usage', None) or {}
                        log_dao.log(run_id, s.get('code'),
                                    usage.get('model', analyzer.model),
                                    usage.get('prompt_tokens', 0),
                                    usage.get('completion_tokens', 0), 0.0)
                    except Exception as e:
                        logger.debug(f"[AI分析] 写 ai_analysis_log 失败（不影响主流程）: {e}")
                    counts['ok'] += 1
                else:
                    _save_failure(s, run_id, getattr(analyzer, '_last_error', None) or '分析失败')
                    counts['failed'] += 1
                if on_progress:
                    on_progress(counts['ok'], counts['failed'],
                                counts['ok'] + counts['failed'])
        except Exception as e:
            logger.warning("[AI分析] %s 并发任务异常: %s", s.get('code'), e)
            with lock:
                counts['failed'] += 1
                if on_progress:
                    on_progress(counts['ok'], counts['failed'],
                                counts['ok'] + counts['failed'])

    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix='aiq') as ex:
        futs = []
        for i, s in enumerate(stocks):
            if i > 0 and interval > 0:
                time.sleep(interval)
            futs.append(ex.submit(_one, s))
        for f in futs:
            f.result()
    return counts['ok'], counts['failed']


class AnalysisQueue:
    """内存任务队列：批次串行执行，批次内股票级并发。重启丢失（见文档）。"""

    def __init__(self, auto_start=True):
        self._lock = threading.Lock()
        self._tasks = {}
        self._seq = 0
        self._event = threading.Event()
        self._thread = None
        if auto_start:
            self.start()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="ai-queue")
            self._thread.start()

    def submit(self, name, mode, stocks, run_id, interval, max_workers, extra=None):
        with self._lock:
            self._seq += 1
            tid = f"q{self._seq}"
            self._tasks[tid] = {"id": tid, "name": name, "mode": mode,
                                "stocks": list(stocks), "run_id": run_id,
                                "interval": interval, "max_workers": max_workers,
                                "extra": extra, "total": len(stocks),
                                "done": 0, "failed": 0, "status": "queued",
                                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._event.set()
        return tid

    def snapshot(self):
        with self._lock:
            return [{k: v for k, v in t.items() if k != "stocks"}
                    for t in sorted(self._tasks.values(),
                                    key=lambda t: t["id"])]

    def cancel(self, task_id):
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t["status"] == "queued":
                t["status"] = "cancelled"
                return True
            return False

    def _loop(self):
        while True:
            task = None
            with self._lock:
                for tid in sorted(self._tasks):
                    if self._tasks[tid]["status"] == "queued":
                        task = self._tasks[tid]
                        task["status"] = "running"
                        break
                if task is None:
                    self._event.clear()
            if task is None:
                self._event.wait(timeout=2.0)
                continue
            self._execute(task)

    def _execute(self, task):
        from src.models.database import RunLogDAO, PipelineProgressDAO
        run_id, stocks, total = task["run_id"], task["stocks"], task["total"]
        progress = PipelineProgressDAO()
        try:
            self._enrich(stocks)
            workers = self._workers_now()
            progress.init_run(run_id, 'analyzing',
                              f'[{task["name"]}] AI分析 0/{total}（{workers} 并发）...',
                              ai_total=total)

            def on_progress(ok, failed, idx):
                with self._lock:
                    task["done"], task["failed"] = ok, failed
                progress.update(run_id, stage_label=f'[{task["name"]}] AI分析 {idx}/{total}...',
                                ai_done=ok, ai_failed=failed)
            ok, failed = analyze_batch_parallel(
                stocks, run_id, interval_seconds=task["interval"],
                max_workers=workers, extra_instruction=task["extra"],
                on_progress=on_progress)
            RunLogDAO().update_analyzed_count(run_id, ok)
            with self._lock:
                task["done"], task["failed"], task["status"] = ok, failed, "done"
            progress.update(run_id, 'done',
                            f'[{task["name"]}] 完成：AI分析{ok}只（失败{failed}只）',
                            ai_done=ok, ai_failed=failed)
        except Exception as e:
            logger.warning("[AI队列] 任务 %s 异常：%s", task["id"], e)
            with self._lock:
                task["status"] = "failed"
            try:
                progress.update(run_id, 'done', f'[{task["name"]}] 分析异常：{e}')
            except Exception:
                pass

    @staticmethod
    def _enrich(stocks):
        from src.models.database import FinancialSummaryDAO
        fs_dao = FinancialSummaryDAO()
        for s in stocks:
            try:
                fs = fs_dao.get(s['code'])
            except Exception:
                fs = None
            if fs:
                for k, v in fs.items():
                    if k not in ('stock_code', 'updated_at') and v is not None:
                        s[k] = v

    @staticmethod
    def _workers_now():
        try:
            from src import llm_config
            return llm_config.get_concurrency()
        except Exception:
            return 2


_queue = None
_queue_lock = threading.Lock()


def get_queue():
    """队列单例（进程内）。"""
    global _queue
    if _queue is None:
        with _queue_lock:
            if _queue is None:
                _queue = AnalysisQueue()
    return _queue


_POLISH_SYSTEM = ('你是股票分析任务拆解助手。把用户的口语需求改写成3-8条可执行的分析指令，'
                  '每条一句话，只输出编号列表，不解释不寒暄。')


def polish_requirement(text):
    """用户口语需求 → 结构化分析指令；失败返回 None。"""
    from src.config import load_config
    from src.analyzer.ai_analyzer import AiAnalyzer
    t = (text or '').strip()
    if not t:
        return None
    analyzer = AiAnalyzer(load_config().get('ai', {}))
    if not analyzer.configured:
        return None
    out, _, _ = analyzer.ask_raw(t[:2000], system=_POLISH_SYSTEM)
    return out


def build_market_context():
    """大盘解盘上下文（指数最新快照，紧凑文本）。"""
    from src.models.database import MarketIndexDAO
    try:
        rows = MarketIndexDAO().get_latest()
    except Exception:
        rows = []
    if not rows:
        return '大盘数据暂无'
    parts = []
    for r in rows[:12]:
        chg = r.get('change_percent')
        chg_s = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else '未知'
        parts.append(f"{r.get('index_name') or r.get('index_code')}"
                     f"{r.get('current_value')}（{chg_s}）")
    return '大盘：' + '；'.join(parts)


def explain_market(requirement=None):
    """大盘解盘：返回 {answer, model, usage}，失败 None（不落库）。"""
    from src.config import load_config
    from src.analyzer.ai_analyzer import AiAnalyzer
    analyzer = AiAnalyzer(load_config().get('ai', {}))
    if not analyzer.configured:
        return None
    req = (requirement or '').strip()[:2000]
    prompt = (build_market_context()
              + '\n\n请给出大盘解盘：趋势判断 + 关键点位/背离 + 操作建议（短线/中线分开一句）。')
    if req:
        prompt += '\n\n用户附加要求：\n' + req
    text, model, usage = analyzer.ask_raw(prompt)
    if text is None:
        return None
    return {'answer': text, 'model': model, 'usage': usage or {}}
