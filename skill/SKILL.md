---
name: job-automation-pipeline
description: Use when building or debugging browser-automation job hunting pipelines — collecting job postings from recruitment sites (Zhaopin/BOSS/Lagou), judging fit with deterministic rules, batch applying, handling recruiter chat messages, or generating progress dashboards. Also use when a recruitment site hides data behind font obfuscation, recommendation ad cards, or a chat list that reorders itself.
---

# 求职自动化流水线

用浏览器自动化 + 确定性规则引擎，把「找工作」拆成可复用、可审计的流水线。

## 何时用这个 skill

- 需要从招聘网站批量采集岗位（含 JD 全文）
- 需要把「这个岗位适不适合我」变成可机械执行的规则
- 需要批量投递/沟通，且要防误投、防风控
- 需要处理招聘方的聊天消息（要简历、问薪资、婉拒）
- 遇到招聘网站的字体混淆、推荐广告卡、列表重排等对抗性设计

## 四阶段流水线

```
采集 → 判定 → 投递 → 看板
 ↓       ↓       ↓       ↓
JSONL  verdicts  CSV   HTML/MD
```

每个阶段独立可跑，数据通过文件解耦。

## 核心设计决策

### 1. 浏览器数据如何落盘

**问题**：浏览器 `page.evaluate` 上下文没有 `fs` 模块。

**方案**：本地 HTTP 服务 + 页面 `fetch`。

```javascript
await fetch('http://127.0.0.1:18765/collect', {
  method: 'POST',
  body: JSON.stringify({ results: batch })
});
```

**为什么可行**：Chrome 把 loopback（127.0.0.1）视为可信来源，不受混合内容限制。服务端加 `Access-Control-Allow-Origin: *` 即可。

**不要**：尝试在浏览器里用 `require('fs')`、`localStorage` 存大量数据、或把数据塞进模型上下文（会撑爆）。

### 2. 判定用规则还是 LLM

**默认用规则**，因为：可复现、可审计、零成本、毫秒级。

**LLM 只用于**规则无法覆盖的模糊判断（如「这个 JD 的技能栈是否超出我的能力」），且**批量离线跑**，不在主链路上。

规则引擎的关键设计：
- **优先级串行**：命中即返回，不继续判断
- **证据留存**：每条拒绝理由都引用 JD 原文片段
- **参数化**：薪资下限、经验上限用环境变量

### 3. JD 的标签区/正文区必须分离

**问题**：JD 开头常有公司行业标签（「本公司属化工行业」），全文本匹配否决词会误杀「化工公司招 IT 运维」。

**方案**：按「岗位职责/任职要求」等标记切分，否决词只查正文区。

```python
BODY_MARKERS = ['岗位职责', '工作职责', '职位描述', '任职要求', '岗位要求',
                '工作内容', '职责描述', '职位信息', '工作职能']

def split_jd(jd):
    best = None
    for mk in BODY_MARKERS:
        p = jd.find(mk)
        if 0 <= p and (best is None or p < best):
            best = p
    if best is None:
        best = int(len(jd) * 0.4)   # 无标记取后 60%
    return jd[:best], jd[best:]      # (标签区, 正文区)
```

## 招聘网站对抗性设计及破解

### 字体混淆（BOSS 直聘）

**现象**：DOM 里薪资是 `-K` 这种乱码，数字部分是 PUA 码点（U+E031-U+E03A）。

**破解**：
1. 截图 → OCR 读出真实薪资
2. 对比 DOM 码点序列
3. 交叉验证多组，建映射表

```javascript
const SAL_MAP = {};
for (let i = 0; i < 10; i++) SAL_MAP[57393 + i] = String(i);

const decode = s => [...(s || '')]
  .map(c => SAL_MAP[c.codePointAt(0)] ?? c)
  .join('');
```

**注意**：JD 正文里的数字同样是 PUA 编码，扫底薪前必须先 decode。

### 推荐广告卡污染（智联）

**现象**：搜索列表插入「为你推荐」广告卡，DOM 结构与正常卡片相同。

**破解**：标题必须含当前关键词才采集。

