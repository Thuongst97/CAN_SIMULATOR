// Loading a large database shows what happens (overlay with the file, the step, a progress bar, the time; status
// bar) and the buttons wait; by path (desktop app) and by upload (browser, with the upload percentage).
// Usage: node <this> OUT URL BIG_DBC      (BIG_DBC: python tools/perf/make_big_dbc.py big.dbc)
const { chromium } = require('playwright');
(async () => {
  const [OUT, URL, BIG] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  p.on('pageerror', (e) => errors.push(e.message));
  const fail = (m) => { throw new Error(m); };
  await p.goto(URL);
  await p.waitForFunction(() => typeof S !== 'undefined' && S);
  const out = {};

  // 1. by path: the overlay shows at once and stays until the tree is built
  const run = p.evaluate((path) => Database.addPaths([path]), BIG);
  await p.waitForSelector('#db-loading', { timeout: 2000 });
  await p.waitForTimeout(400);
  out.path = await p.evaluate(() => ({ title: document.querySelector('.dl-title').textContent, phase: document.querySelector('.dl-phase').textContent,
    time: document.querySelector('.dl-time').textContent, status: document.querySelector('#st-busy').textContent,
    indeterminate: document.querySelector('.dl-bar').classList.contains('indet') }));
  await p.screenshot({ path: OUT + '/db_loading.png' });
  await run;
  out.pathDone = await p.evaluate(() => ({ overlay: !!document.querySelector('#db-loading'), status: document.querySelector('#st-busy').textContent,
    nodes: document.querySelectorAll('#db-tree .node').length, toast: [...document.querySelectorAll('.toast')].map((t) => t.textContent).pop() }));
  if (!out.path.title.includes('big') || out.pathDone.overlay || out.pathDone.status || out.pathDone.nodes < 1000) fail('path ' + JSON.stringify(out));
  if (!/Added big\.dbc in [\d.]+ s/.test(out.pathDone.toast)) fail('toast ' + out.pathDone.toast);

  // 2. a second load while one runs is refused politely
  const r1 = p.evaluate((path) => Database.reload(path), BIG);
  await p.waitForSelector('#db-loading');
  out.busy = await p.evaluate((path) => Database.addPaths([path]).then(() => [...document.querySelectorAll('.toast')].map((t) => t.textContent).pop()), BIG);
  out.reloadTitle = await p.$eval('.dl-title', (e) => e.textContent).catch(() => '');
  await r1;

  // 3. by upload (browser): percentage, then "Reading and checking"
  await p.evaluate(() => post('/api/databases/remove', { path: DB[0].path }).then(refreshState));
  const phases = [];
  await p.exposeFunction('notePhase', (t) => phases.push(t));
  await p.evaluate(() => new MutationObserver(() => { const e = document.querySelector('.dl-phase'); if (e) notePhase(e.textContent); })
    .observe(document.body, { subtree: true, childList: true, characterData: true }));
  const [ch] = await Promise.all([p.waitForEvent('filechooser'), p.click('#db-body [data-add]')]);
  await ch.setFiles(BIG);
  await p.waitForFunction(() => !document.querySelector('#db-loading') && DB.length === 1, null, { timeout: 60000 });
  out.uploadPhases = [...new Set(phases)].filter(Boolean).slice(0, 6);
  if (!out.uploadPhases.some((x) => x.startsWith('Uploading')) || !out.uploadPhases.some((x) => x.startsWith('Reading'))) fail('upload ' + JSON.stringify(out.uploadPhases));

  console.log(JSON.stringify(out));
  console.log('ERRORS', JSON.stringify(errors));
  await b.close();
  if (errors.length) process.exit(1);
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
