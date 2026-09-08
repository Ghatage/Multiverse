function truncate(text, max) {
  if (text.length <= max) return text;
  const suffix = '\n…(truncated lines)';
  const head = text.slice(0, Math.max(0,max-suffix.length));
  return (head.includes('\n') ? head.slice(0,head.lastIndexOf('\n')) : head) + suffix.slice(0,max);
}
async function observe(js, py, {mode='both',target='auto',max_tree_chars=20000} = {}) {
  if (!['tree','screenshot','both'].includes(mode) || !['auto','browser','desktop'].includes(target) || !Number.isInteger(max_tree_chars) || max_tree_chars < 64)
    throw Object.assign(new Error('Invalid observation parameters'),{code:-32600});
  const active = await py.call('active_window');
  if (target === 'auto') target = /- Chromium$/.test(active) ? 'browser' : 'desktop';
  const [width,height] = (process.env.FORK_GEOMETRY || '1440x900').split('x').map(Number);
  const out = {target,active_window:active,ts:new Date().toISOString(),width,height};
  if (target === 'browser') {
    await js.ensure(); const page = js.page();
    out.url = page.url(); out.title = await page.title();
    if (mode !== 'screenshot') out.tree = truncate(`url: ${out.url}\ntitle: ${out.title}\n`+await page.locator('body').ariaSnapshot(),max_tree_chars);
    if (mode !== 'tree') {
      out.screenshot = await js.screenshot();
      const png = Buffer.from(out.screenshot,'base64'); out.width=png.readUInt32BE(16); out.height=png.readUInt32BE(20);
    }
  } else {
    if (mode !== 'screenshot') out.tree = await py.call('atspi_tree',{max_chars:max_tree_chars});
    if (mode !== 'tree') out.screenshot = await py.call('screenshot');
  }
  return out;
}
module.exports={observe};
