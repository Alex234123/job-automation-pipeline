# -*- coding: utf-8 -*-
"""
judge_engine.py — 岗位判定规则引擎

把 JD 全文按硬性规则机械判定为「投 / 不投 / 待定」。
完全确定性，无 LLM 调用，可复现、可审计。

核心设计：
    1. JD 拆分为「标签区 / 正文区」
       - 标签区：公司行业标签（如「化工/制药」），不代表岗位职责
       - 正文区：「岗位职责/任职要求」之后的实际要求
       - 只有正文区命中否决词才否决，避免行业标签误杀
    2. 规则按优先级串行执行，命中即返回
    3. 每条判定都带证据（引用 JD 原文片段）

用法：
    python judge_engine.py            # 增量判定（跳过已判）
    python judge_engine.py --reset    # 重置后全量判定
"""
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

# 薪资下限（元）
SALARY_FLOOR = int(os.environ.get('JOB_SALARY_FLOOR', '4500'))
# 经验上限（年）
EXP_MAX = int(os.environ.get('JOB_EXP_MAX', '3'))

# ============ 规则词表（按需自定义）============

# 强专业设备（只查正文）
STRONG_SPECIALTY = [
    '电站', '变电站', '变电', '输电', '配电', '风电', '光伏', '储能', '充电桩', '继电保护',
    '化工工艺', 'DCS', 'PLC', '自控仪表', '仪表维修', '制冷机组', '暖通', '锅炉',
    '电梯维保', '电梯维修', '起重机械', '叉车', '焊工', '特种作业', '数控', '机床', '模具',
    '汽修', '汽车维修', '半导体', '晶圆', '厂务', '污水处理', '水务', '燃气', '医疗设备',
]

# 编程开发类（标题）
CODING_TITLE = [
    '开发工程师', '开发', '程序员', 'Java', '前端', '后端',
    '算法工程师', '测试开发', '大数据', '软件工程师',
]

# 编程开发类（正文明确职责）
CODING_JD = [
    '编写代码', '代码开发', '系统开发', '程序开发',
    '负责编码', '编码实现', '参与开发',
]

# 高危技术栈（正文命中即否决）
LINUX_DB_SEC = [
    'OSPF', 'VXLAN', 'K8s', 'Kubernetes', '渗透测试', '漏洞扫描', '入侵检测',
    'Shell脚本', '数据库管理', '数据库运维', 'Linux运维', '运维开发',
    '云计算', 'WAF', '攻防',
]
# 软性命中（可降级为待定）
SOFT_TECH = ['Docker', '虚拟化', '服务器运维', 'Kubernetes', 'K8s']

# 销售性质
SALES_TITLE = ['销售', '电销', '客户经理', '渠道经理', '商务专员', '电话销售']
SALES_JD = ['业绩指标', '销售任务', '带动销售', '完成销售目标',
            '销售业绩', '承担销售', '营销任务', '拉新', '地推']

# 工地/工程资料
SITE_CLERK = ['工程资料', '项目资料员', '施工资料', '监理', '工地',
              '驻场资料', '土建', '竣工资料', '施工员']

# 实习/兼职
INTERN = ['实习', '在校生', '应届在读', '寒暑假', '兼职']

# 值班/倒班
DUTY_BAD = ['值班员', '值班长', '监控值班', '7*24', '7×24', '24小时值班', '倒班']

# 职业方向不符（标题命中即拒，除非含正向词）
DIRECTION_BAD = ['客服', '售后', '前台', '服务员', '保安', '骑手',
                 '送餐', '主播', '营业员', '店员', '售票']

# 硬性排除（无白名单豁免）
HARD_DIRECTION_BAD = ['客服', '售后']

# 方向正向词（用于白名单豁免）
DIRECTION_OK = re.compile(
    r'(运维|桌面|IT|网络|系统|行政|文员|内勤|资料|档案|助理|综合管理|专员)'
)

# 证书
CERT_HARD = re.compile(
    r'(须|必须|需|要求).{0,8}(持有|具备|取得).{0,12}证书'
    r'|(持有|具备).{0,12}证书.{0,4}(上岗|方可)'
)
CERT_SOFT = re.compile(r'证书.{0,4}(优先|加分)|(优先|加分).{0,8}证书')

# 免责上下文（命中这些词时高危词不算硬要求）
BENIGN_CTX = re.compile(r'(优先|加分|了解|熟悉即可|不要求|可选)')

# 正文起始标记
BODY_MARKERS = [
    '岗位职责', '工作职责', '职位描述', '任职要求', '岗位要求',
    '工作内容', '职责描述', '【岗位职责】', '职位信息',
    '工作职能', '【工作职责】', '【任职要求】',
]


# ============ 工具函数 ============

