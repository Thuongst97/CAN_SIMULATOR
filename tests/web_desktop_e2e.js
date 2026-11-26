// Desktop-app features of the web UI / Tauri app: measurement Start / Stop (pre-check, F9, armed Cyclic, Send),
// command palette, project and signal board files, Settings.
// Usage: node <this> OUT URL DBC   (server started with --start-stopped, empty data folder)
const fs = require('fs');
const { chromium } = require('playwright');
(async () => {
  const [OUT, URL, DBC] = process.argv.slice(2);
  const b = await chromium.launch();
  const ctx = await b.newContext({ acceptDownloads: true, viewport: { width: 1500, height: 900 } });
  const p = await ctx.newPage();
  const errors = [];
  p.on('pageerror', (e) => errors.push(e.message));
  const fail = (m) => { throw new Error(m); };
  const st = () => p.evaluate(() => ({ measuring: S.measuring, label: document.querySelector('#meas-label').textContent,
    status: document.querySelector('#st-meas').textContent }));
  const out = {};
  await p.goto(URL);
  await p.waitForFunction(() => typeof S !== 'undefined' && S);

  // 1. starts stopped (desktop app)
  out.start = await st();
  if (out.start.measuring || out.start.label !== 'Start' || !out.start.status.includes('Stopped')) fail('start ' + JSON.stringify(out.start));
  // 2. Start without a channel: the pre-check asks
  await p.click('#meas-btn');
  await p.waitForSelector('.modal header:text("No channel is open")');
  await p.screenshot({ path: OUT + '/desk_precheck.png' });
  await p.click('.modal footer .btn:text("Cancel")');
  if ((await st()).measuring) fail('cancel started');
  await p.click('#meas-btn');
  await p.click('.modal footer .btn:text("Start anyway")');
  await p.waitForFunction(() => S.measuring === true);
  out.running = await st();
  await p.keyboard.press('F9');                                             // F9 stops
  await p.waitForFunction(() => S.measuring === false);

  // 3. stopped: Cyclic is armed, Send disabled, nothing in the trace; Start sends
  await p.evaluate(async (dbc) => {
    await post('/api/databases/add', { path: dbc });
    await post('/api/connect', { port: 'SIM1', channel: 'CAN1' });
    const { ids } = await post('/api/tx/add', { messages: ['EngineData'] });
    await post('/api/tx/update', { id: ids[0], cycle: 20, enabled: true });
    await refreshState(); Trace.clear();
  }, DBC);
  await p.click('.rail-btn[data-desk=simulation]');
  await p.waitForTimeout(700);
  out.armed = await p.evaluate(() => ({ send: document.querySelector('#gen-body [data-send]').disabled,
    title: document.querySelector('#gen-body .cyc-chk').title, rows: Trace.total, checked: document.querySelector('#gen-body [data-cyclic]').checked,
    armed: getComputedStyle(document.querySelector('#gen-body .cyc-chk.on'), '::after').content }));
  if (!out.armed.send || out.armed.rows !== 0 || !out.armed.checked || out.armed.armed !== '"armed"') fail('armed ' + JSON.stringify(out.armed));
  await p.click('#meas-btn');                                               // channel open: no question
  await p.waitForFunction(() => S.measuring && Trace.total > 5, null, { timeout: 5000 });
  out.sendEnabled = await p.evaluate(() => !document.querySelector('#gen-body [data-send]').disabled);
  await p.screenshot({ path: OUT + '/desk_running.png' });

  // 4. command palette: Ctrl+K, filter, Enter -> Save signal board
  await p.keyboard.press('Control+k');
  await p.waitForSelector('.palette');
  out.paletteCount = await p.$$eval('.pal-item', (x) => x.length);
  await p.fill('#pal-q', 'save board');
  out.paletteHits = await p.$$eval('.pal-item .pal-label', (x) => x.map((e) => e.textContent));
  await p.screenshot({ path: OUT + '/desk_palette.png' });
  const [d1] = await Promise.all([p.waitForEvent('download'), p.keyboard.press('Enter')]);
  await d1.saveAs(OUT + '/board.json');
  const board = JSON.parse(fs.readFileSync(OUT + '/board.json', 'utf8'));
  out.board = { kind: board.kind, msgs: board.signals.map((s) => s.message), name: d1.suggestedFilename() };
  if (board.kind !== 'autosim-signal-board' || out.board.msgs[0] !== 'EngineData') fail('board ' + JSON.stringify(out.board));

  // 5. open the board again after removing the message
  await p.evaluate(async () => { await post('/api/tx/remove', { id: S.cards[0].id }); await refreshState(); });
  await p.click('#file-btn');
  out.fileMenu = await p.$$eval('.menu div', (x) => x.map((e) => e.textContent));
  const [ch] = await Promise.all([p.waitForEvent('filechooser'), p.click('.menu div:text-is("Open signal board…")')]);
  await ch.setFiles(OUT + '/board.json');
  await p.waitForFunction(() => S.cards.length === 1);
  out.boardBack = await p.evaluate(() => ({ msg: S.cards[0].message, cyclic: S.cards[0].enabled }));

  // 6. project: Ctrl+S downloads it, open it back
  const [d2] = await Promise.all([p.waitForEvent('download'), p.keyboard.press('Control+s')]);
  await d2.saveAs(OUT + '/project.json');
  const proj = JSON.parse(fs.readFileSync(OUT + '/project.json', 'utf8'));
  out.project = { dbs: proj.dbc_paths.length, nodes: proj.nodes.map((n) => `${n.port}:${n.channel_name}:${n.autoconnect}`), signals: proj.signals.length };
  await p.click('#file-btn');
  const [ch2] = await Promise.all([p.waitForEvent('filechooser'), p.click('.menu div:text-is("Open project…")')]);
  await ch2.setFiles(OUT + '/project.json');
  await p.waitForSelector('.toast:has-text("Project opened")');

  // 7. Settings: change, save, kept after a reload
  await p.keyboard.press('Control+,');
  await p.waitForSelector('.modal header:text("Settings")');
  await p.screenshot({ path: OUT + '/desk_settings.png' });
  await p.uncheck('#set-clear');
  await p.check('#set-stopfail');
  await p.selectOption('#set-mos', 'yes');
  await p.click('.modal footer .btn:text("Save")');
  await p.waitForFunction(() => S.prefs.stop_on_failure === true);
  await p.reload();
  await p.waitForFunction(() => typeof S !== 'undefined' && S);
  out.prefs = await p.evaluate(() => S.prefs);
  if (out.prefs.clear_on_start !== false || out.prefs.measure_on_start !== true) fail('prefs ' + JSON.stringify(out.prefs));
  await p.evaluate(() => post('/api/prefs', { clear_on_start: true, stop_on_failure: false, measure_on_start: null }));
  await p.evaluate(() => post('/api/measurement', { running: false }));

  console.log(JSON.stringify(out));
  console.log('ERRORS', JSON.stringify(errors));
  await b.close();
  if (errors.length) process.exit(1);
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
