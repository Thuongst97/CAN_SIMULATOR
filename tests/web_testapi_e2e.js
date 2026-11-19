// Web Test API tab: publish signals by double-click / Add API, a test system GETs and PUTs them, the call log
// and the live values follow. Usage: node <this> OUT URL
const { chromium } = require('playwright');
(async () => {
  const OUT = process.argv[2], URL = process.argv[3], base = URL.replace(/\/$/, '');
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 900 } });
  const errors = [];
  p.on('pageerror', (e) => errors.push(e.message));
  for (let i = 0; i < 40; i++) { try { await p.goto(URL); break; } catch (e) { await new Promise((r) => setTimeout(r, 250)); } }
  await p.waitForSelector('#db-tree .node');
  await p.evaluate(() => post('/api/connect', { port: 'SIM1' }));             // CAN1 open
  await p.click('.rail-btn[data-desk=testapi]');
  await p.waitForSelector('#ta-tree [data-tamsg="EngineData"]');
  await p.dblclick('#ta-tree [data-tamsg="EngineData"]');                     // open the message
  await p.dblclick('#ta-tree [data-tas="Speed"]');                            // publish Speed with defaults
  await p.waitForSelector('#ta-table tr[data-name="Speed"]');
  // Add API… for Gear: GET + SET, value picked from the value table
  await p.click('#ta-add');
  await p.selectOption('.modal [data-f=message]', 'EngineData');
  await p.selectOption('.modal [data-f=signal]', 'Gear');
  const initial = await p.inputValue('.modal [data-f=value]');
  const info = await p.textContent('.modal [data-f=info]');
  await p.fill('.modal [data-f=name]', 'GearPosition');
  await p.check('.modal input[name=ta-acc][value=getset]');
  await p.selectOption('.modal [data-f=pick]', '1');
  const url = await p.textContent('.modal [data-f=url]');
  await p.screenshot({ path: OUT + '/test_api_modal.png' });
  await p.click('.modal footer .btn.primary');
  await p.waitForSelector('#ta-table tr[data-name="GearPosition"]');
  // a test system calls the API (from Node, as an outside client would)
  const put = await fetch(base + '/api/v1/signals/GearPosition', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'D' }) }).then(async (r) => ({ status: r.status, body: await r.json() }));
  await new Promise((r) => setTimeout(r, 300));
  const get = await fetch(base + '/api/v1/signals/GearPosition').then((r) => r.json());
  const ro = await fetch(base + '/api/v1/signals/Speed', { method: 'PUT', body: '{"value": 1}' }).then((r) => r.status);
  const all = await fetch(base + '/api/v1/signals').then((r) => r.json());
  await p.waitForFunction(() => document.querySelectorAll('#ta-log tbody tr').length >= 4);
  await p.waitForFunction(() => /\(D\)/.test(document.querySelector('#ta-table tr[data-name="GearPosition"] [data-f=value]').textContent));
  const row = await p.$$eval('#ta-table tr[data-name="GearPosition"] td', (x) => x.map((e) => e.textContent.trim()));
  const log = await p.$$eval('#ta-log tbody tr', (x) => x.map((r) => [...r.children].map((c) => c.textContent.trim())));
  await p.screenshot({ path: OUT + '/test_api.png' });
  console.log(JSON.stringify({ initial, info, url, put, get, ro, all: Object.keys(all.signals), row, log }));
  console.log('ERRORS', JSON.stringify(errors));
  await b.close();
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
