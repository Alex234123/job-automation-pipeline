# Job Automation Pipeline

> 用浏览器自动化 + 确定性规则引擎构建的求职流水线：采集 → 判定 → 投递 → 看板 → 消息处理

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)

## 这是什么

一个**不依赖招聘网站 API**的求职自动化工具集。核心思路：

```
浏览器页面 ──fetch──> 本地 HTTP 服务 ──> JSONL/CSV 落盘
                                              │
                                              ▼
                                        规则引擎判定
                                              │
                                              ▼
                                    浏览器自动化投递/沟通
```

**为什么不用官方 API**：招聘网站基本不提供个人求职者 API，只能用浏览器自动化。

**为什么判定用规则引擎而不是 LLM**：可复现、可审计、零成本、毫秒级。LLM 只用于规则无法覆盖的模糊判断（如「这个 JD 要求是否超出我的能力」）。

## 核心特性

| 特性 | 说明 |
|---|---|
| **本地 HTTP 落盘** | 解决浏览器沙箱无法写文件的问题 |
| **标签区/正文区分离** | 避免公司行业标签误杀岗位（如化工公司招 IT） |
| **字体混淆破解** | 破解 BOSS 直聘的薪资 PUA 字符编码 |
| **防误投双重校验** | 标题 + 公司双匹配，不一致直接跳过 |
| **列表重排定位** | 每次点击前实时定位 + 点击后验证 |
| **防污染采集** | 只采标题含关键词的卡片，过滤推荐广告 |
| **分段执行** | 规避浏览器自动化工具超时与风控 |

## 快速开始

### 1. 环境要求

```bash
Python 3.8+
Node.js（浏览器自动化依赖）
```

### 2. 启动落盘服务

```bash
python src/collect_server.py
# 监听 127.0.0.1:18765
```

### 3. 采集岗位

在浏览器自动化环境中注入采集器：

```javascript
// 1. 注入驻留脚本
await page.evaluate(collectorCode);

// 2. 启动（fire-and-forget）
await page.evaluate(`window.__startCollector({
  keywords: ['IT运维', '技术支持', '桌面运维'],
  cityCode: '749',
  batchName: '长沙',
  specialArea: ''
})`);

// 3. 轮询进度
const progress = await page.evaluate('window.__collectorProgress()');
```

### 4. 判定岗位

```bash
python src/judge_engine.py
# 输出 verdicts.jsonl：{"key": "岗位|公司", "verdict": "投", "reason": "..."}
```

### 5. 投递

```javascript
const result = await runSegment();
// { applied: 5, recorded: 5, remaining: 120, captcha: false }
```

### 6. 生成看板

```bash
python src/dashboard_generator.py
# 输出 data/dashboard.html + data/progress.md
```

## 项目结构

```
.
├── src/
│   ├── collect_server.py       # 落盘 HTTP 服务
│   ├── judge_engine.py         # 判定规则引擎
│   ├── dashboard_generator.py  # 看板生成器
│   ├── zhaopin_collector.js    # 智联采集器（页面内驻留）
│   ├── zhaopin_applier.js      # 智联投递驱动
│   └── boss_driver.js          # BOSS 驱动（判定+投递+消息）
├── docs/
│   ├── architecture.md         # 架构设计
│   ├── rules.md                # 判定规则详解
│   └── pitfalls.md             # 踩坑记录
├── examples/
│   └── config.example.json     # 配置示例
├── data/                       # 数据目录（.gitignore）
└── README.md
```

## 数据格式

### index.jsonl（采集原始数据）

```json
{
  "title": "IT运维工程师",
  "salary": "6-9K",
  "city_area": "长沙·岳麓区",
  "experience": "1-3年",
  "education": "大专",
  "company": "某科技有限公司",
  "jd": "岗位职责：...任职要求：...",
  "job_url": "https://example.com/job/123",
  "kw": "IT运维",
  "batch": "长沙",
  "time": "2026-01-01T10:00:00"
}
```

### verdicts.jsonl（判定结果）

```json
{"key": "IT运维工程师|某科技有限公司", "verdict": "投", "reason": "通过全部硬性规则"}
```

### applied_jobs.csv（投递记录）

