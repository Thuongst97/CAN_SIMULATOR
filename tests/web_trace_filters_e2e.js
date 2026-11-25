// Web Bus trace filters (logcat-viewer style): Mark / Show only / Hide by CAN ID or name, on / off, order, all off,
// kept after a reload, right-click a row to add one, export. Usage: node <this> OUT URL   (server with tests/data/sample.dbc)
const { chromium } = require('playwright');
(async () => {
  const [OUT, URL] = process.argv.slice(2);
  const b = await chromium.launch();
  const ctx = await b.newContext({ acceptDownloads: true, viewport: { width: 1500, height: 800 } });
  const p = await ctx.newPage();
  const errors = [];
  p.on('pageerror', (e) => errors.push(e.message));
  const fail = (m) => { throw new Error(m); };
  const feed = () => p.evaluate(() => {
    const row = (t, id, name, hex) => [t, 'CAN1', 1, id, false, name, false, false, hex, null, 1];
    Trace.add([row(0.1, 0x100, 'EngineData', 'e3035a0300000000'), row(0.2, 0x200, 'Status', '01'), row(0.3, 0x300, '', '02')]);
    Trace.renderAll(true);
  });
  const rows = () => p.$$eval('#trace-vt tbody tr[data-id]', (x) => x.map((r) => ({ id: +r.dataset.id, mark: r.style.getPropertyValue('--mark') })));
  const ids = async () => (await rows()).map((r) => r.id.toString(16));
  const addFilter = async (by, pattern, mode, name = '') => {
    await p.click('#tf-add');
    await p.click(`#tf-by [data-v=${by}]`);
    await p.fill('#tf-pat', pattern);
    await p.click(`#tf-mode [data-v=${mode}]`);
    if (name) await p.fill('#tf-nm', name);
    await p.click('.modal footer .primary');
  };
  await p.goto(URL);
  await p.evaluate(() => localStorage.removeItem('autosim.trace.filters'));
  await p.reload();
  await p.waitForSelector('#db-tree .node', { state: 'attached' });
  await p.click('.rail-btn[data-desk=analysis]');
  await feed();
  const out = {};
  out.start = await ids();
  if (out.start.join() !== '100,200,300') fail('start ' + out.start);

  await p.click('#t-filters');                                              // panel opens, empty
  out.empty = (await p.textContent('#tf-list')).includes('No filter yet');

  await addFilter('id', '100', 'mark', 'Engine');                           // Mark by ID
  let r = await rows();
  out.mark = r.map((x) => x.mark || '-');
  if (!r[0].mark || r[1].mark || r.length !== 3) fail('mark ' + JSON.stringify(r));

  await addFilter('name', 'Gear', 'only');                                  // Show only by signal name: EngineData has Gear
  out.onlyGear = await ids();
  if (out.onlyGear.join() !== '100') fail('only ' + out.onlyGear);
  await addFilter('id', '200-2FF', 'only');                                 // a second Show only: any of them
  out.onlyTwo = await ids();
  if (out.onlyTwo.join() !== '100,200') fail('only two ' + out.onlyTwo);
  await addFilter('name', 'Status', 'hide');                                // Hide wins over Show only
  out.hide = await ids();
  if (out.hide.join() !== '100') fail('hide ' + out.hide);

  await p.click('.tf-row[data-i="3"] [data-en]');                           // Hide off
  out.hideOff = await ids();
  if (out.hideOff.join() !== '100,200') fail('hide off ' + out.hideOff);
  await p.click('#tf-on');                                                  // all filters off
  out.allOff = await ids();
  if (out.allOff.join() !== '100,200,300' || (await rows()).some((x) => x.mark)) fail('all off');
  await p.click('#tf-on');
  out.label = await p.textContent('#t-filters');                            // "Filters (3/4)"
  out.panel = await p.$$eval('.tf-row', (x) => x.map((e) => e.querySelector('.tf-name').textContent + ' · ' + e.querySelector('.tf-sub').textContent.trim()));
  await p.screenshot({ path: OUT + '/trace_filters.png' });

  await p.reload();                                                         // kept in this browser
  await p.waitForSelector('#db-tree .node', { state: 'attached' });
  await p.click('.rail-btn[data-desk=analysis]');
  await feed();
  out.afterReload = { label: await p.textContent('#t-filters'), ids: await ids() };
  if (out.afterReload.ids.join() !== '100,200') fail('reload ' + JSON.stringify(out.afterReload));

  await p.click('#tf-more');                                                // remove all, then add from a row
  await p.click('.menu div:text-is("Remove all filters")');
  await p.click('#trace-vt tr[data-id="768"]', { button: 'right' });
  out.rowMenu = await p.$$eval('.menu div', (x) => x.map((e) => e.textContent));
  await p.click('.menu div:text-is("Hide  ID 0x300")');
  out.fromRow = await ids();
  if (out.fromRow.join() !== '100,200') fail('from row ' + out.fromRow);
  await p.click('#trace-vt tr[data-id="512"]', { button: 'right' });
  await p.click('.menu div:text-is("Mark  Status")');
  r = await rows();
  if (!r[1].mark || r[0].mark) fail('mark from row ' + JSON.stringify(r));
  out.colors = [...new Set(r.map((x) => x.mark).filter(Boolean))];

  await p.click('#tf-more');                                                // export
  const [d] = await Promise.all([p.waitForEvent('download'), p.click('.menu div:text-is("Export filters…")')]);
  await d.saveAs(OUT + '/filters.json');
  out.export = JSON.parse(require('fs').readFileSync(OUT + '/filters.json', 'utf8')).filters.map((f) => `${f.mode}:${f.by}:${f.pattern}`);

  await p.click('.rail-btn[data-desk=simulation]');                         // the Simulation trace uses them too
  await p.waitForTimeout(300);
  out.mini = await p.$$eval('#mini-vt tbody tr[data-id]', (x) => x.map((e) => (+e.dataset.id).toString(16) + (e.classList.contains('tf-mark') ? '*' : '')));

  console.log(JSON.stringify(out));
  console.log('ERRORS', JSON.stringify(errors));
  await b.close();
  if (errors.length) process.exit(1);
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
