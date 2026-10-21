// ============================================================
// boss_driver.js — BOSS直聘自动化驱动（浏览器端，通过 CDP 执行）
//
// 运行环境：ego-browser Node 沙箱（bcdp_script）
// 可用 API：page.evaluate / page.goto / page.waitForSelector / page.keyboard
//           fetch.server(url, opts) / cdp(method, params)
// 不可用：require / fs / window / page.$$ / page.$$eval
//
// 核心难点与解法：
//   1. 薪资字体混淆 → PUA 码点映射表解码
//   2. 列表实时重排 → 每次点击前重新定位 + 点击后验证
//   3. 合成 click 无效 → 必须用 CDP Input.dispatchMouseEvent
//   4. 弹窗遮挡 → 每次操作前 dismissPopups()
// ============================================================

const sleep = ms => new Promise(r => setTimeout(r, ms));

// ============ 薪资字体解码 ============
// BOSS 用私有字体渲染薪资数字，DOM 中是 PUA 码点（U+E031-U+E03A）
// 映射关系通过「截图 OCR + DOM 码点」交叉验证得出
const SAL_MAP = {};
for (let i = 0; i < 10; i++) {
  SAL_MAP[57393 + i] = String(i);  // 57393='0' ... 57402='9'
}

const decode = s => [...(s || '')]
  .map(c => SAL_MAP[c.codePointAt(0)] !== undefined ? SAL_MAP[c.codePointAt(0)] : c)
  .join('');

// ============ 弹窗清理 ============
async function dismissPopups() {
  await page.evaluate(() => {
    const stay = [...document.querySelectorAll('button, a')]
      .find(b => (b.textContent || '').trim() === '留在此页');
    if (stay) { stay.click(); return; }
    const close = document.querySelector('[class*="dialog"] [class*="close"], .modal-close');
    if (close) close.click();
  });
}

// ============ 规则表（按需自定义）============
const CONFIG = {
  companyBlacklist: [],        // 公司黑名单关键词
  hardOff: ['客服', '售后', '前台', '服务员', '保安', '骑手', '主播',
            '营业员', '店员', '销售', '实习', '在校', '应届'],
  badTitle: ['总助', 'CEO助理', '董事长助理', '总经理助理', '总裁助理',
             '电商', '直播', '社群', '招聘', '人事', 'PMC', '采购',
             '供应链', '站长', '招商', '司机', '配送', '推广', '作者',
             '编辑', '运营', '课程班主任', '无责', '高提', '班次',
             '打字', '坐班', '急招', '电销', '外呼', '邀约', '回访'],
  pitWords: ['无责底薪', '培训费', '押金', '转培训', '先交', '保证金',
             '外包', '劳务派遣', '充电宝', '摆摊', '地推', '拉新',
             '日结', '轮岗', '高提成', '无责'],
  techWords: ['Linux', 'Docker', 'Kubernetes', 'K8s', 'MySQL', 'Oracle',
              'SQL', '网络协议', '防火墙', '交换机配置', '攻防', '渗透',
              'Java', 'Python', 'C++', '前端', '后端', '全栈', '算法',
              '电站', '光伏', '风电', '化工', '仪表', '制冷', '电梯',
              '锅炉', '医疗设备', '半导体', 'CAD', 'BIM', '广联达'],
  dutyWords: ['值班', '倒班', '7*24', '24小时'],
  certWords: ['英语', '四级', '六级', 'CPA', '一建', '二建', '造价师', 'PMP'],
  jdNature: ['解答客户', '处理售后', '客户沟通', '接听', '热线', '招人',
             '售后解答', '客户咨询', '售后文员', '跟单', '电话销售',
             '外呼', '拨打电话', '电话沟通', '电话邀约', '电话回访',
             '打电话', '电话出单', '邀约客户', '电话联系'],
  // 标题必须含以下词根之一
  coreOk: /(运维|技术支持|IT|it|信息技术|信息化|网络|系统|硬件|计算机|电脑|桌面|网管|文员|资料|内勤|档案|行政|综合管理)/,
  salaryFloor: 4500,
};

