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
import sqlite3
import sys
from datetime import datetime
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
.market-row{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:16px 0;margin-bottom:24px;border-bottom:1px solid var(--divider-thin)}
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
@media(max-width:720px){.app{padding:16px}.market-row{grid-template-columns:repeat(2,1fr)}}
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

<div class="market-row" id="cards"></div>
<div class="status-bar" id="statusbar"></div>

<section>
  <div class="section-header"><h2 class="section-title">资产走势</h2><span class="section-count" id="c-nav"></span></div>
  <div class="scroll"><table id="nav"></table></div>
</section>

<section>
  <div class="section-header"><h2 class="section-title">当前持仓</h2><span class="section-count" id="c-pos"></span></div>
  <div class="scroll"><table id="pos"></table></div>
</section>

<section>
  <div class="section-header"><h2 class="section-title">订单记录</h2><span class="section-count" id="c-orders"></span></div>
  <div class="scroll"><table id="orders"></table></div>
</section>

<section>
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

<section>
  <div class="section-header"><h2 class="section-title">运行时刻</h2><span class="section-count" id="c-sched"></span></div>
  <div class="ai-panel">
    <div class="s">交易时段和 AI 窗口由代码写死，不再按面板队列跑：交易日 09:30–11:30、13:00–15:00；09:30–10:00 只执行前一交易日计划；16:30–18:30 数据定稿后做下一交易日计划；盘中每分钟检查异动，只有异动才调 AI；规则/条件单由引擎每分钟执行。</div>
    <div class="ai-row" id="schedrows" style="flex-wrap:wrap"></div>
    <div class="s" id="schedtoday"></div>
    <div class="s">旧版“AI 定时队列”已停用；需要人工补跑时用 CLI：`agent run --plan-only`、`agent event --force`。手动 run 不再被旧队列拦住。</div>
  </div>
</section>

<section>
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

<section>
  <div class="section-header"><h2 class="section-title">条件单</h2><span class="section-count" id="c-cond"></span></div>
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

<section>
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

<section>
  <div class="section-header"><h2 class="section-title">AI 操作流水</h2><span class="section-count" id="c-ops"></span></div>
  <div class="scroll"><table id="ops"></table></div>
</section>

<section>
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

/* ---- 中文映射（名称表来自 /api/names，均为行情源真实名称） ----
   展示规范：股票一律写成 名称(代码)，如 贵州茅台(600519)；
   名称缺失时只写代码，不写“未知”等占位词。 */
let NAMES={};                                   // {symbol: 真实名称}
let QUOTES={};                                  // {symbol: 腾讯实时}（盘中才有）
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
  if(a==="ai:plan")return `计划#${r.plan_id??""}：${r.summary||""}（${r.n_actions??0}条动作、${r.n_conditions??0}条条件${r.n_conditions_rejected?`、${r.n_conditions_rejected}条解析未过`:""}，${r.plan_date?r.plan_date+"开盘": "下一交易日开盘"}执行）`;
  if(a==="sync"){const u=r.updated||{};const ks=Object.keys(u);
    if(!ks.length)return "无更新";
    return ks.map(s=>`${sym(s)}${u[s]<0?"同步失败":`新增 ${u[s]} 根K线`}`).join("、");}
  if(a==="run"||a==="cron-run")return`产生信号 ${r.signals??0} 个，下单 ${r.orders??0} 笔`;
  if(a==="preview")return "风控检查通过";
  return "完成";}
