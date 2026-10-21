# 踩坑记录

记录开发过程中遇到的所有问题及解法。这些都是**真实踩过的坑**，不是理论。

---

## 一、浏览器自动化环境限制

### 1.1 `require is not defined`

**现象**：在 `page.evaluate` 里写 `const fs = require('fs')` 报错。

**原因**：浏览器页面上下文没有 Node.js 运行时。

**解法**：所有需要文件系统的地方，都改成通过本地 HTTP 服务落盘。

```javascript
// ❌ 不行
const fs = require('fs');
fs.writeFileSync('data.json', JSON.stringify(results));

// ✅ 可行
await fetch('http://127.0.0.1:18765/collect', {
  method: 'POST',
  body: JSON.stringify({ results })
});
```

### 1.2 `page.$$eval is not a function`

**现象**：调用 `page.$$eval('.selector', fn)` 报错 `is not a function`。

**原因**：某些浏览器自动化运行时（如精简版 CDP 封装）只暴露 `page.evaluate`。

**解法**：全部改用 `page.evaluate` + 内部 `querySelectorAll`。

```javascript
// ❌ 不行
const titles = await page.$$eval('.job-card', els => els.map(e => e.textContent));

// ✅ 可行
const titles = await page.evaluate(() =>
  [...document.querySelectorAll('.job-card')].map(e => e.textContent)
);
```

### 1.3 工具调用硬超时 120 秒

**现象**：长时间运行的脚本被强制中断，返回 `Runtime.evaluate timed out`。

**原因**：浏览器自动化工具的默认超时，且部分实现忽略自定义 `timeoutMs`。

**解法**：分段执行 + fire-and-forget。

```javascript
// 1. 分段：每段预算 95 秒
const T0 = Date.now(), BUDGET = 95000;
for (const job of queue) {
  if (Date.now() - T0 > BUDGET) break;
  // ...
}

// 2. 长任务用 fire-and-forget 启动，外部轮询
window.__startCollector = function() {
  (async function() {
    for (const kw of keywords) {
      await collectKeyword(kw);   // 不 await 返回，后台跑
    }
  })();
  return 'started';   // 立即返回
};

// 外部轮询
const progress = await page.evaluate('window.__collectorProgress()');
```

### 1.4 页面导航导致注入的脚本丢失

**现象**：`window.__progress is not a function`。

**原因**：`page.goto()` 会重新加载页面，之前 `page.evaluate` 注入的函数全部丢失。

**解法**：
1. 先导航，再注入
2. 驻留脚本内部**绝不能**调用 `page.goto`
3. 需要跳转时，由外部 Node 代码控制，跳转后重新注入

```javascript
// ❌ 错误顺序
await page.evaluate(injectScript);
await page.goto(url);              // 注入的脚本没了
await page.evaluate('window.__start()');  // 报错

// ✅ 正确顺序
await page.goto(url);
await page.evaluate(injectScript);
await page.evaluate('window.__start()');
```

### 1.5 合成 click 无效

**现象**：`element.click()` 调用后页面毫无反应，但手动点击正常。

**原因**：部分页面用 `mousedown`/`mouseup` 而非 `click` 事件，或用事件委托检查 `isTrusted`。

**解法**：用 CDP 派发真实鼠标事件。

```javascript
// ❌ 可能无效
await page.evaluate(() => document.querySelector('.item').click());

// ✅ 真实鼠标事件
const pos = await page.evaluate(() => {
  const el = document.querySelector('.item');
  const r = el.getBoundingClientRect();
  return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
});
await cdp('Input.dispatchMouseEvent', {
  type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: 1
});
await cdp('Input.dispatchMouseEvent', {
  type: 'mouseReleased', x: pos.x, y: pos.y, button: 'left', clickCount: 1
});
```

**关键**：坐标必须用 `getBoundingClientRect()` 的**中心点**（`x + width/2`），不能用左上角。用左上角会点到相邻元素。

---

## 二、数据采集问题

### 2.1 推荐广告卡污染

**现象**：采集到的岗位里混入大量与关键词无关的岗位（如搜「IT运维」却采到「站长岗」「电商助理」）。

**原因**：招聘网站搜索列表会插入「为你推荐」的广告卡，DOM 结构与正常卡片相同。

**诊断**：统计采集记录的关键词命中率。

```python
suspect = []
for k, v in verdicts.items():
    r = recs.get(k)
    if v == '投' and r['kw'] not in r['title']:
        suspect.append((k, r['kw'], r['title']))
# 发现 221 条判投记录里只有 23 条是标题真含关键词的
```

**解法**：标题必须包含当前关键词。

