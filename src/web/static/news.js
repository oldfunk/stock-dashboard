/* 新闻滚动条（2026-10-19）：数据源 /api/news/latest，后台持续拉取，前端只读 */
(function () {
  const track = document.getElementById("newsTrack");
  if (!track) return;

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"
  }[c]));

  function render(items) {
    if (!items || !items.length) {
      track.innerHTML = '<span class="news-empty">暂无新闻，后台采集中</span>';
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
    const html = items.map(one).join("");
    track.innerHTML = '<div class="news-inner">' + html + html + "</div>";
    // 速度与内容长度挂钩：内容越多滚得越久，阅读节奏恒定（约 14 字/秒）
    const chars = track.querySelector(".news-inner").textContent.length;
    const seconds = Math.max(180, Math.min(1200, Math.round(chars / 14)));
    track.querySelector(".news-inner").style.setProperty("--news-speed", seconds + "s");
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

  load();
  setInterval(load, 60000);
})();
