"""A+B+C 模型池单测：429 轮换计数、死亡 TTL、性能加权（无网络）。"""

import sys
import threading
import time

import pytest

from src.analyzer import ai_analyzer
from src.analyzer.ai_analyzer import AiAnalyzer, FreeModelPool


def _pool(models=('a', 'b', 'c')):
    p = FreeModelPool('http://127.0.0.1:9', None)
    p._pool = list(models)
    p._last_refresh = time.time()  # 避免触发网络刷新
    return p


class TestDeadTTL:
    def test_dead_skipped_then_revived(self):
        p = _pool()
        p.mark_dead('a', ttl=3600)
        assert p.acquire() != 'a'
        p._dead['a'] = time.time() - 1  # 过期
        assert 'a' in p._alive()

    def test_remark_extends(self):
        p = _pool()
        p.mark_dead('a', ttl=10)
        first = p._dead['a']
        p.mark_dead('a', ttl=3600)
        assert p._dead['a'] > first


class TestScoring:
    def test_newcomer_neutral_prior(self):
        assert _pool().score('never-seen') == 0.5

    def test_best_wins(self):
        p = _pool()
        for _ in range(5):
            p.record_result('a', ok=True, latency=5.0)
        for _ in range(3):
            p.record_result('b', ok=False)
        assert p.acquire() == 'a'

    def test_slow_model_penalized(self):
        p = _pool(('fast', 'slow'))
        for _ in range(4):
            p.record_result('fast', ok=True, latency=5.0)
            p.record_result('slow', ok=True, latency=200.0)
        assert p.score('fast') > p.score('slow')
        assert p.acquire() == 'fast'

    def test_tie_rotates(self):
        p = _pool()
        first = p.acquire()
        second = p.acquire()
        assert first != second  # 同分（全中性）按游标轮转，不饿死

    def test_timeouts_sink(self):
        p = _pool()
        for _ in range(3):
            p.record_result('t', ok=False, timeout=True)
        assert p.score('t') < 0.5


class TestNote429:
    def test_three_in_a_row_rotates(self):
        an = AiAnalyzer()
        assert an._note_429('m') is False
        assert an._note_429('m') is False
        assert an._note_429('m') is True

    def test_model_change_resets(self):
        an = AiAnalyzer()
        an._note_429('m')
        an._note_429('m')
        assert an._note_429('other') is False

    def test_dead_mark_records_fail(self):
        an = AiAnalyzer()
        an._pool = _pool(('x', 'y'))
        an._mark_model_dead('x')
        assert an._pool._stats['x']['fail'] == 1
        assert 'x' not in an._pool._alive()
        assert an._batch_model is None


# ── 线程安全（2026-10-29，批6-d N16）────────────────────────────────────
# 模型池是进程级单例：ai_queue 的 ThreadPoolExecutor（最多 5 worker）+ 复盘线程
# + 新闻线程同时打它。旧实现一把锁都没有。


@pytest.fixture
def fast_switch():
    """把 GIL 切换间隔压到极小：不加锁的 `st['ok'] += 1` 才会**稳定**丢计数，
    否则用例可能碰巧通过，变成一条不判别的假绿测试。"""
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(old)


class TestThreadSafety:
    def test_concurrent_record_result_keeps_every_count(self, fast_switch):
        p = _pool(('a',))
        n_thread, n_iter = 8, 2000

        def worker():
            for _ in range(n_iter):
                p.record_result('a', ok=True, latency=0.5, timeout=False)

        ts = [threading.Thread(target=worker) for _ in range(n_thread)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        st = p._stats['a']
        assert st['ok'] == n_thread * n_iter, \
            f"读-改-写丢计数：{st['ok']} != {n_thread * n_iter}"
        assert st['n_lat'] == n_thread * n_iter
        assert st['lat_sum'] == pytest.approx(n_thread * n_iter * 0.5)

    def test_mixed_workload_never_raises_and_stays_consistent(self, fast_switch):
        """冒烟加固（**非判别**）：并发混跑不抛异常且账目自洽。

        实测在旧版（无锁）下本用例也通过，所以它杀不掉 N16 的 bug；留着是让
        将来的改动不能把这两条不变量弄坏（不抛异常、`ok+fail` 总量守恒）。
        判别的证据在 `test_concurrent_record_result_keeps_every_count`。
        """
        p = _pool(('a', 'b', 'c'))
        errs = []
        n_rec, n_pick = 500, 300

        def rec(i):
            try:
                for k in range(n_rec):
                    m = "abc"[(i + k) % 3]
                    p.record_result(m, k % 5 != 0, latency=1.0)
                    p.record_quality(m, k % 3 != 0)
            except BaseException as e:
                errs.append(e)

        def pick(i):
            try:
                for k in range(n_pick):
                    p.acquire()
                    p.score('a')
                    p.current_pool()
                    if k % 3 == 0:
                        p.mark_dead("abc"[k % 3], ttl=0.001)
            except BaseException as e:
                errs.append(e)

        ts = ([threading.Thread(target=rec, args=(i,)) for i in range(4)]
              + [threading.Thread(target=pick, args=(i,)) for i in range(4)])
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert not errs, f"并发下有异常逃出：{errs[:2]}"
        total = sum(s['ok'] + s['fail'] for s in p._stats.values())
        assert total == 4 * n_rec, f"结局计数总量不符：{total}"

    def test_refresh_publish_race_does_not_break(self, fast_switch, monkeypatch):
        """`_refresh` 发布新池时并发 `mark_dead` 不抛异常（冒烟加固，**非判别**）。

        想复现的故障是 `_dead` 遍历中被插入 → `RuntimeError: dictionary changed
        size during iteration`。**实测本用例对旧版（无锁）也通过**，因此它不构成
        N16 的证据：第一次 `_refresh` 就会把 `_dead` 剪到只剩池内键（≤50 个），
        遍历窗口随之塌缩，靠加键宽限窗口的做法在真实发布逻辑下不成立。
        该竞态是由"发布整段进锁"在构造上消除的，不是由本用例证明的。
        httpx 打桩，不碰网络。
        """
        class _Resp:
            status_code = 200

            def json(self):
                return {"data": [{"id": f"m{i}-free"} for i in range(50)]}

        class _Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **k):
                return _Resp()

        monkeypatch.setattr(ai_analyzer.httpx, "Client", _Client)
        p = _pool(('a',))
        # 预置一份大死亡名单（起点宽窗口）；但注意第一次 _refresh 就会把它剪到
        # 只剩池内键，窗口塌缩——这正是本用例无法判别旧版的原因，见上面 docstring。
        p._dead = {f"d{i}": time.time() + 1000 for i in range(4000)}
        errs = []

        def churn(i):
            try:
                for k in range(600):
                    p.mark_dead(f"m{(i + k) % 50}-free", ttl=1000)
                    p.record_result(f"m{(i + k) % 50}-free", ok=True)
            except BaseException as e:
                errs.append(e)

        def refresher():
            try:
                for _ in range(300):
                    p._refresh()
            except BaseException as e:
                errs.append(e)

        ts = ([threading.Thread(target=churn, args=(i,)) for i in range(3)]
              + [threading.Thread(target=refresher) for _ in range(2)])
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert not errs, f"发布与改写打架：{errs[:2]}"
        assert p._pool, "刷新后池不应为空"
