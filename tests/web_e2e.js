// Browser test of web mode (run by tests/test_web.py::test_browser_end_to_end): clicks through the UI like a user.
// node tests/web_e2e.js <screenshot dir> <url>
const { chromium } = require('playwright');
(async () => {
  const OUT = process.argv[2], URL = process.argv[3] || 'http://127.0.0.1:6789/';
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 900 } });
  const errors = [];
  p.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  p.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await p.goto(URL);
  await p.waitForSelector('#db-tree .node');
  await p.screenshot({ path: OUT + '/1_setup.png' });
  // connect SIM1 from the form
  await p.fill('#f-port', 'SIM1'); await p.dispatchEvent('#f-port', 'change');
  await p.click('#connect-btn');
  await p.waitForSelector('#chan-table tbody tr td.state-running', { timeout: 5000 });
  // select a database: properties show the Channel choice
  await p.click('#db-tree .node >> nth=0');
  await p.waitForSelector('#db-channel');
  await p.screenshot({ path: OUT + '/2_setup_connected.png' });
  // Simulation: add signals
  await p.click('.rail-btn[data-desk=simulation]');
  await p.click('#add-group');
  await p.fill('#grp-name', 'Powertrain');
  await p.check('[data-msg="EngineData"]');
  await p.check('[data-msg="HU_Request"]');
  await p.click('.modal footer .btn.primary');
  await p.waitForSelector('.msgcard[data-id]');
  const card = p.locator('.msgcard', { hasText: 'EngineData' });
  await card.locator('tr[data-sig="Speed"] [data-gen]').selectOption('sine');
  await p.waitForTimeout(400);
  await p.locator('.msgcard', { hasText: 'EngineData' }).locator('[data-cyclic]').check();
  await p.waitForTimeout(300);
  await p.locator('.msgcard', { hasText: 'HU_Request' }).locator('[data-cyclic]').check();
  // auto response rule through the editor
  await p.click('#ar-add');
  await p.selectOption('[data-pick=rx]', 'HU_Request');
  await p.selectOption('[data-f=match]', 'raw');
  await p.fill('[data-f=value]', '0');
  await p.selectOption('[data-pick=tx]', 'GW_Response');
  await p.fill('[data-f=tx_value]', 'rx+5');
  await p.click('.modal footer .btn.primary');
  await p.check('#ar-enabled');
  await p.waitForTimeout(2500);
  await p.screenshot({ path: OUT + '/3_simulation.png' });
  // Analysis: fixed trace, expand a message, chronological view
  await p.click('.rail-btn[data-desk=analysis]');
  await p.waitForTimeout(500);
  await p.locator('#trace-vt [data-exp]').first().click();
  await p.waitForTimeout(800);
  await p.screenshot({ path: OUT + '/4_analysis_fixed.png' });
  await p.click('#t-fixed');
  await p.waitForTimeout(800);
  await p.screenshot({ path: OUT + '/5_analysis_chrono.png' });
  const info = await p.evaluate(() => ({ total: Trace.total, fixed: Trace.fixed.size, rules: S.auto_response.rules.length,
    hits: S.auto_response.rules[0]?.hits, cards: S.cards.length }));
  console.log(JSON.stringify(info));
  await p.click('#theme-btn'); await p.click('.rail-btn[data-desk=simulation]'); await p.waitForTimeout(600);
  await p.screenshot({ path: OUT + '/6_simulation_dark.png' });
  await p.click('#log-btn'); await p.waitForTimeout(300);
  await p.screenshot({ path: OUT + '/7_log.png' });
  console.log('ERRORS', JSON.stringify(errors));
  await b.close();
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
