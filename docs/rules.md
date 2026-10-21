# 判定规则详解

规则引擎的核心思想：**把「这个岗位适不适合我」拆解成一系列可机械判断的硬性条件**。

所有规则按优先级串行执行，命中即返回，不再往下判断。

## 规则设计原则

1. **硬性优先**：先判断绝对不可接受的条件（值班、销售、学历不符）
2. **正文优先**：强专业/技术栈类规则只查 JD 正文区，不查标签区
3. **证据留存**：每条判定都附带 JD 原文片段，便于事后复核
4. **可调参数**：薪资下限、经验上限等通过环境变量配置

## 规则清单

### 规则 0：实习/在校/兼职

```python
INTERN = ['实习', '在校生', '应届在读', '寒暑假', '兼职']

hits = hit_list(title, INTERN) or hit_list(body[:200], INTERN)
if hits:
    return key, '不投', f'含实习/兼职类词「{hits[0]}」'
```

**检查范围**：标题 + 正文前 200 字（实习信息通常在开头）

**为什么**：求职者需要正式全职岗位，实习/兼职无法满足。

### 规则 0.5：值班/倒班

```python
DUTY_BAD = ['值班员', '值班长', '监控值班', '7*24', '7×24', '24小时值班', '倒班']

hits = hit_list(title, DUTY_BAD) or hit_list(body, DUTY_BAD)
```

**检查范围**：标题 + 全文

**为什么**：值班/倒班严重影响生活作息，且通常是低技术含量的监控岗。

**注意**：`7*24` 和 `7×24` 都要覆盖（不同输入法）。

### 规则 0.6a：硬性方向排除

```python
HARD_DIRECTION_BAD = ['客服', '售后']

for bad in HARD_DIRECTION_BAD:
    if bad in title:
        return key, '不投', f'标题属「{bad}」类（硬性排除）'
```

**检查范围**：仅标题

**为什么**：客服/售后与目标职业方向完全不符，且无白名单豁免（即使标题同时含「运维」也不投）。

### 规则 0.6b：方向不符（有白名单）

```python
DIRECTION_BAD = ['客服', '售后', '前台', '服务员', '保安', '骑手',
                 '送餐', '主播', '营业员', '店员', '售票']

DIRECTION_OK = re.compile(
    r'(运维|桌面|IT|网络|系统|行政|文员|内勤|资料|档案|助理|综合管理|专员)'
)

for bad in DIRECTION_BAD:
    if bad in title and not DIRECTION_OK.search(title):
        return key, '不投', f'标题属「{bad}」类职业方向不符'
```

**白名单逻辑**：标题含「客服」但也含「运维」时放行（如「客服系统运维」）。

**为什么需要白名单**：避免「技术支持（含客服职责）」这类混合岗位被误杀。

### 规则 1：强专业设备

```python
STRONG_SPECIALTY = [
    '电站', '变电站', '变电', '输电', '配电', '风电', '光伏', '储能',
    '充电桩', '继电保护', '化工工艺', 'DCS', 'PLC', '自控仪表',
    '仪表维修', '制冷机组', '暖通', '锅炉', '电梯维保', '电梯维修',
    '起重机械', '叉车', '焊工', '特种作业', '数控', '机床', '模具',
    '汽修', '汽车维修', '半导体', '晶圆', '厂务', '污水处理',
    '水务', '燃气', '医疗设备',
]

hits = hit_list(body, STRONG_SPECIALTY)
```

**检查范围**：**仅正文区**（关键！）

**为什么只查正文**：JD 开头的公司行业标签常写「本公司属化工行业」，但不代表岗位需要化工技能。只在正文区（岗位职责+任职要求）命中才否决。

**效果**：某次全量判定中，这个改动修复了 38 条误杀。

### 规则 2：编程开发

```python
CODING_TITLE = ['开发工程师', '开发', '程序员', 'Java', '前端',
                '后端', '算法工程师', '测试开发', '大数据', '软件工程师']

CODING_JD = ['编写代码', '代码开发', '系统开发', '程序开发',
             '负责编码', '编码实现', '参与开发']
```

