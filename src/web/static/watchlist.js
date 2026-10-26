// 搜索 + 钉选功能 (watchlist.js)

// 生成东方财富个股详情 URL（6/9 开头→sh，其余→sz）
function emStockUrl(code) {
    var prefix = (code.charAt(0) === '6' || code.charAt(0) === '9') ? 'sh' : 'sz';
    return 'https://quote.eastmoney.com/' + prefix + code + '.html';
}

// 视图切换：AI 观察池 / 钉选股票
function switchView(view, event) {
    // 切换 tab 高亮
    document.querySelectorAll('.view-tab').forEach(function(el) {
        el.classList.remove('active');
    });
    if (event && event.target) {
        event.target.closest('.view-tab').classList.add('active');
    }
    var aiContainer = document.getElementById('watchlistAiContainer');
    var watchlist = document.getElementById('watchlistContainer');
    var candidates = document.getElementById('candidatesContainer');
    if (!aiContainer || !watchlist || !candidates) return;
    if (view === 'watchlist-ai') {
        aiContainer.style.display = '';
        watchlist.style.display = 'none';
        candidates.style.display = 'none';
    } else if (view === 'watchlist') {
        aiContainer.style.display = 'none';
        watchlist.style.display = '';
        candidates.style.display = 'none';
        loadWatchlist();
    } else if (view === 'candidates') {
        aiContainer.style.display = 'none';
        watchlist.style.display = 'none';
        candidates.style.display = '';
    }
}

// 钉选 / 取消钉选（通用，候选池卡片和搜索结果都能用）
function toggleWatch(code, btn) {
    if (!code || !btn) return;
    var isWatched = btn.classList.contains('watched');
    var method = isWatched ? 'DELETE' : 'POST';
    fetch('/api/watchlist/' + code, {method: method})
        .then(function(r) { return r.json(); })
        .then(function() {
            if (isWatched) {
                btn.classList.remove('watched');
                btn.textContent = '钉选';
            } else {
                btn.classList.add('watched');
                btn.textContent = '已钉';
            }
            updateWatchCount();
            setTimeout(loadWatchlist, 300);
        })
        .catch(function(e) { console.error('toggleWatch error:', e); });
}

// 加载钉选列表（2026-10-26：服务端渲染同一张候选卡模板，JS 不再拼卡片）
function loadWatchlist() {
    fetch('/api/watchlist/cards')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var el = document.getElementById('watchlistContainer');
            if (!el) return;
            updateWatchCount(data.count);
            el.innerHTML = data.count ? data.html :
                '<div style="padding:40px;text-align:center;color:var(--text-tertiary);">' +
                '还没有钉选的股票<br><small>搜索股票后点"钉选"即可加入跟踪</small></div>';
        })
        .catch(function(e) { console.error('loadWatchlist error:', e); });
}

// 更新 tab 上的钉选计数
function updateWatchCount(n) {
    var el = document.getElementById('watchCount');
    if (!el) return;
    if (typeof n === 'number') {
        el.textContent = '(' + n + ')';
        return;
    }
    fetch('/api/watchlist')
        .then(function(r) { return r.json(); })
        .then(function(items) { el.textContent = '(' + items.length + ')'; })
        .catch(function(){});
}

// 搜索股票
function searchStocks() {
    var input = document.getElementById('searchInput');
    var el = document.getElementById('searchResults');
    if (!input || !el) return;
    var q = input.value.trim();
    if (!q) { el.style.display = 'none'; return; }
    el.innerHTML = '<div style="padding:12px;color:var(--text-tertiary);">搜索中...</div>';
    el.style.display = 'block';
    fetch('/api/search?q=' + encodeURIComponent(q))
        .then(function(r) { return r.json(); })
        .then(function(results) {
            if (results.length === 0) {
                el.innerHTML = '<div class="search-empty">未找到匹配的股票</div>';
                return;
            }
            el.innerHTML = results.map(function(s) {
                var badges = '';
                if (s.in_list) badges += '<span class="badge badge-list">在榜</span>';
                if (s.last_signal) {
                    var st = s.last_signal === 'BUY' ? '买入' : (s.last_signal === 'HOLD' ? '持有' : (s.last_signal === 'AVOID' ? '回避' : s.last_signal));
                    badges += '<span class="badge badge-signal">' + st + '</span>';
                }
                var btnClass = s.watched ? 'watch-btn watched' : 'watch-btn';
                var btnText = s.watched ? '已钉' : '钉选';
                return '<div class="search-item clickable" title="查看详情" onclick="location.href=\'/stock/' + s.code + '\'">' +
                    '<div class="si-info"><span class="si-code">' + s.code + '</span> ' + s.name + ' ' + badges + '</div>' +
                    '<div class="si-metrics">' + (s.summary || '') + '</div>' +
                    '<button class="' + btnClass + '" data-scode="' + s.code + '">' + btnText + '</button>' +
                    '</div>';
            }).join('');
            // 绑定钉选按钮（stopPropagation 防止触发外层跳转）
            el.querySelectorAll('[data-scode]').forEach(function(btn) {
                btn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    toggleWatch(btn.getAttribute('data-scode'), btn);
                });
            });
        })
        .catch(function(e) {
            el.innerHTML = '<div class="search-empty">搜索失败: ' + e.message + '</div>';
        });
}

// 点击页面其他地方关闭搜索结果
document.addEventListener('click', function(e) {
    if (!e.target.closest('.search-box')) {
        var sr = document.getElementById('searchResults');
        if (sr) sr.style.display = 'none';
    }
});

// 回车搜索 + 初始化钉选计数
document.addEventListener('DOMContentLoaded', function() {
    var input = document.getElementById('searchInput');
    if (input) {
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') { e.preventDefault(); searchStocks(); }
        });
    }
    updateWatchCount();
});