```javascript
if (cardTitle(card).indexOf(keyword) < 0) continue;
```

**效果**：4981 张卡只采 717 条真命中。

### 2.2 公司页 URL 被当成职位页 URL

**现象**：投递时全部返回「核对不一致跳过」，29 条 0 成功。

**原因**：采集时从卡片取的 `href` 是公司详情页（`companydetail`），不是职位详情页（`jobdetail`）。

**诊断**：

```python
from collections import Counter
c = Counter()
for r in records:
    u = r['job_url']
    if 'jobdetail' in u: c['job'] += 1
    elif 'companydetail' in u: c['company'] += 1
# {'companydetail': 692, 'job': 0}
```

**解法**：
1. 启动 URL 解析服务
2. 按公司分组，逐个访问公司页
3. **点「在招职位」tab**（关键！默认只显示 3 个职位链接，点 tab 后显示全部 20 个）
4. 提取 jobdetail 链接，回写数据

```javascript
await page.evaluate(() => {
  const tab = [...document.querySelectorAll('[class*="tab-nav__item"]')]
    .find(t => /在招职位/.test(t.textContent || ''));
  if (tab) tab.click();   // 不点这个只有 3 个链接
});
await sleep(1800);
const links = await page.evaluate(() =>
  [...document.querySelectorAll('a[href*=jobdetail]')]
    .map(a => ({ title: a.textContent.trim(), href: a.href.split('?')[0] }))
);
```

**效果**：582 条修复成功，108 条岗位已下线。

### 2.3 数据重复

**现象**：同一个岗位被采集多次。

**原因**：不同关键词的搜索结果有重叠；滚动加载会重复渲染。

**解法**：双重去重。
1. 服务端内存集合 `_EX`（`title|company` 元组）
2. `seen_cards.txt` 持久化（重启后仍生效）

```python
key = (r.get('title','').strip(), r.get('company','').strip())
if key in _EX:
    dups += 1
    continue
_EX.add(key)
```

---

## 三、BOSS 直聘特有问题

### 3.1 薪资字体混淆

**现象**：DOM 里取到的薪资是 `-K`、`-K` 这种乱码，数字部分消失。

**诊断**：检查字符码点。

```javascript
[...'-K'].map(c => c.codePointAt(0));
// [57397, 45, 57402, 75]
// 57397 和 57402 是 PUA 区（Private Use Area）
```

**根因**：BOSS 用私有字体（`kanzhun-mix`）渲染数字，PUA 码点对应字形。

**破解过程**：
1. 截图，用 OCR 读出真实薪资（如「4-9K」「5-7K」）
2. 对比 DOM 中的 PUA 码点序列
3. 交叉验证多组，建立映射表

```javascript
// 验证过程：
// 卡片1: OCR "4-9K"  → DOM [57397, 57402]  → 57397='4', 57402='9'
// 卡片2: OCR "5-7K"  → DOM [57398, 57400]  → 57398='5', 57400='7'
// 卡片3: OCR "8-9K"  → DOM [57401, 57402]  → 57401='8', 57402='9' ✓ 与卡片1一致
// 卡片4: OCR "5-8K"  → DOM [57398, 57401]  → 57398='5'✓, 57401='8'✓
// 卡片5: OCR "10-12K"→ DOM [57394,57393,57394,57395] → 57394='1', 57393='0', 57395='2'
// 完美互洽

const SAL_MAP = {};
for (let i = 0; i < 10; i++) SAL_MAP[57393 + i] = String(i);
```

**注意**：JD 正文里的数字也是 PUA 编码，扫底薪前必须先 decode。

```javascript
const jdDecoded = decode(rawJdText);
const m = jdDecoded.match(/(?:底薪|试用期)[^0-9]{0,8}([0-9]{3,5})/);
```

### 3.2 会话列表实时重排

**现象**：点击 A 会话，打开的是 B 会话；发消息发错人。

**原因**：新消息到达时会话列表重新排序，之前算好的 y 坐标失效。

**血泪教训**：曾把一个会话的婉拒消息发给了另一个会话的 HR，对方回了个「？」。

**解法**：三重校验。