async function refresh(){
 try{
  const [s,ops,pos,ord,nav,nm]=await Promise.all(["/api/status","/api/ops?limit=30","/api/positions","/api/orders?limit=30","/api/nav?limit=60","/api/names"].map(get));
  NAMES=(nm&&nm.data)||{};
  try{const qq=await get("/api/quotes");QUOTES=(qq&&qq.data)||{};}catch(e){QUOTES={};}
  const st=s.data;
  document.getElementById("clock").textContent="行情截至 "+(st.data_asof||"无数据")+" · 页面更新于 "+new Date().toLocaleTimeString("zh-CN");
  document.getElementById("statusbar").innerHTML=`
   <div class="status-item"><span class="status-label">行情截至</span><span class="status-value">${st.data_asof||"无数据"}</span></div>
   <div class="status-item"><span class="status-label">持仓</span><span class="status-value">${st.positions.length} 只</span></div>
   <div class="status-item"><span class="status-label">模式</span><span class="status-value">纸盘 · 只读面板</span></div>`;
  document.getElementById("cards").innerHTML=`
   <div class="market-item"><div class="market-name">总资产 CNY</div><div class="market-value">${fmt(st.total_value,0)}</div><div class="market-change">可用资金 + 持股市值</div></div>
   <div class="market-item"><div class="market-name">浮动盈亏</div><div class="market-value ${cls(st.pnl)}">${st.pnl>=0?"+":""}${fmt(st.pnl)}</div><div class="market-change ${cls(st.pnl)}">${pct(st.pnl_pct)}</div></div>
   <div class="market-item"><div class="market-name">可用资金</div><div class="market-value">${fmt(st.cash,0)}</div><div class="market-change">可下单金额</div></div>
   <div class="market-item"><div class="market-name">持股市值</div><div class="market-value">${fmt(st.market_value,0)}</div><div class="market-change">${st.positions.length} 只股票</div></div>`;
  document.getElementById("c-nav").textContent=(nav.data||[]).length+" 条记录";
  document.getElementById("c-ops").textContent=(ops.data||[]).length+" 条";
  document.getElementById("c-pos").textContent=(st.positions||[]).length+" 只股票";
  document.getElementById("c-orders").textContent=(ord.data||[]).length+" 笔";
  rows(document.getElementById("nav"),[["时间"],["总资产",1],["可用资金",1],["浮动盈亏",1]],
    (nav.data||[]).reverse().map(r=>{const d=new Date(r.timestamp);const pn=Number(r.pnl);
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td class="num">${fmt(r.total_value)}</td><td class="num">${fmt(r.cash)}</td><td class="num ${cls(pn)}">${pn>=0?"+":""}${fmt(pn)}</td></tr>`}).join(""));
  rows(document.getElementById("ops"),[["时间"],["操作"],["详情"],["结果"],["操作后资产",1]],
    (ops.data||[]).map(r=>{const ok=!!r.ok;const d=new Date(r.timestamp);
    const label=ACTION_CN[r.action]||r.action;
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td><b>${esc(label)}</b></td>`+
      `<td class="mut">${esc(paramCN(r.action,r.params))}</td>`+
      `<td class="${ok?"ok":"bad"}">${esc(resultCN(r.action,ok,r.result))}</td>`+
      `<td class="num">${r.total_value_after!=null?fmt(r.total_value_after):"-"}</td></tr>`}).join("")||`<tr><td colspan="5" class="mut">暂无操作记录 —— AI 执行每日结算、买入、卖出后会出现在这里</td></tr>`);
  rows(document.getElementById("pos"),[["股票"],["总股数",1],["可卖股数",1],["买入成本",1],["当前价",1],["持股市值(收盘)",1],["浮动盈亏",1]],
    (st.positions||[]).map(p=>{const pn=(p.current_price-p.avg_cost)*p.total_volume;
    const live=(QUOTES||{})[p.symbol];
    const pxHtml=live&&live.price?`${fmt(live.price)} <span class="tag live">实时</span>`:`${fmt(p.current_price)}`;
    return `<tr><td><b>${esc(sym(p.symbol))}</b></td><td class="num">${p.total_volume}</td><td class="num">${p.available_volume}</td><td class="num">${fmt(p.avg_cost)}</td><td class="num">${pxHtml}</td><td class="num">${fmt(p.market_value,0)}</td><td class="num ${cls(pn)}">${pn>=0?"+":""}${fmt(pn)}</td></tr>`}).join("")||`<tr><td colspan="7" class="mut">空仓</td></tr>`);
  rows(document.getElementById("orders"),[["时间"],["方向"],["股票"],["股数",1],["价格",1],["状态"],["手续费",1]],
    (ord.data||[]).map(r=>{const d=new Date(r.created_at);const buy=r.direction==1;
    const fee=(Number(r.commission)+Number(r.stamp_duty)+Number(r.transfer_fee));
    const stt=r.status=="filled"?`<span class="ok">已成交</span>`:`<span class="bad">已拒绝</span>`;
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td><span class="tag ${buy?"buy":"sell"}">${buy?"买入":"卖出"}</span></td><td><b>${esc(sym(r.symbol))}</b></td><td class="num">${r.volume}</td><td class="num">${fmt(r.filled_price||r.limit_price)}</td><td>${stt}</td><td class="num">${fmt(fee)}</td></tr>`}).join("")||`<tr><td colspan="7" class="mut">暂无订单</td></tr>`);
 }catch(e){document.getElementById("clock").textContent="刷新失败: "+e}
}
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
const COND_ST={armed:["待触发","tag"],filled:["已成交","ok"],cancelled:["已撤销","mut"],
  expired:["已过期","mut"],rejected:["被拒","bad"]};
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
  return `${esc(line)}<br><span class="mut">${esc(bl)}${esc(ex)}（近 ${m.days??0} 个交易日，含真实手续费）</span>`+
    (pr?`<br><span class="mut">${pr}</span>`:"")+
    (note?`<br><span class="mut">注意：${note}</span>`:"")+curve;
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
  rows(box,[["状态"],["股票"],["方向"],["触发条件",1],["金额/股数",1],["有效期"],["理由"],["操作"]],
    d?JSON.parse(d).map(o=>{
      const st=COND_ST[o.status]||[o.status,"mut"];
      const amt=o.side==="buy"?(Number(o.value)?fmt(o.value,0)+" 元":`${o.volume} 股`):`${o.volume?o.volume+" 股":"全部持仓"}`;
      const cond=`${esc(COND_CN[o.cond_type]||o.cond_type)} ${fmt(o.cond_value, o.cond_type.startsWith("pct")?2:2)}`;
      const act=o.status==="armed"?`<button class="ccancel" data-id="${o.id}">撤销</button>`:"";
      return `<tr><td><span class="${st[1]}">${esc(st[0])}</span>${o.source==="ai"?' <span class="tag">AI</span>':""}</td>`+
        `<td><b>${esc(sym(o.symbol))}</b></td><td><span class="tag ${o.side}">${o.side==="buy"?"买入":"卖出"}</span></td>`+
        `<td class="num">${cond}</td><td class="num">${amt}</td>`+
        `<td class="mut">${esc(String(o.expiry||"").replace("T"," ").slice(0,16))}</td>`+
        `<td class="mut">${esc(o.note||"")}</td><td>${act}</td></tr>`;}).join("")
    :`<tr><td colspan="8" class="mut">暂无条件单 —— 条件单现在不成交，盘中触发才下单</td></tr>`);
  box.querySelectorAll(".ccancel").forEach(b=>b.onclick=async()=>{
    b.disabled=true;
    const st=document.getElementById("condstat");
    const r=await guard((extra)=>post("/api/cond/cancel",
      Object.assign({admin_token:tok(),id:Number(b.dataset.id)},extra||{})));
    if(!r.ok){st.textContent="撤销失败："+(r.error||"");b.disabled=false;return}
    st.textContent=`已撤销条件单 #${b.dataset.id}`;
    await condStatus();refresh();
  });
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
    from paper_trading.engine.backtest import FeeModel
    from paper_trading.utils.config import load_config

    t = (load_config(cfg_path).get("trading") or {})
    return FeeModel(
        commission_rate=float(t.get("commission_rate", 0.00025)),
        commission_min=float(t.get("commission_min", 5.0)),
        stamp_duty_rate=float(t.get("stamp_duty_rate", 0.0005)),
        transfer_fee_rate=float(t.get("transfer_fee_rate", 0.00001)))


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


class Handler(BaseHTTPRequestHandler):
    data_db = "data.db"
    account_db = "paper_account.db"
    secrets_path = None

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
            elif u.path == "/api/status":
                from paper_trading.cli import TradingBridge

                b = TradingBridge(data_db=self.data_db, account_db=self.account_db)
                self._json({"ok": True, "data": b.get_status()})
            elif u.path == "/api/positions":
                from paper_trading.cli import TradingBridge

                b = TradingBridge(data_db=self.data_db, account_db=self.account_db)
                self._json({"ok": True, "data": b.get_status()["positions"]})
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
            elif u.path == "/api/cond":
                from paper_trading.engine.conditional import ConditionalOrders

                q = parse_qs(u.query)
                co = ConditionalOrders(self.account_db)
                self._json({"ok": True, "data": {
                    "orders": co.list_all(int(q.get("limit", ["60"])[0] or 60),
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
                limit = int(q.get("limit", ["30"])[0])
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
                if not self._admin_ok(body):
                    self._json({"ok": False, "error": "口令错误或未设置"}, code=403)
                    return
                r = self._bridge().llm_ask(body.get("prompt", ""), body.get("system", ""))
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
