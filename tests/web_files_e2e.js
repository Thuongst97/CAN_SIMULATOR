// Web UI file dialogs and downloads, as a user does them: Add database (file chooser), rules import / export,
// Test API import / export, trace export. Usage: node <this> OUT URL DBC RULES_JSON
const fs = require('fs');
const { chromium } = require('playwright');
(async () => {
  const [OUT, URL, DBC, RULES] = process.argv.slice(2);
  const b = await chromium.launch();
  const ctx = await b.newContext({ acceptDownloads: true, viewport: { width: 1500, height: 850 } });
  const p = await ctx.newPage();
  const errors = [];
  p.on('pageerror', (e) => errors.push(e.message));
  await p.goto(URL);
  await p.waitForSelector('#db-body [data-add]');
  const out = {};
  const choose = async (clickSel, files) => {
    const [ch] = await Promise.all([p.waitForEvent('filechooser', { timeout: 5000 }), p.click(clickSel)]);
    out.multiple = ch.isMultiple();
    await ch.setFiles(files);
  };
  const menuItem = (label) => p.click(`.menu div:text-is("${label}")`);
  const dl = async (action) => {
    const [d] = await Promise.all([p.waitForEvent('download', { timeout: 5000 }), action()]);
    const f = OUT + '/' + d.suggestedFilename();
    await d.saveAs(f);
    return { name: d.suggestedFilename(), size: fs.statSync(f).size, text: fs.readFileSync(f, 'utf8').slice(0, 200) };
  };

  // 1. Add database… (empty state button) -> file chooser -> tree
  await choose('#db-body [data-add]', [DBC]);
  await p.waitForSelector('#db-tree .node b');
  out.tree = await p.$$eval('#db-tree .node b', (x) => x.map((e) => e.textContent));
  out.inputsLeft = await p.$$eval('input[type=file]', (x) => x.length);          // removed after use

  // 2. Simulation > Auto response > More > Import rules… / Export rules…
  await p.click('.rail-btn[data-desk=simulation]');
  await p.click('#ar-more');
  await choose('.menu div:text-is("Import rules…")', [RULES]);
  await p.waitForFunction(() => S && S.auto_response.rules.length > 0);
  out.rules = await p.evaluate(() => S.auto_response.rules.length);
  await p.click('#ar-more');
  out.rulesExport = await dl(() => menuItem('Export rules…'));

  // 3. Test API: add one, export, import
  await p.evaluate(() => post('/api/testapi/add', { message: 'EngineData', signal: 'Speed' }));
  await p.click('.rail-btn[data-desk=testapi]');
  await p.click('#ta-more');
  out.apiExport = await dl(() => menuItem('Export APIs (JSON)…'));
  await p.evaluate(() => post('/api/testapi/remove', { name: 'Speed' }).then(() => TestApi.load()));
  await p.click('#ta-more');
  await choose('.menu div:text-is("Import APIs (JSON)…")', [OUT + '/' + out.apiExport.name]);
  await p.waitForSelector('#ta-table tr[data-name="Speed"]');
  out.apiImported = await p.$$eval('#ta-table tr[data-name]', (x) => x.map((r) => r.dataset.name));

  // 4. Analysis > Bus trace > Export ▾ > CSV
  await p.click('.rail-btn[data-desk=analysis]');
  await p.click('#t-export');
  out.traceExport = await dl(() => menuItem('CSV (.csv)'));

  console.log(JSON.stringify(out));
  console.log('ERRORS', JSON.stringify(errors));
  await b.close();
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
