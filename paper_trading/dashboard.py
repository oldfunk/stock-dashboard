"""只读 Web 仪表盘：直观展示账户、NAV、AI 操作流水。

用法:
    python -m paper_trading.dashboard --port 8080
    # 本机浏览器打开 http://127.0.0.1:8080；局域网其他设备用运行机器的局域网 IP 同端口

纯标准库实现，不写任何数据（只读 SQLite），可与交易进程并存。
"""
from __future__ import annotations

import argparse
import hmac
import json
import math
import sqlite3
import sys
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGE = r"""<!doctype html>
<html lang="zh-CN" data-theme="light" data-color-scheme="cn">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>模拟交易仪表盘</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Noto+Sans+SC:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
/* 设计语言与 stock-dashboard 对齐：浅色主题 / 红涨绿跌 / 8px 圆角 */
:root{
--font-sans:'Inter','Noto Sans SC',-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;
--bg-primary:#f8f9fa;--bg-secondary:#ffffff;--bg-tertiary:#f0f1f3;--bg-hover:#e8eaed;
--text-primary:#1a1c1e;--text-secondary:#5f6368;--text-tertiary:#9aa0a6;
--accent:#1a73e8;--accent-hover:#1557b0;--accent-soft:#e8f0fe;
--green:#137333;--green-bg:#e6f4ea;--red:#c5221f;--red-bg:#fce8e6;
--color-up:var(--red);--color-up-bg:var(--red-bg);--color-down:var(--green);--color-down-bg:var(--green-bg);
--divider:#e0e3e7;--divider-thin:#e8eaed;
--shadow:0 1px 3px rgba(0,0,0,0.04),0 1px 2px rgba(0,0,0,0.02);
--radius:8px;--radius-sm:4px}
[data-theme="dark"]{
--bg-primary:#1a1c1e;--bg-secondary:#202124;--bg-tertiary:#282a2d;--bg-hover:#303134;
--text-primary:#e8eaed;--text-secondary:#9aa0a6;--text-tertiary:#5f6368;
--accent:#8ab4f8;--accent-hover:#aecbfa;--accent-soft:#1a3a5c;
--green:#81c995;--green-bg:#1e3527;--red:#f28b82;--red-bg:#3c2022;
--divider:#3c4043;--divider-thin:#303134;--shadow:0 1px 3px rgba(0,0,0,0.2)}
*{box-sizing:border-box}*,*::before,*::after{margin:0;padding:0}
body{margin:0;background:var(--bg-primary);color:var(--text-primary);font:400 14px/1.5 var(--font-sans);-webkit-font-smoothing:antialiased;transition:background .2s,color .2s}
.app{max-width:1440px;margin:0 auto;padding:24px 32px;overflow-x:hidden}
.header{display:flex;justify-content:space-between;align-items:center;padding-bottom:16px;border-bottom:1px solid var(--divider-thin);margin-bottom:24px}
.header-left{display:flex;align-items:baseline;gap:16px}
.header-title{font-size:18px;font-weight:500;letter-spacing:-.01em;margin:0}
.header-subtitle{font-size:12px;color:var(--text-tertiary)}
.header-right{display:flex;align-items:center;gap:12px}
.live-indicator{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:500;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.05em}
.live-dot{width:6px;height:6px;border-radius:50%;background:var(--green);animation:pulse-dot 2s ease-in-out infinite}
@keyframes pulse-dot{0%,100%{opacity:1}50%{opacity:.4}}
.last-updated{font-size:11px;color:var(--text-tertiary)}
.theme-toggle{background:none;border-width:0;cursor:pointer;padding:6px;border-radius:var(--radius-sm);color:var(--text-secondary);font-size:14px;line-height:1}
.theme-toggle:hover{background:var(--bg-hover);color:var(--text-primary)}
.market-row{display:grid;grid-template-columns:repeat(5,1fr);gap:16px;padding:16px 0;margin-bottom:24px;border-bottom:1px solid var(--divider-thin)}
.kpi-sub{font-size:11px;color:var(--text-tertiary);margin-top:3px}
.kpi-tag{font-size:10px;padding:0 4px;border:1px solid var(--divider);border-radius:3px;color:var(--text-tertiary);margin-left:4px;vertical-align:1px}
.market-item{padding:10px 0}
.market-name{font-size:11px;font-weight:500;color:var(--text-tertiary);letter-spacing:.06em;margin-bottom:2px}
.market-value{font-size:21px;font-weight:300;letter-spacing:-.02em;line-height:1.2;font-variant-numeric:tabular-nums}
.market-change{font-size:12px;margin-top:2px;color:var(--text-tertiary)}
.status-bar{display:flex;gap:24px;padding:10px 0;margin-bottom:24px;font-size:12px;color:var(--text-secondary);border-bottom:1px solid var(--divider-thin);flex-wrap:wrap}
.status-item{display:flex;align-items:center;gap:6px}
.status-label{color:var(--text-tertiary)}
.status-value{font-weight:500;color:var(--text-primary)}
.section-header{display:flex;justify-content:space-between;align-items:center;padding:12px 0 8px;margin-bottom:16px;border-bottom:1px solid var(--divider-thin)}
.section-title{font-size:12px;font-weight:600;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.05em;margin:0}
.section-count{font-size:12px;color:var(--text-tertiary)}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--bg-secondary)}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--divider);white-space:nowrap}
th{color:var(--text-secondary);font-weight:600;font-size:12px}
/* AI 操作流水：详情与结果列自动换行，不左右滑动 */
#ops td:nth-child(3),#ops td:nth-child(4){white-space:normal;word-break:break-word;min-width:220px}
#ops td:nth-child(1),#ops td:nth-child(2),#ops td:nth-child(5){white-space:nowrap}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:var(--bg-hover)}
.num{text-align:right;font-variant-numeric:tabular-nums}
.up{color:var(--color-up)} .down{color:var(--color-down)}
.ok{color:var(--green);font-weight:600} .bad{color:var(--red);font-weight:600}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;font-weight:600}
.tag.buy{background:var(--color-up-bg);color:var(--color-up)} .tag.sell{background:var(--color-down-bg);color:var(--color-down)}
.tag.live{background:var(--accent-soft);color:var(--accent)}
.scroll{overflow-x:auto}
/* 长列表折叠：默认只显示最新 N 条，其余收起（最新在上） */
tfoot .more-cell{padding:0;border-bottom:none;text-align:center}
.more-btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;width:100%;padding:9px 12px;margin:0;border-width:0;border-top:1px dashed var(--divider);background:transparent;color:var(--text-tertiary);font-size:12px;font-family:inherit;letter-spacing:.02em;cursor:pointer}
.more-btn:hover{color:var(--accent);background:var(--bg-hover)}
.more-btn::before{content:"";width:0;height:0;border-left:4px solid transparent;border-right:4px solid transparent;border-top:5px solid currentColor;transition:transform .15s}
.more-btn[data-open="1"]::before{transform:rotate(180deg)}
.mut{color:var(--text-tertiary)}
.ai-panel{background:var(--bg-secondary);border:1px solid var(--divider);border-radius:var(--radius);padding:14px 16px;margin-bottom:14px;box-shadow:var(--shadow)}
.ai-row{display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap}
.ai-row label{font-size:13px;color:var(--text-secondary);white-space:nowrap}
.ai-row select,.ai-row input,.ai-row textarea{font-size:13px;padding:6px 8px;border:1px solid var(--divider);border-radius:var(--radius-sm);background:var(--bg-secondary);color:var(--text-primary);font-family:inherit}
.ai-row button{font-size:13px;padding:6px 14px;border-radius:var(--radius-sm);border:1px solid var(--divider);background:var(--bg-secondary);color:var(--text-primary);cursor:pointer}
.ai-row button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.ai-row button.primary:hover{background:var(--accent-hover)}
.ai-msg{font-size:13px;min-height:18px;margin:4px 0}
.ai-answer{white-space:pre-wrap;font-size:13px;line-height:1.7;background:var(--bg-primary);border:1px solid var(--divider);border-radius:var(--radius-sm);padding:10px;margin:6px 0}
.s{font-size:12px;color:var(--text-tertiary);margin-top:2px}
/* P0（T95）：曲线 / KPI / 今日卡 / 迷你走势 */
.overview-grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:20px;margin-bottom:20px}
.card{background:var(--bg-secondary);border:1px solid var(--divider);border-radius:var(--radius);padding:16px;box-shadow:var(--shadow)}
.card-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap}
.card-head .section-title{margin:0}
.seg{display:inline-flex;border:1px solid var(--divider);border-radius:var(--radius-sm);overflow:hidden}
.seg button{border:0;background:transparent;color:var(--text-secondary);font:inherit;font-size:12px;padding:5px 10px;cursor:pointer}
.seg button+button{border-left:1px solid var(--divider)}
.seg button.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.chart-wrap{position:relative;width:100%;height:260px;margin:6px 0 2px}
#perfchart{width:100%;height:100%;display:block;overflow:visible}
.chart-tip{position:absolute;pointer-events:none;display:none;background:var(--bg-secondary);border:1px solid var(--divider);border-radius:var(--radius-sm);padding:8px 10px;font-size:12px;line-height:1.65;box-shadow:var(--shadow);z-index:5;white-space:nowrap}
.cov{font-size:11px;color:var(--text-tertiary);margin-top:6px;display:flex;gap:8px;flex-wrap:wrap}
.cov .badge{border:1px solid var(--divider);border-radius:3px;padding:0 5px}
.cov.warn .badge{border-color:#e8b4b1;color:var(--red)}
.today-grid{display:flex;flex-direction:column}
.today-row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid var(--divider-thin);font-size:13px}
.today-row:last-child{border-bottom:none}
.today-row .lab{color:var(--text-tertiary);font-size:12px}
.today-row .val{font-weight:500;text-align:right}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);margin-right:6px;vertical-align:1px}
.dot.bad{background:var(--red)}.dot.warn{background:#e8a33d}
.spark{width:76px;height:24px;display:block}
.fold{margin:0 0 24px;border:1px solid var(--divider);border-radius:var(--radius);background:var(--bg-secondary)}
.fold summary{cursor:pointer;padding:10px 14px;font-size:12px;color:var(--text-secondary);font-weight:600;letter-spacing:.04em}
.fold summary .s{font-weight:400;margin-left:6px}
.fold[open] summary{border-bottom:1px solid var(--divider)}
.fold .scroll{padding:0 4px 4px}
.empty{padding:36px 8px;text-align:center;color:var(--text-tertiary);font-size:13px}
/* CSV 导出：与 section 标题同一行，轻量按钮，不抢结果信息 */
.csv-btn{font-size:11px;color:var(--accent);text-decoration:none;border:1px solid var(--divider);border-radius:3px;padding:1px 6px;margin-left:8px;white-space:nowrap}
.csv-btn:hover{background:var(--accent-soft)}
/* P1（T97）：任务视图 + 运维卡 */
.tabs{display:flex;gap:4px;margin:-8px 0 20px;border-bottom:1px solid var(--divider-thin);overflow-x:auto}
.tabs button{border:0;background:transparent;font:inherit;font-size:13px;color:var(--text-secondary);padding:9px 12px;border-bottom:2px solid transparent;cursor:pointer;white-space:nowrap}
.tabs button:hover{background:var(--bg-hover)}
.tabs button.on{color:var(--text-primary);font-weight:600;border-bottom-color:var(--accent)}
[data-tab]:not(button){display:none}
[data-tab]:not(button).on{display:block}
.overview-grid[data-tab].on{display:grid}
.hb-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.hb{border:1px solid var(--divider-thin);border-radius:var(--radius-sm);padding:10px 12px}
.hb .k{font-size:11px;color:var(--text-tertiary);letter-spacing:.04em}
.hb .v{font-size:13px;font-weight:600;margin-top:2px}
.hb .a{font-size:11px;color:var(--text-tertiary);margin-top:2px}
.hb.bad{border-color:#e8b4b1}
.hb.warn{border-color:#eed3a8}
.plan-line{display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid var(--divider-thin);font-size:13px}
.plan-line:last-child{border-bottom:none}
.plan-line .lab{color:var(--text-tertiary);font-size:12px}
.status-chip{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;font-weight:600;background:var(--bg-tertiary);color:var(--text-secondary)}
.status-chip.ok{background:var(--green-bg);color:var(--green)}
.status-chip.bad{background:var(--red-bg);color:var(--red)}
.status-chip.warn{background:#fff3e0;color:#b26a00}
@media(max-width:900px){.hb-grid{grid-template-columns:repeat(2,1fr)}}
#schemelist{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:8px}
#schemelist label{white-space:normal}
@media(max-width:1100px){.overview-grid{grid-template-columns:1fr}.market-row{grid-template-columns:repeat(3,1fr)}}
@media(max-width:720px){.app{padding:16px}.market-row{grid-template-columns:repeat(2,1fr)}.chart-wrap{height:220px}}
</style>
</head>
<body>
<div class="app">
<header class="header">
  <div class="header-left"><h1 class="header-title">模拟交易仪表盘</h1><span class="header-subtitle">纸盘交易 · 只读</span></div>
  <div class="header-right">
    <div class="live-indicator"><span class="live-dot"></span><span>运行中</span></div>
    <span class="last-updated" id="clock">加载中…</span>
    <button class="theme-toggle" id="themeBtn" title="切换深色/浅色">◐</button>
  </div>
</header>

<nav class="tabs" id="tabs" role="tablist" aria-label="页面视图">
  <button data-tab="overview" class="on" role="tab">总览</button>
  <button data-tab="trading" role="tab">交易</button>
  <button data-tab="ops" role="tab">运维</button>
  <button data-tab="control" role="tab">控制台</button>
</nav>

<div class="market-row" id="cards"></div>
<div class="status-bar" id="healthbar" style="display:none;border-color:#e0524d;color:#e0524d"></div>
<div class="status-bar" id="statusbar"></div>

<div class="overview-grid" data-tab="overview">
  <section class="card">
    <div class="card-head">
      <h2 class="section-title">收益曲线</h2>
      <a class="csv-btn" href="/api/export.csv?type=performance">导出 CSV</a>
      <div class="seg" id="perfmodes" role="tablist" aria-label="曲线口径">
        <button data-mode="nav" class="on" role="tab" aria-selected="true">总资产</button>
        <button data-mode="ret" role="tab" aria-selected="false">累计收益率</button>
        <button data-mode="dd" role="tab" aria-selected="false">回撤</button>
      </div>
    </div>
    <div class="chart-wrap" id="chartwrap">
      <svg id="perfchart" role="img" aria-label="收益曲线"></svg>
      <div class="chart-tip" id="charttip"></div>
    </div>
    <div class="cov" id="perf-cov"></div>
  </section>
  <section class="card">
    <div class="card-head"><h2 class="section-title">今日 / 系统</h2><span class="s" id="ov-date"></span></div>
    <div class="today-grid" id="todaygrid"><div class="s">加载中…</div></div>
  </section>
</div>

<section data-tab="overview" style="margin-bottom:16px">
  <div class="section-header"><h2 class="section-title">分时</h2><span class="section-count" id="c-intraday"></span></div>
  <div class="card">
    <div class="chart-wrap" id="intradaywrap" style="height:220px">
      <svg id="intradaychart" role="img" aria-label="今日分时权益"></svg>
      <div class="chart-tip" id="intradaytip"></div>
    </div>
    <div class="s" id="intraday-note"></div>
  </div>
</section>

<details class="fold" id="navfold" data-tab="overview">
  <summary>净值明细 <span class="s" id="c-nav"></span><a class="csv-btn" href="/api/export.csv?type=nav" onclick="event.stopPropagation()">CSV</a></summary>
  <div class="scroll"><table id="nav"></table></div>
</details>

<section data-tab="overview">
  <div class="section-header"><h2 class="section-title">当前持仓</h2><span class="section-count" id="c-pos"></span><a class="csv-btn" href="/api/export.csv?type=positions">CSV</a></div>
  <div class="scroll"><table id="pos"></table></div>
</section>

<section data-tab="trading">
  <div class="section-header"><h2 class="section-title">订单记录</h2><span class="section-count" id="c-orders"></span><a class="csv-btn" href="/api/export.csv?type=orders">CSV</a></div>
  <div class="scroll"><table id="orders"></table></div>
</section>

<section data-tab="trading">
  <div class="section-header"><h2 class="section-title">意图队列</h2><span class="section-count" id="c-intents"></span><a class="csv-btn" href="/api/export.csv?type=intents">CSV</a></div>
  <div class="scroll"><table id="intents"></table></div>
</section>

<section data-tab="control">
  <div class="section-header"><h2 class="section-title">投资方案</h2><span class="section-count" id="c-scheme"></span></div>
  <div class="ai-panel">
    <div class="s">三种方案只选其一。母价值需 Stock Dashboard 在同一台机器；自定义点行内“设置”写自然语言交易策略。切换只写本地文件，不进 git。</div>
    <div class="ai-row" id="schemelist"></div>
    <div class="ai-row" id="instructionBox" style="display:none">
      <textarea id="instruction" rows="3" placeholder="自定义指令，例如：只做银行股反弹，单只最多买5000元，跌破买入价5%就卖" style="flex:1;min-width:240px"></textarea>
    </div>
    <div class="ai-row">
      <button id="btn-scheme" class="primary">保存方案与指令</button>
      <span id="schemestat" class="mut" style="align-self:center"></span>
    </div>
    <div class="s" id="motherschemes"></div>
  </div>
</section>

<section data-tab="control">
  <div class="section-header"><h2 class="section-title">运行时刻</h2><span class="section-count" id="c-sched"></span></div>
  <div class="ai-panel">
    <div class="s">交易时段和 AI 窗口由代码写死，不再按面板队列跑：交易日 09:30–11:30、13:00–15:00；09:30–10:00 只执行前一交易日计划；16:30–18:30 数据定稿后做下一交易日计划；盘中每分钟检查异动，只有异动才调 AI；规则/条件单由引擎每分钟执行。</div>
    <div class="ai-row" id="schedrows" style="flex-wrap:wrap"></div>
    <div class="s" id="schedtoday"></div>
    <div class="s">旧版“AI 定时队列”已停用；需要人工补跑时用 CLI：`agent run --plan-only`、`agent event --force`。手动 run 不再被旧队列拦住。</div>
  </div>
</section>

<section data-tab="ops">
  <div class="section-header"><h2 class="section-title">运行健康</h2><span class="section-count" id="c-health"></span></div>
  <div class="card">
    <div class="hb-grid" id="hbgrid"></div>
    <div class="s" id="health-det"></div>
  </div>
</section>

<section data-tab="ops">
  <div class="section-header"><h2 class="section-title">今日体检</h2><span class="section-count" id="c-checks"></span></div>
  <div class="card"><div id="checksbox" class="s">加载中…</div></div>
</section>

<section data-tab="ops">
  <div class="section-header"><h2 class="section-title">最新计划与数据新鲜度</h2><span class="section-count" id="c-plan"></span></div>
  <div class="card"><div id="planbox" class="s">加载中…</div></div>
</section>

<section data-tab="ops">
  <div class="section-header"><h2 class="section-title">数据来源</h2><span class="section-count" id="c-prov"></span></div>
  <div class="ai-panel">
    <div class="s" id="prov-sum">数据来源：加载中…</div>
    <div class="scroll"><table id="prov"></table></div>
  </div>
</section>

<section data-tab="control">
  <div class="section-header"><h2 class="section-title">规则引擎</h2><span class="section-count" id="c-engine"></span></div>
  <div class="ai-panel">
    <div class="s" id="engine-sum">规则引擎：加载中…</div>
    <div class="ai-row" id="engine-rules"></div>
    <div id="engine-trig"></div>
    <div class="ai-row">
      <button id="btn-engine-backtest" class="primary">回测规则集</button>
      <select id="engine-grid" style="width:170px">
        <option value="ma_cross">网格：均线交叉</option>
        <option value="rsi_zone">网格：RSI 区间</option>
        <option value="trailing_stop">网格：移动止损</option>
        <option value="vwap_dev">网格：偏离均价（日内）</option>
        <option value="minute_ma">网格：分钟均线（日内）</option>
      </select>
      <button id="btn-engine-optimize">参数网格</button>
      <button id="btn-engine-wf">样本外检验</button>
      <span id="engine-stat" class="mut" style="align-self:center"></span>
    </div>
    <div id="engine-out" class="s"></div>
  </div>
</section>

<section data-tab="trading">
  <div class="section-header"><h2 class="section-title">条件单</h2><span class="section-count" id="c-cond"></span><a class="csv-btn" href="/api/export.csv?type=cond">CSV</a></div>
  <div class="ai-panel">
    <div class="s">条件单 = 现在不成交、只挂条件。盘中引擎每分钟核对实时价，触发即过风控下单（AI 设计或人工添加均可）。</div>
    <div class="ai-row">
      <input id="cond-sym" placeholder="代码 600519" style="width:110px">
      <select id="cond-side"><option value="buy">买入</option><option value="sell">卖出</option></select>
      <select id="cond-type">
        <option value="price_below">价跌破</option>
        <option value="price_above">价升破</option>
        <option value="pct_below">日内跌超%</option>
        <option value="pct_above">日内涨超%</option>
      </select>
      <input id="cond-trigger" type="number" step="0.01" placeholder="触发值" style="width:110px">
      <input id="cond-value" type="number" step="100" placeholder="金额(元)" style="width:110px">
      <input id="cond-expiry" type="date" style="width:150px">
      <button id="btn-cond-add" class="primary">挂条件单</button>
      <button id="btn-cond-design">AI 设计</button>
      <span id="condstat" class="mut" style="align-self:center"></span>
    </div>
    <div class="scroll"><table id="conds"></table></div>
  </div>
</section>

<section data-tab="control">
  <div class="section-header"><h2 class="section-title">AI 问答</h2></div>
  <div class="ai-panel">
    <div class="ai-row">
      <textarea id="q" rows="3" placeholder="例如：结合持仓和行情，评价一下当前三只股票" style="flex:1;min-width:240px"></textarea>
    </div>
    <div class="ai-row">
      <button id="btn-ask" class="primary">提问（记流水）</button>
    </div>
    <div id="ans" class="ai-msg">回答会显示在这里，同时记入下方操作流水。每次提问会自动附带账户、持仓、近期行情与操作记录，不用你贴数据。</div>
  </div>
</section>

<section data-tab="ops">
  <div class="section-header"><h2 class="section-title">AI 操作流水</h2><span class="section-count" id="c-ops"></span></div>
  <div class="scroll"><table id="ops"></table></div>
</section>

<section data-tab="control">
  <div class="section-header"><h2 class="section-title">模型设置</h2></div>
  <div class="ai-panel">
    <div class="s">先选厂商（接口地址自动填好），填 Key，拉模型列表选一个，保存即可。**本地接口（Ollama / LM Studio / vLLM / 局域网网关）不需要 Key，留空就行**。管理口令由浏览器自动保管，不用填也不用记；**只改模型名/厂商/Key 不需要口令，改接口地址才需要口令**（或填写 API Key 接管一次）。</div>
    <div class="ai-row"><label>厂商 / 接口地址 / 模型</label></div>
    <div class="ai-row">
      <select id="preset">
        <option value="deepseek">DeepSeek</option>
        <option value="qwen">通义千问（阿里）</option>
        <option value="moonshot">Kimi（月之暗面）</option>
        <option value="glm">智谱 GLM</option>
        <option value="doubao">豆包（火山引擎 Ark）</option>
        <option value="openai">OpenAI</option>
        <option value="ollama">本机 Ollama（无需 Key）</option>
        <option value="lmstudio">本机 LM Studio（无需 Key）</option>
        <option value="vllm">本机 vLLM（无需 Key）</option>
        <option value="lan">局域网网关（无需 Key）</option>
        <option value="custom">自定义（兼容网关/代理）</option>
      </select>
      <input id="base" placeholder="接口地址 https://…/v1" style="flex:2;min-width:200px">
    </div>
    <div class="ai-row">
      <input id="apikey" type="password" placeholder="API Key（本地接口可留空；只存本机，不回显）" style="flex:2;min-width:200px">
    </div>
    <div class="ai-row">
      <select id="modelsel" style="flex:1;min-width:160px">
        <option value="">下拉选择（先点“拉取模型列表”）</option>
      </select>
      <input id="model" placeholder="或手填模型名称" style="flex:2;min-width:200px">
    </div>
    <div class="ai-row">
      <button id="btn-models">拉取模型列表</button>
      <button id="btn-save" class="primary">保存配置</button>
      <span id="llmstat" class="mut" style="align-self:center"></span>
    </div>
    <div class="s">Key 只保存在本机 secrets.local.json（0600 权限），永不进 git、不进日志；页面只显示掩码。保存一次长期有效：重启不丢、换模型不用重填。**本地/局域网接口不填 Key 也能拉模型和对话**（选那类预设会自动清掉旧云端 Key，避免把 Key 发到别的机器）。局域网使用，不要把面板暴露到公网。</div>
  </div>
</section>
</div>

<script>
const fmt=(n,d=2)=>n==null?"-":Number(n).toLocaleString("zh-CN",{minimumFractionDigits:d,maximumFractionDigits:d});
const pct=n=>n==null?"-":(n*100).toFixed(2)+"%";
const cls=n=>n>0?"up":n<0?"down":"mut";
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function get(u){const r=await fetch(u);return r.json()}
function rows(t,head,body){t.innerHTML="<thead><tr>"+head.map(h=>`<th${h[1]?' class="num"':''}>${h[0]}</th>`).join("")+"</tr></thead><tbody>"+body+"</tbody>"}

/* 长列表折叠渲染：默认只显示最新 N 条（bodyArr 须已按最新在上排序），
   其余收进 tfoot 的“展开剩余 N 条”。展开状态按表 id 记忆，刷新不丢。 */
const COLLAPSE={};
function rowsC(t,head,bodyArr,showN,emptyLabel){
  t._head=head;t._body=bodyArr;t._showN=showN;t._empty=emptyLabel;
  const total=bodyArr.length, open=!!COLLAPSE[t.id];
  const vis=open?bodyArr:bodyArr.slice(0,showN);
  const hid=open?[]:bodyArr.slice(showN);
  t.innerHTML="<thead><tr>"+head.map(h=>`<th${h[1]?' class="num"':''}>${h[0]}</th>`).join("")+"</tr></thead>"+
    "<tbody>"+(total?vis.join(""):
      `<tr><td colspan="${head.length}" class="mut">${emptyLabel||"暂无记录"}</td></tr>`)+"</tbody>"+
    (hid.length?`<tfoot><tr><td colspan="${head.length}" class="more-cell">`+
      `<button class="more-btn" type="button" data-open="${open?"1":""}">${open?"收起":`展开剩余 ${hid.length} 条`}</button></td></tr></tfoot>`:"");
  const btn=t.querySelector(".more-btn");
  if(btn)btn.onclick=()=>{COLLAPSE[t.id]=!COLLAPSE[t.id];rowsC(t,t._head,t._body,t._showN,t._empty);};
  if(t._after)t._after();
}

/* ---- 中文映射（名称表来自 /api/names，均为行情源真实名称） ----
   展示规范：股票一律写成 名称(代码)，如 贵州茅台(600519)；
   名称缺失时只写代码，不写“未知”等占位词。 */
let NAMES={};                                   // {symbol: 真实名称}
const sym=c=>NAMES[c]?`${NAMES[c]}(${c})`:c;
const ACTION_CN={
  "run":"每日结算","cron-run":"每日结算（定时任务）","run:dry-run":"试运行（仅预览，不下单）",
  "sync":"同步行情","buy":"买入","sell":"卖出","preview":"下单试算","status":"查询账户",
  "nav":"查询净值","history":"查询记录","names":"刷新股票名称",
  "llm:ask":"AI 问答","llm:config":"保存模型配置","ai:decide":"AI 决策","ai:plan":"AI 计划",
  "ai:review":"AI 事件复盘","ai:event":"AI 事件触发",
  "engine:buy":"规则引擎买入","engine:sell":"规则引擎卖出",
  "cond:design":"AI 设计条件单","cond:buy":"条件单触发买入","cond:sell":"条件单触发卖出"};
function errCN(e){
  if(!e)return"";
  const M=[[/multiple of 100/i,"数量必须为 100 股的整数倍"],
    [/Insufficient cash/i,"可用资金不足"],
    [/Insufficient available/i,"可卖股数不足（当天买入的要下一个交易日才能卖）"],
    [/Risk rejected/i,"风控拒绝"],
    [/exceeds max/i,"超过单笔下单金额上限"],
    [/Position limit exceeded/i,"单只股票持仓超限"],
    [/Total position limit exceeded/i,"账户总持仓超限"],
    [/Drawdown halt/i,"触发回撤熔断，已暂停交易"],
    [/over limit-up/i,"买入价超过涨停价"],
    [/below limit-down/i,"卖出价低于跌停价"],
    [/below bar low/i,"买入价低于当日成交区间"],
    [/above bar high/i,"卖出价高于当日成交区间"],
    [/No data for/i,"没有该股票的行情数据"],
    [/no limit price/i,"未指定买卖价格"],
    [/Invalid direction/i,"买卖方向参数错误"]];
  for(const[r,c]of M){if(r.test(e))return c}
  return e;
}
function paramCN(a,j){let p={};try{p=JSON.parse(j||"{}")}catch(e){}
  if(a==="run"||a==="cron-run"||a==="run:dry-run"||a==="sync")return`股票：${(p.symbols||[]).map(sym).join("、")}`;
  if(a==="buy"||a==="sell"){const px=(p.price!=null&&p.price!=="")?`，限价 ${fmt(p.price)} 元`:"";return `${sym(p.symbol)} ${p.volume}股${px}`;}
  if(a==="llm:ask")return `问 ${p.model||"AI"}：${(p.prompt||"").slice(0,120)}`;
  if(a==="ai:decide")return `${p.mode==="dry"?"试运行":"实盘"}：股票 ${(p.symbols||[]).map(sym).join("、")}${p.summary?"——"+p.summary.slice(0,80):""}`;
  if(a==="ai:review")return `事件复盘：股票 ${(p.pool||[]).map(sym).join("、")||"空"}${p.event?"——"+String(p.event).slice(0,100):""}`;
  if(a==="ai:event")return `异动 ${(p.events||[]).length} 个：${(p.selected||[]).map(sym).join("、")}${p.dry_run?"（试运行）":""}`;
  if(a==="llm:config")return `厂商 ${p.preset||""}，模型 ${p.model||"未填"}`;
  if(a==="cond:design")return `股票池 ${(p.pool||[]).map(sym).join("、")||"空"}，挂上 ${p.n??0} 条`;
  if(a==="engine:buy"||a==="engine:sell"||a==="cond:buy"||a==="cond:sell"){
    const c=p.cond?`，触发条件 ${p.cond}`:"";
    return `${sym(p.symbol)} ${p.volume??""}${p.volume?"股":""} @ ${fmt(p.price)} 元${c}`;}
  if(a==="ai:plan")return `休盘计划（数据截至${p.asof||"未知"}）：股票 ${(p.symbols||[]).map(sym).join("、")}`;
  if(a==="preview")return `${sym(p.symbol)} ${(p.direction==="buy"?"买入":"卖出")} ${p.volume}股`;
  return Object.entries(p).map(([k,v])=>`${k}=${Array.isArray(v)?v.map(sym).join("、"):v}`).join(" ");}
function resultCN(a,ok,j){let r={};try{r=JSON.parse(j||"{}")}catch(e){}
  if(!ok)return errCN(r.error||"被拒");
  if(a==="buy"||a==="sell"){const fee=(Number(r.commission)||0)+(Number(r.stamp_duty)||0)+(Number(r.transfer_fee)||0);
    return `成交价 ${fmt(r.filled_price)} 元，手续费 ${fmt(fee)} 元`;}
  if(a==="run:dry-run")return`产生信号 ${r.signals??0} 个（仅预览，未下单）`;
  if(a==="llm:ask")return (r.answer||"").slice(0,200);
  if(a==="ai:decide"){const sk=r.skipped||"";
    if(sk==="no-fresh-bars")return `无今日新行情（最新 ${r.latest||"未知"}），跳过（节假日或源未更新）`;
    if(sk==="already-decided")return "今日已决策，跳过";
    if(sk==="not-in-schedule")return "旧定时队列已停用；现在按代码运行时刻执行（见“运行时刻”）";
    if(sk==="drawdown-halt")return `回撤熔断（${(r.drawdown*100).toFixed(2)}%），停手`;
    if(sk==="daily-loss-halt")return `日亏熔断（${(r.pnl_pct*100).toFixed(2)}%），停手`;
    const ds=r.decisions||[],cs=r.conditions||{};
    const nc=(cs.created||[]).length,nr=(cs.rejected||[]).length;
    const np=(cs.parse_rejected||[]).length;
    const ctail=(nc||nr||np)?`；条件单 ${nc} 条${nr?`（${nr} 条被拒）`:""}${np?`（解析未通过 ${np} 条）`:""}`:"";
    if(!ds.length)return nc?`挂条件单 ${nc} 条`:"无动作";
    return ds.map(d=>{const dir=d.action==="buy"?"买入":d.action==="sell"?"卖出":"持有";
      return `${dir}${sym(d.symbol)}${d.volume||""}股(${d.status||""})`}).join("；")+ctail;}
  if(a==="ai:review"){const n=(r.created||[]).length,bad=(r.rejected||[]).length;
    const pr=r.parse_rejected||[];
    return `解析 ${r.parsed??0} 条，挂上 ${n} 条${bad?`，${bad} 条被拒`:""}${pr.length?`，解析未通过 ${pr.length} 条（${pr[0].why||""}）`:""}${r.skipped&&r.skipped.length?`，跳过 ${r.skipped.length} 条重复`:""}`;}
  if(a==="ai:event")return `触发 ${(r.events||[]).length} 个异动，复盘 ${(r.selected||[]).map(sym).join("、")||"无"}${r.dry_run?"（试运行）":""}`;
  if(a==="llm:config")return `已保存（${r.base_url||""}）`;
  if(a==="cond:design"){const n=(r.created||[]).length,bad=(r.rejected||[]).length;
    const pr=(r.parse_rejected||[]).length;
    return (bad||pr)?`挂上 ${n} 条，${bad+pr} 条未通过校验`:(n?`挂上 ${n} 条条件单`:"没有符合条件的条件单");}
  if(a==="engine:buy"||a==="engine:sell"||a==="cond:buy"||a==="cond:sell"){
    const fee=(Number(r.commission)||0)+(Number(r.stamp_duty)||0)+(Number(r.transfer_fee)||0);
    return `成交价 ${fmt(r.filled_price)} 元，手续费 ${fmt(fee)} 元`;}
  if(a==="ai:plan"){
    if(r.skipped==="no-fresh-data"){
      const f=r.freshness||{};
      return `计划跳过：数据覆盖不足（${f.fresh??"?"}/${f.total??"?"}，最旧 ${f.oldest||"未知"}；持仓陈旧 ${Object.keys(f.holdings_stale||{}).join("、")||"无"}）`;
    }
    return `计划#${r.plan_id??""}：${r.summary||""}（${r.n_actions??0}条动作、${r.n_conditions??0}条条件${r.n_conditions_rejected?`、${r.n_conditions_rejected}条解析未过`:""}，${r.plan_date?r.plan_date+"开盘": "下一交易日开盘"}执行）`;}
  if(a==="sync"){const u=r.updated||{};const ks=Object.keys(u);
    if(!ks.length)return "无更新";
    return ks.map(s=>`${sym(s)}${u[s]<0?"同步失败":`新增 ${u[s]} 根K线`}`).join("、");}
  if(a==="run"||a==="cron-run")return`产生信号 ${r.signals??0} 个，下单 ${r.orders??0} 笔`;
  if(a==="preview")return "风控检查通过";
  return "完成";}
let PERF=null,OV=null,INTS=[],INTRA=null,PERFMODE="nav";
function dayDiff(iso){if(!iso)return null;const t=Date.parse(iso+"T00:00:00");
  return isNaN(t)?null:Math.max(0,Math.round((Date.now()-t)/86400000));}
function sparkSVG(vals){
  const v=(vals||[]).filter(x=>x!=null&&isFinite(x));
  if(v.length<2)return '<span class="mut">—</span>';
  const w=76,h=24,p=2,lo=Math.min(...v),hi=Math.max(...v),r=(hi-lo)||1;
  const pts=v.map((x,i)=>`${p+i*(w-2*p)/(v.length-1)},${h-p-(x-lo)/r*(h-2*p)}`).join(" ");
  const up=v[v.length-1]>=v[0];
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${up?'var(--color-up)':'var(--color-down)'}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
function niceStep(raw){
  if(!(raw>0))return 1;
  const p=Math.pow(10,Math.floor(Math.log10(raw))),n=raw/p;
  return (n<=1?1:n<=2?2:n<=5?5:10)*p;
}
function drawPerf(){
  const svg=document.getElementById("perfchart");
  const wrap=document.getElementById("chartwrap");
  const tip=document.getElementById("charttip");
  const cov=document.getElementById("perf-cov");
  if(!svg||!wrap)return;
  const S=(PERF&&PERF.series)||[];
  const c=(PERF&&PERF.coverage)||{};
  const miss=(c.missing||[]).length;
  if(cov){
    cov.className="cov"+(miss?" warn":"");
    cov.innerHTML=`<span class="badge">${c.present||S.length}/${c.expected||S.length} 交易日</span>`+
      (miss?`<span class="badge" title="${esc((c.missing||[]).slice(0,5).join("、"))}">缺 ${miss} 日</span>`:"")+
      `<span>${esc((PERF&&PERF.definitions)||"")}</span>`;
  }
  if(S.length<2){
    svg.innerHTML='<text x="50%" y="48%" text-anchor="middle" fill="var(--text-tertiary)" font-size="13">净值记录不足（至少 2 个交易日）</text>';
    tip.style.display="none";return;
  }
  const W=Math.max(320,wrap.clientWidth||800),H=Math.max(200,wrap.clientHeight||260);
  svg.setAttribute("viewBox",`0 0 ${W} ${H}`);
  const P={l:64,r:14,t:12,b:26},mode=PERFMODE;
  const vals=S.map(d=>mode==="nav"?d.total_value:(mode==="ret"?d.ret:-d.drawdown));
  const t0=Date.parse(S[0].date),t1=Date.parse(S[S.length-1].date),span=Math.max(1,t1-t0);
  const x=i=>P.l+(Date.parse(S[i].date)-t0)/span*(W-P.l-P.r);
  let lo=Math.min(...vals),hi=Math.max(...vals);
  if(mode==="nav"){const init=PERF.initial_cash||100000;lo=Math.min(lo,init);hi=Math.max(hi,init);}
  if(mode==="dd"){lo=Math.min(lo,-0.0001);hi=0;}
  if(hi-lo<1e-9)hi=lo+1;
  const pad=(hi-lo)*0.08;lo-=pad;hi+=pad;
  const y=v=>P.t+(hi-v)/(hi-lo)*(H-P.t-P.b);
  const missSet=new Set(c.missing||[]);
  let g="";
  const step=niceStep((hi-lo)/4);
  const start=Math.ceil(lo/step)*step;
  for(let v=start;v<=hi+1e-9;v+=step){const yy=y(v);
    g+=`<line x1="${P.l}" y1="${yy}" x2="${W-P.r}" y2="${yy}" stroke="var(--divider-thin)" stroke-width="1"/>`;
    const lab=mode==="nav"?fmt(v,0):pct(v);
    g+=`<text x="${P.l-8}" y="${yy+4}" text-anchor="end" font-size="11" fill="var(--text-tertiary)">${lab}</text>`;}
  if(mode==="nav"){const init=PERF.initial_cash||100000,yy=y(init);
    g+=`<line x1="${P.l}" y1="${yy}" x2="${W-P.r}" y2="${yy}" stroke="var(--divider)" stroke-width="1" stroke-dasharray="4 4"/>`;
    g+=`<text x="${W-P.r}" y="${Math.max(P.t+10,yy-5)}" text-anchor="end" font-size="11" fill="var(--text-tertiary)">初始资金 ${fmt(init,0)}</text>`;}
  const gapBetween=(a,b)=>{const d0=Date.parse(a),d1=Date.parse(b);
    for(const m of missSet){const t=Date.parse(m);if(t>d0&&t<d1)return true;}return false;};
  let solid="",dashed="";
  for(let i=1;i<S.length;i++){const seg=`${x(i-1)},${y(vals[i-1])} ${x(i)},${y(vals[i])} `;
    if(gapBetween(S[i-1].date,S[i].date))dashed+=seg;else solid+=seg;}
  const base=(mode==="dd")?0:lo;
  const area=`${x(0)},${y(base)} `+S.map((_,i)=>`${x(i)},${y(vals[i])}`).join(" ")+` ${x(S.length-1)},${y(base)}`;
  const dots=S.length<=60?S.map((_,i)=>`<circle cx="${x(i)}" cy="${y(vals[i])}" r="2.6" fill="var(--accent)"/>`).join(""):"";
  const xlabs=[0,Math.floor((S.length-1)/2),S.length-1].filter((v,i,a)=>a.indexOf(v)===i)
    .map(i=>`<text x="${x(i)}" y="${H-6}" text-anchor="middle" font-size="11" fill="var(--text-tertiary)">${S[i].date.slice(5)}</text>`).join("");
  svg.innerHTML=`<defs><linearGradient id="pg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity=".16"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>`+
    g+
    (mode==="dd"?`<polygon points="${area}" fill="var(--red-bg)"/>`:`<polygon points="${area}" fill="url(#pg)"/>`)+
    `<polyline points="${solid}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`+
    (dashed?`<polyline points="${dashed}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-dasharray="4 4" opacity=".75"/>`:"")+
    dots+xlabs+
    `<rect id="perfhit" x="${P.l}" y="${P.t}" width="${W-P.l-P.r}" height="${H-P.t-P.b}" fill="transparent"/>`;
  const hit=svg.querySelector("#perfhit");
  hit.addEventListener("mousemove",ev=>{
    const r=svg.getBoundingClientRect(),px=(ev.clientX-r.left)*(W/r.width);
    let best=0,bd=1e9;for(let i=0;i<S.length;i++){const dx=Math.abs(x(i)-px);if(dx<bd){bd=dx;best=i;}}
    const e=S[best],d=best>0?(e.total_value/S[best-1].total_value-1):null;
    tip.style.display="block";
    tip.innerHTML=`<b>${e.date}</b><br>总资产 ${fmt(e.total_value)}<br>累计 ${(e.ret>=0?"+":"")+pct(e.ret)}`+
      (d==null?"":`<br>当日 ${(d>=0?"+":"")+pct(d)}`)+`<br>回撤 ${pct(e.drawdown)}`;
    const left=Math.min(W-200,Math.max(0,x(best)+14));
    tip.style.left=(left/W*100)+"%";tip.style.top="10px";
  });
  hit.addEventListener("mouseleave",()=>{tip.style.display="none";});
}
function renderToday(){
  const g=document.getElementById("todaygrid");
  if(!g)return;
  if(!OV){g.innerHTML='<div class="s">今日数据加载失败 · 等待重试</div>';return;}
  const f=OV.fills||{},cd=OV.conditions||{},it=OV.intents||{};
  const h=OV.health||{},ck=OV.checks;
  document.getElementById("ov-date").textContent=OV.date||"";
  const dot=h.healthy===false?'<span class="dot bad"></span>'
    :((h.alerts||[]).length?'<span class="dot warn"></span>':'<span class="dot"></span>');
  const rows=[
    ["健康",dot+(h.healthy===false?`告警 ${(h.alerts||[]).length} 项`:"正常"),h.healthy===false?"bad":""],
    ["今日成交",`${f.n||0} 笔（买 ${f.buy||0} / 卖 ${f.sell||0}）`,""],
    ["条件单",`${cd.armed||0} 挂 / ${cd.queued_rq||0} 已交框架 / 今日 ${cd.filled_today||0} 成交`,""],
    ["意图",`执行 ${it.executed_today||0} / 未决 ${it.pending||0} / 拒绝 ${it.rejected_today||0}`,""],
    ["已实现", (() => {
      const pf=(PERF&&PERF.summary)||{};
      if(pf.realized_pnl==null)return "—";
      const wr=pf.win_rate==null?"—":`${(pf.win_rate*100).toFixed(0)}%`;
      const un=Object.values(pf.unmatched_sell_volume||{}).reduce((a,b)=>a+b,0);
      return `${pf.realized_pnl>=0?"+":""}${fmt(pf.realized_pnl)}（${pf.win_trades||0}胜/${pf.lose_trades||0}负 · 胜率 ${wr}）· 费 ${fmt(pf.fees_total||0)}${un?` · 未匹配卖出 ${un}`:""}`;
    })(), cls(((PERF&&PERF.summary)||{}).realized_pnl)],
    ["体检",ck?`${ck.green} 绿 / ${ck.warn} 黄 / ${ck.red} 红 · ${String(ck.at||"").slice(11,16)}`:"今日未体检",
      ck&&ck.red?"bad":(ck&&ck.warn?"":"ok")],
    ["行情",`截至 ${OV.quote_asof||(OV.status&&OV.status.data_asof)||"—"}${OV.prices_ready===false?"（待今日行情）":""}`,""],
  ];
  g.innerHTML=rows.map(r=>`<div class="today-row"><span class="lab">${r[0]}</span>`+
    `<span class="val ${r[2]}"${ck&&r[0]==="体检"&&((ck.red_ids||[]).length||(ck.warn_ids||[]).length)?` title="${esc([].concat(ck.red_ids||[],ck.warn_ids||[]).join("、"))}"`:""}>${r[1]}</span></div>`).join("");
}
function chip(status){
  const m={executed:["ok","已执行"],rejected:["bad","被拒"],expired:["","已过期"],
           pending:["warn","待执行"],queued:["warn","排队"]};
  const c=m[String(status)]||["",status||"—"];
  return `<span class="status-chip ${c[0]}">${esc(c[1])}</span>`;
}
function renderIntents(){
  const t=document.getElementById("intents"); if(!t)return;
  document.getElementById("c-intents").textContent=(INTS||[]).length+" 条";
  rowsC(t,[["时间"],["标的"],["方向"],["股数",1],["来源"],["状态"],["结果"]],(INTS||[]).map(r=>{
    const d=new Date(r.created_at),buy=String(r.side).toUpperCase()==="BUY";
    let why="";try{const o=JSON.parse(r.result||"{}");
      if(o.order_id!=null)why=`成交 #${o.order_id}`+(o.price!=null?` @${o.price}`:"");
      else if(o.reason==="FILLED")why="框架已成交（账本未落）";
      else why=o.reason||o.why||o.error||o.fill_biz_id||"";
    }catch(e){}
    const ref=String(r.rule_ref||"");
    const src=ref.startsWith("cond:")?"条件 #"+ref.slice(5):(ref?"规则/计划":"—");
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td><b>${esc(r.symbol||"")}</b></td>`+
      `<td><span class="tag ${buy?"buy":"sell"}">${buy?"买入":"卖出"}</span></td>`+
      `<td class="num">${r.volume||0}</td><td class="mut">${esc(src)}</td><td>${chip(r.status)}</td>`+
      `<td class="mut">${esc(String(why).slice(0,120))}</td></tr>`;
  }),10,"暂无意图");
}
function renderOpsPanels(){
  const h=(OV&&OV.health)||{},ck=h.checks||{};
  const ch=document.getElementById("c-health");
  if(ch)ch.textContent=Object.keys(ck).length?`${Object.values(ck).filter(x=>x&&x.state==="stale").length} 项异常`:"—";
  const g=document.getElementById("hbgrid");
  if(g){
    const labels=[["engine","引擎"],["rq_tick","rq 消费跳"],["minute","分钟采集"],["agent","AI agent"]];
    g.innerHTML=labels.map(([k,lab])=>{
      const v=ck[k]||{},st=v.state||"无记录";
      const stTxt={"ok":"正常","not-due":"未到时段","stale":"停滞"}[st]||st;
      const bad=st==="stale"?"bad":(st==="ok"?"":(st==="not-due"?"":"warn"));
      const age=(v.age_sec!=null?Math.round(v.age_sec)+"s 前":"—");
      return `<div class="hb ${bad}"><div class="k">${lab}</div><div class="v">${esc(stTxt)}</div>`+
        `<div class="a">${age}${v.limit_sec?` / 阈值 ${Math.round(v.limit_sec)}s`:""}</div></div>`;
    }).join("");
  }
  const hd=document.getElementById("health-det");
  if(hd)hd.textContent=h.healthy===false?("告警："+(h.alerts||[]).join("；")):("看门狗检查于 "+(h.checked_at||"—"));
  const c=OV&&OV.checks;
  document.getElementById("c-checks").textContent=c?`${c.green}/${c.warn}/${c.red}`:"未跑";
  const cb=document.getElementById("checksbox");
  if(cb)cb.innerHTML=c?`<div class="plan-line"><span class="lab">结果</span><span>${c.green} 绿 / ${c.warn} 黄 / ${c.red} 红 · ${esc(String(c.at||"").slice(11,16))}</span></div>`+
    ((c.red_ids||[]).length?`<div class="plan-line"><span class="lab bad">红</span><span class="bad">${esc(c.red_ids.join("、"))}</span></div>`:"")+
    ((c.warn_ids||[]).length?`<div class="plan-line"><span class="lab">黄</span><span class="mut">${esc(c.warn_ids.join("、"))}</span></div>`:"")
    :"今日未体检（15:30 自动跑）";
  const p=OV&&OV.plan;
  document.getElementById("c-plan").textContent=p?("计划 #"+p.id):"无";
  const pb=document.getElementById("planbox");
  if(pb){
    if(!p){pb.textContent="暂无计划记录";}
    else{
      const f=p.freshness||{};
      const lines=[
        ["计划",`#${p.id} · ${p.plan_date||"—"} · ${p.status||"—"}`],
        ["数据截至",`${p.asof||"—"}${f.oldest?"（最旧 "+f.oldest+"）":""}`],
        ["当日覆盖",f.total?`${f.fresh}/${f.total}（${(Number(f.coverage||0)*100).toFixed(0)}%）`:"无新鲜度记录（T96 之前）"],
        ["摘要",p.summary||"—"],
      ];
      pb.innerHTML=lines.map(([a,b])=>`<div class="plan-line"><span class="lab">${a}</span><span>${esc(String(b))}</span></div>`).join("")
        +((p.actions_rejected||[]).length?`<div class="plan-line"><span class="lab bad">整备拒绝</span><span class="bad">${esc(p.actions_rejected.map(x=>`${x.symbol}：${x.reason}`).join("；"))}</span></div>`:"")
        +(f.holdings_stale&&Object.keys(f.holdings_stale).length?`<div class="plan-line"><span class="lab bad">持仓陈旧</span><span class="bad">${esc(Object.entries(f.holdings_stale).map(([k,v])=>k+":"+v).join("、"))}</span></div>`:"");
    }
  }
}
function drawIntraday(){
  const svg=document.getElementById("intradaychart");
  const wrap=document.getElementById("intradaywrap");
  const tip=document.getElementById("intradaytip");
  const note=document.getElementById("intraday-note");
  const cnt=document.getElementById("c-intraday");
  if(!svg||!wrap)return;
  const S=(INTRA&&INTRA.series)||[];
  if(!INTRA||!INTRA.available||S.length<2){
    svg.innerHTML='<text x="50%" y="48%" text-anchor="middle" fill="var(--text-tertiary)" font-size="13">今日无分时（非交易日/分钟线未到）</text>';
    if(cnt)cnt.textContent="—";
    if(note)note.textContent=INTRA?(INTRA.note||""):"";
    if(tip)tip.style.display="none";
    return;
  }
  const W=Math.max(320,wrap.clientWidth||800),H=Math.max(180,wrap.clientHeight||220);
  svg.setAttribute("viewBox",`0 0 ${W} ${H}`);
  const P={l:64,r:14,t:12,b:24};
  const t0=Date.parse(S[0].ts),t1=Date.parse(S[S.length-1].ts),span=Math.max(1,t1-t0);
  const x=ts=>P.l+(Date.parse(ts)-t0)/span*(W-P.l-P.r);
  const vals=S.map(p=>Number(p.total));
  let lo=Math.min(...vals),hi=Math.max(...vals);
  if(hi-lo<1e-9)hi=lo+1;
  const pad=(hi-lo)*0.10;lo-=pad;hi+=pad;
  const y=v=>P.t+(hi-v)/(hi-lo)*(H-P.t-P.b);
  let g="";
  for(let k=0;k<=2;k++){const v=lo+(hi-lo)*k/2,yy=y(v);
    g+=`<line x1="${P.l}" y1="${yy}" x2="${W-P.r}" y2="${yy}" stroke="var(--divider-thin)"/>`+
       `<text x="${P.l-8}" y="${yy+4}" text-anchor="end" font-size="11" fill="var(--text-tertiary)">${fmt(v,0)}</text>`;}
  const pts=S.map(p=>`${x(p.ts)},${y(p.total)}`).join(" ");
  const area=`${x(S[0].ts)},${y(lo)} ${pts} ${x(S[S.length-1].ts)},${y(lo)}`;
  const xlabs=[0,Math.floor((S.length-1)/2),S.length-1].filter((v,i,a)=>a.indexOf(v)===i)
    .map(i=>`<text x="${x(S[i].ts)}" y="${H-6}" text-anchor="middle" font-size="11" fill="var(--text-tertiary)">${S[i].ts.slice(11,16)}</text>`).join("");
  svg.innerHTML=`<defs><linearGradient id="ig" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity=".16"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>`+
    g+`<polygon points="${area}" fill="url(#ig)"/>`+
    `<polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>`+
    `<circle cx="${x(S[0].ts)}" cy="${y(S[0].total)}" r="2.8" fill="var(--accent)"/>`+
    `<circle cx="${x(S[S.length-1].ts)}" cy="${y(S[S.length-1].total)}" r="2.8" fill="var(--accent)"/>`+
    xlabs+`<rect id="ihit" x="${P.l}" y="${P.t}" width="${W-P.l-P.r}" height="${H-P.t-P.b}" fill="transparent"/>`;
  if(cnt)cnt.textContent=`${INTRA.date.slice(5)} · ${S.length} 点 · ${INTRA.fills||0} 笔成交`;
  if(note){
    let t=(INTRA.fallback?`今日无分钟线，显示最近交易日 ${INTRA.date}；`:"")+(INTRA.note||"");
    if(INTRA.reconstruction_ok===false)t+="（反推出现负仓位/负现金，需核账）";
    note.textContent=t;
  }
  const hit=svg.querySelector("#ihit");
  hit.addEventListener("mousemove",ev=>{
    const r=svg.getBoundingClientRect(),px=(ev.clientX-r.left)*(W/r.width);
    let best=0,bd=1e9;for(let i=0;i<S.length;i++){const dx=Math.abs(x(S[i].ts)-px);if(dx<bd){bd=dx;best=i;}}
    const p=S[best];
    tip.style.display="block";
    tip.innerHTML=`<b>${p.ts.slice(11,16)}</b><br>总资产 ${fmt(p.total)}<br>现金 ${fmt(p.cash)}<br>市值 ${fmt(p.mv)}`;
    const left=Math.min(W-170,Math.max(0,x(p.ts)+12));tip.style.left=(left/W*100)+"%";tip.style.top="10px";
  });
  hit.addEventListener("mouseleave",()=>{tip.style.display="none";});
}
document.getElementById("perfmodes").addEventListener("click",e=>{
  const b=e.target.closest("button[data-mode]");if(!b)return;
  PERFMODE=b.dataset.mode;
  document.querySelectorAll("#perfmodes button").forEach(x=>{
    const on=x===b;x.classList.toggle("on",on);x.setAttribute("aria-selected",on?"true":"false");});
  drawPerf();
});
window.addEventListener("resize",()=>{clearTimeout(window.__ptResize);window.__ptResize=setTimeout(drawPerf,120);});

async function refresh(){
 try{
  const [s,ops,pos,ord,nav,nm,prov]=await Promise.all(["/api/status","/api/ops?limit=30","/api/positions","/api/orders?limit=30","/api/nav?limit=60","/api/names","/api/data-provenance"].map(get));
  const [perfR,ovR,intR,intraR]=await Promise.all([
    get("/api/performance").catch(()=>({ok:false})),
    get("/api/overview").catch(()=>({ok:false})),
    get("/api/intents?limit=100").catch(()=>({ok:false})),
    get("/api/intraday").catch(()=>({ok:false}))]);
  PERF=(perfR&&perfR.ok)?perfR.data:null;
  OV=(ovR&&ovR.ok)?ovR.data:null;
  INTS=(intR&&intR.ok)?intR.data:[];
  INTRA=(intraR&&intraR.ok)?intraR.data:null;
  NAMES=(nm&&nm.data)||{};
  // T22 看门狗红条：不健康才显示；恢复后后端状态翻回 healthy=true ⇒ 红条自动消失
  try{const hh=await get("/api/health");const h=(hh&&hh.data)||{};
    const bar=document.getElementById("healthbar");
    if(h.healthy===false){bar.style.display="";
      bar.innerHTML='<span class="dot bad"></span>生产健康告警：'+(h.alerts||[]).map(esc).join("；")
        +" <span class=\"mut\">看门狗 "+esc(h.checked_at||"")+"</span>";}
    else{bar.style.display="none";bar.innerHTML="";}
  }catch(e){}
  const st=s.data;
  const plist=(pos&&pos.data)||st.positions||[];
  const pf=(PERF&&PERF.summary)||{};
  const liveMode=!!(OV&&OV.estimated&&OV.prices_ready&&OV.live_total!=null);
  const navTotal=liveMode?OV.live_total
    :((pf.total_value!=null)?pf.total_value:st.total_value);
  const navCash=liveMode?OV.cash
    :((pf.cash!=null)?pf.cash:st.cash);
  const navMv=liveMode?OV.live_market_value
    :((pf.market_value!=null)?pf.market_value:st.market_value);
  const asof=(OV&&OV.quote_asof)||st.data_asof||"无数据";
  const todayPnl=(OV&&OV.today_pnl!=null)?OV.today_pnl:null;
  const todayPct=(OV&&OV.today_pnl!=null)?OV.today_pnl_pct:null;
  const est=!!(OV&&OV.estimated&&OV.today_pnl!=null);
  const expo=(navTotal?navMv/navTotal:null);
  document.getElementById("clock").textContent="行情截至 "+asof+" · 页面更新于 "+new Date().toLocaleTimeString("zh-CN");
  document.getElementById("statusbar").innerHTML=`
   <div class="status-item"><span class="status-label">行情截至</span><span class="status-value">${asof}</span></div>
   <div class="status-item"><span class="status-label">持仓</span><span class="status-value">${plist.length} 只</span></div>
   <div class="status-item"><span class="status-label">模式</span><span class="status-value">纸盘 · 只读面板</span></div>`;
  document.getElementById("cards").innerHTML=`
   <div class="market-item"><div class="market-name">总资产 CNY</div><div class="market-value">${fmt(navTotal,0)}</div><div class="kpi-sub">现金 ${fmt(navCash,0)} + 市值 ${fmt(navMv,0)}${liveMode?" · 盘中价":(PERF&&PERF.asof?" · 收盘 "+PERF.asof:"")}</div></div>
   <div class="market-item"><div class="market-name">今日盈亏</div><div class="market-value ${cls(todayPnl)}">${todayPnl==null?"—":(todayPnl>=0?"+":"")+fmt(todayPnl)}</div><div class="kpi-sub ${cls(todayPnl)}">${todayPnl==null?"待今日行情":pct(todayPct)}${est?'<span class="kpi-tag">盘中估算</span>':""}</div></div>
   <div class="market-item"><div class="market-name">累计收益</div><div class="market-value ${cls(pf.total_return)}">${pf.total_return==null?"—":(pf.total_return>=0?"+":"")+pct(pf.total_return)}</div><div class="kpi-sub ${cls(pf.total_pnl)}">${pf.total_pnl==null?"—":(pf.total_pnl>=0?"+":"")+fmt(pf.total_pnl)} · 自 ${(PERF&&PERF.series&&PERF.series[0])?PERF.series[0].date:"—"}</div></div>
   <div class="market-item"><div class="market-name">最大回撤</div><div class="market-value">${pf.mdd==null?"—":pct(pf.mdd)}</div><div class="kpi-sub">${pf.mdd_date?"发生于 "+pf.mdd_date:""}${pf.current_drawdown!=null?" · 当前 "+pct(pf.current_drawdown):""}</div></div>
   <div class="market-item"><div class="market-name">仓位</div><div class="market-value">${expo==null?"—":pct(expo)}</div><div class="kpi-sub">现金占比 ${expo==null?"—":pct(1-expo)}</div></div>`;
  drawPerf();
  renderToday();
  renderIntents();
  renderOpsPanels();
  drawIntraday();
  const navRows=(nav.data||[]);
  const navDays=new Set(navRows.map(r=>String(r.timestamp).slice(0,10))).size;
  document.getElementById("c-nav").textContent=`${navRows.length} 行 · ${navDays} 个交易日（同日多条保留）`;
  document.getElementById("c-ops").textContent=(ops.data||[]).length+" 条";
  document.getElementById("c-pos").textContent=(plist||[]).length+" 只股票";
  document.getElementById("c-orders").textContent=(ord.data||[]).length+" 笔";
  rowsC(document.getElementById("nav"),[["时间"],["总资产",1],["可用资金",1],["浮动盈亏",1]],
    (nav.data||[]).map(r=>{const d=new Date(r.timestamp);const pn=Number(r.pnl);
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td class="num">${fmt(r.total_value)}</td><td class="num">${fmt(r.cash)}</td><td class="num ${cls(pn)}">${pn>=0?"+":""}${fmt(pn)}</td></tr>`}),
    10,"暂无净值记录");
  rowsC(document.getElementById("ops"),[["时间"],["操作"],["详情"],["结果"],["操作后资产",1]],
    (ops.data||[]).map(r=>{const ok=!!r.ok;const d=new Date(r.timestamp);
    const label=ACTION_CN[r.action]||r.action;
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td><b>${esc(label)}</b></td>`+
      `<td class="mut">${esc(paramCN(r.action,r.params))}</td>`+
      `<td class="${ok?"ok":"bad"}">${esc(resultCN(r.action,ok,r.result))}</td>`+
      `<td class="num">${r.total_value_after!=null?fmt(r.total_value_after):"-"}</td></tr>`}),
    10,"暂无操作记录 —— AI 执行每日结算、买入、卖出后会出现在这里");
  rows(document.getElementById("pos"),[["代码"],["名称"],["股数",1],["可卖",1],["成本",1],["现价",1],["市值",1],["占比",1],["浮盈亏",1],["天数",1],["距峰值",1],["走势"]],
    (plist||[]).map(p=>{const pn=(p.current_price-p.avg_cost)*p.total_volume;
    const pxHtml=`${fmt(p.current_price)}`+(p.price_source==="分钟"
      ?` <span class="tag live">${esc(p.price_time||"分钟")}</span>`:"");
    const share=navTotal?p.market_value/navTotal:null;
    const vsPeak=(p.peak_price>0&&p.current_price>0)?(p.current_price/p.peak_price-1):null;
    const d=dayDiff(p.entry_day);
    return `<tr><td><b>${esc(p.symbol)}</b></td><td class="mut">${esc(NAMES[p.symbol]||"—")}</td>`+
      `<td class="num">${p.total_volume}</td><td class="num">${p.available_volume}</td>`+
      `<td class="num">${fmt(p.avg_cost)}</td><td class="num">${pxHtml}</td>`+
      `<td class="num">${fmt(p.market_value,0)}</td><td class="num">${share==null?"—":pct(share)}</td>`+
      `<td class="num ${cls(pn)}">${pn>=0?"+":""}${fmt(pn)}</td>`+
      `<td class="num">${d==null?"—":d}</td>`+
      `<td class="num ${cls(vsPeak)}">${vsPeak==null?"—":(vsPeak>=0?"+":"")+pct(vsPeak)}</td>`+
      `<td>${sparkSVG(p.spark)}</td></tr>`}).join("")
    ||`<tr><td colspan="12" class="mut">空仓</td></tr>`);
  rowsC(document.getElementById("orders"),[["时间"],["方向"],["股票"],["股数",1],["价格",1],["状态"],["手续费",1]],
    (ord.data||[]).map(r=>{const d=new Date(r.created_at);const buy=r.direction==1;
    const fee=(Number(r.commission)+Number(r.stamp_duty)+Number(r.transfer_fee));
    const stt=r.status=="filled"?`<span class="ok">已成交</span>`:`<span class="bad">已拒绝</span>`;
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td><span class="tag ${buy?"buy":"sell"}">${buy?"买入":"卖出"}</span></td><td><b>${esc(sym(r.symbol))}</b></td><td class="num">${r.volume}</td><td class="num">${fmt(r.filled_price||r.limit_price)}</td><td>${stt}</td><td class="num">${fmt(fee)}</td></tr>`}),
    10,"暂无订单");
  const provIf=(prov&&prov.data&&prov.data.interfaces)||{};
  document.getElementById("c-prov").textContent=Object.keys(provIf).length+" 个接口";
  document.getElementById("prov-sum").textContent=
    `行情数据源 ${(prov&&prov.data&&prov.data.daily_source)||"auto"}；缺失接口 ${((prov&&prov.data&&prov.data.unavailable)||[]).join("、")||"无"}`;
  rowsC(document.getElementById("prov"),[["接口"],["来源"],["截至"],["状态"]],
    Object.entries(provIf).map(([k,v])=>{
      const src=Array.isArray(v.sources)?v.sources.join("、"):(v.source||"-");
      const isMother=String(src).startsWith("mother");
      const isUn=src==="unavailable";
      const st=(v.stale?`<span class="bad">过期</span> `:"")+
        ((v.available===false||isUn)?`<span class="bad">缺失</span>`:`<span class="ok">可用</span>`);
      return `<tr><td><b>${esc(k)}</b></td><td><span class="tag ${isMother?"":(isUn?"sell":"buy")}">${esc(src)}</span></td><td class="mut">${esc(v.asof||"-")}</td><td>${st}</td></tr>`;
    }),12,"暂无来源数据");
 }catch(e){document.getElementById("clock").textContent="刷新失败: "+e}
}
/* ---- P1：任务视图（总览 / 交易 / 运维 / 控制台） ---- */
const TAB_IDS=["overview","trading","ops","control"];
function applyTab(tab){
  const t=TAB_IDS.includes(tab)?tab:"overview";
  document.querySelectorAll("#tabs button").forEach(b=>{
    const on=b.dataset.tab===t;b.classList.toggle("on",on);
    b.setAttribute("aria-selected",on?"true":"false");});
  document.querySelectorAll("[data-tab]:not(button)").forEach(el=>el.classList.toggle("on",el.dataset.tab===t));
  if(t==="overview"&&typeof drawPerf==="function")setTimeout(()=>{drawPerf();drawIntraday();},0);
}
function hashTab(){
  const h=(location.hash||"").replace(/^#\/?/,"");
  return TAB_IDS.includes(h)?h:"overview";
}
document.getElementById("tabs").addEventListener("click",e=>{
  const b=e.target.closest("button[data-tab]");if(!b)return;
  location.hash="#/"+b.dataset.tab;
});
window.addEventListener("hashchange",()=>applyTab(hashTab()));
applyTab(hashTab());

refresh();setInterval(refresh,15000);

/* ---- 模型设置与 AI 问答（口令浏览器自动保管，用户无感） ---- */
const tok=()=> (localStorage.getItem("pt_adm")||"");
const PRESET_URLS={deepseek:"https://api.deepseek.com/v1",qwen:"https://dashscope.aliyuncs.com/compatible-mode/v1",moonshot:"https://api.moonshot.cn/v1",glm:"https://open.bigmodel.cn/api/paas/v4",doubao:"https://ark.cn-beijing.volces.com/api/v3",openai:"https://api.openai.com/v1",ollama:"http://127.0.0.1:11434/v1",lmstudio:"http://127.0.0.1:1234/v1",vllm:"http://127.0.0.1:8000/v1",lan:"",custom:""};
const PRESET_MODELS={deepseek:"deepseek-chat",qwen:"qwen-plus",moonshot:"moonshot-v1-8k",glm:"glm-4-flash",doubao:"",openai:"gpt-4o-mini",ollama:"",lmstudio:"",vllm:"",lan:"",custom:""};
const TOKENLESS=new Set(["ollama","lmstudio","vllm","lan"]);
const KNOWN_URLS=new Set(Object.values(PRESET_URLS));
document.getElementById("preset").addEventListener("change",e=>{
  const b=document.getElementById("base");
  if(!b.value.trim()||KNOWN_URLS.has(b.value.trim()))b.value=PRESET_URLS[e.target.value]||"";
  const m=document.getElementById("model");
  if(!m.value.trim()&&PRESET_MODELS[e.target.value])m.value=PRESET_MODELS[e.target.value];
  const k=document.getElementById("apikey");
  k.placeholder=TOKENLESS.has(e.target.value)
    ?"本地接口无需 Key（留空即可；服务端若设了令牌才填）"
    :"API Key（只存本机，不回显）";
});
async function post(u,b){const r=await fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});return r.json()}
async function llmStatus(){
  const s=await get("/api/llm/status");const d=s.data||{};
  const cred=d.tokenless?"本地接口（无令牌）":`Key ${d.key_masked}`;
  document.getElementById("llmstat").textContent=d.configured?`已配置：${d.preset} / ${d.model||"未选模型"} / ${cred}（长期保存，重启不丢）`:"未配置：先填接口地址（云端还要 Key），点保存配置；本地接口不用 Key";
  document.getElementById("apikey").placeholder=d.has_key?`已保存 Key（${d.key_masked}），换模型不用重填；换 Key 才需填写`:"API Key（本地接口可留空；只存本机，不回显）";
  if(d.preset)document.getElementById("preset").value=d.preset;
  if(d.base_url)document.getElementById("base").value=d.base_url;
  if(d.model)document.getElementById("model").value=d.model;
}
document.getElementById("btn-models").onclick=async()=>{
  const st=document.getElementById("llmstat");
  st.textContent="拉取中…";
  const r=await get("/api/llm/models");   // 不带口令：拉取不被门禁拦截，失败看原因
  if(!r.ok){
    st.textContent="拉取失败："+(r.error||"未知原因")+(r.hint==="save"?"（在下面填好点「保存配置」即可）":"");
    return;
  }
  const sel=document.getElementById("modelsel");sel.innerHTML='<option value="">下拉选择…</option>';
  (r.models||[]).forEach(m=>{const o=document.createElement("option");o.value=m;o.textContent=m;sel.appendChild(o)});
  st.textContent=`共 ${(r.models||[]).length} 个模型，下拉选一个（会自动填入右侧），或直接手填`+(r.warning?`；${r.warning}`:"");
};
document.getElementById("modelsel").addEventListener("change",e=>{
  if(e.target.value)document.getElementById("model").value=e.target.value;
});
document.getElementById("btn-save").onclick=async()=>{
  const r=await post("/api/llm/config",{admin_token:tok(),preset:document.getElementById("preset").value,
    base_url:document.getElementById("base").value,model:document.getElementById("model").value,
    api_key:document.getElementById("apikey").value});
  if(!r.ok){alert("保存失败："+(r.error||""));return}
  document.getElementById("apikey").value="";
  if(r.admin_token)localStorage.setItem("pt_adm",r.admin_token);
  await llmStatus();refresh();
  alert("已保存，以后换模型直接改，不用再碰口令。"+(r.warning?("\n\n提示："+r.warning):""));
};
document.getElementById("btn-ask").onclick=async()=>{
  const q=document.getElementById("q").value.trim();if(!q){alert("先写问题");return}
  const ans=document.getElementById("ans");ans.className="ai-msg";ans.textContent="思考中…";
  const r=await post("/api/llm/ask",{admin_token:tok(),prompt:q});
  if(!r.ok){ans.textContent="失败："+(r.error||"");return}
  ans.className="ai-answer";ans.textContent=r.answer||"(空回答)";
  refresh();
};
/* 主题：跟随 stock-dashboard，localStorage 持久化，默认浅色 */
try{
  const saved=localStorage.getItem("pt_theme");
  if(saved)document.documentElement.setAttribute("data-theme",saved);
}catch(e){}
document.getElementById("themeBtn").onclick=()=>{
  const cur=document.documentElement.getAttribute("data-theme")==="dark"?"light":"dark";
  document.documentElement.setAttribute("data-theme",cur);
  try{localStorage.setItem("pt_theme",cur)}catch(e){}
};
/* ---- 投资方案（单选切换，写本地文件不进 git） ---- */
let instructionInit="";
function toggleInstruction(e){e.preventDefault();
  const box=document.getElementById("instructionBox");
  box.style.display=(box.style.display==="none")?"":"none";}
async function schemeStatus(){
  const s=await get("/api/schemes");const d=(s&&s.data)||{};
  document.getElementById("c-scheme").textContent=d.active?`当前：${d.active}（${d.source}）`:"";
  const box=document.getElementById("schemelist");box.innerHTML="";
  const byName={};(d.schemes||[]).forEach(sc=>{byName[sc.name]=sc});
  [["mother","母价值"], ["general","通用默认"], ["custom","自定义"]].forEach(([name,label])=>{
    const sc=byName[name];if(!sc)return;
    const lab=document.createElement("label");
    lab.style.cssText="display:flex;gap:6px;align-items:flex-start;font-size:13px;min-width:220px;flex:1";
    const gone=sc.available===false;
    if(gone){lab.style.opacity="0.45"}
    const radio=document.createElement("input");radio.type="radio";radio.name="scheme";radio.value=sc.name;
    if(gone)radio.disabled=true;
    if(sc.name===d.active)radio.checked=true;
    const tx=document.createElement("span");
    const uni=(sc.universe||"").includes("screening")?"选股范围：Stock Dashboard 筛选"
      :((sc.universe||"").includes("watchlist")?"选股范围：观察池":"选股范围：本地配置池");
    tx.innerHTML=`<b>${esc(label)} · ${esc(sc.title||sc.name)}</b>${gone?' <span class="mut">（需 Stock Dashboard 在同一台机器）</span>':""}<br><span class="mut">${esc(sc.desc||"")}</span><br><span class="mut">${uni}</span>${sc.name==="custom"?' <a href="#" onclick="toggleInstruction(event)">设置</a>':""}`;
    lab.appendChild(radio);lab.appendChild(tx);box.appendChild(lab);
  });
  if(d.instruction!==undefined){document.getElementById("instruction").value=d.instruction||"";instructionInit=d.instruction||"";}
  const ms=document.getElementById("motherschemes");
  ms.textContent=(d.mother&&d.mother.length)?"Stock Dashboard 策略（选股侧，供参照）："+d.mother.map(m=>`${m.name}(${m.key},${m.n_rules}条规则)`).join("、"):"Stock Dashboard 未在同一台机器，暂无母策略参照";
}
document.getElementById("btn-scheme").onclick=async()=>{
  const sel=document.querySelector('input[name="scheme"]:checked');
  if(!sel){alert("先选一个方案");return}
  const iv=document.getElementById("instruction").value;
  const send=async(extra)=>{
    const body=Object.assign({admin_token:tok(),name:sel.value},extra||{});
    if(iv!==instructionInit)body.instruction=iv;  // 没改就不发，避免误清空已存指令
    return await post("/api/schemes/active",body);
  };
  let r=await send();
  if(!r.ok&&r.error&&r.error.indexOf("口令")>=0){
    const k=prompt("口令失效，输入 API Key 接管（仅本机使用）：");
    if(k){r=await send({api_key:k});}
  }
  if(!r.ok){alert("切换失败："+(r.error||""));return}
  if(r.data&&r.data.admin_token)localStorage.setItem("pt_adm",r.data.admin_token);
  document.getElementById("schemestat").textContent=r.data.message;
  document.getElementById("instructionBox").style.display="none";
  await schemeStatus();refresh();
};
schemeStatus();
async function schedStatus(){
  try{
    const s=await get("/api/agent/runtime");const d=(s&&s.data)||{};
    const cal=d.calendar||{};
    document.getElementById("c-sched").textContent=d.trading_day?"交易日":"休市";
    const box=document.getElementById("schedrows");
    box.innerHTML=(d.windows||[]).map(w=>
      `<span class="tag" title="${esc(w.note||"")}">${esc(w.time)}–${esc(w.end)} ${esc(w.label)}</span>`
    ).join(" ");
    const plan=d.pending_plan;
    document.getElementById("schedtoday").textContent=
      `现在：${d.phase||"—"}；交易日历：${cal.available?`未来可看到 ${cal.max_date}（${cal.future_days??0} 个交易日，${cal.source}${cal.needs_sync?"，需重新同步":""}）`:"未同步（按周末回退）"}；`+
      (plan?`待执行计划 #${plan.id}（${plan.plan_date}）：${plan.summary||"无摘要"}`:`下一计划目标日：${d.plan_target||"—"}，暂无待执行计划`);
  }catch(e){}
}
schedStatus();
llmStatus();

/* ---- 规则引擎：规则集一览 + 回测（带基准与超额）+ 参数网格 ---- */
const RULE_CN={ma_cross:"均线金叉/死叉",rsi_zone:"RSI 超买超卖区",price_level:"价格档位",
  pct_change:"涨跌幅阈值",trailing_stop:"移动止损",time_stop:"持仓时限",
  vwap_dev:"偏离当日均价",intraday_pullback:"日内冲高回落",minute_ma_cross:"分钟均线交叉"};
const COND_CN={price_below:"价跌破",price_above:"价升破",pct_below:"日内跌超%",pct_above:"日内涨超%"};
const COND_ST={armed:["待触发","tag"],queued_rq:["已交框架","tag"],filled:["已成交","ok"],
  cancelled:["已撤销","mut"],expired:["已过期","mut"],rejected:["被拒","bad"]};
async function engineStatus(){
  try{
    const r=await get("/api/engine");const d=(r&&r.data)||{};
    const n=(d.rules||[]).length;
    document.getElementById("c-engine").textContent=n?`${n} 条规则`:"未配置";
    document.getElementById("engine-sum").innerHTML=
      `规则引擎：${d.enabled?"已开启":"已关闭"} · 每 ${d.interval_sec??60} 秒检查一次 · 单笔下限 ${fmt(d.min_ticket_cny,0)} 元 · 日内上限 ${d.max_orders_per_day??"-"} 笔`+
      (d.valid?`<span class="ok"> 配置校验通过</span>`:`<span class="bad"> 配置有问题：${esc(d.message||"")}</span>`);
    document.getElementById("engine-rules").innerHTML=(d.rules||[]).map(r=>{
      const a=r.action||{};
      const amt=a.all?"全部持仓":(a.value?fmt(a.value,0)+" 元":(a.volume?a.volume+" 股":""));
      return `<span class="tag">${esc(RULE_CN[r.type]||r.type)} · ${esc((r.symbols||[]).map(sym).join("、")||"未指定")} · ${esc(JSON.stringify(r.params||{}))} · ${a.side==="sell"?"卖出":"买入"} ${amt}</span>`;
    }).join("")
      ||`<span class="mut">还没有规则：在 config.yaml 的 engine.rules 里加，或让 AI 设计条件单</span>`;
    const tr=(d.triggers||[]);
    if(tr.length){
      const box=document.getElementById("engine-trig");
      box.innerHTML=`<div class="s mut">最近触发（最多 20 条）</div>`+
        tr.map(t=>`<div class="mut">${esc(String(t.fired_at||"").replace("T"," ").slice(5,16))} · ${esc(sym(t.rule_id||""))} · ${esc(t.reason||"")}${t.price!=null?` @ ${fmt(t.price)}`:""}</div>`).join("");
    }
  }catch(e){document.getElementById("engine-sum").textContent="规则引擎状态读取失败："+e}
}
function btCN(m){
  if(!m)return"";
  const b=m.benchmark_buy_hold||{};
  const line=`策略 ${fmt(m.total_return_pct)}% / 回撤 ${fmt(m.max_drawdown_pct)}% / 夏普 ${fmt(m.sharpe_annual)} / ${m.trades??0} 笔 / 费用 ${fmt(m.total_fees)} 元`;
  const bl=b.total_return_pct!=null?`基准买入持有 ${fmt(b.total_return_pct)}%`:"基准无数据";
  const ex=m.excess_pct!=null?` · 超额 ${m.excess_pct>=0?"+":""}${fmt(m.excess_pct)}%`:"";
  const curve=m.ascii_curve?`<br><span class="mono" style="font-size:11px;letter-spacing:-1px">${esc(m.ascii_curve)}</span>`:"";
  const pr=Object.entries(m.per_rule||{}).map(([k,v])=>`${esc(k)} ${v.trades??0} 笔`).join("；");
  // 只提示「配置/数据级」的注意事项（没有 symbol 的那条），逐笔跳过原因不刷屏
  const note=(m.skipped_sample||[]).filter(s=>s&&s.why&&!s.symbol).map(s=>esc(s.why)).slice(0,2).join("；");
  // T17（纪律 4「不静默」）：被跳过的信号按原因分类计数并展示 + 流失率，
  // 不再只给总数或 10~20 条样本——整类流失（如「金额不足一手」）必须看得见
  const sc=Object.entries(m.skip_counts||{}).map(([k,v])=>`${esc(k)} ${v}`).join("；");
  return `${esc(line)}<br><span class="mut">${esc(bl)}${esc(ex)}（近 ${m.days??0} 个交易日，含真实手续费）</span>`+
    (pr?`<br><span class="mut">${pr}</span>`:"")+
    (note?`<br><span class="mut">注意：${note}</span>`:"")+
    (sc?`<br><span class="mut">跳过分类：${sc}（${m.skipped??0}/${m.order_attempts??0} 次尝试被跳过，流失 ${m.signal_drop_pct??0}%）</span>`:"")+curve;
}
async function guard(fn){
  // 写接口统一走口令 + 凭 Key 接管（与其它写操作同一套，不新增第二套鉴权）
  let r=await fn();
  if(!r.ok&&r.error&&r.error.indexOf("口令")>=0){
    const k=prompt("口令失效，输入 API Key 接管（仅本机使用）：");
    if(k)r=await fn({api_key:k});
  }
  if(r&&r.data&&r.data.admin_token)localStorage.setItem("pt_adm",r.data.admin_token);
  return r;
}
document.getElementById("btn-engine-backtest").onclick=async()=>{
  const out=document.getElementById("engine-out");
  out.textContent="回测中（读本地日线，按 T+1 与真实费率撮合）…";
  const r=await guard((extra)=>post("/api/engine/backtest",
    Object.assign({admin_token:tok(),days:120,curve:true},extra||{})));
  out.innerHTML=r.ok?btCN(r.data):`<span class="bad">回测失败：${esc(r.error||"")}</span>`;
  document.getElementById("engine-stat").textContent=r.ok?"已完成":"";
};
document.getElementById("btn-engine-optimize").onclick=async()=>{
  const out=document.getElementById("engine-out");
  out.textContent="参数网格搜索中（同一段数据、同一费率，按超额排序）…";
  const r=await guard((extra)=>post("/api/engine/optimize",
    Object.assign({admin_token:tok(),grid:document.getElementById("engine-grid").value,
                  days:120,top:8},extra||{})));
  if(!r.ok){out.innerHTML=`<span class="bad">搜索失败：${esc(r.error||"")}</span>`;return}
  const d=r.data;
  out.innerHTML=`<div class="s">共 ${d.variants} 组参数，基准买入持有 ${fmt(d.benchmark_pct)}%，按超额排序（前 ${(d.top||[]).length} 组）`+
    (d.intraday?`，日内网格实际只覆盖 ${d.intraday_days} 个交易日（分钟线只存最近几日）`:``)+
    (d.dropped_no_trade?`；另有 ${d.dropped_no_trade} 组几乎不触发已剔除`:``)+`</div>`+
    `<div class="scroll"><table><thead><tr><th>标的</th><th>参数</th><th class="num">收益</th><th class="num">回撤</th><th class="num">夏普</th><th class="num">笔数</th><th class="num">超额</th></tr></thead><tbody>`+
    (d.top||[]).map(t=>`<tr><td><b>${esc(sym(t.symbol))}</b></td><td class="mut">${esc(JSON.stringify(t.params))}</td>`+
      `<td class="num ${cls(t.ret_pct)}">${fmt(t.ret_pct)}%</td><td class="num">${fmt(t.mdd_pct)}%</td>`+
      `<td class="num">${fmt(t.sharpe)}</td><td class="num">${t.trades}</td>`+
      `<td class="num ${cls(t.excess_pct)}">${t.excess_pct>=0?"+":""}${fmt(t.excess_pct)}%</td></tr>`).join("")+
    `</tbody></table></div><div class="s mut">${esc(d.note||"")}</div>`;
  document.getElementById("engine-stat").textContent="已完成";
};
document.getElementById("btn-engine-wf").onclick=async()=>{
  const out=document.getElementById("engine-out");
  out.textContent="样本外检验中（前 60% 选参数，后 40% 检验）…";
  const r=await guard((extra)=>post("/api/engine/walkforward",
    Object.assign({admin_token:tok(),grid:document.getElementById("engine-grid").value,
                  days:250,split:0.6,top:5},extra||{})));
  if(!r.ok){out.innerHTML=`<span class="bad">检验失败：${esc(r.error||"")}</span>`;return}
  const d=r.data, sp=d.split||{};
  out.innerHTML=`<div class="s">样本内 ${esc((sp.in_sample||[]).join(" → "))}（${sp.in_days} 天）→ `+
    `样本外 ${esc((sp.out_of_sample||[]).join(" → "))}（${sp.oos_days} 天）；`+
    `样本内第一名超额 ${fmt(d.sample_in_best_excess)}%，样本外 ${d.n_oos_positive}/${d.n_tested} 组为正</div>`+
    // T36：过拟合差距 = 事后最优 − 样本内第 1 名（旧版把同一个数当两个用，量不出来）
    `<div class="s">样本内第 1 名到样本外还剩 ${fmt(d.in_best_oos_excess_pct)}%；`+
    `事后最优 ${fmt(d.oracle_oos_excess_pct)}%（${esc(sym(d.oracle_oos_symbol||"—"))}）；`+
    `<b class="${(d.overfit_gap_pct||0)>0?'bad':'good'}">过拟合差距 ${fmt(d.overfit_gap_pct)}%</b></div>`+
    `<div class="scroll"><table><thead><tr><th>标的</th><th>参数</th><th class="num">样本内超额</th><th class="num">样本外收益</th><th class="num">样本外超额</th><th class="num">笔数</th><th class="num">夏普</th></tr></thead><tbody>`+
    (d.out_of_sample||[]).map(t=>`<tr><td><b>${esc(sym(t.symbol))}</b></td><td class="mut">${esc(JSON.stringify(t.params))}</td>`+
      `<td class="num ${cls(t.in_excess_pct)}">${t.in_excess_pct>=0?"+":""}${fmt(t.in_excess_pct)}%</td>`+
      `<td class="num ${cls(t.oos_ret_pct)}">${fmt(t.oos_ret_pct)}%</td>`+
      `<td class="num ${cls(t.oos_excess_pct)}">${t.oos_excess_pct>=0?"+":""}${fmt(t.oos_excess_pct)}%</td>`+
      `<td class="num">${t.oos_trades}</td><td class="num">${fmt(t.oos_sharpe)}</td></tr>`).join("")+
    `</tbody></table></div><div class="s mut">${esc(d.note||"")}</div>`;
  document.getElementById("engine-stat").textContent="已完成";
};
engineStatus();

/* ---- 条件单：人工挂 / AI 设计 / 手动检查 / 撤销 ---- */
function condRow(){
  const box=document.getElementById("conds");
  const d=(box.dataset.d)||"";
  const os=d?JSON.parse(d):[];
  // 最新在上：按创建时间倒序（缺失时保持原顺序）
  os.sort((a,b)=>String(b.created_at||"").localeCompare(String(a.created_at||"")));
  const body=os.map(o=>{
      const st=COND_ST[o.status]||[o.status,"mut"];
      const amt=o.side==="buy"?(Number(o.value)?fmt(o.value,0)+" 元":`${o.volume} 股`):`${o.volume?o.volume+" 股":"全部持仓"}`;
      const cond=`${esc(COND_CN[o.cond_type]||o.cond_type)} ${fmt(o.cond_value, o.cond_type.startsWith("pct")?2:2)}`;
      const act=o.status==="armed"?`<button class="ccancel" data-id="${o.id}">撤销</button>`:"";
      return `<tr><td><span class="${st[1]}">${esc(st[0])}</span>${o.source==="ai"?' <span class="tag">AI</span>':""}</td>`+
        `<td><b>${esc(sym(o.symbol))}</b></td><td><span class="tag ${o.side}">${o.side==="buy"?"买入":"卖出"}</span></td>`+
        `<td class="num">${cond}</td><td class="num">${amt}</td>`+
        `<td class="mut">${esc(String(o.expiry||"").replace("T"," ").slice(0,16))}</td>`+
        `<td class="mut">${esc(o.note||"")}</td><td>${act}</td></tr>`;});
  box._after=()=>{box.querySelectorAll(".ccancel").forEach(b=>b.onclick=async()=>{
    b.disabled=true;
    const st=document.getElementById("condstat");
    const r=await guard((extra)=>post("/api/cond/cancel",
      Object.assign({admin_token:tok(),id:Number(b.dataset.id)},extra||{})));
    if(!r.ok){st.textContent="撤销失败："+(r.error||"");b.disabled=false;return}
    st.textContent=`已撤销条件单 #${b.dataset.id}`;
    await condStatus();refresh();
  });};
  rowsC(box,[["状态"],["股票"],["方向"],["触发条件",1],["金额/股数",1],["有效期"],["理由"],["操作"]],
    body,8,"暂无条件单 —— 条件单现在不成交，盘中触发才下单");
}
async function condStatus(){
  try{
    const r=await get("/api/cond");const d=(r&&r.data)||{};
    const os=d.orders||[];
    document.getElementById("c-cond").textContent=`待触发 ${d.armed??0} 条`;
    document.getElementById("conds").dataset.d=JSON.stringify(os);
    condRow();
    const q=(d.triggers||[]).filter(t=>String(t.source||"").indexOf("cond")>=0);
    if(q.length)document.getElementById("condstat").textContent=`条件单已触发 ${q.length} 次（见操作流水「条件单触发」）`;
  }catch(e){}
}
document.getElementById("btn-cond-add").onclick=async()=>{
  const st=document.getElementById("condstat");
  const b=Object.assign({admin_token:tok(),
    symbol:document.getElementById("cond-sym").value.trim(),
    side:document.getElementById("cond-side").value,
    cond_type:document.getElementById("cond-type").value,
    cond_value:Number(document.getElementById("cond-trigger").value),
    value:Number(document.getElementById("cond-value").value)||0,
    expiry:document.getElementById("cond-expiry").value||""});
  if(!/^\d{6}$/.test(b.symbol)){st.textContent="请填 6 位股票代码";return}
  if(!(b.cond_value>0)){st.textContent="请填触发值（价格或涨跌幅%）";return}
  if(b.side==="buy"&&!b.value){st.textContent="买入条件单要填金额（元）";return}
  st.textContent="提交中…";
  const r=await guard((extra)=>post("/api/cond",Object.assign(b,extra||{})));
  st.textContent=r.ok?((r.data&&r.data.message)||"已挂"):("失败："+(r.error||""));
  if(r.ok){document.getElementById("cond-trigger").value="";document.getElementById("cond-value").value="";}
  await condStatus();refresh();
};
document.getElementById("btn-cond-design").onclick=async()=>{
  const st=document.getElementById("condstat");st.textContent="模型设计中（只出条件、挂上不成交）…";
  const r=await guard((extra)=>post("/api/cond/design",
    Object.assign({admin_token:tok(),pool_from:"auto",max:5,max_days:3},extra||{})));
  if(!r.ok){st.textContent="设计失败："+(r.error||"");return}
  const d=r.data||{};
  st.textContent=`已挂 ${d.n_created??0} 条${(d.n_rejected||[]).length?`，${d.n_rejected.length} 条未通过校验`:""}`;
  await condStatus();refresh();
};
condStatus();
</script>
</body>
</html>"""