```javascript
async function safeOpen(name, company) {
  for (let attempt = 0; attempt < 4; attempt++) {
    // 1. 每次重新扫描列表
    const pos = await page.evaluate((args) => {
      const lis = [...document.querySelectorAll('li')].filter(l => l.querySelector('.name-text'));
      const hit = lis.find(l => {
        const n = (l.querySelector('.name-text')?.textContent || '').trim();
        if (n !== args.name) return false;
        const spans = [...(l.querySelector('.name-box')?.querySelectorAll('span') || [])]
          .map(s => s.textContent.trim());
        return spans.join(' ').includes(args.company);   // 姓名+公司双匹配
      });
      if (!hit) return null;
      const warp = hit.querySelector('.friend-content-warp') || hit;
      const r = warp.getBoundingClientRect();
      if (r.y < 160 || r.y > 850) {   // 不在可视区
        hit.scrollIntoView({ block: 'center' });
        return { needScroll: true };
      }
      return { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
    }, { name, company });

    if (!pos) return { status: 'not-found' };
    if (pos.needScroll) { await sleep(1000); continue; }

    // 2. CDP 真实鼠标点击
    await cdp('Input.dispatchMouseEvent', { type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
    await cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
    await sleep(3200);

    // 3. 验证右侧是否显示目标公司
    const ok = await page.evaluate(c => document.body.textContent.includes(c), company);
    if (ok) return { status: 'opened' };
  }
  return { status: 'verify-failed' };
}
```

### 3.3 简历同意按钮不是 button

**现象**：`[...document.querySelectorAll('button')].find(b => b.textContent === '同意')` 找不到。

**原因**：BOSS 的「同意/拒绝」是 `<span class="btn btn-agree">`，不是 `<button>`。

**解法**：用类选择器。

```javascript
document.querySelector('.btn-agree')?.click();   // 同意简历
document.querySelector('.btn-reject')?.click();  // 拒绝
```

### 3.4 输入框不是 textarea

**现象**：设置 `textarea.value = 'xxx'` 后点发送，消息为空。

**原因**：聊天输入框是 `div[contenteditable="true"]`，不是 textarea。

**解法**：设置 `textContent` 并触发 `InputEvent`（React/Vue 需要）。

```javascript
const input = document.querySelector('[contenteditable="true"], textarea');
input.focus();
if (input.tagName === 'TEXTAREA') {
  input.value = text;
} else {
  input.textContent = text;
  input.dispatchEvent(new InputEvent('input', { bubbles: true }));   // 关键
}
```

### 3.5 投递后弹窗阻塞

**现象**：投递几条后卡住，后续操作无响应。

**原因**：投递成功会弹「留在此页 / 继续沟通」对话框，遮住页面。

**解法**：每次操作前清理弹窗。

```javascript
async function dismissPopups() {
  await page.evaluate(() => {
    const stay = [...document.querySelectorAll('button, a')]
      .find(b => b.textContent.trim() === '留在此页');
    if (stay) { stay.click(); return; }
    const close = document.querySelector('[class*="dialog"] [class*="close"], .modal-close');
    if (close) close.click();
  });
}
```

---

## 四、规则引擎问题

### 4.1 行业标签误杀

**现象**：明明符合要求的岗位被判「不投」，原因是 JD 提到「化工」行业。

**原因**：JD 开头常有公司介绍（「本公司属化工行业」），全文本匹配强专业词会误杀。

**解法**：JD 切分标签区/正文区，强专业词只查正文。

```python
def split_jd(jd):
    best = None
    for mk in ['岗位职责', '任职要求', '职位描述', ...]:
        p = jd.find(mk)
        if 0 <= p and (best is None or p < best):
            best = p
    if best is None:
        best = int(len(jd) * 0.4)   # 无标记取后 60%
    return jd[:best], jd[best:]

tags, body = split_jd(jd)
hits = hit_list(body, STRONG_SPECIALTY)   # 只查 body
```

**效果**：修复 38 条误杀。

### 4.2 拼接字符串破坏正则

**现象**：底薪扫描规则在测试用例上不生效。

**原因**：`full = tags + ' ' + body` 在切分点插入空格，把「底薪3400」拆成「底薪 3400」之外的情况，或者数字被切断。

**解法**：扫原始 JD，不扫拼接结果。

```python
# ❌ 可能失败
m = re.search(r'底薪[^0-9]{0,6}([0-9]{3,6})', full)

# ✅ 正确
m = re.search(r'底薪[^0-9]{0,6}([0-9]{3,6})', jd)
```

### 4.3 卡片薪资与 JD 底薪不一致

**现象**：卡片写「5-10K」，投递后发现 JD 写「底薪 3000 + 提成」。

**原因**：招聘网站卡片显示的是「综合薪资」，JD 正文才是真实底薪结构。

**解法**：规则 9a，扫 JD 中的底薪数字。

```python
m_base = re.search(r'底薪[^0-9]{0,6}([0-9]{3,6})', jd) or \
         re.search(r'([0-9]{3,6})底薪', title)
if m_base:
    n = int(m_base.group(1))
    if 100 <= n < 450:
        n *= 10    # 处理「底薪35百」这种百元单位
    if n < SALARY_FLOOR:
        return key, '不投', f'底薪{n}元 < {SALARY_FLOOR}'
```

