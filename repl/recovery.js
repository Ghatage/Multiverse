// Concrete reconstruction for the local browser fixture; never replay saved clicks.
const fs = require('fs/promises');
const crypto = require('crypto');
async function fileState(paths) {
  const result = {};
  for (const name of paths || []) {
    if (typeof name !== 'string' || !name.startsWith('/home/user/') || name.split('/').includes('..')) throw new Error('Invalid evidence file path');
    try {
      const stat = await fs.lstat(name);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 10485760) throw new Error('Unsupported evidence file');
      const data = await fs.readFile(name);
      result[name] = {exists:true, size:data.length, sha256:crypto.createHash('sha256').update(data).digest('hex')};
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      result[name] = {exists:false};
    }
  }
  return result;
}
async function browserState(js) {
  await js.ensure();
  const pages = js.ctx.context.pages();
  const tabs = [];
  for (const page of pages) {
    tabs.push({url:page.url(), active:page === js.page(), state:await page.evaluate(() => ({
      fields:[...document.querySelectorAll('input,textarea,select')].map((el,index) => ({index,tag:el.tagName,type:el.type,name:el.name,id:el.id,value:el.value,checked:Boolean(el.checked)})),
      scroll:[scrollX,scrollY]
    }))});
  }
  return {tabs};
}
async function restoreBrowser(js, snapshot) {
  if (!snapshot || !Array.isArray(snapshot.tabs) || snapshot.tabs.length > 30) throw new Error('Invalid browser manifest');
  for (const tab of snapshot.tabs) {
    const url = new URL(tab.url);
    if (tab.url !== 'about:blank' && (url.host !== 'host.docker.internal:3000' || !url.pathname.startsWith('/t/'))) throw new Error('Browser restore requires a supported local app');
  }
  await js.ensure();
  const existing = js.ctx.context.pages();
  while (existing.length > snapshot.tabs.length) await existing.pop().close();
  for (let index=0; index<snapshot.tabs.length; index++) {
    const tab = snapshot.tabs[index], page = existing[index] || await js.ctx.context.newPage();
    await page.goto(tab.url,{waitUntil:'domcontentloaded',timeout:15000});
    await page.evaluate(state => {
      const elements = [...document.querySelectorAll('input,textarea,select')];
      for (const saved of state.fields) {
        const el = elements[saved.index];
        if (!el || el.tagName !== saved.tag || el.name !== saved.name || el.id !== saved.id || el.type !== saved.type) throw new Error('Browser form structure changed during recovery');
        el.value = saved.value;
        if ('checked' in el) el.checked = saved.checked;
      }
      scrollTo(...state.scroll);
    },tab.state);
    if (tab.active) {js.ctx.page = page; await page.bringToFront();}
  }
  const actual = await browserState(js);
  if (JSON.stringify(actual) !== JSON.stringify(snapshot)) throw new Error('Browser reconstruction did not verify');
  return {verified:true};
}
module.exports = {fileState,browserState,restoreBrowser};
