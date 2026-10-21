# -*- coding: utf-8 -*-
"""
dashboard_generator.py — 数据看板生成器

读取三个数据源，生成：
    1. dashboard.html — ECharts 可视化看板（深色主题）
    2. progress.md    — Markdown 进度报告

设计原则：纯数据驱动，禁止硬编码任何数字。

数据源：
    index.jsonl    — 采集原始数据
    verdicts.jsonl — 判定结果
    applied_jobs.csv — 投递记录
"""
import collections
import csv
import datetime
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ============ 配置 ============
DATA_DIR = os.environ.get('JOB_DATA_DIR', './data')
IDX = os.path.join(DATA_DIR, 'jd_dump', 'index.jsonl')
VER = os.path.join(DATA_DIR, 'jd_dump', 'verdicts.jsonl')
APPLIED = os.path.join(DATA_DIR, 'applied_jobs.csv')
OUT_HTML = os.path.join(DATA_DIR, 'dashboard.html')
OUT_MD = os.path.join(DATA_DIR, 'progress.md')


# ============ 数据加载 ============

def load_index():
    recs = []
    if not os.path.exists(IDX):
        return recs
    with open(IDX, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    return recs


def load_verdicts():
    vs = {}
    if not os.path.exists(VER):
        return vs
    with open(VER, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
                vs[v['key']] = v
            except Exception:
                pass
    return vs


def load_applied():
    rows = []
    if not os.path.exists(APPLIED):
        return rows
    with open(APPLIED, encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        header = None
        for row in reader:
            if header is None:
                header = row
                continue
            if len(row) >= 6:
                rows.append(dict(zip(
                    ['时间', '岗位', '薪资', '地点', '公司', '结果'], row[:6]
                )))
    return rows


# ============ 薪资解析 ============

def salary_floor(s):
    """解析薪资下限，用于分桶统计"""
    if not s:
        return None
    if '面议' in s:
        return None
    m = re.match(r'(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)\s*(千|K|k|万|元)', s.strip())
    if m:
        lo = float(m.group(1))
        unit = m.group(3)
        if unit in ('千', 'K', 'k'):
            lo *= 1000
        elif unit == '万':
            lo *= 10000
        return int(lo)
    return None


# ============ 主流程 ============

def main():
    recs = load_index()
    verdicts = load_verdicts()
    applied = load_applied()

    # ---- 统计 ----
    n_total = len(recs)
    by_batch = collections.Counter(r.get('batch', '?') for r in recs)
    by_kw = collections.Counter(
        (r.get('batch', '?'), r.get('kw', '?')) for r in recs
    )

    judged = 0
    v_counts = collections.Counter()
    pend_keys = []
    for r in recs:
        key = (r.get('title', '') or '').strip() + '|' + (r.get('company', '') or '').strip()
        v = verdicts.get(key)
        if v:
            judged += 1
            v_counts[v['verdict']] += 1
            if v['verdict'] == '待定':
                pend_keys.append((key, r))

    n_applied = len(applied)
    to_apply = max(0, v_counts.get('投', 0) - n_applied)

    # 已投薪资分布
    sal_dist = collections.Counter()
    for a in applied:
        fl = salary_floor(a.get('薪资', ''))
        if fl is None:
            sal_dist['面议/不明'] += 1
        elif fl < 5000:
            sal_dist['4.5-5千'] += 1
        elif fl < 6000:
            sal_dist['5-6千'] += 1
        elif fl < 8000:
            sal_dist['6-8千'] += 1
        elif fl < 10000:
            sal_dist['8-10千'] += 1
        else:
            sal_dist['10千+'] += 1

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

    # ---- progress.md ----
    md = []
    md.append('# 求职自动化进度看板（自动生成）\n')
    md.append(f'> 更新时间：{now}  \n')
    md.append('> 数据源：index.jsonl / verdicts.jsonl / applied_jobs.csv（真实数据驱动）\n')
    md.append('\n## ① 核心指标\n')
    md.append('| 指标 | 数值 |\n|---|---|')
    md.append(f'| 收集岗位总数（含JD全文） | {n_total} |')
    md.append('| 批次分布 | ' + '、'.join(
        f'{k} {v}' for k, v in by_batch.most_common()) + ' |')
    md.append(f'| 已判定 | {judged} |')
    md.append(f'| 判定结果 | 投 {v_counts.get("投", 0)} / '
              f'不投 {v_counts.get("不投", 0)} / '
              f'待定 {v_counts.get("待定", 0)} |')
    md.append(f'| 已投递 | {n_applied} |')
    md.append(f'| 待投 | {to_apply} |')
    md.append(f'| 待人工拍板 | {len(pend_keys)} |')

    md.append('\n## ② 批次×关键词岗位数\n')
    md.append('| 批次 | 关键词 | 岗位数 |\n|---|---|---|')
    for (b, k), c in sorted(by_kw.items()):
        md.append(f'| {b} | {k} | {c} |')

    md.append('\n## ③ 已投递记录\n')
    if applied:
        md.append('| 时间 | 岗位 | 薪资 | 地点 | 公司 | 结果 |')
        md.append('|---|---|---|---|---|---|')
        for a in applied:
            md.append(f"| {a['时间']} | {a['岗位']} | {a['薪资']} | "
                      f"{a['地点']} | {a['公司']} | {a['结果']} |")
    else:
        md.append('（暂无）')

    md.append('\n## ④ 待拍板清单\n')
    if pend_keys:
        md.append('| 岗位 | 公司 | 薪资 | 判定理由 |')
        md.append('|---|---|---|---|')
        for key, r in pend_keys:
            md.append(f"| {r.get('title', '')} | {r.get('company', '')} | "
                      f"{r.get('salary', '')} | {verdicts[key]['reason'][:60]} |")
    else:
        md.append('（无）')

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md) + '\n')

    # ---- dashboard.html ----
    kw_list = sorted(
        [{'name': f'{b}·{k}', 'value': c} for (b, k), c in by_kw.items()],
        key=lambda x: -x['value']
    )[:20]
    kw_top_json = json.dumps(kw_list, ensure_ascii=False)
    sal_labels = json.dumps(list(sal_dist.keys()), ensure_ascii=False)
    sal_vals = json.dumps(list(sal_dist.values()))

    applied_rows = ''.join(
        f"<tr><td>{a['时间']}</td><td>{a['岗位']}</td><td>{a['薪资']}</td>"
        f"<td>{a['地点']}</td><td>{a['公司']}</td><td>{a['结果']}</td></tr>"
        for a in applied
    )

    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>求职自动化看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
* {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px;
}}
h1 {{ font-size: 24px; margin: 0 0 8px; }}
.sub {{ color: #94a3b8; font-size: 13px; margin-bottom: 24px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          gap: 16px; margin-bottom: 32px; }}
.card {{ background: #1e293b; border-radius: 12px; padding: 20px;
         border: 1px solid #334155; }}
.card .v {{ font-size: 32px; font-weight: 700; color: #38bdf8; line-height: 1.2; }}
.card .l {{ font-size: 12px; color: #94a3b8; margin-top: 6px; }}
.charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 32px; }}
.chart {{ background: #1e293b; border-radius: 12px; padding: 20px;
          height: 380px; border: 1px solid #334155; }}
table {{ width: 100%; border-collapse: collapse; background: #1e293b;
         border-radius: 12px; overflow: hidden; border: 1px solid #334155; }}
th, td {{ padding: 10px 14px; text-align: left; font-size: 13px;
          border-bottom: 1px solid #334155; }}
th {{ background: #334155; font-weight: 600; }}
tr:last-child td {{ border-bottom: none; }}
</style></head>
<body>
<h1>求职自动化看板</h1>
<div class="sub">更新时间：{now} ｜ 数据源：index.jsonl / verdicts.jsonl / applied_jobs.csv</div>

<div class="cards">
  <div class="card"><div class="v">{n_total}</div><div class="l">采集岗位</div></div>
  <div class="card"><div class="v">{judged}</div><div class="l">已判定</div></div>
  <div class="card"><div class="v">{v_counts.get("投", 0)}</div><div class="l">判定通过</div></div>
  <div class="card"><div class="v">{n_applied}</div><div class="l">已投递</div></div>
  <div class="card"><div class="v">{v_counts.get("待定", 0)}</div><div class="l">待拍板</div></div>
</div>

<div class="charts">
  <div class="chart" id="chart-kw"></div>
  <div class="chart" id="chart-sal"></div>
</div>

<h2 style="font-size:18px;margin:0 0 12px">已投递记录</h2>
<table>
<thead><tr><th>时间</th><th>岗位</th><th>薪资</th><th>地点</th><th>公司</th><th>结果</th></tr></thead>
<tbody>{applied_rows}</tbody>
</table>

<script>
// 关键词 TOP20
const kwChart = echarts.init(document.getElementById('chart-kw'), 'dark');
kwChart.setOption({{
  backgroundColor: 'transparent',
  title: {{ text: '关键词 TOP20', textStyle: {{ fontSize: 14, color: '#e2e8f0' }} }},
  tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }} }},
  grid: {{ left: 130, right: 30, top: 50, bottom: 20 }},
  xAxis: {{ type: 'value', axisLabel: {{ color: '#94a3b8' }} }},
  yAxis: {{
    type: 'category',
    data: {kw_top_json}.map(x => x.name).reverse(),
    axisLabel: {{ fontSize: 10, color: '#94a3b8' }}
  }},
  series: [{{
    type: 'bar',
    data: {kw_top_json}.map(x => x.value).reverse(),
    itemStyle: {{ color: '#38bdf8', borderRadius: [0, 4, 4, 0] }}
  }}]
}});

// 薪资分布饼图
const salChart = echarts.init(document.getElementById('chart-sal'), 'dark');
salChart.setOption({{
  backgroundColor: 'transparent',
  title: {{ text: '已投递薪资分布', textStyle: {{ fontSize: 14, color: '#e2e8f0' }} }},
  tooltip: {{ trigger: 'item', formatter: '{{b}}: {{c}} ({{d}}%)' }},
  series: [{{
    type: 'pie',
    radius: ['40%', '70%'],
    data: {sal_labels}.map((k, i) => ({{ name: k, value: {sal_vals}[i] }})),
    label: {{ fontSize: 11, color: '#94a3b8' }},
    itemStyle: {{ borderColor: '#1e293b', borderWidth: 2 }}
  }}]
}});

window.addEventListener('resize', () => {{
  kwChart.resize();
  salChart.resize();
}});
</script>
</body></html>'''

    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

    print(json.dumps({
        'total': n_total, 'judged': judged,
        '投': v_counts.get('投', 0), '不投': v_counts.get('不投', 0),
        '待定': v_counts.get('待定', 0), 'applied': n_applied,
        'to_apply': to_apply
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
