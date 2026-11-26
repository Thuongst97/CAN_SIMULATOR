// Interactive generator groups: Add group (name + messages), + adds to a group, fold / unfold (kept after a reload),
// rename, move, remove with a confirmation.  Usage: node <this> OUT URL DBC
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
  await p.evaluate(async (dbc) => { await post('/api/databases/add', { path: dbc }); await refreshState(); }, DBC);
  await p.click('.rail-btn[data-desk=simulation]');
  const groups = () => p.$$eval('#gen-body .grp', (x) => x.map((g) => ({ name: g.querySelector('.grp-name').textContent,
    count: g.querySelector('.grp-count').textContent, folded: g.classList.contains('folded'),
    msgs: [...g.querySelectorAll('.msgcard .name')].map((n) => n.textContent) })));
  const out = {};

  // 1. empty: "No group yet" with Add group
  out.empty = await p.textContent('#gen-body .empty h3');
  if (out.empty !== 'No group yet') fail('empty ' + out.empty);
  // 2. Add group: name + EngineData
  await p.click('#add-group');
  await p.waitForSelector('.modal header:text("Add group")');
  await p.click('.modal footer .btn.primary');                     // no name: refused
  if (!(await p.$('.modal'))) fail('accepted without a name');
  await p.fill('#grp-name', 'Powertrain');
  await p.check('[data-msg="EngineData"]');
  await p.screenshot({ path: OUT + '/groups_add_dialog.png' });
  await p.click('.modal footer .btn.primary');
  await p.waitForSelector('.grp[data-g="Powertrain"] .msgcard');
  // 3. + adds Status to Powertrain
  await p.click('.grp[data-g="Powertrain"] [data-gadd]');
  await p.waitForSelector('.modal header:text("Add signals to Powertrain")');
  out.inGroupHint = await p.textContent('.modal [data-m="EngineData"] .aux');
  await p.check('[data-msg="Status"]');
  await p.click('.modal footer .btn.primary');
  await p.waitForFunction(() => document.querySelectorAll('.grp[data-g="Powertrain"] .msgcard').length === 2);
  // 4. an empty group
  await p.click('#add-group');
  await p.fill('#grp-name', 'Body');
  await p.click('.modal footer .btn.primary');
  await p.waitForSelector('.grp[data-g="Body"] .grp-empty');
  out.afterAdd = await groups();
  if (JSON.stringify(out.afterAdd.map((g) => [g.name, g.msgs.length])) !== '[["Powertrain",2],["Body",0]]') fail('groups ' + JSON.stringify(out.afterAdd));
  await p.screenshot({ path: OUT + '/groups.png' });
  // 5. fold Powertrain; kept after a reload
  await p.click('.grp[data-g="Powertrain"] [data-gfold]');
  if (!(await groups())[0].folded) fail('fold');
  await p.reload();
  await p.waitForFunction(() => typeof S !== 'undefined' && S);
  await p.click('.rail-btn[data-desk=simulation]');
  await p.waitForSelector('.grp[data-g="Powertrain"]');
  out.foldKept = (await groups())[0].folded;
  if (!out.foldKept) fail('fold not kept');
  await p.click('.grp[data-g="Powertrain"] [data-gfold]');
  // 6. rename Powertrain -> Drive
  await p.click('.grp[data-g="Powertrain"] [data-gmore]');
  await p.click('.menu div:text-is("Rename group…")');
  await p.fill('#grp-new', 'Drive');
  await p.keyboard.press('Enter');
  await p.waitForSelector('.grp[data-g="Drive"]');
  // 7. move Body up
  await p.click('.grp[data-g="Body"] [data-gmore]');
  await p.click('.menu div:text-is("Move up")');
  await p.waitForFunction(() => document.querySelector('#gen-body .grp').dataset.g === 'Body');
  // 8. remove Drive: asks, then its messages go
  await p.click('.grp[data-g="Drive"] [data-gmore]');
  await p.click('.menu div:text-is("Remove group…")');
  await p.waitForSelector('.modal header:text("Remove group")');
  out.removeText = (await p.textContent('.modal .mb')).replace(/\s+/g, ' ').trim();
  await p.click('.modal footer .btn:text("Remove")');
  await p.waitForFunction(() => S.cards.length === 0 && S.groups.length === 1);
  out.end = await groups();

  console.log(JSON.stringify(out));
  console.log('ERRORS', JSON.stringify(errors));
  await b.close();
  if (errors.length) process.exit(1);
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
