/* 新闻滚动条（2026-10-19）：数据源 /api/news/latest，刷新按钮触发抓取 */
/* eslint-disable */
(function () {
  const track = document.getElementById("newsTrack");
  const btn = document.getElementById("newsRefresh");
  if (!track) return;

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"
  }[c]));

  function render(items) {
    if (!items || !items.length) {
      track.innerHTML = '<span class="news-empty">暂无新闻，点击刷新抓取</span>';
      return;
    }
    const one = (it) => {
      let cls = "flat";
      if (it.sentiment === "利好") cls = "good";
      else if (it.sentiment === "利空") cls = "bad";
      const sent = it.sentiment
        ? '<span class="news-sent ' + cls + '">' + esc(it.sentiment) + "</span>"
        : "";
      const who = it.name || it.code;
      const tag = who ? '<span class="news-code">' + esc(who) + "</span>" : "";
      const body = esc(it.title);
      const inner = it.url
        ? '<a href="' + esc(it.url) + '" target="_blank" rel="noopener">' + body + "</a>"
        : body;
      return '<span class="news-item">' + sent + tag + inner + "</span>";
    };
    // 复制一份用于无缝滚动
    const html = items.map(one).join("");
    track.innerHTML = '<div class="news-inner">' + html + html + "</div>";
  }

  async function load() {
    try {
      const r = await fetch("/api/news/latest?limit=30");
      const d = await r.json();
      render(d.items || []);
    } catch (e) {
      track.innerHTML = '<span class="news-empty">新闻加载失败</span>';
    }
  }

  if (btn) {
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = "抓取中";
      try {
        await fetch("/api/news/ingest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ per_code: 8 }),
        });
        await load();
      } catch (e) {
        track.innerHTML = '<span class="news-empty">抓取失败，稍后再试</span>';
      } finally {
        btn.disabled = false;
        btn.textContent = "刷新";
      }
    };
  }

  load();
  setInterval(load, 300000);
})();
