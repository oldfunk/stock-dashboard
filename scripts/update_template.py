#!/usr/bin/env python3
"""Update the index.html template with new features"""
import re

with open('G:/hermes/stock-dashboard/src/web/templates/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

changes = 0

# 1. Add 净利率 cell after 毛利率, before OCF/股
old = '>毛利率</div>\n                        <div class="value' + " {{ 'good' if stock.gross_margin is not none"
new = '>毛利率</div>\n                        <div class="value' + " {{ 'good' if stock.gross_margin is not none"
# Use a more unique anchor
old_block = '                        <div class="label">毛利率</div>\n                        <div class="value {{ \'good\' if stock.gross_margin is not none and stock.gross_margin >= 30 else (\'warn\' if stock.gross_margin is not none and stock.gross_margin >= 15 else \'bad\') }}\">\n                            {{ "%.1f"|format(stock.gross_margin) if stock.gross_margin is not none else \'--\' }}%\n                        </div>\n                    </div>\n                    <div class="metric-cell">\n                        <div class="label">OCF/股</div>'

new_block = '                        <div class="label">毛利率</div>\n                        <div class="value {{ \'good\' if stock.gross_margin is not none and stock.gross_margin >= 30 else (\'warn\' if stock.gross_margin is not none and stock.gross_margin >= 15 else \'bad\') }}\">\n                            {{ "%.1f"|format(stock.gross_margin) if stock.gross_margin is not none else \'--\' }}%\n                        </div>\n                    </div>\n                    <div class="metric-cell">\n                        <div class="label">净利率</div>\n                        <div class="value {{ \'good\' if stock.net_margin is not none and stock.net_margin >= 15 else (\'warn\' if stock.net_margin is not none and stock.net_margin >= 5 else \'bad\') }}\">\n                            {{ "%.1f"|format(stock.net_margin) if stock.net_margin is not none else \'--\' }}%\n                        </div>\n                    </div>\n                    <div class="metric-cell">\n                        <div class="label">OCF/股</div>'

if old_block in content:
    content = content.replace(old_block, new_block, 1)
    changes += 1
    print(f"Change 1: 毛利率→净利率 cell added")
else:
    print("⚠️ Change 1 failed: old_block not found")

# 2. Update 5-year summary line
old_summary = 'ROE: {{ "%.1f"|format(stock._summary.roe_5y_avg) if stock._summary.roe_5y_avg else \'--\' }}%\n                            {% if stock._summary.net_margin_5y_avg %} | 净利: {{ "%.1f"|format(stock._summary.net_margin_5y_avg) }}%{% endif %}\n                            {% if stock._summary.ocf_latest %} | OCF: {{ "%.2f"|format(stock._summary.ocf_latest) }}{% endif %}'

new_summary = 'ROE: {{ "%.1f"|format(stock._summary.roe_5y_avg) if stock._summary.roe_5y_avg else \'--\' }}%\n                            {% if stock._summary.intcov_5y_avg %} | 利息覆盖: {{ "%.1f"|format(stock._summary.intcov_5y_avg) }}x{% endif %}\n                            {% if stock._summary.fcf_5y_sum %} | 5年FCF: {{ "%.1f"|format(stock._summary.fcf_5y_sum / 1e8) }}亿{% endif %}\n                            {% if stock._summary.share_dilution_5y %} | 稀释: {{ "%.1f"|format(stock._summary.share_dilution_5y) }}%{% endif %}'

if old_summary in content:
    content = content.replace(old_summary, new_summary, 1)
    changes += 1
    print(f"Change 2: 5-year summary updated")
else:
    print("⚠️ Change 2 failed: old_summary not found")

# 3. Add mirror_counts display to risks tab
old_risks_close = '无逆向分析数据\' }}</div>\n                </div>'
new_risks_close = """无逆向分析数据' }}</div>
                    {% if stock.mirror_counts %}
                    <div style="margin-top:8px;padding:8px;background:var(--gold-bg);border-radius:var(--radius-sm);font-size:11px;">
                        &#128161; 镜子测试：{{ stock.mirror_counts }} 句含转折词
                        {% if stock.mirror_counts > 2 %}<span style="color:var(--red);font-weight:500;"> ⚠️ 超2句，违反纪律</span>{% endif %}
                    </div>
                    {% endif %}
                </div>"""

if old_risks_close in content:
    content = content.replace(old_risks_close, new_risks_close, 1)
    changes += 1
    print(f"Change 3: mirror_counts added")
else:
    print("⚠️ Change 3 failed: old_risks_close not found")

# 4. Update knowledge panel - add new sections before 综合评分体系
old_kp_end = '''        <div class="kp-section">
            <div class="kp-title" onclick="toggleKP(this)\"><span class="arrow">&#9654;</span> 综合评分体系</div>
            <div class="kp-body">'''
new_kp_sections = '''        <div class="kp-section">
            <div class="kp-title" onclick="toggleKP(this)\"><span class="arrow">&#9654;</span> 净利率 <span style="font-weight:400;color:var(--accent);font-size:11px;">NEW</span></div>
            <div class="kp-body">
                <b>含义</b>：净利润 - 营业收入。反映定价能力和成本控制。<br>
                <b>价值区间</b>：<span class="bad">&lt; 5%</span> &middot; <span class="warn">5~15%</span> &middot; <span class="good">&gt; 15% 优秀</span><br>
                <ul>
                    <li>高净利率 + 高OCF = 高利润质量</li>
                    <li>筛选门槛：5年均净利率 &lt; 5% 排除</li>
                </ul>
            </div>
        </div>

        <div class="kp-section">
            <div class="kp-title" onclick="toggleKP(this)\"><span class="arrow">&#9654;</span> 利息覆盖 <span style="font-weight:400;color:var(--accent);font-size:11px;">NEW</span></div>
            <div class="kp-body">
                <b>含义</b>：营业利润 &divide; 利息费用。衡量付息能力。<br>
                <b>安全区间</b>：<span class="bad">&lt; 2x 危险</span> &middot; <span class="warn">2~5x</span> &middot; <span class="good">&gt; 5x 安全</span><br>
                <ul>
                    <li>&lt; 1x = 利润不够付利息</li>
                    <li>筛选门槛：5年均利息覆盖 &lt; 2x 排除</li>
                </ul>
            </div>
        </div>

        <div class="kp-section">
            <div class="kp-title" onclick="toggleKP(this)\"><span class="arrow">&#9654;</span> FCF 自由现金流 <span style="font-weight:400;color:var(--accent);font-size:11px;">NEW</span></div>
            <div class="kp-body">
                <b>含义</b>：经营现金流 + 投资现金流。实际可支配现金。<br>
                <b>判断</b>：<span class="good">正数</span> 造血 &middot; <span class="bad">负数</span> 烧钱<br>
                <ul>
                    <li>持续正FCF是巴菲特最看重的指标</li>
                    <li>筛选门槛：5年FCF累计 &le; 0 且 &gt; 1亿负值 排除</li>
                </ul>
            </div>
        </div>

        <div class="kp-section">
            <div class="kp-title" onclick="toggleKP(this)\"><span class="arrow">&#9654;</span> ROIC <span style="font-weight:400;color:var(--accent);font-size:11px;">NEW</span></div>
            <div class="kp-body">
                <b>含义</b>：税后营业利润 &divide; (总资产 - 现金 - 无息负债)。比ROE更纯粹。<br>
                <b>价值区间</b>：<span class="bad">&lt; 5%</span> &middot; <span class="warn">5~10%</span> &middot; <span class="good">&gt; 10%</span><br>
                <ul>
                    <li>ROIC &gt; WACC才能创造价值</li>
                    <li>数据从AKShare利润表采集</li>
                </ul>
            </div>
        </div>

        <div class="kp-section">
            <div class="kp-title" onclick="toggleKP(this)\"><span class="arrow">&#9654;</span> 综合评分体系</div>
            <div class="kp-body">'''

if old_kp_end in content:
    content = content.replace(old_kp_end, new_kp_sections, 1)
    changes += 1
    print(f"Change 4: knowledge panel updated")
else:
    print("⚠️ Change 4 failed: old_kp_end not found")

# 5. Update footer
old_footer = 'Stock Dashboard v0.1 &middot; Data via AKShare &middot; Not investment advice'
new_footer = 'Stock Dashboard v2.0 &middot; \u6570\u636e\u6e90: AKShare + \u4e1c\u65b9\u8d22\u5bcc + \u817e\u8baf\u884c\u60c5 &middot; \u975e\u6295\u8d44\u5efa\u8bae'
if old_footer in content:
    content = content.replace(old_footer, new_footer, 1)
    changes += 1
    print(f"Change 5: footer updated")
else:
    print("⚠️ Change 5 failed: old_footer not found")

if changes > 0:
    with open('G:/hermes/stock-dashboard/src/web/templates/index.html', 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"\n✅ {changes} changes applied")
else:
    print("\n❌ No changes applied!")
    # Debug: show context around search targets
    for target in ['毛利率', 'OCF/股', 'net_margin_5y_avg', '无逆向分析', '综合评分体系', 'Not investment advice']:
        idx = content.find(target)
        if idx >= 0:
            print(f"  Found '{target}' at offset {idx}")
        else:
            print(f"  MISSING '{target}'")