def _ro(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _limit_arg(q: dict, default: int = 30, maxv: int = 500) -> int:
    """查询参数 limit 的安全解析：坏值回默认，负数/超大值夹到 1..maxv。

    旧实现 `int(q.get("limit"))`：`?limit=abc` 直接 500；`?limit=-1` 在 SQLite
    里等于"不限制"，一次请求能把整张 op_log 拉出来。面板是只读页，但错误输入
    不该变成 500 或大响应。
    """
    try:
        v = int(q.get("limit", [str(default)])[0])
    except (TypeError, ValueError, IndexError):
        v = default
    return max(1, min(v, int(maxv)))


_EXPORT_SQL = {
    "nav": ("nav", "SELECT id, timestamp, cash, market_value, total_value,"
                   " available_cash, pnl, pnl_pct FROM nav_history ORDER BY id"),
    "positions": ("positions", "SELECT symbol, total_volume, available_volume,"
                               " avg_cost, peak_price, entry_day, last_update"
                               " FROM positions ORDER BY symbol"),
    "orders": ("orders", "SELECT order_id, symbol, direction, volume, limit_price,"
                         " filled_price, filled_volume, status, commission,"
                         " stamp_duty, transfer_fee, created_at, filled_at"
                         " FROM orders ORDER BY order_id"),
    "fills": ("fills", "SELECT fill_id, order_id, symbol, direction, volume, price,"
                       " commission, stamp_duty, transfer_fee, timestamp"
                       " FROM fills ORDER BY fill_id"),
    "intents": ("intents", "SELECT biz_id, symbol, side, volume, rule_ref, status,"
                           " created_at, result FROM rq_intents"
                           " ORDER BY created_at"),
    "cond": ("conditional_orders", "SELECT id, symbol, side, cond_type, cond_value,"
                                   " volume, value, expiry, status, filled_at,"
                                   " filled_price, created_at FROM conditional_orders"
                                   " ORDER BY id"),
}


def _export_csv(account_db: str, kind: str, limit: int = 5000) -> tuple[str, str]:
    """只读导出 CSV：返回 (filename, text)。`kind=performance` 用净值指标。"""
    import csv
    import io

    kind = str(kind or "nav")
    if kind == "performance":
        perf = _perf_payload(account_db)
        cols = ["date", "total_value", "ret", "drawdown"]
        rows = [[p["date"], p["total_value"], p["ret"], p["drawdown"]]
                for p in perf["series"]]
    else:
        spec = _EXPORT_SQL.get(kind)
        if not spec:
            raise ValueError(f"未知导出类型：{kind}（可选 {sorted(_EXPORT_SQL)} + performance）")
        conn = _ro(account_db)
        try:
            cur = conn.execute(spec[1] + " LIMIT ?", (int(limit),))
            cols = [d[0] for d in cur.description]
            rows = [list(r) for r in cur.fetchall()]
        finally:
            conn.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    stamp = datetime.now().strftime("%Y%m%d")
    return f"paper_trading_{kind}_{stamp}.csv", buf.getvalue()


def _takeover_token(body: dict, secrets_path) -> str:
    """凭 Key 接管：body.api_key 与已存一致时返回可存的口令，否则空串。
    供换浏览器/首次使用场景（与 llm/config 的接管规则一致）。"""
    from paper_trading.llm import load_secrets
    try:
        data = load_secrets(secrets_path)
    except Exception:
        return ""
    key = str((body or {}).get("api_key") or "").strip()
    saved = ((data.get("provider") or {}).get("api_key") or "")
    want = (data.get("admin_token") or "")
    if key and saved and key == saved and want:
        return want
    return ""


def _engine_view(bridge) -> dict:
    """规则引擎总览：配置 + 规则集 + 最近触发记录（面板只读展示用）。"""
    from paper_trading.engine.config import load_engine_config, validate
    from paper_trading.engine.rules import RULE_TYPES

    cfg_path = getattr(bridge, "config_path", "config.yaml") or "config.yaml"
    cfg = load_engine_config(cfg_path)
    ok, msg = validate(cfg)
    rules = cfg.get("rules") or []
    return {
        "enabled": bool(cfg.get("enabled")),
        "interval_sec": cfg.get("interval_sec"),
        "min_ticket_cny": cfg.get("min_ticket_cny"),
        "max_orders_per_day": cfg.get("max_orders_per_day"),
        "valid": ok, "message": msg,
        "rule_types": list(RULE_TYPES),
        "rules": [{"id": r.get("id"), "type": r.get("type"),
                   "symbols": (r.get("symbols") or ([r["symbol"]] if r.get("symbol") else [])),
                   "params": r.get("params"), "action": r.get("action"),
                   "once": bool(r.get("once")), "enabled": r.get("enabled", True)}
                  for r in rules],
        "triggers": _engine_triggers(bridge.account_db_path),
    }


def _engine_triggers(account_db: str, limit: int = 20) -> list[dict]:
    """最近规则/条件触发流水（表不存在时静默返回空，面板不因此报错）。"""
    conn = _ro(account_db)
    try:
        rows = conn.execute(
            "SELECT * FROM engine_triggers ORDER BY id DESC LIMIT ?",
            (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _backtest_payload(bridge, days: int, symbols, cash: float,
                      slippage: float, curve: bool) -> tuple[bool, dict, str]:
    """回测（面板与 CLI 共用同一口径）。"""
    from paper_trading.engine.backtest import (Backtester, buy_and_hold,
                                              load_minutes, metrics)
    from paper_trading.engine.config import load_engine_config, validate
    from paper_trading.engine.rules import INTRADAY_TYPES

    cfg_path = getattr(bridge, "config_path", "config.yaml") or "config.yaml"
    cfg = load_engine_config(cfg_path)
    ok, msg = validate(cfg)
    if not ok:
        return False, {}, f"规则配置非法：{msg}"
    syms = set(symbols or [])
    for r in (cfg.get("rules") or []):
        syms.update(r.get("symbols") or ([r["symbol"]] if r.get("symbol") else []))
    syms = sorted(s for s in syms if s)
    if not syms:
        return False, {}, "规则集里没有标的：请先在 config.yaml 的 engine.rules 配股票"
    bars_by_sym, day_list = _load_bars(bridge, syms, days)
    if not bars_by_sym:
        return False, {}, "本地没有日线数据，请先同步（sync）"
    fee = _fee_of(cfg_path)
    minutes_by_day = {}
    if any(str(r.get("type") or "") in INTRADAY_TYPES
           for r in (cfg.get("rules") or [])):
        minutes_by_day = load_minutes(bridge.data_db, syms, day_list)
    res = Backtester(cfg, fee=fee, slippage_pct=float(slippage or 0.0)).run(
        bars_by_sym, day_list, initial_cash=float(cash or 100000.0),
        minutes_by_day=minutes_by_day)
    m = metrics(res, len(day_list))
    m["per_rule"] = res.per_rule
    m["trades_detail"] = res.trades[-30:]
    m["days"] = len(day_list)
    m["symbols"] = syms
    m["intraday_days"] = res.intraday_days
    m["skipped_sample"] = res.skipped[:10]
    bench = buy_and_hold(bars_by_sym, day_list, float(cash or 100000.0), fee)
    if bench:
        m["benchmark_buy_hold"] = bench
        m["excess_pct"] = round(m["total_return_pct"] - bench["total_return_pct"], 2)
    if curve and res.equity_curve:
        eq = [c["equity"] for c in res.equity_curve]
        blocks = "▁▂▃▄▅▆▇█"
        lo, hi = min(eq), max(eq)
        span = (hi - lo) or 1.0
        m["ascii_curve"] = "".join(
            blocks[min(len(blocks) - 1,
                       int((v - lo) / span * (len(blocks) - 1)))] for v in eq)
    return True, m, ""


def _load_bars(bridge, syms: list[str], days: int):
    """取日线 + 交易日集合（回测与网格共用，避免两处口径漂移）。"""
    bars_by_sym, all_days = {}, set()
    for s in syms:
        bars = bridge.data_db.get_bars(s, limit=int(days) + 10)
        if bars:
            bars_by_sym[s] = bars
            all_days.update(b.timestamp.date().isoformat() for b in bars)
    return bars_by_sym, sorted(all_days)[-int(days):]


def _fee_of(cfg_path: str):
    """面板回测/网格用的费用口径：**只从 config 的 TradingConfig 取**（T51 口径唯一）。

    旧写法读 `load_config(cfg)["trading"]`，那个键不存在（返回的是 `trading_config`），
    所以面板一直按硬编码费率算，与实盘/CLI 不同源——改 `config.yaml` 不生效。
    """
    from paper_trading.engine.backtest import FeeModel
    from paper_trading.utils.config import load_config

    return FeeModel.from_trading_config(load_config(cfg_path)["trading_config"])


def _optimize_payload(bridge, grid: str, symbols, days: int, cash: float,
                      value: float, top: int) -> tuple[bool, dict, str]:
    """参数网格搜索（面板与 CLI 共用）。"""
    from paper_trading.engine.backtest import load_minutes
    from paper_trading.engine.config import load_engine_config
    from paper_trading.engine.optimize import GRIDS, INTRADAY_GRIDS, grid_search

    if grid not in GRIDS:
        return False, {}, f"未知网格 {grid}（可选：{','.join(GRIDS)}）"
    cfg_path = getattr(bridge, "config_path", "config.yaml") or "config.yaml"
    cfg = load_engine_config(cfg_path)
    syms = set(symbols or [])
    for r in (cfg.get("rules") or []):
        syms.update(r.get("symbols") or ([r["symbol"]] if r.get("symbol") else []))
    syms = sorted(s for s in syms if s)
    if not syms:
        return False, {}, "请指定标的或在规则集里配股票"
    bars_by_sym, day_list = _load_bars(bridge, syms, days)
    if not bars_by_sym:
        return False, {}, "本地没有日线数据，请先同步（sync）"
    minutes = {}
    if grid in INTRADAY_GRIDS:
        minutes = load_minutes(bridge.data_db, syms, day_list)
    out = grid_search(cfg, bars_by_sym, day_list, grid, syms, value=float(value),
                      fee=_fee_of(cfg_path), initial_cash=float(cash),
                      top=int(top or 10), minutes_by_day=minutes)
    return (not out.get("error")), out, str(out.get("error") or "")


def _walkforward_payload(bridge, grid: str, symbols, days: int, cash: float,
                         value: float, top: int,
                         split: float) -> tuple[bool, dict, str]:
    """样本内选参数 → 样本外检验（面板与 CLI 共用）。"""
    from paper_trading.engine.backtest import load_minutes
    from paper_trading.engine.config import load_engine_config
    from paper_trading.engine.optimize import GRIDS, INTRADAY_GRIDS, walk_forward

    if grid not in GRIDS:
        return False, {}, f"未知网格 {grid}（可选：{','.join(GRIDS)}）"
    cfg_path = getattr(bridge, "config_path", "config.yaml") or "config.yaml"
    cfg = load_engine_config(cfg_path)
    syms = set(symbols or [])
    for r in (cfg.get("rules") or []):
        syms.update(r.get("symbols") or ([r["symbol"]] if r.get("symbol") else []))
    syms = sorted(s for s in syms if s)
    if not syms:
        return False, {}, "请指定标的或在规则集里配股票"
    bars_by_sym, day_list = _load_bars(bridge, syms, days)
    if not bars_by_sym:
        return False, {}, "本地没有日线数据，请先同步（sync）"
    minutes = {}
    if grid in INTRADAY_GRIDS:
        minutes = load_minutes(bridge.data_db, syms, day_list)
    out = walk_forward(cfg, bars_by_sym, day_list, grid, syms, value=float(value),
                       fee=_fee_of(cfg_path), initial_cash=float(cash),
                       top=int(top or 5), split=float(split or 0.6),
                       minutes_by_day=minutes)
    return (not out.get("error")), out, str(out.get("error") or "")


_PROV_CACHE: dict = {"ts": 0.0, "data": None}


def _data_provenance(bridge, symbols: list) -> dict:
    """数据来源汇总（接口级 source/asof/stale/available），30s 缓存。"""
    import time

    now = time.time()
    if _PROV_CACHE["data"] is not None and now - _PROV_CACHE["ts"] < 30:
        return _PROV_CACHE["data"]
    from paper_trading.integration import mother as _m

    cov = _m.coverage(symbols)
    interfaces: dict = {}
    for k, v in cov.items():
        if isinstance(v, dict) and "source" in v:
            interfaces[k] = {"source": v.get("source"), "asof": v.get("asof"),
                             "stale": v.get("stale"), "available": v.get("available")}
        elif isinstance(v, dict):
            items = [x for x in v.values() if isinstance(x, dict)]
            srcs = sorted({x.get("source") for x in items if x.get("source")})
            if srcs:
                interfaces[k] = {
                    "sources": srcs,
                    "stale": any(x.get("stale") for x in items),
                    "available": any(x.get("available") for x in items)}
    data = {
        "daily_source": getattr(bridge, "daily_source", "auto"),
        "interfaces": interfaces,
        "unavailable": [k for k, v in interfaces.items()
                        if v.get("available") is False
                        or v.get("sources") == ["unavailable"]],
    }
    _PROV_CACHE.update(ts=now, data=data)
    return data


#: 面板指标口径（单测见 tests/test_dashboard_perf_t95.py）
_PERF_DEFINITIONS = ("收盘 NAV 序列；同日多条取最后一条；缺日不补；"
                     "收益率相对初始资金（无出入金）；波动/夏普按 252 日年化，rf=0；"
                     "已实现盈亏按 FIFO 含费用")


def _fifo_realized(fills: list[dict]) -> dict:
    """按成交顺序 FIFO 计算已实现盈亏（含买卖费用）。

    卖出来自历史重建（reset/旧线）而买入手数不足时，只累计**匹配部分**，
    未匹配量单独报 `unmatched_sell_volume`——不拿 0 成本硬算，避免虚增收益。
    """
    from collections import defaultdict, deque

    lots: dict[str, deque] = defaultdict(deque)
    realized = fees = 0.0
    wins = losses = 0
    unmatched: dict[str, int] = {}
    for f in fills or []:
        try:
            vol, px = int(f.get("volume") or 0), float(f.get("price") or 0)
            direction = int(f.get("direction") or 0)
        except (TypeError, ValueError):
            continue
        if vol <= 0:
            continue
        fee = sum(float(f.get(k) or 0.0) for k in
                  ("commission", "stamp_duty", "transfer_fee"))
        fees += fee
        sym = str(f.get("symbol") or "")
        if direction == 1:
            lots[sym].append([vol, (px * vol + fee) / vol])
            continue
        left, sell_unit = vol, (px * vol - fee) / vol
        pnl = 0.0
        while left > 0 and lots[sym]:
            lot = lots[sym][0]
            take = min(left, lot[0])
            pnl += (sell_unit - lot[1]) * take
            lot[0] -= take
            left -= take
            if lot[0] == 0:
                lots[sym].popleft()
        if vol - left > 0:
            realized += pnl
            wins += 1 if pnl > 0 else 0
            losses += 1 if pnl < 0 else 0
        if left > 0:
            unmatched[sym] = unmatched.get(sym, 0) + left
    closed = wins + losses
    return {
        "realized_pnl": round(realized, 4),
        "fees_total": round(fees, 4),
        "win_trades": wins,
        "lose_trades": losses,
        "closed_trades": closed,
        "win_rate": round(wins / closed, 4) if closed else None,
        "unmatched_sell_volume": unmatched,
    }


def _intraday_payload(account_db: str, data_db, day: str | None = None) -> dict:
    """当日分时权益（按成交时间线 + 分钟线重建）。

    口径：从**当前账本**反推目标日开盘（先反推目标日之后的成交，再反推当日
    成交），然后按当日分钟线给持仓估值；成交在时间戳 ≤ 当前 bar 时生效。
    这不是逐笔撮合，只用于看“今天钱怎么走的”；`reconstruction_ok=false` 时
    说明反推出现负仓位/负现金，图仍画但需人工核账。
    """
    explicit = day is not None
    day = str(day or date.today().isoformat())[:10]
    requested = day
    conn = _ro(account_db)
    try:
        cash = float(conn.execute("SELECT cash FROM account LIMIT 1").fetchone()[0])
        pos = {str(r["symbol"]): int(r["total_volume"] or 0)
               for r in conn.execute("SELECT symbol, total_volume FROM positions")}
        fills = [dict(r) for r in conn.execute(
            "SELECT fill_id, symbol, direction, volume, price, commission,"
            " stamp_duty, transfer_fee, timestamp FROM fills ORDER BY fill_id")]
    finally:
        conn.close()

    def _fee(f: dict) -> float:
        return sum(float(f.get(k) or 0.0) for k in
                   ("commission", "stamp_duty", "transfer_fee"))

    def _forward(f: dict, p: dict, c: float):
        vol, px = int(f["volume"] or 0), float(f["price"] or 0)
        sym = str(f["symbol"])
        if int(f["direction"]) == 1:
            p[sym] = p.get(sym, 0) + vol
            c -= px * vol + _fee(f)
        else:
            p[sym] = p.get(sym, 0) - vol
            c += px * vol - _fee(f)
        return p, c

    def _backward(f: dict, p: dict, c: float):
        vol, px = int(f["volume"] or 0), float(f["price"] or 0)
        sym = str(f["symbol"])
        if int(f["direction"]) == 1:
            p[sym] = p.get(sym, 0) - vol
            c += px * vol + _fee(f)
        else:
            p[sym] = p.get(sym, 0) + vol
            c -= px * vol - _fee(f)
        return p, c

    end_pos, end_cash = dict(pos), cash
    for f in reversed(fills):
        if str(f["timestamp"])[:10] > day:
            end_pos, end_cash = _backward(f, end_pos, end_cash)
    start_pos, start_cash = dict(end_pos), end_cash
    day_fills = [f for f in fills if str(f["timestamp"])[:10] == day]
    for f in reversed(day_fills):
        start_pos, start_cash = _backward(f, start_pos, start_cash)

    try:
        dconn = _ro(str(data_db))
        try:
            p5 = dconn.execute(
                "SELECT symbol, ts, close FROM minute_bars"
                " WHERE substr(ts,1,10)=? AND period='5' ORDER BY ts",
                (day,)).fetchall()
            rows = p5 or dconn.execute(
                "SELECT symbol, ts, close FROM minute_bars"
                " WHERE substr(ts,1,10)=? ORDER BY ts", (day,)).fetchall()
            bars: dict[str, dict[str, float]] = {}
            for r in rows:
                if r["close"] is None:
                    continue
                bars.setdefault(str(r["ts"]), {})[str(r["symbol"])] = float(r["close"])
            prior: dict[str, float] = {}
            row = dconn.execute(
                "SELECT MAX(substr(timestamp,1,10)) FROM daily_bars_raw"
                " WHERE substr(timestamp,1,10) < ?", (day,)).fetchone()
            if row and row[0]:
                prior = {str(r["symbol"]): float(r["close"]) for r in dconn.execute(
                    "SELECT symbol, close FROM daily_bars_raw"
                    " WHERE substr(timestamp,1,10)=? AND close IS NOT NULL", (row[0],))}
        finally:
            dconn.close()
    except sqlite3.Error:
        bars, prior = {}, {}

    if not bars:
        if not explicit:
            latest = None
            try:
                lc = _ro(str(data_db))
                try:
                    row = lc.execute(
                        "SELECT MAX(substr(ts,1,10)) FROM minute_bars"
                        " WHERE substr(ts,1,10) <= ?", (day,)).fetchone()
                    latest = row[0] if row else None
                finally:
                    lc.close()
            except sqlite3.Error:
                latest = None
            if latest and latest < day:
                out = _intraday_payload(account_db, data_db, latest)
                out["fallback"] = True
                out["requested_date"] = day
                return out
        return {"date": day, "available": False, "series": [],
                "fills": len(day_fills),
                "note": "当日无分钟线（非交易日/采集未到）；分时不可用"}

    stamps = sorted(bars)
    first_px = dict(bars[stamps[0]])
    start_prices = {**first_px, **prior}          # 09:30 优先昨收，缺则首根
    cur_pos, cur_cash = dict(start_pos), start_cash
    day_fills_sorted = sorted(day_fills, key=lambda f: str(f["timestamp"]))

    def _value(p: dict, c: float, px_map: dict) -> tuple[float, float]:
        mv = sum(float(v) * float(px_map.get(s, last_price.get(s, 0.0)))
                 for s, v in p.items() if v > 0)
        return mv, c + mv

    last_price = dict(start_prices)
    start_mv = sum(float(v) * float(start_prices.get(s, 0.0))
                   for s, v in cur_pos.items() if v > 0)
    series = [{"ts": f"{day}T09:30", "total": round(cur_cash + start_mv, 4),
               "cash": round(cur_cash, 4), "mv": round(start_mv, 4)}]
    fi = 0
    for ts in stamps:
        while (fi < len(day_fills_sorted)
               and str(day_fills_sorted[fi]["timestamp"])[:16] <= ts[:16]):
            cur_pos, cur_cash = _forward(day_fills_sorted[fi], cur_pos, cur_cash)
            fi += 1
        px_map = bars[ts]
        last_price.update(px_map)
        mv, total = _value(cur_pos, cur_cash, px_map)
        series.append({"ts": ts, "total": round(total, 4),
                       "cash": round(cur_cash, 4), "mv": round(mv, 4)})

    ok = (all(v >= 0 for v in start_pos.values())
          and all(v >= 0 for v in cur_pos.values()) and start_cash >= 0)
    return {"date": day, "available": True, "series": series,
            "fills": len(day_fills), "reconstruction_ok": bool(ok),
            "start": {"cash": round(start_cash, 4),
                      "positions": {k: v for k, v in start_pos.items() if v}},
            "note": "按当前账本反推当日开盘；分钟线估值，非逐笔撮合"}


def _perf_payload(account_db: str) -> dict:
    """净值曲线 + 面板指标（只读；唯一口径在服务端，前端只负责画）。

    2026-11-19 生产实测：`nav_history` 34 行但只有 26 个交易日（reset/补记
    会在同一天留下两行），且缺 8 个交易日。所以这里必须：同日取最后一条、
    缺日显性报告——否则曲线会把"没记录"画成"没变化"。
    """
    init = 100_000.0
    rows: list[tuple] = []
    fills: list[dict] = []
    try:
        conn = _ro(account_db)
        try:
            has_nav = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nav_history'"
            ).fetchone()
            if has_nav:
                a = conn.execute("SELECT initial_cash FROM account LIMIT 1").fetchone()
                if a and a[0]:
                    init = float(a[0])
                rows = [(str(r["timestamp"])[:10], float(r["total_value"]),
                         float(r["cash"] or 0.0), float(r["market_value"] or 0.0))
                        for r in conn.execute(
                            "SELECT timestamp, total_value, cash, market_value"
                            " FROM nav_history ORDER BY id")]
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                            " AND name='fills'").fetchone():
                fills = [dict(r) for r in conn.execute(
                    "SELECT fill_id, symbol, direction, volume, price, commission,"
                    " stamp_duty, transfer_fee FROM fills ORDER BY fill_id")]
        finally:
            conn.close()
    except sqlite3.Error:
        rows = []
        fills = []

    by_day: dict[str, tuple] = {}
    for d, tv, cash, mv in rows:
        by_day[d] = (tv, cash, mv)          # 同日多条：后写覆盖前写（取最后）
    days = sorted(by_day)

    series: list[dict] = []
    peak: float | None = None
    for d in days:
        tv = by_day[d][0]
        peak = tv if peak is None else max(peak, tv)
        series.append({"date": d, "total_value": round(tv, 4),
                       "ret": round(tv / init - 1, 6),
                       "drawdown": round(1 - tv / peak, 6)})

    summary = {
        "total_return": None, "total_pnl": None,
        "total_value": None, "cash": None, "market_value": None,
        "today_pnl": None, "today_pnl_pct": None,
        "mdd": None, "mdd_date": None, "current_drawdown": None,
        "vol_annual": None, "sharpe": None,
        "win_days": None, "lose_days": None, "exposure": None,
        "realized_pnl": None, "fees_total": None, "win_trades": None,
        "lose_trades": None, "closed_trades": None, "win_rate": None,
        "unmatched_sell_volume": {},
        "estimated": False,
    }
    if fills:
        summary.update(_fifo_realized(fills))
    if days:
        last_tv, last_cash, last_mv = by_day[days[-1]]
        summary["total_return"] = round(last_tv / init - 1, 6)
        summary["total_pnl"] = round(last_tv - init, 4)
        summary["total_value"] = round(last_tv, 4)
        summary["cash"] = round(last_cash, 4)
        summary["market_value"] = round(last_mv, 4)
        summary["exposure"] = round(last_mv / last_tv, 6) if last_tv else None
    if len(days) >= 2:
        navs = [by_day[d][0] for d in days]
        rets = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs))]
        peak_v, mdd, mdd_at = navs[0], 0.0, days[0]
        for i, v in enumerate(navs):
            peak_v = max(peak_v, v)
            dd = 1 - v / peak_v
            if dd > mdd:
                mdd, mdd_at = dd, days[i]
        mean = sum(rets) / len(rets)
        var = (sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
               if len(rets) > 1 else 0.0)
        sd = math.sqrt(var) if var > 0 else 0.0
        summary.update({
            "today_pnl": round(navs[-1] - navs[-2], 4),
            "today_pnl_pct": round(navs[-1] / navs[-2] - 1, 6),
            "mdd": round(mdd, 6), "mdd_date": mdd_at,
            "current_drawdown": round(1 - navs[-1] / max(navs), 6),
            "vol_annual": round(sd * math.sqrt(252), 6) if sd else None,
            "sharpe": (round(mean / sd * math.sqrt(252), 4)
                       if sd else None),
            "win_days": sum(1 for r in rets if r > 0),
            "lose_days": sum(1 for r in rets if r < 0),
        })

    expected: list[str] = []
    if days:
        try:
            from paper_trading.utils.trading_calendar import is_trading_day

            d = date.fromisoformat(days[0])
            end = date.fromisoformat(days[-1])
            while d <= end:
                if is_trading_day(d):
                    expected.append(d.isoformat())
                d += timedelta(days=1)
        except Exception:                       # noqa: BLE001 - 日历坏了不拖垮面板
            expected = list(days)
    coverage = {
        "expected": len(expected) or len(days),
        "present": len(days),
        "missing": [d for d in expected if d not in by_day],
    }
    return {"initial_cash": init, "series": series, "summary": summary,
            "coverage": coverage, "asof": days[-1] if days else None,
            "definitions": _PERF_DEFINITIONS}