// ============ 判定函数 ============
function judge(title, salary, tags, jdDecoded, company) {
  const C = CONFIG;

  // 1. 公司黑名单
  for (const b of C.companyBlacklist) {
    if (company.includes(b)) return { v: '不投', r: `黑名单「${b}」` };
  }

  // 2. 标题必须含核心词根
  if (!C.coreOk.test(title)) {
    return { v: '不投', r: '标题无核心词根' };
  }

  // 3. 薪资下限
  const m = (salary || '').match(/(\d+)-(\d+)K/);
  if (!m) return { v: '不投', r: '薪资无法解析' };
  if (parseInt(m[1]) * 1000 < C.salaryFloor) {
    return { v: '不投', r: `下限${m[1]}K<${C.salaryFloor / 1000}K` };
  }

  // 4. 硬排除
  for (const w of C.hardOff) {
    if (title.includes(w)) return { v: '不投', r: `标题「${w}」` };
  }

  // 5. 方向偏差
  for (const w of C.badTitle) {
    if (title.includes(w)) return { v: '不投', r: `「${w}」类` };
  }

  // 6. 学历/经验
  if (tags.includes('本科') || tags.includes('硕士') || tags.includes('博士')) {
    return { v: '不投', r: '学历本科+' };
  }
  if (tags.includes('3-5年') || tags.includes('5-10年') || tags.includes('10年以上')) {
    return { v: '不投', r: '经验3年+' };
  }

  // 7. 坑位词
  for (const w of C.pitWords) {
    if (jdDecoded.includes(w) || title.includes(w)) {
      return { v: '不投', r: `坑「${w}」` };
    }
  }

  // 8. 技术门槛
  for (const w of C.techWords) {
    if (jdDecoded.includes(w)) return { v: '不投', r: `JD「${w}」` };
  }

  // 9. 值班
  for (const w of C.dutyWords) {
    if (jdDecoded.includes(w)) return { v: '不投', r: `值班「${w}」` };
  }

  // 10. 证书
  for (const w of C.certWords) {
    if (jdDecoded.includes(w)) return { v: '不投', r: `要求「${w}」` };
  }

  // 11. JD 底薪深扫（关键：卡片薪资可能虚高）
  const bm = jdDecoded.match(/(?:底薪|基本工资|无责底薪|试用期)[^0-9]{0,8}([0-9]{3,5})/);
  if (bm) {
    let n = parseInt(bm[1]);
    if (n >= 100 && n < 450) n *= 10;
    if (n < C.salaryFloor) return { v: '不投', r: `JD底薪${bm[1]}<${C.salaryFloor}` };
  }

  // 12. JD 岗位性质（识别「文员」包装下的电话岗）
  for (const w of C.jdNature) {
    if (jdDecoded.includes(w)) return { v: '不投', r: `JD实为「${w}」类` };
  }

  return { v: '投', r: `通过（${salary}）` };
}

// ============ 批量判定 + 投递 ============
async function runBatch(options = {}) {
  const T0 = Date.now();
  const BUDGET = options.budget || 95000;   // 95 秒预算（规避工具超时）
  const MAX_APPS = options.maxApps || 5;

  // 滚动加载全部卡片
  let last = 0;
  for (let k = 0; k < 8; k++) {
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(1800);
    const n = await page.evaluate(() => document.querySelectorAll('.job-card-box').length);
    if (n === last) break;
    last = n;
  }

  const total = last;
  const results = [];
  let applied = 0;
  let captchaHit = false;

  for (let i = 0; i < total; i++) {
    if (Date.now() - T0 > BUDGET - 8000) break;
    if (applied >= MAX_APPS) break;

    try {
      await dismissPopups();
      await page.evaluate((idx) => {
        const cs = document.querySelectorAll('.job-card-box');
        if (cs[idx]) cs[idx].scrollIntoView({ block: 'center' });
        cs[idx].click();
      }, i);
      await sleep(1600);

      const info = await page.evaluate(() => {
        const d = document.querySelector('.job-detail-container');
        if (!d) return null;
        const body = d.querySelector('.job-detail-body');
        const comp = d.querySelector('.job-boss-info');
        return {
          title: (d.querySelector('[class*="name"], h1, h2') || {}).textContent || '',
          sal: (d.querySelector('[class*="salary"]') || {}).textContent || '',
          jdRaw: body ? body.textContent : '',
          tags: [...d.querySelectorAll('li')].map(l => l.textContent.trim()).join('/'),
          company: comp ? comp.textContent.replace(/\s+/g, ' ').trim() : '',
          captcha: !!(document.querySelector('.geetest_wrapper, #captcha, iframe[src*="captcha"]')),
          hasChat: [...document.querySelectorAll('button, a')]
            .some(b => (b.textContent || '').trim() === '立即沟通' && d.contains(b))
        };
      });

      if (!info || !info.title) continue;
      if (info.captcha) { captchaHit = true; break; }

      const salDecoded = decode(info.sal);
      const jdDecoded = decode(info.jdRaw);
      const j = judge(info.title, salDecoded, info.tags, jdDecoded, info.company);

      if (j.v === '投' && info.hasChat) {
        await page.evaluate(() => {
          const d = document.querySelector('.job-detail-container');
          const btn = [...document.querySelectorAll('button, a')]
            .find(b => (b.textContent || '').trim() === '立即沟通' && d.contains(b));
          if (btn) btn.click();
        });
        await sleep(2200);
        await dismissPopups();
        results.push({ title: info.title, sal: salDecoded, company: info.company, r: '已沟通' });
        applied++;
        await sleep(800);
      } else {
        results.push({ title: info.title, sal: salDecoded, company: info.company, r: j.r });
      }
    } catch (e) {
      results.push({ title: 'ERR', r: String(e).slice(0, 40) });
    }
  }

  return { total, applied, captcha: captchaHit, results };
}

