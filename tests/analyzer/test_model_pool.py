"""A+B+C 模型池单测：429 轮换计数、死亡 TTL、性能加权（无网络）。"""

import time

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
