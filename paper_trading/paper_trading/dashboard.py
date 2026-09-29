"""只读 Web 仪表盘：直观展示账户、NAV、AI 操作流水。

用法:
    python -m paper_trading.dashboard --port 8080
    # 浏览器/手机打开 http://<pi-ip>:8080

纯标准库实现，不写任何数据（只读 SQLite），可与交易进程并存。
"""
from __future__ import annotations

import argparse
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
    <div class="s">先选厂商（接口地址自动填好），填 Key，拉模型列表选一个，保存即可。管理口令由浏览器自动保管，不用填也不用记；换浏览器后凭 API Key 保存一次即接管。</div>
    <div class="ai-row"><label>厂商 / 接口地址 / 模型</label></div>
    <div class="ai-row">
      <select id="preset">
        <option value="deepseek">DeepSeek</option>
        <option value="qwen">通义千问（阿里）</option>
        <option value="moonshot">Kimi（月之暗面）</option>
        <option value="glm">智谱 GLM</option>
        <option value="doubao">豆包（火山引擎 Ark）</option>
        <option value="openai">OpenAI</option>
        <option value="custom">自定义（兼容网关/代理）</option>
      </select>
      <input id="base" placeholder="接口地址 https://…/v1" style="flex:2;min-width:200px">
    </div>
    <div class="ai-row">
      <input id="apikey" type="password" placeholder="API Key（只存本机，不回显）" style="flex:2;min-width:200px">
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
    <div class="s">Key 只保存在本机 secrets.local.json（0600 权限），永不进 git、不进日志；页面只显示掩码。局域网使用，不要把面板暴露到公网。</div>
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
  "llm:ask":"AI 问答","llm:config":"保存模型配置","ai:decide":"AI 决策","ai:plan":"AI 计划"};
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
  if(a==="llm:config")return `厂商 ${p.preset||""}，模型 ${p.model||"未填"}`;
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
    if(sk==="no-fresh-bars")return "无今日新行情，跳过（节假日或源未更新）";
    if(sk==="already-decided")return "今日已决策，跳过";
    if(sk==="drawdown-halt")return `回撤熔断（${(r.drawdown*100).toFixed(2)}%），停手`;
    if(sk==="daily-loss-halt")return `日亏熔断（${(r.pnl_pct*100).toFixed(2)}%），停手`;
    const ds=r.decisions||[];
    if(!ds.length)return "无动作";
    return ds.map(d=>{const dir=d.action==="buy"?"买入":d.action==="sell"?"卖出":"持有";
      return `${dir}${sym(d.symbol)}${d.volume||""}股(${d.status||""})`}).join("；");}
  if(a==="llm:config")return `已保存（${r.base_url||""}）`;
  if(a==="ai:plan")return `计划#${r.plan_id??""}：${r.summary||""}（${r.n_actions??0}条，开盘执行）`;
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
const PRESET_URLS={deepseek:"https://api.deepseek.com/v1",qwen:"https://dashscope.aliyuncs.com/compatible-mode/v1",moonshot:"https://api.moonshot.cn/v1",glm:"https://open.bigmodel.cn/api/paas/v4",doubao:"https://ark.cn-beijing.volces.com/api/v3",openai:"https://api.openai.com/v1",custom:""};
const PRESET_MODELS={deepseek:"deepseek-chat",qwen:"qwen-plus",moonshot:"moonshot-v1-8k",glm:"glm-4-flash",doubao:"",openai:"gpt-4o-mini",custom:""};
const KNOWN_URLS=new Set(Object.values(PRESET_URLS));
document.getElementById("preset").addEventListener("change",e=>{
  const b=document.getElementById("base");
  if(!b.value.trim()||KNOWN_URLS.has(b.value.trim()))b.value=PRESET_URLS[e.target.value]||"";
  const m=document.getElementById("model");
  if(!m.value.trim()&&PRESET_MODELS[e.target.value])m.value=PRESET_MODELS[e.target.value];
});
async function post(u,b){const r=await fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});return r.json()}
async function llmStatus(){
  const s=await get("/api/llm/status");const d=s.data||{};
  document.getElementById("llmstat").textContent=d.configured?`已配置：${d.preset} / ${d.model||"未选模型"} / Key ${d.key_masked}`:"未配置：先填接口地址与 Key，点保存配置";
  document.getElementById("apikey").placeholder=d.has_key?`已保存 Key（${d.key_masked}），换模型不用重填；换 Key 才需填写`:"API Key（只存本机，不回显）";
  if(d.preset)document.getElementById("preset").value=d.preset;
  if(d.base_url)document.getElementById("base").value=d.base_url;
  if(d.model)document.getElementById("model").value=d.model;
}
document.getElementById("btn-models").onclick=async()=>{
  const t=tok();if(!t){alert("请先保存一次配置（口令会自动生成并由浏览器保管）");return}
  document.getElementById("llmstat").textContent="拉取中…";
  const r=await get("/api/llm/models?token="+encodeURIComponent(t));
  if(!r.ok){document.getElementById("llmstat").textContent="拉取失败："+(r.error||"");return}
  const sel=document.getElementById("modelsel");sel.innerHTML='<option value="">下拉选择…</option>';
  (r.models||[]).forEach(m=>{const o=document.createElement("option");o.value=m;o.textContent=m;sel.appendChild(o)});
  document.getElementById("llmstat").textContent=`共 ${(r.models||[]).length} 个模型，下拉选一个（会自动填入右侧），或直接手填`;
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
  alert("已保存，以后换模型直接改，不用再碰口令。");
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
llmStatus();
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
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
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
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return {}

    def _admin_ok(self, body: dict) -> bool:
        """写操作口令校验（头 X-Admin-Token 或 body.admin_token）。"""
        from paper_trading.llm import load_secrets

        want = (load_secrets(self.secrets_path).get("admin_token") or "")
        if not want:
            return False  # 未配置过=未设口令，拒绝写操作，引导先保存配置
        got = (self.headers.get("X-Admin-Token") or "") or str(body.get("admin_token") or "")
        return bool(got) and got == want

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
            elif u.path == "/api/llm/status":
                self._json({"ok": True, "data": self._bridge().llm_status()})
            elif u.path == "/api/llm/models":
                # GET 携带口令：/api/llm/models?token=xxx（仅本机局域网使用，勿外网暴露）
                q = parse_qs(u.query)
                tok = (q.get("token") or [""])[0]
                if not self._admin_ok({"admin_token": tok}):
                    self._json({"ok": False, "error": "口令错误或未设置（先保存一次配置生成口令）"},
                               code=403)
                else:
                    r = self._bridge().llm_models()
                    self._json(r, code=200 if r.get("ok") else 502)
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
                # 口令规则：未配置过→直接存；已配置→要口令，或凭 Key 接管（换浏览器不用旧口令）
                if st0.get("configured") and not self._admin_ok(body) \
                        and not str(body.get("api_key") or "").strip():
                    self._json({"ok": False,
                                "error": "口令错误（换了浏览器？填写 API Key 后保存即可接管）"},
                               code=403)
                    return
                r = self._bridge().llm_save(
                    body.get("preset", "custom"), body.get("base_url", ""),
                    body.get("model", ""), body.get("api_key", ""))
                self._json(r, code=200 if r.get("ok") else 400)
            elif u.path == "/api/llm/ask":
                if not self._admin_ok(body):
                    self._json({"ok": False, "error": "口令错误或未设置"}, code=403)
                    return
                r = self._bridge().llm_ask(body.get("prompt", ""), body.get("system", ""))
                self._json(r, code=200 if r.get("ok") else 502)
            else:
                self._send(404, b"not found", "text/plain")
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
