// Bus trace with live traffic (the fixed view repaints ~10 times a second): a click on a message row opens /
// closes its signals every time, and a right-click menu closes on a click elsewhere.  Usage: node <this> OUT URL DBC
const { chromium } = require('playwright');
(async () => {
  const [OUT, URL, DBC] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  p.on('pageerror', (e) => errors.push(e.message));
  const fail = (m) => { throw new Error(m); };
  await p.goto(URL);
  await p.waitForFunction(() => typeof S !== 'undefined' && S);
  await p.evaluate(async (dbc) => {
    await post('/api/databases/add', { path: dbc });
    await post('/api/connect', { port: 'SIM1', channel: 'CAN1' });
    const { ids } = await post('/api/tx/add', { messages: ['EngineData'] });
    await post('/api/tx/generator', { id: ids[0], name: 'Speed', kind: 'ramp' });    // payload changes: rows flash
    await post('/api/tx/update', { id: ids[0], cycle: 10, enabled: true });
    if (!S.measuring) await post('/api/measurement', { running: true });
    await refreshState();
  }, DBC);
  await p.click('.rail-btn[data-desk=analysis]');
  const row = '#trace-vt tr[data-key][data-name="EngineData"]';
  await p.waitForSelector(row);
  const sigs = () => p.$$eval('#trace-vt tr.sig', (x) => x.length);
  const out = { clicks: [] };
  for (let i = 0; i < 6; i++) {                                  // open, close, open … on the name cell
    await p.click(`${row} td:nth-child(5)`);
    await p.waitForTimeout(250);
    out.clicks.push(await sigs());
  }
  if (out.clicks.join() !== '3,0,3,0,3,0') fail('row click ' + out.clicks);
  await p.click(`${row} td:nth-child(5)`);
  await p.waitForTimeout(300);
  await p.screenshot({ path: OUT + '/trace_click_open.png' });

  // right-click menu: a left click elsewhere (on the live trace, or on the toolbar) closes it
  await p.click(`${row} td:nth-child(7)`, { button: 'right' });
  out.menuOpen = await p.$$eval('.menu', (x) => x.length);
  await p.mouse.click(600, 600);                                 // empty part of the trace
  out.menuAfterTrace = await p.$$eval('.menu', (x) => x.length);
  await p.click(`${row} td:nth-child(3)`, { button: 'right' });
  await p.click('#t-name');
  out.menuAfterToolbar = await p.$$eval('.menu', (x) => x.length);
  await p.click(`${row} td:nth-child(3)`, { button: 'right' });
  await p.click(`${row} td:nth-child(1)`, { button: 'right' });   // right-click again (left of the menu): one menu
  out.menuAfterSecondRight = await p.$$eval('.menu', (x) => x.length);
  if (out.menuOpen !== 1 || out.menuAfterTrace !== 0 || out.menuAfterToolbar !== 0 || out.menuAfterSecondRight !== 1) fail('menu ' + JSON.stringify(out));

  await p.evaluate(() => post('/api/tx/stop_all'));
  console.log(JSON.stringify(out));
  console.log('ERRORS', JSON.stringify(errors));
  await b.close();
  if (errors.length) process.exit(1);
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