**检查范围**：标题（CODING_TITLE）+ 正文（CODING_JD）

**为什么分开**：标题含「开发」直接拒；正文只在明确要求写代码时拒。

### 规则 3：高危技术栈

```python
LINUX_DB_SEC = [
    'OSPF', 'VXLAN', 'K8s', 'Kubernetes', '渗透测试', '漏洞扫描',
    '入侵检测', 'Shell脚本', '数据库管理', '数据库运维', 'Linux运维',
    '运维开发', '云计算', 'WAF', '攻防',
]

SOFT_TECH = ['Docker', '虚拟化', '服务器运维', 'Kubernetes', 'K8s']
```

**分级处理**：
- 硬性命中（不在 SOFT_TECH 里）→ 直接不投
- 软性命中（Docker/虚拟化等）→ 如果上下文有「优先/了解/可选」则放行

```python
hard_hits = [k for k in hits if k not in SOFT_TECH]
if hard_hits:
    idx = body.find(hard_hits[0])
    ctx = BENIGN_CTX.search(body[max(0, idx-30): idx+len(hard_hits[0])+15])
    if not ctx:
        return key, '不投', f'正文要求高危技能「{hard_hits[0]}」'
```

**BENIGN_CTX** = `(优先|加分|了解|熟悉即可|不要求|可选)`

**为什么分级**：Docker/虚拟化是加分项而非硬门槛，而 K8s 运维/渗透测试是专业技能。

### 规则 4：销售性质

```python
SALES_TITLE = ['销售', '电销', '客户经理', '渠道经理', '商务专员', '电话销售']

SALES_JD = ['业绩指标', '销售任务', '带动销售', '完成销售目标',
            '销售业绩', '承担销售', '营销任务', '拉新', '地推']
```

**检查范围**：标题 + 正文

**为什么**：销售岗压力大、收入不稳定，且与目标职业方向不符。

### 规则 5：工地/工程资料员

```python
SITE_CLERK = ['工程资料', '项目资料员', '施工资料', '监理', '工地',
              '驻场资料', '土建', '竣工资料', '施工员']
```

**为什么**：工地资料员需长期驻场，工作环境差。

### 规则 6：证书硬要求

```python
CERT_HARD = re.compile(
    r'(须|必须|需|要求).{0,8}(持有|具备|取得).{0,12}证书'
    r'|(持有|具备).{0,12}证书.{0,4}(上岗|方可)'
)

CERT_SOFT = re.compile(r'证书.{0,4}(优先|加分)|(优先|加分).{0,8}证书')

m = CERT_HARD.search(body)
if m and not CERT_SOFT.search(body):
    return key, '不投', f'正文硬性要求证书'
```

**逻辑**：
- 命中 CERT_HARD（「必须持有 XX 证书」）且未命中 CERT_SOFT（「证书优先」）→ 不投
- 只有「证书优先」→ 放行

**为什么**：无证者无法满足硬性要求，但「优先」不影响投递。

### 规则 7：学历

```python
if edu in ('本科', '硕士', '博士'):
    if not re.search(r'大专(也可|及以上|优先|学历)', body):
        return key, '不投', f'详情页学历要求{edu}且正文无大专豁免'

if re.search(r'本科及以上', body) and not re.search(r'大专(也可|及以上|优先|学历)', body):
    return key, '不投', '正文写明本科及以上且无大专豁免'
```

**豁免检测**：正文出现「大专也可/大专及以上/大专优先」则放行。

**为什么**：有些岗位详情页标「本科」，但正文写「大专优秀者亦可」。

### 规则 8：经验

```python
m = re.match(r'(\d+)(?:-(\d+))?\s*年', exp)
if m:
    lo = int(m.group(1))
    hi = int(m.group(2) or m.group(1))
    if lo > EXP_MAX or hi > EXP_MAX:
        return key, '不投', f'经验要求{exp}，超过{EXP_MAX}年'
```

