// Reconstruct declared saved state during boot, before agent mutation ownership.
// This does not restore process memory, unsaved buffers or external accounts.
const fs = require('fs/promises');
const path = require('path');
const crypto = require('crypto');
const {spawn} = require('child_process');
const STATE = '/state/desktop-session.json';
const SEED = '/state/desktop-seed.json';
const APPS = {text:['mousepad', '--disable-server'], writer:['libreoffice', '--writer'], calc:['libreoffice', '--calc']};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function read(name) {
  try { return JSON.parse(await fs.readFile(name, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}
async function write(name, value) {
  await fs.writeFile(name + '.tmp', JSON.stringify(value), {mode:0o600});
  await fs.rename(name + '.tmp', name);
}
async function target(js, page) {
  const client = await js.ctx.context.newCDPSession(page);
  try { return (await client.send('Target.getTargetInfo')).targetInfo.targetId; }
  finally { await client.detach(); }
}
async function pageFor(js, id) {
  for (let attempt=0; attempt<50; attempt++) {
    for (const page of js.ctx.context.pages()) if (await target(js, page) === id) return page;
    await pause(50);
  }
  throw new Error('Created browser tab did not attach');
}
async function openWindows(js, windows, activeWindow) {
  const browser = await js.browser.newBrowserCDPSession();
  const previous = js.ctx.context.pages(), created = [], report = [];
  try {
    // Keep an existing tab alive until replacements exist, so Chromium stays up.
    const requested = windows.length ? windows : [{tabs:['about:blank'], active_tab:0}];
    for (const win of requested) {
      const pages = [];
      let windowId;
      for (const url of win.tabs) {
        const {targetId} = await browser.send('Target.createTarget', {url:'about:blank', newWindow:pages.length === 0, background:false});
        const actual = await browser.send('Browser.getWindowForTarget', {targetId});
        if (windowId !== undefined && actual.windowId !== windowId) throw new Error('Browser window grouping failed');
        windowId = actual.windowId;
        const page = await pageFor(js, targetId);
        pages.push(page);
        report.push({url, window:created.length, tab:pages.length-1, status:'pending'});
      }
      created.push(pages);
    }
    for (const page of previous) await page.close();
    const all = created.flat();
    // Bounded parallel loading; a network error is reported per tab, not hidden.
    for (let offset=0; offset<all.length; offset+=10) {
      await Promise.all(all.slice(offset,offset+10).map(async (page,i) => {
        const item = report[offset+i];
        try {
          await page.goto(item.url, {waitUntil:'commit', timeout:5000});
          item.status = 'opened'; item.actual_url = page.url();
        } catch (error) { item.status = 'error'; item.error = error.message.split('\n')[0]; }
      }));
    }
    for (let i=0; i<created.length; i++) await created[i][requested[i].active_tab].bringToFront();
    js.ctx.page = created[activeWindow][requested[activeWindow].active_tab];
    await js.page().bringToFront();
    return report;
  } finally { await browser.detach(); }
}
async function launch(document) {
  const args = APPS[document.app];
  if (!args || !document.path.startsWith('/home/user/Desktop/Fork/')) throw new Error('Invalid saved document');
  await fs.access(document.path);
  return new Promise((resolve, reject) => {
    const child = spawn(args[0], [...args.slice(1), document.path], {stdio:'ignore', detached:true});
    child.once('error', reject);
    child.once('spawn', () => { child.unref(); resolve(); });
  });
}
async function restore(js) {
  const seed = await read(SEED);
  let state = await read(STATE);
  if (seed) {
    state = {version:1, windows:seed.windows, active_window:seed.active_window, documents:[]};
    // A fresh import gets a new directory even when based on an imported image.
    const directory = '/home/user/Desktop/Fork/' + crypto.randomBytes(6).toString('hex');
    await fs.mkdir(directory, {recursive:true, mode:0o700});
    for (const [index, doc] of seed.documents.entries()) {
      const folder = path.join(directory, String(index));
      await fs.mkdir(folder, {mode:0o700});
      const name = path.join(folder, doc.name);
      if (path.dirname(name) !== folder) throw new Error('Invalid document filename');
      const content = Buffer.from(doc.content_base64, 'base64');
      await fs.writeFile(name, content, {mode:0o600, flag:'wx'});
      state.documents.push({name:doc.name, app:doc.app, path:name});
    }
    await write(STATE, state);
    await fs.unlink(SEED);
  }
  if (!state) return;
  const report = {status:'ready', tabs:[], documents:[]};
  if (state.windows !== null) report.tabs = await openWindows(js, state.windows, state.active_window);
  for (const document of state.documents) {
    try {
      await launch(document);
      const bytes = await fs.readFile(document.path);
      report.documents.push({...document, status:'launched', sha256:crypto.createHash('sha256').update(bytes).digest('hex')});
    } catch (error) { report.documents.push({...document, status:'error', error:error.message}); }
  }
  if ([...report.tabs, ...report.documents].some(item => item.status === 'error')) report.status = 'partial';
  await write('/state/desktop-report.json', report);
}
async function capture(js) {
  const state = await read(STATE);
  if (!state || state.windows === null) return;
  const browser = await js.browser.newBrowserCDPSession();
  try {
    const groups = new Map();
    let activeWindow = 0;
    for (const page of js.ctx.context.pages()) {
      const {windowId} = await browser.send('Browser.getWindowForTarget', {targetId:await target(js,page)});
      if (!groups.has(windowId)) groups.set(windowId, {tabs:[],active_tab:0});
      const win = groups.get(windowId);
      if (await page.evaluate(() => document.visibilityState === 'visible')) win.active_tab = win.tabs.length;
      if (page === js.page()) activeWindow = [...groups.keys()].indexOf(windowId);
      win.tabs.push(page.url());
    }
    state.windows = [...groups.values()]; state.active_window = activeWindow;
    await write(STATE, state);
  } finally { await browser.detach(); }
}
module.exports = {restore, capture};
