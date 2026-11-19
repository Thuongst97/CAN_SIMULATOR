// Web Bus trace name filter: Speed|Gear|Status, wildcards, exclusions, recent filters. Usage: node <this> OUT URL
const { chromium } = require('playwright');
(async () => {
  const OUT = process.argv[2], URL = process.argv[3];
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1500, height: 800 } });
  const errors = [];
  p.on('pageerror', (e) => errors.push(e.message));
  await p.goto(URL);
  await p.waitForSelector('#db-tree .node');
  await p.click('.rail-btn[data-desk=analysis]');
  await p.evaluate(() => {
    const row = (t, id, name, hex) => [t, 'CAN1', 1, id, false, name, false, false, hex, null, 1];
    Trace.add([row(0.1, 0x100, 'EngineData', 'e3035a0300000000'), row(0.2, 0x200, 'Status', '01'),
      row(0.3, 0x300, '', '02')]);
    Trace.renderAll(true);
  });
  const names = () => p.$$eval('#trace-vt tbody tr:not(.sig) td:nth-child(5)', (x) => x.map((e) => e.textContent));
  const sigs = () => p.$$eval('#trace-vt tbody tr.sig td:nth-child(5)', (x) => x.map((e) => e.textContent));
  const out = {};
  await p.fill('#t-name', 'Speed|Gear|Status');
  await p.waitForFunction(() => document.querySelectorAll('#trace-vt tbody tr.sig').length === 3);
  out.both = await names(); out.bothSigs = await sigs();
  await p.screenshot({ path: OUT + '/trace_filter.png' });
  await p.press('#t-name', 'Enter');                                   // kept in the recent list
  await p.fill('#t-name', '!Temp|!Status');
  out.excl = await names();
  await p.fill('#t-name', 'eng*');
  out.wild = await names();
  await p.press('#t-name', 'Enter');
  await p.fill('#t-name', '');
  out.all = await names();
  await p.click('#t-recent');
  out.recent = await p.$$eval('.menu div', (x) => x.map((e) => e.textContent));
  await p.click('.menu div:nth-child(2)');                             // Speed|Gear|Status back
  out.restored = await p.inputValue('#t-name');
  out.stored = await p.evaluate(() => Recent.load());
  console.log(JSON.stringify(out));
  console.log('ERRORS', JSON.stringify(errors));
  await b.close();
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