def _today_checks(account_db: str, today: str) -> dict | None:
    """读当天最后一条 daily-check 记录（cron 写的 jsonl；面板不现场跑）。"""
    p = Path(str(account_db)).resolve().parent / "logs" / "daily_check.jsonl"
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return None
    for ln in reversed(lines[-80:]):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get("kind") != "daily_check" or str(d.get("today")) != today:
            continue
        return {"at": d.get("at"), "green": d.get("green"), "warn": d.get("warn"),
                "red": d.get("red"), "red_ids": d.get("red_ids") or [],
                "warn_ids": d.get("warn_ids") or [],
                "rollback_required": d.get("rollback_required")}
    return None


def _overview_payload(bridge) -> dict:
    """今日计数 + 健康 + 体检摘要：给"今日/系统"卡用（只读聚合）。"""
    from paper_trading.utils import heartbeat as hb

    st = bridge.get_status()
    today = datetime.now().date().isoformat()
    db = bridge.broker.db_path
    fills = {"n": 0, "buy": 0, "sell": 0}
    conds = {"armed": 0, "queued_rq": 0, "filled_today": 0, "created_today": 0}
    intents = {"executed_today": 0, "rejected_today": 0, "pending": 0}
    navs: list[tuple[str, float]] = []
    plan = None
    try:
        conn = _ro(db)
        try:
            tabs = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "fills" in tabs:
                for r in conn.execute(
                        "SELECT direction, COUNT(*) n FROM fills"
                        " WHERE substr(timestamp,1,10)=? GROUP BY direction", (today,)):
                    n = int(r["n"])
                    fills["n"] += n
                    if int(r["direction"]) == 1:
                        fills["buy"] += n
                    else:
                        fills["sell"] += n
            if "conditional_orders" in tabs:
                for r in conn.execute(
                        "SELECT status, COUNT(*) n FROM conditional_orders"
                        " GROUP BY status"):
                    if r["status"] in ("armed", "queued_rq"):
                        conds[r["status"]] = int(r["n"])
                conds["filled_today"] = int(conn.execute(
                    "SELECT COUNT(*) FROM conditional_orders"
                    " WHERE status='filled' AND substr(filled_at,1,10)=?",
                    (today,)).fetchone()[0])
                conds["created_today"] = int(conn.execute(
                    "SELECT COUNT(*) FROM conditional_orders"
                    " WHERE substr(created_at,1,10)=?", (today,)).fetchone()[0])
            if "rq_intents" in tabs:
                for r in conn.execute(
                        "SELECT status, COUNT(*) n FROM rq_intents"
                        " WHERE substr(created_at,1,10)=? GROUP BY status", (today,)):
                    if r["status"] == "executed":
                        intents["executed_today"] = int(r["n"])
                    elif r["status"] == "rejected":
                        intents["rejected_today"] = int(r["n"])
                intents["pending"] = int(conn.execute(
                    "SELECT COUNT(*) FROM rq_intents"
                    " WHERE status IN ('pending','queued')").fetchone()[0])
            if "nav_history" in tabs:
                navs = [(str(r[0])[:10], float(r[1])) for r in conn.execute(
                    "SELECT timestamp, total_value FROM nav_history ORDER BY id")]
            if "agent_plans" in tabs:
                row = conn.execute(
                    "SELECT id, plan_date, status, plan_json FROM agent_plans"
                    " ORDER BY id DESC LIMIT 1").fetchone()
                if row:
                    try:
                        pj = json.loads(row["plan_json"] or "{}")
                    except ValueError:
                        pj = {}
                    plan = {"id": row["id"], "plan_date": row["plan_date"],
                            "status": row["status"], "asof": pj.get("asof"),
                            "summary": str(pj.get("summary") or "")[:160],
                            "freshness": pj.get("freshness"),
                            "actions_rejected": [
                                {"symbol": a.get("symbol"),
                                 "reason": str(a.get("reject_reason") or "")[:120]}
                                for a in (pj.get("actions_rejected") or [])
                                if isinstance(a, dict)][:6]}
        finally:
            conn.close()
    except sqlite3.Error:
        pass

    by_day = {d: tv for d, tv in navs}
    days = sorted(by_day)
    positions = st.get("positions") or []
    prices = _display_price_map(getattr(bridge, "data_db", None), today)
    mv = 0.0
    missing: list[str] = []
    for p in positions:
        info = prices.get(p.get("symbol"))
        px = info["price"] if info else float(p.get("current_price") or 0.0)
        mv += float(p.get("total_volume") or 0) * px
        if not info or info["asof"] != today:
            missing.append(p.get("symbol"))
    live_total = float(st.get("cash") or 0.0) + mv
    prices_ready = not missing
    quote_asof = max((prices[p["symbol"]]["asof"] for p in positions
                      if p.get("symbol") in prices), default=None)
    estimated = False
    today_pnl = None
    base = None
    if days and days[-1] == today:
        prev = by_day[days[-2]] if len(days) >= 2 else None
        today_pnl = (by_day[days[-1]] - prev) if prev is not None else 0.0
        base = prev if prev else by_day[days[-1]]
    elif days and prices_ready:
        last = by_day[days[-1]]
        base = last
        today_pnl = live_total - last
        estimated = True
    elif days:
        estimated = True                 # 今日还没有可信价：不拿昨日价冒充
    pct = (today_pnl / base) if (today_pnl is not None and base) else None
    snap = hb.read_status(max_age_sec=300) or {}
    probe = hb.probe(thresholds={"rq_tick": hb.RQ_TICK_THRESHOLD,
                                 "minute": hb.MINUTE_THRESHOLD})
    health = {**probe, **snap,
              "checks": {**(probe.get("checks") or {}),
                         **(snap.get("checks") or {})}}
    return {
        "date": today,
        "status": st,
        "today_pnl": None if today_pnl is None else round(today_pnl, 4),
        "today_pnl_pct": None if pct is None else round(pct, 6),
        "estimated": estimated,
        "live_total": round(live_total, 4),
        "live_market_value": round(mv, 4),
        "cash": round(float(st.get("cash") or 0.0), 4),
        "fills": fills,
        "conditions": conds,
        "intents": intents,
        "checks": _today_checks(db, today),
        "plan": plan,
        "quote_asof": quote_asof,
        "prices_ready": prices_ready,
        "health": {"healthy": health.get("healthy"),
                   "alerts": health.get("alerts") or [],
                   "checked_at": health.get("checked_at"),
                   "checks": health.get("checks") or {}},
    }


