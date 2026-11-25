// Benchmark of the web UI (what the Tauri app shows): database load and a heavy bus trace, without hardware (SIM1).
//   node tools/perf/bench_web.js URL SERVER_PID DBC [MESSAGES=50] [SECONDS=8] [CYCLE=10]
// Same workload and the same measures as tools/perf/bench_qt.py:
//   db_load: until the database tree is shown (the page's main thread busy meanwhile: frozen_ms = longest task)
//   per view (analysis fixed / chronological, simulation): page main-thread busy % (cpu_pct), frames painted per
//   second (fps), event-loop latency of a 10 ms timer (lag_ms), trace rows received per second, backend CPU %.
// Chromium here stands for WebView2 (Windows: Chromium); prints one JSON line.
const fs = require('fs');
const { chromium } = require('playwright');
const [URL, PID, DBC, MSGS = '50', SECS = '8', CYCLE = '10'] = process.argv.slice(2);
const ticks = () => { const f = fs.readFileSync(`/proc/${PID}/stat`, 'utf8').split(') ')[1].split(' '); return (+f[11] + +f[12]) / 100; };

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
  const cdp = await p.context().newCDPSession(p);
  await cdp.send('Performance.enable');
  const busy = async () => (await cdp.send('Performance.getMetrics')).metrics.find((m) => m.name === 'TaskDuration').value;
  const errors = [];
  p.on('pageerror', (e) => errors.push(e.message));
  await p.goto(URL);
  await p.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
  await p.reload();
  await p.waitForFunction(() => typeof S !== 'undefined' && S);
  // probes: 10 ms timer lateness, painted frames, longest task, rows received
  await p.evaluate(() => {
    window.B = { lags: [], frames: 0, longest: 0, rows: 0 };
    let last = performance.now();
    setInterval(() => { const n = performance.now(); B.lags.push(Math.max(0, n - last - 10)); last = n; }, 10);
    const raf = () => { B.frames++; requestAnimationFrame(raf); }; requestAnimationFrame(raf);
    new PerformanceObserver((l) => { for (const e of l.getEntries()) B.longest = Math.max(B.longest, e.duration); }).observe({ entryTypes: ['longtask'] });
    const add = Trace.add.bind(Trace); Trace.add = (rows, i) => { B.rows += rows.length; return add(rows, i); };
  });
  const reset = () => p.evaluate(() => { B.lags = []; B.frames = 0; B.longest = 0; B.rows = 0; });
  const lagStats = () => p.evaluate(() => {
    const s = [...B.lags].sort((a, b) => a - b); if (!s.length) s.push(0);
    const mean = s.reduce((a, b) => a + b, 0) / s.length;
    return { mean: +mean.toFixed(2), p95: +s[Math.max(0, Math.floor(s.length * 0.95) - 1)].toFixed(1), max: +s[s.length - 1].toFixed(1) };
  });
  const res = { ui: 'web', messages: +MSGS, cycle_ms: +CYCLE };

  // 1. database load
  await reset();
  let t0 = Date.now();
  await p.evaluate((path) => Database.addPaths([path]), DBC);
  await p.waitForFunction(() => DB.length > 0 && document.querySelectorAll('#db-tree .node').length > 1000, null, { timeout: 120000 });
  res.db_load = { until_shown_ms: Date.now() - t0, frozen_ms: Math.round(await p.evaluate(() => B.longest)),
    messages: await p.evaluate(() => DB.reduce((n, d) => n + d.messages.length, 0)) };
  await p.waitForTimeout(1500);
  res.db_load.lag_after_ms = await lagStats();
  // close and open again the Messages folder (1500 rows), and search "Torque" (24 000 signals)
  const folder = () => p.evaluate(() => [...document.querySelectorAll('#db-tree .node')].find((x) => /Messages \(/.test(x.textContent)).querySelector('.tw').click());
  await folder();
  t0 = Date.now(); await folder();
  await p.waitForFunction(() => document.querySelectorAll('#db-tree .node').length > 1000);
  res.db_tree_open_ms = Date.now() - t0;
  await reset(); t0 = Date.now();
  await p.fill('#db-search', 'Sig03_Torque');                           // a hit in each of the 1500 messages
  await p.waitForFunction(() => Database.search === 'sig03_torque');
  res.db_search_ms = Date.now() - t0;                                     // (includes the 150 ms typing pause)
  res.db_search_longest_task_ms = Math.round(await p.evaluate(() => B.longest));
  await p.fill('#db-search', '');
  await p.waitForFunction(() => Database.search === '');

  // 2. traffic: MSGS messages on SIM1, every CYCLE ms
  t0 = Date.now();
  await p.evaluate(async ([n, cycle]) => {
    await post('/api/connect', { port: 'SIM1', channel: 'CAN1' });
    const names = DB[0].messages.slice(0, n).map((m) => m.name);
    const { ids } = await post('/api/tx/add', { messages: names, channel: 'CAN1' });
    for (const id of ids) await post('/api/tx/update', { id, cycle, enabled: true });
    await refreshState();
  }, [+MSGS, +CYCLE]);
  res.add_messages_ms = Date.now() - t0;
  await p.waitForTimeout(2000);

  const phases = [['analysis', true], ['analysis', false], ['simulation', true]];
  for (const [desk, fixed] of phases) {
    await p.click(`.rail-btn[data-desk=${desk}]`);
    if (desk === 'analysis' && (await p.evaluate(() => Trace.fixedMode)) !== fixed) await p.click('#t-fixed');
    await p.waitForTimeout(800);
    await reset();
    const b0 = await busy(), c0 = ticks(), w0 = Date.now();
    await p.waitForTimeout(+SECS * 1000);
    const dt = (Date.now() - w0) / 1000;
    res[`${desk}_${fixed ? 'fixed' : 'chronological'}`] = {
      cpu_pct: +((await busy() - b0) / dt * 100).toFixed(1),
      fps: +((await p.evaluate(() => B.frames)) / dt).toFixed(1),
      lag_ms: await lagStats(),
      longest_task_ms: Math.round(await p.evaluate(() => B.longest)),
      rows_per_s: Math.round((await p.evaluate(() => B.rows)) / dt),
      backend_cpu_pct: +((ticks() - c0) / dt * 100).toFixed(1),
    };
  }
  await p.evaluate(() => post('/api/tx/stop_all'));
  res.errors = errors;
  console.log(JSON.stringify(res));
  await b.close();
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