def parse_salary_floor(s):
    """解析薪资下限（元），失败返回 None

    支持格式：5-8千 / 6-9K / 1-1.2万 / 8千以上 / 面议
    """
    if not s:
        return None
    s = s.strip()
    if '面议' in s:
        return None

    # 区间格式：5-8千 / 6-9K / 1-1.2万
    m = re.match(r'(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)\s*(千|K|k|万|元)', s)
    if m:
        lo = float(m.group(1))
        unit = m.group(3)
        if unit in ('千', 'K', 'k'):
            lo *= 1000
        elif unit == '万':
            lo *= 10000
        return int(lo)

    # 单值格式：8千 / 1.2万以上 / 8000元以下
    m = re.match(r'(\d+(?:\.\d+)?)\s*(千|K|k|万|元)(以下|以上)?', s)
    if m:
        v = float(m.group(1))
        unit = m.group(2)
        suffix = m.group(3) or ''
        if unit in ('千', 'K', 'k'):
            v *= 1000
        elif unit == '万':
            v *= 10000
        if suffix == '以下':
            return 0
        return int(v)

    # 兜底：抓第一个 4+ 位数字
    m = re.search(r'(\d{4,})', s)
    return int(m.group(1)) if m else None


def split_jd(jd):
    """JD 拆为 (标签区, 正文区)

    正文 = 第一个职责标记之后的内容
    无标记时取后 60%（前 40% 视为公司介绍/标签）
    """
    jd_c = re.sub(r'\s+', ' ', jd or '')
    best = None
    for mk in BODY_MARKERS:
        p = jd_c.find(mk)
        if 0 <= p and (best is None or p < best):
            best = p
    if best is None:
        best = int(len(jd_c) * 0.4)
    return jd_c[:best], jd_c[best:]


def find_evidence(text, kw, span=25):
    """提取关键词上下文作为证据"""
    idx = text.find(kw)
    if idx < 0:
        return None
    start = max(0, idx - span)
    end = min(len(text), idx + len(kw) + span)
    return text[start:end].strip()


def hit_list(text, kws):
    """返回命中的关键词列表"""
    return [k for k in kws if k in text]


# ============ 主判定函数 ============