def _display_price_map(data_db, today: str) -> dict:
    """持仓展示价（优先级）：今日 raw 收盘 > 今日分钟线 > 最近 raw 收盘。

    只用于**展示**，不回流到撮合/风控/账本：
    - 收盘后（15:40 raw 落库）用当日 raw，与 NAV 同源；
    - 盘中 raw 还没今天，用当日分钟线（每 15 分钟更新），标注"分钟 HH:MM"；
    - 开盘前只有上一交易日 raw，标注"收盘"。
    这样"现价 / 市值 / 今日盈亏"在任意时点都能互相对上；不会再出现
    "价格是实时的、市值是昨天的"这种半截数据。
    """
    p = getattr(data_db, "db_path", None) or getattr(data_db, "path", None)
    out: dict[str, dict] = {}
    if not p:
        return out
    try:
        conn = _ro(str(p))
        try:
            tabs = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            latest = None
            if "daily_bars_raw" in tabs:
                row = conn.execute(
                    "SELECT MAX(substr(timestamp,1,10)) FROM daily_bars_raw").fetchone()
                latest = row[0] if row else None
                if latest:
                    for r in conn.execute(
                            "SELECT symbol, close FROM daily_bars_raw"
                            " WHERE substr(timestamp,1,10)=?", (latest,)):
                        if r[1] is None:
                            continue
                        out[r[0]] = {"price": float(r[1]), "asof": latest,
                                     "source": "收盘", "time": ""}
            if "minute_bars" in tabs:
                rows = conn.execute(
                    "SELECT symbol, ts, close FROM minute_bars"
                    " WHERE substr(ts,1,10)=? ORDER BY ts", (today,)).fetchall()
                for r in rows:
                    if r[2] is None:
                        continue
                    sym = r[0]
                    cur = out.get(sym)
                    if cur and cur["asof"] == today and cur["source"] == "收盘":
                        continue                     # 今日 raw 优先于分钟
                    out[sym] = {"price": float(r[2]), "asof": today,
                                "source": "分钟", "time": str(r[1])[11:16]}
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return out


