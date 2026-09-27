/* AI 分析共享库（index / stock_detail / watchlist_detail 共用）。
   后端：POST /api/llm/analyze（once 单股），GET /api/progress（进度）。
   注意：index.html 内另有一份同名实现（历史原因），改动时两边同步。 */
function aiPostJSON(url, data) {
    return new Promise(function(resolve, reject) {
        var xhr = new XMLHttpRequest();
        xhr.open('POST', url, true);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.onload = function() {
            var body = null;
            try { body = JSON.parse(xhr.responseText); } catch (e) {}
            if (xhr.status >= 200 && xhr.status < 300) { resolve(body); }
            else { reject({status: xhr.status, body: body}); }
        };
        xhr.onerror = function() { reject({status: 0, body: null}); };
        xhr.send(JSON.stringify(data));
    });
}

function aiGetJSON(url) {
    return new Promise(function(resolve, reject) {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', url, true);
        xhr.onload = function() {
            try {
                if (xhr.status === 200) { resolve(JSON.parse(xhr.responseText)); }
                else { reject(new Error('Status ' + xhr.status)); }
            } catch (e) { reject(e); }
        };
        xhr.onerror = function() { reject(new Error('network')); };
        xhr.send();
    });
}

function aiAnalyzeError(status, body) {
    var detail = (body && body.detail) || '';
    if (status === 409) return '任务在跑';
    if (status === 404) return '不在本轮';
    if (detail.indexOf('API Key') >= 0) return '未配Key';
    return detail.slice(0, 8) || '启动失败';
}

function analyzeOne(code, btn) {
    var orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '分析中…';
    aiPostJSON('/api/llm/analyze', {mode: 'once', code: code})
        .then(function(d) {
            if (d && d.status === 'started') { aiPollDone(btn, orig); }
            else {
                btn.textContent = '无需补跑';
                setTimeout(function() { btn.textContent = orig; btn.disabled = false; }, 3000);
            }
        })
        .catch(function(e) {
            btn.textContent = aiAnalyzeError(e.status, e.body);
            setTimeout(function() { btn.textContent = orig; btn.disabled = false; }, 4000);
        });
}

function aiPollDone(btn, orig) {
    var tries = 0;
    var timer = setInterval(function() {
        tries++;
        aiGetJSON('/api/progress').then(function(p) {
            if (p.stage === 'done' || tries > 60) {
                clearInterval(timer);
                btn.textContent = '完成✓';
                setTimeout(function() { location.reload(); }, 900);
            }
        }).catch(function() {
            if (tries > 60) {
                clearInterval(timer);
                btn.textContent = orig;
                btn.disabled = false;
            }
        });
    }, 3000);
}