```csv
时间,岗位,薪资,地点,公司,结果
01-01 10:30,IT运维工程师,6-9K,长沙·岳麓区,某科技有限公司,已投递
```

## 规则引擎说明

判定按优先级串行执行，命中即返回：

| 序号 | 规则 | 判定 |
|---|---|---|
| 0 | 实习/在校/兼职 | 不投 |
| 0.5 | 值班/倒班 | 不投 |
| 0.6a | 硬性方向排除（客服/售后） | 不投 |
| 0.6b | 方向不符（前台/服务员等） | 不投 |
| 1 | 强专业设备（只查正文） | 不投 |
| 2 | 编程开发 | 不投 |
| 3 | 高危技术栈 | 不投 |
| 4 | 销售性质 | 不投 |
| 5 | 工地/工程资料员 | 不投 |
| 6 | 证书硬要求 | 不投 |
| 7 | 学历不符 | 不投 |
| 8 | 经验超限 | 不投 |
| 9 | 薪资低于下限 | 不投 |
| 9a | 底薪+绩效，底薪不足 | 不投 |
| 10 | 通过 | 投 |

详细说明见 [docs/rules.md](docs/rules.md)。

## 关键实现技巧

### 1. 本地 HTTP 落盘

浏览器沙箱无法 `require('fs')`，但 Chrome 把 `127.0.0.1` 视为可信来源，页面可直接 `fetch` 本地服务：

```javascript
await fetch('http://127.0.0.1:18765/collect', {
  method: 'POST',
  body: JSON.stringify({ results: batch })
});
```

### 2. 字体混淆破解

BOSS 直聘用私有字体渲染薪资数字，DOM 中是 PUA 码点。通过「截图 OCR + DOM 码点」交叉验证建立映射：

```javascript
const SAL_MAP = {};
for (let i = 0; i < 10; i++) SAL_MAP[57393 + i] = String(i);

const decode = s => [...(s || '')]
  .map(c => SAL_MAP[c.codePointAt(0)] ?? c)
  .join('');
```

### 3. 列表重排定位

会话列表会因新消息实时重排，固定坐标会点错人。解法：每次点击前重新定位 + 点击后验证。

```javascript
// 定位 → 点击 → 验证 → 失败重试
for (let attempt = 0; attempt < 4; attempt++) {
  const pos = await locate(name, company);      // 实时扫描
  if (!pos) return { status: 'not-found' };
  await realClick(pos.x, pos.y);                // CDP 真实鼠标事件
  await sleep(3000);
  if (await verify(company)) return { status: 'opened' };
}
```

### 4. 防污染采集

搜索列表会插入推荐广告卡。解法：标题必须含当前关键词。

```javascript
if (cardTitle(card).indexOf(keyword) < 0) continue;
```

### 5. 标签区/正文区分离

JD 开头的公司行业标签不代表岗位要求。解法：按「岗位职责」等标记切分。

```python
def split_jd(jd):
    best = None
    for mk in BODY_MARKERS:
        p = jd.find(mk)
        if 0 <= p and (best is None or p < best):
            best = p
    return jd[:best], jd[best:]  # (标签区, 正文区)
```

## 反爬与风控

| 措施 | 实现 |
|---|---|
| 请求间隔 | 每条 1.3-2.8 秒 |
| 批量休息 | 每 3 条休息 15 秒，段间 25 秒 |
| 验证码检测 | 检测到即停止，不硬闯 |
| 登录失效检测 | 检测到即停止，避免误记账 |
| 每日上限 | 检测平台提示，超限即停 |

## 免责声明

本项目仅供**个人求职效率提升**使用。使用时请注意：

1. **遵守平台服务条款**——自动化操作可能违反部分平台规则，风险自负
2. **控制请求频率**——避免给平台造成负担
3. **不用于商业用途**——不得转售或用于批量代投服务
4. **数据安全**——采集的数据包含个人信息，请自行妥善保管

## 贡献

欢迎提交 Issue 和 PR，特别是：

- 其他招聘平台的适配（如前程无忧、猎聘）
- 更精准的判定规则
- 反爬策略优化

## License

[MIT](LICENSE)