def _raw_closes(data_db, symbol: str, n: int = 10) -> list[float]:
    """最近 n 根 raw 收盘（升序），供迷你走势用。"""
    p = getattr(data_db, "db_path", None) or getattr(data_db, "path", None)
    if not p:
        return []
    try:
        conn = _ro(str(p))
        try:
            rows = conn.execute(
                "SELECT close FROM daily_bars_raw WHERE symbol=?"
                " ORDER BY timestamp DESC LIMIT ?", (symbol, int(n))).fetchall()
            return [float(r[0]) for r in reversed(rows) if r[0] is not None]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def _positions_payload(bridge) -> list[dict]:
    """持仓表数据：现价/市值/走势用展示价（今日 raw > 分钟 > 最近 raw）。"""
    st = bridge.get_status()
    today = datetime.now().date().isoformat()
    prices = _display_price_map(getattr(bridge, "data_db", None), today)
    pos = {p.symbol: p for p in bridge.broker.get_all_positions()}
    for row in st["positions"]:
        p = pos.get(row["symbol"])
        row["peak_price"] = getattr(p, "peak_price", None)
        row["entry_day"] = getattr(p, "entry_day", None)
        info = prices.get(row["symbol"])
        if info:
            row["current_price"] = info["price"]
            row["market_value"] = float(row["total_volume"]) * info["price"]
            row["price_asof"] = info["asof"]
            row["price_source"] = info["source"]
            row["price_time"] = info["time"]
        else:
            row["price_asof"] = None
            row["price_source"] = "日线"
            row["price_time"] = ""
        row["spark"] = _raw_closes(getattr(bridge, "data_db", None),
                                   row["symbol"], 10)
    return st["positions"]