### 4.4 「文员」包装的电话岗

**现象**：标题是「资料审核员」「行政文员」，投递后 HR 说「主要是打电话邀约客户」。

**原因**：招聘方用中性标题包装电话销售岗。

**解法**：JD 岗位性质检测。

```python
JD_NATURE = ['解答客户', '处理售后', '客户沟通', '接听', '热线',
             '招人', '售后解答', '客户咨询', '售后文员', '跟单',
             '电话销售', '外呼', '拨打电话', '电话沟通', '电话邀约',
             '电话回访', '打电话', '电话出单', '邀约客户', '电话联系']

for w in JD_NATURE:
    if w in jdDecoded:
        return key, '不投', f'JD实为「{w}」类'
```

**补充**：还有一类是「核实客户资料 + 预约客户上门」，需要把「预约」「邀约上门」也加入词表。

---

## 五、风控与限流

### 5.1 每日投递上限

**现象**：投到一定数量后提示「今日投递已超过上限，明天再试吧」。

**数据**：
- 智联：约 163 条/日
- BOSS：150 条/日

**解法**：检测提示文本，命中即停止当日任务。

```javascript
const t = document.body.textContent;
if (t.includes('今日投递已超过上限') || t.includes('您已达到沟通上限')) {
  // 记录并停止
}
```

### 5.2 验证码

**现象**：操作一段时间后弹出滑块/点选验证码。

**解法**：检测即停，不硬闯。

```javascript
const captcha = !!(document.querySelector(
  '.geetest_wrapper, #captcha, iframe[src*="captcha"]'
));
if (captcha) { captchaHit = true; break; }
```

**注意**：有些验证码是 `iframe`，要检查 `src` 含 `captcha`。

### 5.3 登录态失效

**现象**：操作中途跳到登录页，后续投递全部失败但被记录成「已投递」。

**解法**：检测登录弹窗，命中即停且**不落盘**。

```javascript
const cs = await page.evaluate(() => {
  const t = document.body.textContent;
  return {
    sent: /已向对方发送/.test(t),
    login: /扫码登录|密码登录|验证码登录/.test(t)
  };
});
if (cs.login && !cs.sent) {
  loginHit = true;
  break;   // 不记录，避免误记账
}
```

**关键**：只有确认「已向对方发送」才记为成功。

---

## 六、性能与稳定性

### 6.1 大量数据导致上下文爆炸

**现象**：采集几千条后，每次工具调用返回的数据塞满上下文。

**解法**：数据不经过模型上下文，直接落盘。

```
浏览器 → fetch → 本地服务 → 文件
         （模型只看到「saved: 5」这样的摘要）
```

### 6.2 服务进程意外退出

**现象**：长时间空闲后 `curl` 本地服务返回 `SERVER_DOWN`。

**解法**：重新启动服务，并检查数据完整性（服务重启会重新加载 `_EX` 集合）。

```bash
python src/collect_server.py &
curl http://127.0.0.1:18765/state
```

### 6.3 CSV 编码问题

**现象**：Excel 打开 CSV 中文乱码。

**解法**：用 UTF-8 BOM。

```python
with open('applied_jobs.csv', 'a', encoding='utf-8-sig', newline='') as f:
    #                                        ^^^^ BOM
```

---

## 七、经验总结

### 7.1 调试方法论

1. **先截图确认状态**：不要假设页面在哪，截图看一眼
2. **打印中间变量**：`console.log` 关键数据，不要盲猜
3. **小步验证**：先跑 1 条，成功再批量
4. **留证据**：每条判定都记 reason，方便事后追溯

### 7.2 反爬对抗原则

1. **不硬闯验证码**：检测到就停，人工处理
2. **控制频率**：模拟人类节奏（1.3-2.8 秒/条）
3. **分段休息**：每 3 条休息 15 秒，每段休息 25 秒
4. **遵守上限**：平台说停就停

### 7.3 数据一致性原则

1. **单一事实源**：投递记录只认 CSV，队列实时从 CSV 计算
2. **幂等写入**：去重键 `title|company`
3. **失败不记账**：只有确认成功才记录
4. **可回溯**：每条判定带 reason，每个投递带时间戳

### 7.4 规则引擎设计原则

1. **确定性优先**：能用规则就不用 LLM
2. **证据留存**：拒绝理由必须引用原文
3. **分级处理**：硬性 vs 软性（优先/加分）
4. **可回扫**：新增规则后能批量重判历史数据
5. **参数化**：薪资下限、经验上限做成可配置
