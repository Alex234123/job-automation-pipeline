// ============================================================
// zhaopin_collector.js — 智联招聘采集器（页面内驻留脚本）
//
// 注入到搜索页后常驻运行，逐关键词采集：
//   切换关键词 → 滚动加载 → 逐卡点击 → 读详情 → 批量上报
//
// 关键设计：
//   1. 防污染：只采标题含当前关键词的卡片（智联会插入推荐广告卡）
//   2. 不刷新：关键词切换用输入框+Enter，避免页面重载丢状态
//   3. 分段上报：每 5 条 flush 一次，规避单次请求过大
//   4. 驻留运行：fire-and-forget 启动，外部轮询 __progress()
// ============================================================

(function () {
  if (window.__collectorRunning) return 'already running';
  window.__collectorRunning = true;

  // ============ 配置 ============
  const CONFIG = {
    keywords: [],                    // 由外部注入
    cityCode: '',                    // 城市代码
    batchName: '',                   // 批次名（如「长沙」）
    specialArea: '',                 // 特殊区域筛选（如「萧山」）
    collectEndpoint: 'http://127.0.0.1:18765/collect',
    flushSize: 5,                    // 每 N 条上报一次
    scrollRounds: 12,                // 最多滚动轮数
    scrollWait: 1600,                // 每轮滚动等待 ms
    cardClickWait: 1300,             // 点卡后等待详情加载 ms
  };

  const state = {
    kwIdx: 0,
    cardsSeen: 0,
    cardsMatched: 0,
    totalSaved: 0,
    phase: 'init',
    err: ''
  };

  function sleep(ms) {
    return new Promise(function (r) { setTimeout(r, ms); });
  }

  function getCards() {
    return Array.prototype.slice.call(document.querySelectorAll('.job-card'));
  }

  function cardTitle(c) {
    const t = c.querySelector('.vue-clamp__text');
    return t ? t.textContent.trim() : '';
  }

  // ============ 切换关键词（不刷新页面）============
  async function switchKeyword(kw) {
    const box = document.querySelector('.query-sug__input');
    const btn = document.querySelector('.query-sug__button');
    if (!box) return false;

    box.focus();
    box.value = kw;
    box.dispatchEvent(new Event('input', { bubbles: true }));
    await sleep(500);

    if (btn) {
      btn.click();
    } else {
      box.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
      }));
    }
    await sleep(3000);
    return true;
  }

  // ============ 滚动加载全部卡片 ============
  async function scrollToEnd() {
    let last = 0;
    let same = 0;
    for (let i = 0; i < CONFIG.scrollRounds; i++) {
      window.scrollTo(0, document.body.scrollHeight);
      await sleep(CONFIG.scrollWait);
      const n = getCards().length;
      if (n === last) {
        same++;
        if (same >= 2) break;   // 连续两轮无增长，视为到底
      } else {
        same = 0;
      }
      last = n;
    }
    return last;
  }

  // ============ 提取详情面板数据 ============
  function extractDetail() {
    const p = document.querySelector('.job-detail-summary');
    if (!p) return null;

    const q = function (sel) {
      const e = p.querySelector(sel);
      return e ? e.textContent.replace(/\s+/g, ' ').trim() : '';
    };

    const tags = Array.prototype.map.call(
      p.querySelectorAll('.job-detail-summary__tag'),
      function (t) { return t.textContent.trim(); }
    );

    const jdEl = document.querySelector('.describtion-card__detail-content')
              || document.querySelector('.job-description');

    // 尝试从当前选中卡取 jobdetail 链接
    let url = '';
    try {
      const active = document.querySelector(
        '.job-card--active [href*="jobdetail"], .job-card--active a'
      );
      if (active) url = active.href;
    } catch (e) { /* ignore */ }

    return {
      title: q('.job-detail-summary__title-text'),
      salary: q('.job-detail-summary__salary'),
      city_area: tags[0] || '',
      experience: tags[1] || '',
      education: tags[2] || '',
      company: q('.job-detail-summary__company-name'),
      jd: jdEl ? jdEl.textContent.replace(/\s+/g, ' ').trim() : '',
      job_url: url
    };
  }

  // ============ 批量上报 ============
  async function flush(batch, kw) {
    try {
      const resp = await fetch(CONFIG.collectEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify({ results: batch, kw: kw })
      });
      const j = await resp.json();
      state.totalSaved += j.saved || 0;
    } catch (e) {
      state.err = 'flush: ' + e;
    }
  }

  // ============ 采集单个关键词 ============
  async function collectKeyword(kw) {
    const ok = await switchKeyword(kw);
    if (!ok) {
      state.err = 'searchbox not found';
      return;
    }
    await sleep(2000);

    await scrollToEnd();

    const cards = getCards();
    state.cardsSeen += cards.length;

    const batch = [];
    for (let i = 0; i < cards.length; i++) {
      const c = cards[i];
      const t = cardTitle(c);

      // 防污染核心：标题必须含当前关键词
      // 智联搜索列表会插入「为你推荐」广告卡，标题与关键词无关
      if (t.indexOf(kw) < 0) continue;

      state.cardsMatched++;

      try { c.scrollIntoView({ block: 'center' }); } catch (e) { /* ignore */ }
      await sleep(250);

      try { c.click(); } catch (e) { continue; }
      await sleep(CONFIG.cardClickWait);

      const d = extractDetail();
      if (!d || !d.title || !d.company || !d.jd) continue;
      // 详情标题再验一次，防止点错卡
      if (d.title.indexOf(kw) < 0) continue;

      d.kw = kw;
      d.batch = (CONFIG.specialArea && d.city_area.indexOf(CONFIG.specialArea) >= 0)
        ? CONFIG.specialArea
        : CONFIG.batchName;
      d.time = new Date().toISOString().slice(0, 19);

      batch.push(d);

      if (batch.length >= CONFIG.flushSize) {
        await flush(batch.slice(), kw);
        batch.length = 0;
      }
    }

    if (batch.length) await flush(batch, kw);

    // 标记关键词完成
    try {
      await fetch(CONFIG.collectEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify({ results: [], kw: kw, complete: true })
      });
    } catch (e) { /* ignore */ }

    state.kwIdx++;
  }

  // ============ 启动入口（fire-and-forget）============
  window.__startCollector = function (config) {
    if (config) Object.assign(CONFIG, config);
    (async function () {
      for (const kw of CONFIG.keywords) {
        state.phase = 'collect:' + kw;
        await collectKeyword(kw);
        await sleep(2000);
      }
      state.phase = 'done';
      window.__collectorRunning = false;
    })();
    return 'started';
  };

  window.__collectorProgress = function () {
    return JSON.stringify(state);
  };

  return 'injected';
})();