class Handler(BaseHTTPRequestHandler):
    data_db = "data.db"
    account_db = "paper_account.db"
    secrets_path = None

    def _send(self, code: int, body: bytes, ctype: str,
              extra_headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        from paper_trading.utils.jsonx import safe

        body = json.dumps(safe(data), ensure_ascii=False, default=str,
                          allow_nan=False).encode()
        self._send(code, body, "application/json; charset=utf-8")

    def _bridge(self):
        from paper_trading.cli import TradingBridge

        return TradingBridge(data_db=self.data_db, account_db=self.account_db,
                            secrets_path=self.secrets_path)

    def _read_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n).decode("utf-8", "ignore")
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except ValueError as e:
            # 坏 JSON 以前静默变空 body，用户看到的是误导性报错（如"未知厂商"）。
            # 这里直接报出真实原因：请求体不是合法 JSON。
            raise ValueError(f"请求体不是合法 JSON：{e}") from None
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _same_origin(self) -> bool:
        """写请求来源校验：浏览器跨站 POST（CSRF）默认拒绝。

        同源 POST 会带 `Origin` 且 host == `Host`；非浏览器客户端
        （curl / CLI）不带 `Origin`，放行（本机与局域网运维不受影响）。
        """
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        try:
            host = (self.headers.get("Host") or "").split(":")[0]
            return (urlparse(origin).hostname or "").lower() == host.lower()
        except Exception:                       # noqa: BLE001
            return False

    def _admin_ok(self, body: dict) -> bool:
        """写操作口令校验（头 X-Admin-Token 或 body.admin_token）。"""
        from paper_trading.llm import load_secrets

        want = (load_secrets(self.secrets_path).get("admin_token") or "")
        if not want:
            return False  # 未配置过=未设口令，拒绝写操作，引导先保存配置
        got = (self.headers.get("X-Admin-Token") or "") or str(body.get("admin_token") or "")
        return bool(got) and hmac.compare_digest(got, want)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif u.path == "/api/health":
                # 只读看门狗状态（var/health_status.json + 实时心跳体检），不碰账本
                from paper_trading.utils import heartbeat as hb

                st = hb.read_status(max_age_sec=300)   # Q11：快照超 5 分钟=无效
                if not st:                             # → 落 probe() 实时判
                    st = hb.probe()
                self._json({"ok": True, "data": st})
            elif u.path == "/api/status":
                from paper_trading.cli import TradingBridge

                b = TradingBridge(data_db=self.data_db, account_db=self.account_db)
                self._json({"ok": True, "data": b.get_status()})
            elif u.path == "/api/data-provenance":
                from paper_trading.cli import TradingBridge

                b = TradingBridge(data_db=self.data_db, account_db=self.account_db)
                syms = list(b.stock_pool) + [p.symbol for p in
                                             b.broker.get_all_positions()]
                self._json({"ok": True, "data": _data_provenance(b, syms)})
            elif u.path == "/api/performance":
                # 收益曲线 + KPI 指标：只读 account db，不构造 bridge（快）
                self._json({"ok": True, "data": _perf_payload(self.account_db)})
            elif u.path == "/api/export.csv":
                q = parse_qs(u.query)
                kind = (q.get("type") or ["nav"])[0]
                limit = _limit_arg(q, 5000, 20000)
                try:
                    filename, text = _export_csv(self.account_db, kind, limit)
                except ValueError as exc:
                    self._json({"ok": False, "error": str(exc)}, code=400)
                    return
                self._send(200, ("\ufeff" + text).encode("utf-8"),
                           "text/csv; charset=utf-8",
                           {"Content-Disposition":
                            f'attachment; filename="{filename}"'})
            elif u.path == "/api/intraday":
                q = parse_qs(u.query)
                day = (q.get("date") or [None])[0]
                self._json({"ok": True, "data": _intraday_payload(
                    self.account_db, self.data_db, day)})
            elif u.path == "/api/overview":
                # 今日计数 / 健康 / 体检摘要：一次请求供"今日/系统"卡
                self._json({"ok": True, "data": _overview_payload(self._bridge())})
            elif u.path == "/api/positions":
                self._json({"ok": True, "data": _positions_payload(self._bridge())})
            elif u.path == "/api/names":
                conn = _ro(self.data_db)
                try:
                    rows = conn.execute(
                        "SELECT symbol, name FROM stock_pool "
                        "WHERE name IS NOT NULL AND name != ''"
                    ).fetchall()
                    self._json({"ok": True, "data": {r["symbol"]: r["name"] for r in rows}})
                finally:
                    conn.close()
            elif u.path == "/api/schemes":
                from paper_trading.strategy import all_schemes, mother_strategies
                from paper_trading.strategy.schemes import (
                    CUSTOM_ID, GENERAL_ID, MOTHER_ID, custom_instruction)

                from pathlib import Path as _P
                cur = self._bridge()
                _root = _P(__file__).resolve().parents[1]
                _ss = all_schemes(_root)
                _g = _ss.get(GENERAL_ID)
                _items = [
                    {"name": MOTHER_ID, "title": "Stock Dashboard 价值",
                     "desc": "Stock Dashboard 价值投资理念；需在同一台机器",
                     "source": "mother",
                     "available": MOTHER_ID in _ss,
                     "universe": "screening", "exits": "论点卖出条件一票否决"},
                    {"name": GENERAL_ID, "title": _g.title, "desc": _g.desc,
                     "source": "general", "available": True,
                     "universe": _g.universe_source, "exits": _g.exits_note},
                    {"name": CUSTOM_ID, "title": "自定义指令",
                     "desc": "你用自然语言写交易策略，AI 照此执行（风控钳制不变）",
                     "source": "custom", "available": True,
                     "universe": "config", "exits": "以你的指令为准"},
                ]
                self._json({"ok": True, "data": {
                    "active": cur.scheme.name, "source": cur.scheme_source,
                    "instruction": custom_instruction(_root),
                    "schemes": _items,
                    "mother": mother_strategies()}})
            elif u.path in ("/api/agent/runtime", "/api/agent/schedule"):
                from paper_trading.agent.runtime import snapshot as _runtime

                b = self._bridge()
                today = datetime.now().date().isoformat()
                try:
                    pending = b.broker.get_pending_plan(today)
                except Exception:
                    pending = None
                try:
                    data = _runtime(pending_plan=pending,
                                    data_asof=b.data_db.get_data_asof(),
                                    holidays=b.broker._holidays())
                except Exception:
                    data = _runtime(pending_plan=pending)
                data["legacy_schedule"] = (u.path == "/api/agent/schedule")
                self._json({"ok": True, "data": data})
            elif u.path == "/api/quotes":                # 盘中实时行情（60s 服务端缓存；非交易时段回空）
                from paper_trading.data import realtime as _rt

                q = parse_qs(u.query)
                syms = [s for s in (q.get("symbols") or [""])[0].split(",") if s.strip()]
                if not syms:
                    try:
                        b0 = self._bridge()
                        syms = [p.symbol for p in b0.broker.get_all_positions()]
                        syms += [s for s in b0.data_db.get_pool_symbols()
                                 if s not in syms]
                    except Exception:
                        syms = []
                try:
                    self._json({"ok": True, "data": _rt.get_quotes(syms[:20]),
                                "live": _rt.is_trading_session()})
                except Exception as e:
                    self._json({"ok": True, "data": {}, "live": False,
                                "error": str(e)[:120]})
            elif u.path == "/api/engine":
                self._json({"ok": True, "data": _engine_view(self._bridge())})
            elif u.path == "/api/intents":
                q = parse_qs(u.query)
                limit = _limit_arg(q, 50, 500)
                status = (q.get("status") or [None])[0]
                conn = _ro(self.account_db)
                try:
                    sql = ("SELECT biz_id, symbol, side, volume, rule_ref, status,"
                           " created_at, result FROM rq_intents")
                    args: list = []
                    if status:
                        sql += " WHERE status=?"
                        args.append(status)
                    sql += " ORDER BY created_at DESC LIMIT ?"
                    args.append(limit)
                    self._json({"ok": True,
                                "data": [dict(r) for r in conn.execute(sql, args)]})
                finally:
                    conn.close()
            elif u.path == "/api/cond":
                from paper_trading.engine.conditional import ConditionalOrders

                q = parse_qs(u.query)
                co = ConditionalOrders(self.account_db)
                self._json({"ok": True, "data": {
                    "orders": co.list_all(_limit_arg(q, 60),
                                          q.get("status", [None])[0]),
                    "armed": len(co.pending()),
                    "triggers": _engine_triggers(self.account_db)}})
            elif u.path == "/api/llm/status":
                self._json({"ok": True, "data": self._bridge().llm_status()})
            elif u.path == "/api/llm/models":
                # 拉模型不设口令门禁：面板是局域网只读页，Key 不出本机、不回显。
                # 拉不到就如实回失败原因（未配置/Key 被拒/超时/地址非法），一律 200 + ok 字段，
                # 前端按 ok 显示原因，不做"先保存配置才能拉"这类拦截。
                r = self._bridge().llm_models()
                self._json(r, code=200)
            elif u.path in ("/api/nav", "/api/ops", "/api/orders", "/api/fills"):
                q = parse_qs(u.query)
                limit = _limit_arg(q, 30)
                conn = _ro(self.account_db)
                try:
                    # 表名/列名只取自下方固定映射（用户输入仅决定走哪条分支），
                    # limit 走参数化，因此此处 f-string 无注入面
                    table = {"nav": "nav_history", "ops": "op_log",
                             "orders": "orders", "fills": "fills"}[u.path.split("/")[2]]
                    order_col = {"nav_history": "id", "op_log": "id",
                                 "orders": "order_id", "fills": "fill_id"}[table]
                    rows = conn.execute(
                        f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                    self._json({"ok": True, "data": [dict(r) for r in rows]})
                finally:
                    conn.close()
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, code=500)

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        try:
            if not self._same_origin():
                self._json({"ok": False, "error": "跨站请求被拒绝（Origin 与 Host 不一致）"},
                           code=403)
                return
            body = self._read_json()
            if u.path == "/api/schemes/active":
                _tok_out = ""
                if not self._admin_ok(body):
                    # 凭 Key 接管（换浏览器/首次使用；与 llm/config 接管规则一致）
                    _tok_out = _takeover_token(body, self.secrets_path)
                    if not _tok_out:
                        self._json({"ok": False, "error": "口令错误（换了浏览器？切换时填写 API Key 即可接管）"}, code=403)
                        return
                from paper_trading.strategy import set_active

                from pathlib import Path as _P
                ok, msg = set_active(
                    _P(__file__).resolve().parents[1], str(body.get("name", "")),
                    body.get("instruction"))
                _data = {"message": msg} if ok else None
                if ok and _tok_out:
                    _data["admin_token"] = _tok_out
                self._json({"ok": ok, "data": _data,
                            "error": None if ok else msg},
                           code=200 if ok else 400)
            elif u.path == "/api/llm/config":
                st0 = self._bridge().llm_status()
                # 门禁只护「接口地址」：base_url 决定已存 Key 会被发去哪里，
                # 换成攻击者地址 = Key 外泄，所以改地址必须口令，或凭**与已存一致**的
                # API Key 接管（证明知道当前密钥，v0.6.9 起收紧）。
                # 改模型名/厂商/Key 是低风险写操作，直接放行——面板对用户的承诺是
                # "换模型不用重填/不用口令"。但这类免口令保存**不回传** admin_token：
                # 否则任何局域网客户端改一次模型就拿到全部写权限。
                base_in = str(body.get("base_url") or "").strip()
                key_in = str(body.get("api_key") or "").strip()
                base_changed = bool(base_in) and base_in != str(st0.get("base_url") or "")
                authenticated = self._admin_ok(body)
                # 接管改地址必须证明知道**当前已存**的 Key（v0.6.9 起强校验）：
                # 否则局域网内任何人随便填一个 Key 就能改地址并领走写入口令。
                takeover_ok = bool(base_changed and key_in
                                   and _takeover_token(body, self.secrets_path))
                if st0.get("configured") and base_changed and not authenticated \
                        and not takeover_ok:
                    _msg = ("口令错误（换了浏览器？）。改接口地址需要口令，"
                            "或填写当前已存 API Key 接管；只改模型名/厂商可直接保存，无需口令"
                            if st0.get("has_key") else
                            "口令错误：当前是本地/无令牌接口，没有 Key 可用来接管。"
                            "请在原浏览器继续修改，或删除 secrets.local.json 后重新配置")
                    self._json({"ok": False, "error": _msg}, code=403)
                    return
                # 覆盖已存 Key 也必须鉴权：否则攻击者改一个 Key，再凭该 Key
                # 走 `_takeover_token` 领走口令（等价于任意写权限）。
                # 首次设置 Key / 切到免令牌本地预设（清空 Key）不在此列。
                from paper_trading.llm import TOKENLESS_PRESETS, load_secrets

                existing_key = str(
                    (load_secrets(self.secrets_path).get("provider") or {})
                    .get("api_key") or "")
                key_changed = bool(key_in) and key_in != existing_key
                tokenless_preset = str(body.get("preset") or "") in TOKENLESS_PRESETS
                if st0.get("configured") and key_changed and not authenticated \
                        and not tokenless_preset:
                    self._json({"ok": False, "error": (
                        "修改 API Key 需要口令（或删除 secrets.local.json 后重配）；"
                        "只改模型名/厂商可直接保存")}, code=403)
                    return
                # 口令回传口径：只改模型/Key 仍免口令，但**不再**顺手回传口令——
                # 否则局域网内任何人一次“换模型”就能拿到写权限。只在这三种
                # 情况回传：首次配置、调用方已持有效口令、base_url 接管成功。
                allow_token = ((not st0.get("configured")) or authenticated
                               or takeover_ok)
                r = self._bridge().llm_save(
                    body.get("preset", "custom"), base_in,
                    body.get("model", ""), key_in,
                    issue_token=allow_token)
                self._json(r, code=200 if r.get("ok") else 400)
            elif u.path == "/api/agent/fire":
                # 手动触发：立即跑指定动作，不占队列名额（交易用 force，无视已决策闸；
                # 风控/钳制不变；与 cron/CLI 共用运行锁防并发下单）
                if not self._admin_ok(body):
                    _tok_out = _takeover_token(body, self.secrets_path)
                    if not _tok_out:
                        self._json({"ok": False, "error": "口令错误（换了浏览器？先在模型设置里保存一次即接管）"}, code=403)
                        return
                else:
                    _tok_out = ""
                from paper_trading.agent import schedule as _sched

                act = str(body.get("action") or "").strip().lower()
                if act not in _sched.ACTIONS:
                    self._json({"ok": False,
                                "error": f"未知动作（仅支持 {','.join(_sched.ACTIONS)}）"},
                               code=400)
                    return
                from paper_trading.agent import AgentTrader
                from paper_trading.utils.run_lock import run_lock

                from pathlib import Path as _P
                try:
                    with run_lock(_P(__file__).resolve().parents[1] / "paper_trading.lock",
                                  timeout=30):
                        b = self._bridge()
                        syms = list(b.stock_pool)
                        t = AgentTrader(b, b.agent_cfg)
                        if act == "sync":
                            res = b.sync_data(syms)
                            b.broker.log_operation(
                                "sync", {"symbols": syms}, res.get("ok", False),
                                {"updated": res.get("updated")}, None, None)
                            n = sum(v for v in (res.get("updated") or {}).values()
                                    if isinstance(v, int) and v > 0)
                            msg = f"同步完成：新增 {n} 根K线"
                        elif act == "plan":
                            res = t.run(syms, plan_only=True)
                            msg = (f"计划#{res.get('plan_id')}已存（{res.get('asof')}定稿），开盘执行"
                                   if res.get("ok") else f"做计划失败：{res.get('error')}")
                        else:
                            res = t.run(syms, dry_run=(act == "analyze"), force=True)
                            ds = res.get("decisions", []) if res.get("ok") else []
                            fills = sum(1 for d in ds
                                        if str(d.get("status", "")).startswith("已成交"))
                            holds = sum(1 for d in ds if d.get("action") == "hold")
                            rej = len(ds) - fills - holds
                            head = "试运行完成" if act == "analyze" else "实盘决策完成"
                            msg = (f"{head}：成交{fills}笔，持有{holds}条，拒绝{rej}条"
                                   f"（详见操作流水）" if res.get("ok")
                                   else f"执行失败：{res.get('error')}")
                except RuntimeError:
                    self._json({"ok": False, "error": "有定时/手动任务正在跑，稍后再试"},
                               code=409)
                    return
                _data = {"message": msg, "result": res}
                if _tok_out:
                    _data["admin_token"] = _tok_out
                self._json({"ok": res.get("ok", False), "data": _data,
                            "error": None if res.get("ok") else msg},
                           code=200 if res.get("ok") else 502)
            elif u.path in ("/api/engine/backtest", "/api/engine/optimize",
                            "/api/engine/walkforward"):
                # 写操作同口径：口令（或凭 API Key 接管），与其它写接口一致
                if not self._admin_ok(body):
                    _tok_out = _takeover_token(body, self.secrets_path)
                    if not _tok_out:
                        self._json({"ok": False,
                                    "error": "口令错误（换了浏览器？先在模型设置里保存一次即接管）"},
                                   code=403)
                        return
                else:
                    _tok_out = ""
                b = self._bridge()
                if u.path.endswith("backtest"):
                    ok, data, err = _backtest_payload(
                        b, int(body.get("days") or 120),
                        body.get("symbols") or [], float(body.get("cash") or 100000.0),
                        float(body.get("slippage") or 0.0), True)
                elif u.path.endswith("walkforward"):
                    from paper_trading.engine.optimize import walk_forward

                    ok, data, err = _walkforward_payload(
                        b, str(body.get("grid") or "ma_cross"),
                        body.get("symbols") or [], int(body.get("days") or 250),
                        float(body.get("cash") or 100000.0),
                        float(body.get("value") or 10000.0), int(body.get("top") or 5),
                        float(body.get("split") or 0.6))
                else:
                    ok, data, err = _optimize_payload(
                        b, str(body.get("grid") or "ma_cross"),
                        body.get("symbols") or [], int(body.get("days") or 250),
                        float(body.get("cash") or 100000.0),
                        float(body.get("value") or 10000.0), int(body.get("top") or 10))
                if ok and _tok_out:
                    data["admin_token"] = _tok_out
                self._json({"ok": ok, "data": data if ok else None,
                            "error": err or None}, code=200 if ok else 400)
            elif u.path in ("/api/cond", "/api/cond/cancel", "/api/cond/design",
                            "/api/cond/tick"):
                if not self._admin_ok(body):
                    _tok_out = _takeover_token(body, self.secrets_path)
                    if not _tok_out:
                        self._json({"ok": False,
                                    "error": "口令错误（换了浏览器？先在模型设置里保存一次即接管）"},
                                   code=403)
                        return
                else:
                    _tok_out = ""
                from paper_trading.cli import design_conditions, engine_min_ticket
                from paper_trading.engine.conditional import ConditionalOrders
                from paper_trading.engine.runner import ConditionRunner

                orders = ConditionalOrders(self.account_db)
                b = self._bridge()
                if u.path == "/api/cond":
                    ok, msg, oid = orders.create({
                        "symbol": body.get("symbol"), "side": body.get("side"),
                        "cond_type": body.get("cond_type"),
                        "cond_value": body.get("cond_value"),
                        "volume": body.get("volume"), "value": body.get("value"),
                        "expiry": body.get("expiry"), "note": body.get("note"),
                        "source": "manual"},
                        min_ticket=engine_min_ticket())
                    data = {"id": oid, "message": msg,
                            "orders": orders.list_all(60)} if ok else None
                    if ok and _tok_out:
                        data["admin_token"] = _tok_out
                    self._json({"ok": ok, "data": data,
                                "error": None if ok else msg},
                               code=200 if ok else 400)
                    return
                if u.path == "/api/cond/cancel":
                    cid = int(body.get("id") or 0)
                    ok = orders.cancel(cid)
                    self._json({"ok": ok,
                                "data": {"cancelled": ok, "id": cid,
                                         "orders": orders.list_all(60)} if ok else None,
                                "error": None if ok else "条件单不存在或已成交"},
                               code=200 if ok else 400)
                    return
                if u.path == "/api/cond/tick":
                    from paper_trading.engine.config import load_engine_config

                    eng = load_engine_config("config.yaml")
                    r = ConditionRunner(b, orders,
                                        dry_run=bool(body.get("dry_run")),
                                        enabled=bool(eng.get("enabled")),
                                        min_ticket_cny=engine_min_ticket(),
                                        max_per_day=8).tick()
                    r["orders"] = orders.list_all(60)
                    if _tok_out:
                        r["admin_token"] = _tok_out
                    self._json({"ok": True, "data": r})
                    return
                ok, data, err = design_conditions(
                    b, orders, pool_from=str(body.get("pool_from") or "auto"),
                    pool_limit=int(body.get("pool_limit") or 15),
                    max_conds=int(body.get("max") or 5),
                    max_days=int(body.get("max_days") or 3),
                    dry_run=bool(body.get("dry_run")))
                if ok and _tok_out:
                    data["admin_token"] = _tok_out
                data = {**data, "orders": orders.list_all(60)}
                self._json({"ok": ok, "data": data if ok else None,
                            "error": err or None}, code=200 if ok else 400)
            elif u.path == "/api/llm/ask":
                # 2026-11-03 bug 修复：AI 问答是**只读咨询**（prompt→LLM→回复 +
                # 审计流水），不写配置/不碰资金——此前被误列入写操作门禁，
                # 换浏览器/无 admin_token 时（模型设置完全正确）也报
                # "口令错误或未设置"。内网面板 + 无 Key 时 llm_ask 自然失败，
                # 保留审计留痕即可，不再要写口令。
                syms = [str(s) for s in (body.get("symbols") or []) if str(s).strip()]
                r = self._bridge().llm_ask(
                    body.get("prompt", ""), body.get("system", ""),
                    symbols=syms or None,
                    # 默认精简（T63：咨询不需要全池历史）；面板显式传 slim=false 才带全量
                    slim=bool(body.get("slim", True)),
                    max_tokens=body.get("max_tokens") or None)
                self._json(r, code=200 if r.get("ok") else 502)
            elif u.path == "/api/agent/schedule":
                # 旧的手动队列已由代码运行时接管：不再允许面板改写。
                self._json({"ok": False,
                            "error": "旧“AI 定时队列”已停用；运行时刻由代码写死，"
                                     "请在“运行时刻”查看状态，人工补跑用 CLI。"},
                           code=410)
            else:
                self._send(404, b"not found", "text/plain")
        except ValueError as e:            # 请求体不合法：400 + 真实原因（不是 500）
            self._json({"ok": False, "error": str(e)}, code=400)
        except Exception as e:
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, code=500)

    def log_message(self, fmt: str, *args) -> None:  # quiet access log
        pass


def main() -> None:
    global_handler = Handler
    ap = argparse.ArgumentParser(description="Read-only paper trading dashboard")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data-db", default="data.db")
    ap.add_argument("--account-db", default="paper_account.db")
    ap.add_argument("--secrets", default=None, help="LLM 密钥文件路径")
    args = ap.parse_args()
    global_handler.data_db = args.data_db
    global_handler.account_db = args.account_db
    global_handler.secrets_path = args.secrets

    srv = ThreadingHTTPServer((args.host, args.port), global_handler)
    print(f"Dashboard: http://{args.host}:{args.port}  ({datetime.now().isoformat()})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.server_close()


if __name__ == "__main__":
    main()
