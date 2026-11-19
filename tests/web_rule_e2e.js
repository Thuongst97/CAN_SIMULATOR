// Browser test of the auto response editor in web mode (tests/test_web.py::test_browser_rule_editor_value_tables)
const { chromium } = require('playwright');
(async () => {
  const OUT = process.argv[2], URL = process.argv[3];
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 900 } });
  const errors = [];
  p.on('pageerror', (e) => errors.push(e.message));
  await p.goto(URL);
  await p.waitForSelector('#db-tree .node');
  await p.click('.rail-btn[data-desk=simulation]');
  await p.click('#ar-add');
  await p.selectOption('[data-pick=rx]', 'EngineData');
  await p.selectOption('[data-f=match]', 'signal');
  await p.selectOption('[data-f=signal]', 'Gear');
  const vis = await p.isVisible('[data-vpick]');
  const opts = await p.$$eval('[data-vpick] option', (o) => o.map((x) => x.textContent));
  await p.selectOption('[data-vpick]', '2');
  const condVal = await p.inputValue('[data-f=value]');
  await p.selectOption('[data-f=signal]', 'Speed');
  const info = await p.textContent('[data-vinfo]');
  await p.selectOption('[data-f=signal]', 'Gear');
  await p.selectOption('[data-pick=tx]', 'EngineData');
  await p.selectOption('[data-f=tx_mode]', 'signal');
  await p.selectOption('[data-tsp="Gear"]', '3');
  const gearVal = await p.inputValue('[data-ts="Gear"]');
  await p.fill('[data-ts="Speed"]', '88');
  const unit = await p.textContent('[data-sigs] tbody tr:nth-child(1) td:nth-child(3)');
  const rng = await p.textContent('[data-sigs] tbody tr:nth-child(1) td:nth-child(4)');
  await p.screenshot({ path: OUT + '/rule_editor.png' });
  await p.click('.modal footer .btn.primary');
  await p.waitForTimeout(500);
  const rule = await p.evaluate(() => S.auto_response.rules[0]);
  console.log(JSON.stringify({ vis, opts, condVal, info, gearVal, unit, rng, signal: rule.signal, value: rule.value,
    tx_signals: rule.tx_signals, condition: rule.condition }));
  console.log('ERRORS', JSON.stringify(errors));
  await b.close();
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