def judge(rec):
    """判定单条岗位

    Args:
        rec: dict，含 title/jd/company/salary/education/experience

    Returns:
        (key, verdict, reason)
        verdict ∈ {'投', '不投', '待定'}
    """
    title = (rec.get('title', '') or '').strip()
    jd = rec.get('jd', '') or ''
    company = (rec.get('company', '') or '').strip()
    salary = rec.get('salary', '') or ''
    edu = (rec.get('education', '') or '').strip()
    exp = (rec.get('experience', '') or '').strip()

    tags, body = split_jd(jd)
    key = f"{title}|{company}"

    # ---- 规则 0：实习/在校/兼职 ----
    hits = hit_list(title, INTERN) or hit_list(body[:200], INTERN)
    if hits:
        return key, '不投', f'含实习/兼职类词「{hits[0]}」'

    # ---- 规则 0.5：值班/倒班 ----
    hits = hit_list(title, DUTY_BAD) or hit_list(body, DUTY_BAD)
    if hits:
        ev = find_evidence(body, hits[0]) or hits[0]
        return key, '不投', f'值班/倒班类岗位「{hits[0]}」: …{ev}…'

    # ---- 规则 0.6a：硬性方向排除（无白名单豁免）----
    for bad in HARD_DIRECTION_BAD:
        if bad in title:
            return key, '不投', f'标题属「{bad}」类（硬性排除）'

    # ---- 规则 0.6b：方向不符（有白名单豁免）----
    for bad in DIRECTION_BAD:
        if bad in title and not DIRECTION_OK.search(title):
            return key, '不投', f'标题属「{bad}」类职业方向不符'

    # ---- 规则 1：强专业设备（只查正文）----
    hits = hit_list(body, STRONG_SPECIALTY)
    if hits:
        ev = find_evidence(body, hits[0])
        return key, '不投', f'正文含强专业词「{hits[0]}」: …{ev}…'

    # ---- 规则 2：编程开发 ----
    hits = hit_list(title, CODING_TITLE)
    if hits:
        return key, '不投', f'标题属编程开发类「{hits[0]}」'
    hits = hit_list(body, CODING_JD)
    if hits:
        ev = find_evidence(body, hits[0])
        return key, '不投', f'正文核心职责为编码「{hits[0]}」: …{ev}…'

    # ---- 规则 3：高危技术栈 ----
    hits = hit_list(body, LINUX_DB_SEC)
    hard_hits = [k for k in hits if k not in SOFT_TECH]
    if hard_hits:
        ev = find_evidence(body, hard_hits[0])
        idx = body.find(hard_hits[0])
        ctx = BENIGN_CTX.search(body[max(0, idx - 30): idx + len(hard_hits[0]) + 15])
        if not ctx:
            return key, '不投', f'正文要求高危技能「{hard_hits[0]}」: …{ev}…'

    # ---- 规则 4：销售性质 ----
    hits = hit_list(title, SALES_TITLE)
    if hits:
        return key, '不投', f'标题属销售类「{hits[0]}」'
    hits = hit_list(body, SALES_JD)
    if hits:
        ev = find_evidence(body, hits[0])
        return key, '不投', f'正文含销售指标「{hits[0]}」: …{ev}…'

    # ---- 规则 5：工地/工程资料员 ----
    hits = hit_list(title, SITE_CLERK) or hit_list(body, SITE_CLERK)
    if hits:
        ev = find_evidence(body, hits[0]) or hits[0]
        return key, '不投', f'工地/工程资料类「{hits[0]}」: …{ev}…'

    # ---- 规则 6：证书硬要求 ----
    m = CERT_HARD.search(body)
    if m and not CERT_SOFT.search(body):
        ctx = body[max(0, m.start() - 15): m.end() + 15]
        return key, '不投', f'正文硬性要求证书: …{ctx}…'

    # ---- 规则 7：学历 ----
    if edu in ('本科', '硕士', '博士'):
        if not re.search(r'大专(也可|及以上|优先|学历)', body):
            return key, '不投', f'详情页学历要求{edu}且正文无大专豁免'
    if re.search(r'本科及以上', body) and not re.search(r'大专(也可|及以上|优先|学历)', body):
        return key, '不投', '正文写明本科及以上且无大专豁免'

    # ---- 规则 8：经验 ----
    m = re.match(r'(\d+)(?:-(\d+))?\s*年', exp)
    if m:
        lo = int(m.group(1))
        hi = int(m.group(2) or m.group(1))
        if lo > EXP_MAX or hi > EXP_MAX:
            return key, '不投', f'经验要求{exp}，超过{EXP_MAX}年'
    elif exp in ('3-5年', '5-10年', '10年以上'):
        return key, '不投', f'经验要求{exp}，超过{EXP_MAX}年'
    m2 = re.search(r'(\d+)年以上.{0,6}(工作经验|经验)', body)
    if m2 and int(m2.group(1)) > EXP_MAX:
        return key, '不投', f'正文要求{m2.group(1)}年以上经验'

    # ---- 规则 9：薪资 ----
    floor = parse_salary_floor(salary)

    # 9a. 底薪+绩效结构，底薪低于下限
    m_base = re.search(r'底薪[^0-9]{0,6}([0-9]{3,6})', jd) or \
             re.search(r'([0-9]{3,6})底薪', title)
    if m_base:
        n = int(m_base.group(1))
        if 100 <= n < 450:  # 百元单位
            n *= 10
        if n < SALARY_FLOOR:
            return key, '不投', f'底薪+绩效结构，底薪{n}元 < {SALARY_FLOOR}（{salary}）'

    if floor is None:
        return key, '待定', f'薪资「{salary or "空"}」无法判定下限（面议/未填）'
    if floor < SALARY_FLOOR:
        return key, '不投', f'薪资下限{floor}元 < {SALARY_FLOOR}（{salary}）'

    # ---- 规则 10：通过 ----
    return key, '投', f'通过全部硬性规则（薪资{salary}，学历{edu or "未标"}，经验{exp or "未标"}）'


def main():
    reset = '--reset' in sys.argv

    done = set()
    if os.path.exists(VER) and not reset:
        with open(VER, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    done.add(json.loads(line)['key'])
                except Exception:
                    pass
    elif reset and os.path.exists(VER):
        import shutil
        shutil.copy(VER, VER + '.bak')
        open(VER, 'w', encoding='utf-8').close()

    stats = {'投': 0, '不投': 0, '待定': 0, 'skipped': 0}
    out_lines = []

    if not os.path.exists(IDX):
        print(json.dumps({'error': f'index not found: {IDX}'}, ensure_ascii=False))
        return

    with open(IDX, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not (rec.get('jd') or '').strip():
                continue  # 无 JD 不判
            key, verdict, reason = judge(rec)
            if key in done:
                stats['skipped'] += 1
                continue
            done.add(key)
            stats[verdict] += 1
            out_lines.append(json.dumps(
                {'key': key, 'verdict': verdict, 'reason': reason},
                ensure_ascii=False
            ))

    if out_lines:
        os.makedirs(os.path.dirname(VER), exist_ok=True)
        with open(VER, 'a', encoding='utf-8') as f:
            f.write('\n'.join(out_lines) + '\n')

    print(json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
