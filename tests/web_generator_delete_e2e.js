// Interactive generator: remove a message with the trash button and its in-place confirmation; menus open every
// time (a menu opened while another one is open stays open).  Usage: node <this> OUT URL DBC
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
    await post('/api/tx/add', { messages: ['EngineData', 'Status'] });
    await refreshState();
  }, DBC);
  await p.click('.rail-btn[data-desk=simulation]');
  await p.waitForSelector('.msgcard [data-del]');
  const cards = () => p.$$eval('.msgcard .mh .name', (x) => x.map((e) => e.textContent));
  const del = '.msgcard:has(.name:text-is("EngineData")) [data-del]';
  const out = {};

  // 1. the trash button asks in a dialog; Cancel keeps the message (and has the focus: Enter does not remove)
  await p.click(del);
  await p.waitForSelector('.modal header:text("Remove message")');
  out.asked = await p.evaluate(() => ({ text: document.querySelector('.modal .mb').textContent.replace(/\s+/g, ' ').trim(),
    buttons: [...document.querySelectorAll('.modal footer .btn')].map((b) => b.textContent), focus: document.activeElement.textContent }));
  await p.screenshot({ path: OUT + '/gen_delete_confirm.png' });
  if (!out.asked.text.includes('EngineData') || out.asked.focus !== 'Cancel') fail('ask ' + JSON.stringify(out.asked));
  await p.keyboard.press('Enter');                               // Enter on Cancel
  await p.waitForSelector('.modal', { state: 'detached' });
  if ((await cards()).length !== 2) fail('enter removed');
  // 2. Escape cancels too
  await p.click(del);
  await p.waitForSelector('.modal');
  await p.keyboard.press('Escape');
  await p.waitForSelector('.modal', { state: 'detached' });
  if ((await cards()).length !== 2) fail('escape removed');
  // 3. Remove: gone
  await p.click(del);
  await p.click('.modal footer .btn:text("Remove")');
  await p.waitForFunction(() => S.cards.length === 1);
  out.left = await cards();
  if (out.left.join() !== 'Status') fail('remove ' + out.left);

  // 4. menus: opening a menu while another is open keeps the new one; outside / Escape close it
  const menus = () => p.$$eval('.menu', (x) => x.length);
  await p.click('#ar-more');
  out.m1 = await menus();
  await p.click('#file-btn');                                    // another menu while one is open: it stays
  await p.waitForTimeout(100);
  out.m3 = await p.$$eval('.menu div', (x) => x.map((e) => e.textContent).slice(0, 1));
  await p.mouse.click(700, 700);                                 // outside: closed
  out.m4 = await menus();
  await p.click('#ar-more');
  await p.keyboard.press('Escape');                              // Escape: closed
  out.m5 = await menus();
  for (let i = 0; i < 6; i++) {                                  // open / close many times: it opens every time
    await p.click(i % 2 ? '#file-btn' : '#ar-more');
    await p.waitForTimeout(50);
    if ((await menus()) !== 1) fail('menu try ' + i);
    if (i % 3 === 2) await p.keyboard.press('Escape');
  }
  if (out.m1 !== 1 || out.m3[0] !== 'Open project…' || out.m4 !== 0 || out.m5 !== 0) fail('menus ' + JSON.stringify(out));

  console.log(JSON.stringify(out));
  console.log('ERRORS', JSON.stringify(errors));
  await b.close();
  if (errors.length) process.exit(1);
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