```javascript
if (cardTitle(card).indexOf(keyword) < 0) continue;
```

**效果**：某次采集中 4981 张卡只有 717 张真命中（14%）。

### 会话列表实时重排（BOSS 聊天）

**现象**：新消息到达后列表重排，固定坐标点击会点错人、发错消息。

**破解**：三重校验。

```javascript
async function safeOpen(name, company) {
  for (let attempt = 0; attempt < 4; attempt++) {
    // 1. 每次重新扫描，姓名+公司双匹配
    const pos = await locate(name, company);
    if (!pos) return { status: 'not-found' };
    // 2. CDP 真实鼠标事件（合成 click 无效）
    await realClick(pos.x, pos.y);
    await sleep(3000);
    // 3. 验证右侧是否显示目标公司
    if (await verify(company)) return { status: 'opened' };
  }
  return { status: 'verify-failed' };
}
```

**坐标必须用 `getBoundingClientRect()` 的中心点**（`x + width/2`），用左上角会点到相邻元素。

### 公司页 vs 职位页 URL

**现象**：采集到的 URL 是 `companydetail` 而非 `jobdetail`，投递全部失败。

**破解**：进公司页后**点「在招职位」tab**，默认只渲染 3 个链接，点后显示全部。

```javascript
await page.evaluate(() => {
  const tab = [...document.querySelectorAll('[class*="tab-nav__item"]')]
    .find(t => /在招职位/.test(t.textContent || ''));
  if (tab) tab.click();
});
```

## 投递防误投

标题 + 公司**双重校验**，任一不一致即跳过：

```javascript
const titleOk = norm(info.title).includes(norm(job.title).slice(0, 10));
const companyOk = norm(info.company).includes(norm(job.company).slice(0, 8));
if (!titleOk || !companyOk) {
  results.push({ ...job, result: '核对不一致跳过' });
  continue;
}
```

**只记账成功**：只有确认页面出现「已向对方发送」才记为投递成功。

## 风控与限流

| 措施 | 参数 |
|---|---|
| 单条间隔 | 1.3-2.8 秒 |
| 每 3 条休息 | 15 秒 |
| 段间休息 | 25 秒 |
| 每段预算 | 95 秒（规避工具超时） |
| 每日上限 | 智联 ~163，BOSS 150 |

**检测即停，不硬闯**：
- 验证码：`.geetest_wrapper, #captcha, iframe[src*="captcha"]`
- 登录失效：`/扫码登录|密码登录|验证码登录/`
- 上限提示：`/今日投递已超过上限|您已达到沟通上限/`

## 规则引擎的典型规则

按优先级串行，命中即返回：

| 序号 | 规则 | 判定 |
|---|---|---|
| 0 | 实习/在校/兼职 | 不投 |
| 0.5 | 值班/倒班 | 不投 |
| 0.6a | 硬性方向排除（客服/售后） | 不投（无豁免） |
| 0.6b | 方向不符（前台/服务员等） | 不投（有白名单） |
| 1 | 强专业设备（只查正文） | 不投 |
| 2 | 编程开发 | 不投 |
| 3 | 高危技术栈（Linux/K8s/数据库） | 不投（软性可豁免） |
| 4 | 销售性质 | 不投 |
| 5 | 工地/工程资料员 | 不投 |
| 6 | 证书硬要求 | 不投（「优先」豁免） |
| 7 | 学历不符 | 不投（「大专也可」豁免） |
| 8 | 经验超限 | 不投 |
| 9 | 薪资低于下限 | 不投 |
| 9a | 底薪+绩效，底薪不足 | 不投 |
| 10 | 通过 | 投 |

### 关键规则：底薪深扫

**问题**：卡片写「5-10K」，JD 正文写「底薪 3000 + 提成」。

**方案**：扫 JD 原始文本（不是拼接结果，避免空格拆断数字）。

```python
m = re.search(r'底薪[^0-9]{0,6}([0-9]{3,6})', jd) or \
    re.search(r'([0-9]{3,6})底薪', title)
if m:
    n = int(m.group(1))
    if 100 <= n < 450: n *= 10   # 百元单位
    if n < SALARY_FLOOR: return key, '不投', f'底薪{n}元不足'
```

