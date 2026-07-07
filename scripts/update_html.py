#!/usr/bin/env python3
"""Update HTML: add indices, remove emoji, wireframe style"""
import re

with open('G:/hermes/stock-dashboard/src/web/templates/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

print(f"File size: {len(html)} chars")

# 1. Remove emojis
emojis_removed = 0
for em in ['⛔', '📈', '📖', '🧾', '🔍', '🟢', '🔴', '🟡', '🟣', '💡']:
    cnt = html.count(em)
    if cnt > 0:
        html = html.replace(em, '')
        emojis_removed += cnt
        print(f"  Removed '{em}': {cnt}x")
print(f"Total emojis removed: {emojis_removed}")

# 2. Fix the mirror counts display
old_mirror = '&#128161; 镜子测试：{{ stock.mirror_counts }} 句含转折词\n                        {% if stock.mirror_counts > 2 %}<span style="color:var(--red);font-weight:500;"> ⚠️ 超2句，违反纪律</span>{% endif %}'
new_mirror = '镜子测试: {{ stock.mirror_counts }} 句含转折词\n                        {% if stock.mirror_counts > 2 %}<span style="color:var(--red);font-weight:500;"> 超2句, 违反纪律</span>{% endif %}'
if old_mirror in html:
    html = html.replace(old_mirror, new_mirror)
    print("  Mirror counts display cleaned")
else:
    # Try a simpler approach for the mirror section
    print("  Mirror pattern not found - checking alternative...")
    if '镜子测试' in html:
        print("  (镜子测试 found but different pattern)")

# 3. Update hist-status texts
html = html.replace('&#9679; 有分析', '有分析')
html = html.replace('&#9675; 已尝试', '已尝试')
html = html.replace('&#9679;', '')
html = html.replace('&#9675;', '')
print("  Hist status cleaned")

# 4. Update market row CSS for more indices
# Find the .market-row section
old_market_css = '''        .market-row {
            display: flex;
            gap: 32px;
            padding: 16px 0;
            margin-bottom: 24px;
            border-bottom: 1px solid var(--divider-thin);
        }

        .market-item {
            flex: 1;
        }'''

new_market_css = '''        .market-row {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 16px;
            padding: 16px 0;
            margin-bottom: 24px;
            border-bottom: 1px solid var(--divider-thin);
        }

        .market-item {
            padding: 10px 0;
        }'''

if old_market_css in html:
    html = html.replace(old_market_css, new_market_css)
    print("  Market row: flex -> 5-col grid")
else:
    print("  Market CSS not found - checking actual...")
    idx = html.find('market-row')
    if idx > 0:
        print(f"  Found 'market-row' at {idx}")
        print(f"  Context: {html[idx:idx+200][:200]}...")

# 5. Update responsive for the grid market
old_resp = '''        @media (max-width: 900px) {
            .app { padding: 16px; }
            .market-row { gap: 16px; flex-wrap: wrap; }
            .market-item { flex: 1 1 40%; }'''

new_resp = '''        @media (max-width: 1100px) {
            .market-row { grid-template-columns: repeat(4, 1fr); }
        }

        @media (max-width: 900px) {
            .app { padding: 16px; }
            .market-row { grid-template-columns: repeat(3, 1fr); gap: 12px; }'''

if old_resp in html:
    html = html.replace(old_resp, new_resp)
    print("  Responsive breakpoints updated")
else:
    print("  Responsive CSS not found")
    # Find it
    idx = html.find('max-width: 900px')
    if idx > 0:
        print(f"  Found at {idx}: {html[idx:idx+250]}")

# 6. Update market value font size (smaller for more items)
old_font = '.market-value {\n            font-size: 26px;'
new_font = '.market-value {\n            font-size: 21px;'
if old_font in html:
    html = html.replace(old_font, new_font)
    print("  Market value font: 26px -> 21px")
else:
    print("  Market font not found")

# 7. Update market change font
html = html.replace('font-size: 13px;', 'font-size: 12px;')
print("  Market change font adjusted")

# 8. Fix the font on market-item for compactness
if 'margin-bottom: 4px' in html:
    html = html.replace('margin-bottom: 4px;', 'margin-bottom: 2px;')
    print("  Market name margin tightened")

with open('G:/hermes/stock-dashboard/src/web/templates/index.html', 'w', encoding='utf-8') as f:
    f.write(html)
print("\nFile written")
