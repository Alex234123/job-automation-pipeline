// ============================================================
// zhaopin_applier.js — 智联招聘投递驱动（浏览器端）
//
// 投递流程：
//   拉取队列 → 打开职位页 → 双重校验 → 点击投递 → 确认弹窗 → 记录
//
// 关键设计：
//   1. 防误投：标题 + 公司双重校验，不一致直接跳过
//   2. 验证码检测：命中即停，避免被封
//   3. 登录失效检测：命中即停，避免误记账
//   4. 节奏控制：每 3 条休息 15 秒，段间休息 25 秒
// ============================================================

const sleep = ms => new Promise(r => setTimeout(r, ms));

// ============ 配置 ============
const CONFIG = {
  queueEndpoint: 'http://127.0.0.1:18765/apply/queue',
  resultEndpoint: 'http://127.0.0.1:18765/apply/result',
  maxAppsPerSegment: 5,       // 每段投递数（规避工具超时）
  segmentBudget: 95000,       // 每段预算 ms
  restAfter3: 15000,          // 每 3 条休息
  restBetweenSegments: 25000, // 段间休息
};

// ============ 工具：规范化字符串（去空格）============
const norm = s => (s || '').replace(/\s+/g, '');

// ============ 工具：时间戳 ============
function stamp() {
  return new Date().toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit'
  }).replace(/\//g, '-');
}

// ============ 拉取待投队列 ============
async function fetchQueue() {
  const resp = await fetch.server(CONFIG.queueEndpoint, { method: 'GET' });
  const data = JSON.parse(String(resp));
  return data.queue || [];
}

// ============ 上报投递结果 ============
async function reportResults(results) {
  if (!results.length) return 0;
  const resp = await fetch.server(CONFIG.resultEndpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain' },
    body: JSON.stringify({ results })
  });
  const data = JSON.parse(String(resp));
  return data.recorded || 0;
}

// ============ 投递单条 ============
async function applyOne(job) {
  await page.goto(job.job_url);
  await page.waitForSelector('.summary-planes__title, h1', { timeout: 15000 })
    .catch(() => null);
  await sleep(2200);

  const info = await page.evaluate(() => {
    const titleEl = document.querySelector('.summary-planes__title, h1');
    const compEl = document.querySelector(
      '.company-summary__name-row, .company-info__header-left'
    );
    return {
      title: titleEl ? titleEl.textContent : '',
      company: compEl ? compEl.textContent.replace(/\s+/g, ' ').trim() : '',
      captcha: !!(document.querySelector('.geetest_wrapper, #captcha, iframe[src*="captcha"]')),
      hasApply: [...document.querySelectorAll('button')].some(
        b => b.textContent.trim() === '立即投递'
          && !String(b.className).includes('deliver-greeting-modal')
      ),
      hasChat: [...document.querySelectorAll('button')].some(
        b => /继续沟通/.test(b.textContent)
          && !String(b.className).includes('deliver-greeting-modal')
      )
    };
  });

  // 验证码
  if (info.captcha) return { status: 'captcha' };

  // 双重校验
  const titleOk = norm(info.title).includes(norm(job.title).slice(0, 10))
               || norm(job.title).includes(norm(info.title).slice(0, 10));
  const companyOk = norm(info.company).includes(norm(job.company).slice(0, 8))
                 || norm(job.company).includes(norm(info.company).slice(0, 8));

  if (!titleOk || !companyOk) return { status: 'mismatch' };
  if (!info.hasApply && info.hasChat) return { status: 'already_contacted' };
  if (!info.hasApply) return { status: 'no_button' };

  // 点击投递
  await page.evaluate(() => {
    const btn = [...document.querySelectorAll('button')].find(
      b => b.textContent.trim() === '立即投递'
        && !String(b.className).includes('deliver-greeting-modal')
    );
    if (btn) {
      btn.scrollIntoView({ block: 'center' });
      btn.click();
    }
  });
  await sleep(2800);

  // 检测确认弹窗 / 登录失效
  const cs = await page.evaluate(() => {
    const t = document.body.textContent;
    return {
      sent: /已向对方发送/.test(t),
      login: /扫码登录|密码登录|验证码登录|账号密码登录/.test(t)
    };
  });

  if (cs.login && !cs.sent) return { status: 'login_expired' };

  if (cs.sent) {
    // 关闭弹窗
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('button, a')]
        .find(x => x.textContent.trim() === '留在此页');
      if (b) b.click();
    });
    await sleep(800);
    return { status: 'applied' };
  }

  return { status: 'not_confirmed' };
}

// ============ 批量投递（一段）============
async function runSegment() {
  const T0 = Date.now();
  const queue = await fetchQueue();

  if (!queue.length) {
    return { done: true, queue: 0 };
  }

  const results = [];
  let appliedCount = 0;
  let captchaHit = false;
  let loginHit = false;

  for (const job of queue) {
    if (appliedCount >= CONFIG.maxAppsPerSegment) break;
    if (Date.now() - T0 > CONFIG.segmentBudget) break;

    try {
      const r = await applyOne(job);

      if (r.status === 'captcha') { captchaHit = true; break; }
      if (r.status === 'login_expired') { loginHit = true; break; }

      const statusMap = {
        applied: '已投递',
        mismatch: '核对不一致跳过',
        already_contacted: '已沟通过跳过',
        no_button: '无投递按钮跳过',
        not_confirmed: '弹窗未确认',
      };

      results.push({
        time: stamp(),
        title: job.title,
        salary: job.salary,
        city: job.city,
        company: job.company,
        result: statusMap[r.status] || r.status
      });

      if (r.status === 'applied') appliedCount++;

      // 每 3 条休息
      if (appliedCount === 3 && Date.now() - T0 < CONFIG.segmentBudget - 20000) {
        await sleep(CONFIG.restAfter3);
      }
    } catch (e) {
      results.push({
        time: stamp(),
        title: job.title,
        salary: job.salary,
        city: job.city,
        company: job.company,
        result: '异常:' + String(e).slice(0, 40)
      });
    }
  }

  const recorded = await reportResults(results);

  return {
    applied: appliedCount,
    recorded,
    remaining: Math.max(0, queue.length - appliedCount),
    captcha: captchaHit,
    needLogin: loginHit,
    results: results.map(r => r.title.slice(0, 20) + '→' + r.result.slice(0, 12))
  };
}

// ============ 导出 ============
module.exports = {
  CONFIG, fetchQueue, reportResults, applyOne, runSegment, stamp, norm
};