### 关键规则：岗位性质检测

**问题**：标题是「资料审核员」「行政文员」，实际是电话邀约销售。

**方案**：扫 JD 中的电话类动词。

```python
JD_NATURE = ['解答客户', '处理售后', '客户沟通', '接听', '热线',
             '电话销售', '外呼', '拨打电话', '电话沟通', '电话邀约',
             '电话回访', '打电话', '电话出单', '邀约客户', '电话联系']
```

## 消息处理策略

| 场景 | 动作 |
|---|---|
| HR 索要简历 | 点「同意」发简历 → 再问岗位详情+薪资结构 |
| HR 主动打招呼 | 说明自己的方向+期望，问是否有匹配岗位 |
| HR 已介绍岗位 | 判断符合否 → 符合则问薪资 → 不符合则婉拒 |
| HR 已明确拒绝 | 无需回复 |
| 疑似培训贷/索要微信 | 拒绝交换 + 婉拒 |
| 电话邀约岗 | 婉拒 |

**按钮选择器**（BOSS）：
- 同意简历：`.btn-agree`（是 span 不是 button）
- 拒绝简历：`.btn-reject`
- 发送：文本为「发送」的 button/a 最后一个

**输入框**是 `div[contenteditable="true"]`，不是 textarea，需要触发 `InputEvent`：

```javascript
input.textContent = text;
input.dispatchEvent(new InputEvent('input', { bubbles: true }));
```

## 数据一致性原则

1. **单一事实源**：投递记录只认 CSV，队列实时从 CSV 计算
2. **幂等写入**：去重键 `title|company`
3. **失败不记账**：只有确认成功才记录
4. **可回溯**：每条判定带 reason，每次投递带时间戳

## 常见错误与排查

| 症状 | 原因 | 解决 |
|---|---|---|
| `require is not defined` | 浏览器无 Node 运行时 | 用本地 HTTP 服务 |
| `page.$$eval is not a function` | 运行时只暴露 evaluate | 改用 `page.evaluate` + `querySelectorAll` |
| 点击无反应 | 合成 click 无效 | 用 CDP `Input.dispatchMouseEvent` |
| `window.__xxx is not a function` | 导航后注入丢失 | 先导航再注入；驻留脚本内不导航 |
| 全部「核对不一致」 | URL 是公司页不是职位页 | 点「在招职位」tab 重新解析 |
| 全部「薪资无法解析」 | 字体混淆未解码 | 建 PUA 映射表 |
| 点错会话/发错人 | 列表重排 | 三重校验定位 |
| 工具超时中断 | 单次任务过长 | 分段执行（95 秒/段） |

## 工作流程参考

1. **确认平台页面结构**：先用一次探测确认选择器（不要假设）
2. **小规模验证**：先跑 1-5 条，确认流程通
3. **启动落盘服务**：`python collect_server.py`
4. **采集**：注入驻留脚本，轮询进度
5. **判定**：跑规则引擎，检查统计分布是否合理
6. **抽样复核**：人工看 10-20 条判定理由，确认规则无误
7. **投递**：分段跑，每段检查结果
8. **处理消息**：按未读排序逐条处理
9. **生成看板**：输出 HTML + Markdown

## 完整实现参考

搜索开源项目 `job-automation-pipeline`（求职自动化流水线），包含：
- `collect_server.py` — 落盘 HTTP 服务
- `judge_engine.py` — 判定规则引擎
- `zhaopin_collector.js` — 智联采集器（防污染）
- `zhaopin_applier.js` — 智联投递（双重校验）
- `boss_driver.js` — BOSS 驱动（字体解码+三重校验）
- `dashboard_generator.py` — 看板生成器
- `docs/architecture.md` — 架构设计
- `docs/rules.md` — 规则详解
- `docs/pitfalls.md` — 踩坑记录

## 合规提醒

- 遵守平台服务条款，自动化操作可能违反部分平台规则
- 控制请求频率，避免给平台造成负担
- 不用于商业用途（不得转售或批量代投）
- 采集数据含个人信息，妥善保管
