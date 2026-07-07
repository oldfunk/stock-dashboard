# Fix README data collection section

with open('G:/hermes/stock-dashboard/README.md', 'r', encoding='utf-8') as f:
    content = f.read()

changes = 0

# Fix 1: Data source diagram
old = "腾讯行情 (qt.gtimg.cn)\n  ├─ 实时行情: PE/PB/市值/价格/涨跌\n  └─ 大盘指数: 上证/深证/创业板\n\nAKShare（社区维护的数据工具箱 → 东方财富/同花顺底层）\n  ├─ stock_yjbb_em       → 全A股批量财务（1次调用5879只：ROE/毛利率/OCF/EPS/增长率）\n  ├─ stock_financial_abstract_ths → 逐只深度历史（7~20年：净利率/负债率/流动比率/速动比率）\n  ├─ stock_profit_sheet  → 利润表明细（利息费用/总股本/营业利润）\n  └─ stock_cash_flow_sheet → 现金流量表（经营/投资/筹资现金流）"

new = ("腾讯行情 (qt.gtimg.cn)\n"
       "  └─ 实时行情: PE/PB/市值/价格/涨跌（仅用于初筛过滤）\n"
       "\n"
       "AKShare（社区维护的中国金融数据工具箱）\n"
       "  ├─ stock_yjbb_em（东方财富底层）→ 全A股批量财务: ROE/毛利率/OCF/EPS/增长率\n"
       "  ├─ stock_financial_abstract_ths（同花顺底层）→ 逐只深度历史: 净利率/负债率/流动比率\n"
       "  ├─ stock_profit_sheet_by_report_em → 利润表明细: 利息费用/总股本\n"
       "  └─ stock_cash_flow_sheet_by_report_em → 现金流量表: 经营/投资现金流 → FCF")

if old in content:
    content = content.replace(old, new)
    changes += 1
    print("Fix 1: Data source diagram")
else:
    print("Fix 1 FAILED")

# Fix 2: AKShare rationale
old2 = ("- **为什么选 AKShare**：社区主力维护的中国金融数据工具箱，数据来源覆盖东方财富、"
        "同花顺、新浪、腾讯等多个渠道。底层 API 如果变更，AKShare 会被社区修复后自动更新，"
        "无需手动适配。长期数据稳定性优于直连东财 API。")
new2 = ("- **为什么选 AKShare**：社区主力维护的中国金融数据工具箱，数据来源覆盖东方财富、"
        "同花顺等多个渠道。底层 API 如果变更，AKShare 会被社区修复后自动更新。"
        "长期数据稳定性优于手写直连。")

# Check both > and - formats
import re
if old2 in content:
    content = content.replace(old2, new2)
    changes += 1
    print("Fix 2: AKShare note")
else:
    # Try with > format
    old2b = old2.replace('- **', '> **')
    new2b = new2.replace('- **', '> **')
    if old2b in content:
        content = content.replace(old2b, new2b)
        changes += 1
        print("Fix 2: AKShare note (> format)")
    else:
        print("Fix 2 FAILED")

# Fix 3: Pipeline steps
old3 = ("每日 15:30（收盘后自动触发）:\n"
        "  1. 腾讯API → 全A股行情 → stock_snapshot\n"
        "  2. 东财API → 当前财务数据（165列全量）\n"
        "  3. 初筛（PE/PB/市值/排除ST）\n"
        "  4. 历史财务采集 → 进入 financial_history\n"
        "  5. 重建 5年汇总 → financial_summary\n"
        "  6. 7条门规筛选 → 计算评分 → Top 20\n"
        "  7. AI分析（镜子测试+逆向思考）")

new3 = ("每日 15:30（收盘后自动触发）:\n"
        "  1. 腾讯行情 → 全A股行情（PE/PB/市值）→ 初筛预过滤\n"
        "  2. AKShare stock_yjbb_em → 当前财务（ROE/毛利率/OCF）\n"
        "  3. AKShare stock_financial_abstract_ths → 逐只深度补充（净利率/负债率）\n"
        "  4. AKShare 利润表+现金流表 → 历史财务采集 → financial_history\n"
        "  5. 重建 5年汇总 → financial_summary\n"
        "  6. 7条门规筛选 → 计算评分 → 候选池\n"
        "  7. AI分析（镜子测试+逆向思考）→ Top 20")

if old3 in content:
    content = content.replace(old3, new3)
    changes += 1
    print("Fix 3: Pipeline steps")
else:
    # Try finding it with regex
    match = re.search(r'每日 15:30.*?Top 20', content, re.DOTALL)
    if match:
        print(f"Found pipeline text at offset {match.start()}: {repr(match.group()[:80])}")
    print("Fix 3 FAILED")

# Fix 4: Installation
old4 = ("bash\\ngit clone https://github.com/your-repo/stock-dashboard.git\\n"
        "cd stock-dashboard\\npip install -r requirements.txt\\n"
        "python src/orchestrator.py    # 首次运行: 采集+筛选\\n"
        "python scripts/run_ai_analysis.py  # AI分析\\n"
        "python src/web/routes.py      # 启动看板\\n")
new4 = ("bash\\ngit clone https://github.com/oldfunk/stock-dashboard.git\\n"
        "cd stock-dashboard\\npython3 -m venv .venv\\n"
        "source .venv/bin/activate  # Windows: .venv\\\\Scripts\\\\activate\\n"
        "pip install akshare fastapi uvicorn jinja2 httpx schedule\\n"
        "python -m src.collector.akshare_fetcher && python -m src.web.routes\\n")

if old4 in content:
    content = content.replace(old4, new4)
    changes += 1
    print("Fix 4: Installation")
else:
    # Find the install section
    idx = content.find('pip install -r')
    if idx > 0:
        print(f"  Found 'pip install -r' at {idx}")
        print(f"  Context: {repr(content[idx-40:idx+80])}")
    print("Fix 4 FAILED")

# Fix 5: No API key note
old5 = "无需 API Key（腾讯行情 + 东财 datacenter 均为免费公开接口）。"
new5 = "无需 API Key（腾讯行情 + 东方财富 + 同花顺均为免费公开接口）。"
if old5 in content:
    content = content.replace(old5, new5)
    changes += 1
    print("Fix 5: API note")
else:
    print("Fix 5 FAILED")

# Fix 6: Tech stack
old6 = "- **数据采集**: httpx + 腾讯行情API + 东方财富datacenter"
new6 = "- **数据采集**: httpx + AKShare（东方财富/同花顺底层）+ 腾讯行情API"
if old6 in content:
    content = content.replace(old6, new6)
    changes += 1
    print("Fix 6: Tech stack")
else:
    print("Fix 6 FAILED")

# Fix 7: AI Berkshire section
old7 = "4. **简化代理指标** — 利息覆盖/稀释率/FCF 使用东财可直接获取的字段"
new7 = "4. **简化代理指标** — 利息覆盖/稀释率/FCF 使用 AKShare 可获取的字段"
if old7 in content:
    content = content.replace(old7, new7)
    changes += 1
    print("Fix 7: AI Berkshire section")
else:
    print("Fix 7 FAILED")

with open('G:/hermes/stock-dashboard/README.md', 'w', encoding='utf-8') as f:
    f.write(content)
print(f"\\nTotal changes: {changes}")