**EXP_MAX** 默认 3（可通过环境变量调整）。

**双检查**：详情页字段 + 正文「X 年以上经验」。

### 规则 9：薪资

```python
floor = parse_salary_floor(salary)

# 9a. 底薪+绩效结构
m_base = re.search(r'底薪[^0-9]{0,6}([0-9]{3,6})', jd) or \
         re.search(r'([0-9]{3,6})底薪', title)
if m_base:
    n = int(m_base.group(1))
    if 100 <= n < 450:
        n *= 10    # 百元单位
    if n < SALARY_FLOOR:
        return key, '不投', f'底薪+绩效结构，底薪{n}元 < {SALARY_FLOOR}'

if floor is None:
    return key, '待定', f'薪资「{salary}」无法判定'
if floor < SALARY_FLOOR:
    return key, '不投', f'薪资下限{floor}元 < {SALARY_FLOOR}'
```

**为什么有规则 9a**：招聘网站卡片常写「5-10K」，但 JD 正文写「底薪 3000 + 提成」。只看卡片会误判。

**扫描位置**：标题 + 原始 JD（**不是** body，因为薪资结构常在「岗位职责」之前）

**待定处理**：薪资「面议」无法判断 → 标记待定，交人工决策。

### 规则 10：通过

```python
return key, '投', f'通过全部硬性规则（薪资{salary}，学历{edu}，经验{exp}）'
```

## 薪资解析函数

支持多种格式：

| 输入 | 输出 | 说明 |
|---|---|---|
| `5-8千` | 5000 | 中文单位 |
| `6-9K` | 6000 | 英文单位 |
| `1-1.2万` | 10000 | 万元单位 |
| `8千以上` | 8000 | 单值+后缀 |
| `8000元以下` | 0 | 上限表述 |
| `面议` | None | 无法判定 |
| `薪资优厚` | None | 无法判定 |

## 词表维护建议

### 新增否决词

1. 在对应常量中添加
2. 跑 `--reset` 全量重判
3. 对比新旧结果，人工抽查翻转的记录

### 回扫翻转

用户新增规则后，需要对已判定记录做回扫：

```python
recs = {}
for l in open('index.jsonl'):
    r = json.loads(l)
    recs[r['title'] + '|' + r['company']] = r

flips = []
for l in open('verdicts.jsonl'):
    v = json.loads(l)
    if v['verdict'] != '投':
        continue
    r = recs.get(v['key'])
    if r and any(bad in r['title'] for bad in NEW_BAD_WORDS):
        flips.append(v['key'])

# 重写 verdicts.jsonl
lines = []
for l in open('verdicts.jsonl'):
    v = json.loads(l)
    if v['key'] in set(flips):
        lines.append(json.dumps({
            'key': v['key'], 'verdict': '不投', 'reason': '新规则命中'
        }, ensure_ascii=False))
    else:
        lines.append(l.rstrip('\n'))
open('verdicts.jsonl', 'w').write('\n'.join(lines) + '\n')
```

### 调参

| 参数 | 环境变量 | 默认值 |
|---|---|---|
| 薪资下限 | `JOB_SALARY_FLOOR` | 4500 |
| 经验上限 | `JOB_EXP_MAX` | 3 |

## 规则演进示例

实际使用中的规则演进过程（可作为调优参考）：

| 触发原因 | 新增规则 | 翻转数量 |
|---|---|---|
| 发现客服岗混入 | HARD_DIRECTION_BAD | 44 条 |
| 发现卡片薪资虚高 | 规则 9a 底薪深扫 | 8 条 |
| 发现行业标签误杀 | 标签区/正文区分离 | 修复 38 条 |
| 发现推荐广告污染 | 标题必须含关键词 | 清洗 201 条 |
| 发现「文员」包装电话岗 | JD 岗位性质检测 | 拦截 10+ 条 |
| 发现设备运维混入 | 设备地推类词表 | 拦截 30+ 条 |

**结论**：规则引擎不是一次写完的，是在使用中不断发现漏网、补充词表迭代出来的。