// ============ 消息处理：安全定位 ============
/**
 * 三重校验打开会话
 *
 * 为什么需要三重校验：
 *   BOSS 会话列表会因新消息实时重排，固定坐标会点错人。
 *
 * @param {string} name 会话姓名
 * @param {string} comp 公司关键词
 * @returns {{status: string, pos?: object}}
 */
async function safeOpen(name, comp) {
  for (let attempt = 0; attempt < 4; attempt++) {
    // 1. 实时扫描列表，姓名+公司双匹配
    const pos = await page.evaluate((args) => {
      const lis = [...document.querySelectorAll('li')]
        .filter(l => l.querySelector('.name-text'));
      const hit = lis.find(l => {
        const n = ((l.querySelector('.name-text') || {}).textContent || '').trim();
        if (n !== args.name) return false;
        const nb = l.querySelector('.name-box');
        const spans = nb
          ? [...nb.querySelectorAll('span')].map(s => (s.textContent || '').trim())
          : [];
        return spans.join(' ').includes(args.comp);
      });
      if (!hit) return null;
      const warp = hit.querySelector('.friend-content-warp') || hit;
      const r = warp.getBoundingClientRect();
      // 不在可视区则滚动
      if (r.y < 160 || r.y > 850) {
        hit.scrollIntoView({ block: 'center' });
        return { needScroll: true };
      }
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    }, { name, comp });

    if (!pos) return { status: 'not-found' };
    if (pos.needScroll) { await sleep(1000); continue; }

    // 2. CDP 真实鼠标事件（合成 click 无效）
    await cdp('Input.dispatchMouseEvent',
      { type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
    await cdp('Input.dispatchMouseEvent',
      { type: 'mouseReleased', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
    await sleep(3200);

    // 3. 验证右侧窗口是否显示目标公司
    const ok = await page.evaluate((c) => document.body.textContent.includes(c), comp);
    if (ok) return { status: 'opened', pos };
  }
  return { status: 'verify-failed' };
}

// ============ 消息处理：发送 ============
async function sendMessage(text) {
  const typed = await page.evaluate((t) => {
    const input = document.querySelector('[contenteditable="true"], textarea');
    if (!input) return false;
    input.focus();
    if (input.tagName === 'TEXTAREA') {
      input.value = t;
    } else {
      input.textContent = t;
      input.dispatchEvent(new InputEvent('input', { bubbles: true }));
    }
    return true;
  }, text);
  if (!typed) return false;

  await sleep(800);
  return await page.evaluate(() => {
    const btns = [...document.querySelectorAll('button, a')]
      .filter(e => e.offsetParent !== null && /^发送$/.test((e.textContent || '').trim()));
    if (btns.length) { btns[btns.length - 1].click(); return true; }
    return false;
  });
}

// ============ 消息处理：简历同意/拒绝 ============
async function handleResumeRequest(agree) {
  return await page.evaluate((ag) => {
    const sel = ag ? '.btn-agree' : '.btn-reject';
    const btn = document.querySelector(sel);
    if (btn && btn.offsetParent !== null) { btn.click(); return true; }
    return false;
  }, agree);
}

// ============ 消息处理：读取岗位卡片 ============
async function getJobCard() {
  return await page.evaluate(() => {
    const card = document.querySelector('.chat-position-content');
    if (!card) return null;
    return {
      name: (card.querySelector('.position-name') || {}).textContent || '',
      salary: (card.querySelector('.salary') || {}).textContent || '',
      city: (card.querySelector('.city') || {}).textContent || ''
    };
  });
}

// ============ 消息处理：完整流程 ============
async function processConversation(name, comp, question) {
  const open = await safeOpen(name, comp);
  if (open.status !== 'opened') return open;

  // 检查是否有简历请求
  const hasResumeReq = await page.evaluate(() => {
    const bubbles = [...document.querySelectorAll('[class*="message-item"], [class*="bubble"]')]
      .filter(e => e.offsetParent !== null)
      .map(b => b.textContent || '').join(' ');
    return bubbles.includes('我想要一份您的附件简历');
  });

  if (hasResumeReq) {
    await handleResumeRequest(true);
    await sleep(2200);
  }

  const sent = await sendMessage(question);
  return { status: sent ? 'done' : 'send-failed', resumeSent: hasResumeReq };
}

// ============ 导出 ============
module.exports = {
  SAL_MAP, decode, dismissPopups, judge, runBatch,
  safeOpen, sendMessage, handleResumeRequest, getJobCard,
  processConversation, CONFIG
};
