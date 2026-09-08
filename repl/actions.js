// Closed mutation API. A request performs one operation; no code/evaluate escape.
const fs = require('fs/promises');
const path = require('path');
const {execFileSync} = require('child_process');
const proxy = require('./cdp_proxy');
const marker = '/state/mutation-lease.json';
let lease = null;
const settings = ['AcceptPointerEvents', 'AcceptKeyEvents', 'AcceptCutText', 'SendCutText', 'AcceptSetDesktopSize'];
function vnc(name, value) {
  return execFileSync('vncconfig', value === undefined ? ['-get', name] : ['-set', `${name}=${value}`],
    {env: {...process.env, DISPLAY: ':1'}, timeout: 5000, encoding: 'utf8'}).trim();
}
async function initialize() {
  try {
    const saved=JSON.parse(await fs.readFile(marker,'utf8'));
    lease = {orphaned:true,...saved};
    proxy.lock(true);
    for (const setting of settings) vnc(setting, '0');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}
async function begin(p) {
  if (lease) throw new Error('Mutation lease already held or requires reconciliation');
  if (!/^[a-f0-9]{64}$/.test(p.token || '') || typeof p.run_id !== 'string') throw new Error('Invalid mutation lease');
  const previous = Object.fromEntries(settings.map(name => [name, vnc(name)]));
  lease = {token:p.token, run_id:p.run_id, previous};
  proxy.lock(true);
  // A durable marker survives a server crash. A rebooted checkpoint stays locked
  // until an explicit recovery operation reconciles its old incarnation.
  const handle = await fs.open(marker, 'wx');
  try { await handle.writeFile(JSON.stringify({run_id:p.run_id,previous})); await handle.sync(); }
  finally { await handle.close(); }
  for (const name of settings) {
    vnc(name, '0');
    if (vnc(name) !== '0') throw new Error('Could not disable raw VNC input');
  }
  return {owned:true, input_mode:'view_only'};
}
function authorized(p) { return Boolean(lease?.token && p.token === lease.token); }
async function end(p) {
  if (!authorized(p)) throw new Error('Mutation lease token mismatch');
  if(p.keep_isolated) {lease={orphaned:true,run_id:lease.run_id,previous:lease.previous};return {released:true,isolated:true};}
  for (const [name,value] of Object.entries(lease.previous)) vnc(name,value);
  await fs.unlink(marker);
  lease = null;
  proxy.lock(false);
  return {released:true};
}
function state() { return {held: Boolean(lease), orphaned: Boolean(lease?.orphaned)}; }
function validate(action) {
  const fields = {navigate:['url'], new_tab:['url'], pointer_click:['x','y'], pointer_drag:['points'], press_key:['key'], fill:['selector','value'], click:['selector'], select:['selector','value'],
    check:['selector','checked'], write_file:['path','content']};
  const expected = fields[action?.operation];
  if (!expected || Object.keys(action).sort().join() !== ['operation',...expected].sort().join()) throw new Error('Unsupported action arguments');
  for (const key of expected) {
    if (key === 'points') {
      if (!Array.isArray(action.points) || action.points.length < 2 || action.points.length > 256) throw new Error('A drag requires 2-256 points');
      for (const point of action.points) {
        if (!point || Object.keys(point).sort().join() !== 'x,y') throw new Error('Invalid drag point');
        validate({operation:'pointer_click', ...point});
      }
    } else if (key === 'x' || key === 'y') {
      if (!Number.isFinite(action[key]) || action[key] < 0 || action[key] >= 16384) throw new Error('Invalid pointer coordinate');
    } else if (typeof action[key] !== (key === 'checked' ? 'boolean' : 'string')) throw new Error('Invalid action argument type');
  }
  if (['navigate','new_tab'].includes(action.operation) && !/^https?:$/.test(new URL(action.url).protocol)) throw new Error('Unsupported URL scheme');
  if (action.operation === 'press_key' && (!action.key.trim() || action.key.length > 80)) throw new Error('Invalid key chord');
  if (action.selector !== undefined && !action.selector.trim()) throw new Error('Empty selector');
  if (Buffer.byteLength(JSON.stringify(action)) > 65536) throw new Error('Action too large');
}
async function execute(js, p) {
  if (!authorized(p)) throw new Error('An action requires its mutation lease');
  const action = p.action;
  validate(action);
  if (action.operation === 'write_file') {
    const target = action.path;
    if (!path.isAbsolute(target) || target.split('/').includes('..')) throw new Error('Invalid file path');
    const parent = await fs.realpath(path.dirname(target));
    if (parent !== '/home/user' && !parent.startsWith('/home/user/')) throw new Error('File path outside /home/user');
    // Do not follow a final symlink outside the approved tree.
    const constants = require('fs').constants;
    const handle = await fs.open(target, constants.O_WRONLY | constants.O_CREAT | constants.O_TRUNC | constants.O_NOFOLLOW, 0o600);
    try { await handle.writeFile(action.content); await handle.sync(); }
    finally { await handle.close(); }
    return {completed:true};
  }
  await js.ensure();
  if (action.operation === 'new_tab') {
    js.ctx.page = await js.ctx.context.newPage();
    await js.page().bringToFront();
  }
  const page = js.page();
  const options = {timeout: Math.min(30000, Math.max(1, p.timeout_ms || 30000))};
  if (['navigate','new_tab'].includes(action.operation)) await page.goto(action.url, {...options, waitUntil:'domcontentloaded'});
  else if (['pointer_click','pointer_drag','press_key'].includes(action.operation)) await require('./pointer').execute(page, action);
  else {
    const locator = page.locator(action.selector);
    if (action.operation === 'fill') await locator.fill(action.value, options);
    if (action.operation === 'click') await locator.click(options);
    if (action.operation === 'select') await locator.selectOption(action.value, options);
    if (action.operation === 'check') await locator.setChecked(action.checked, options);
  }
  // Navigation and click handlers can return before fetch-driven UI updates.
  // A bounded network-idle predicate must complete before capture is admitted.
  if (['navigate','click'].includes(action.operation)) await page.waitForLoadState('networkidle', {timeout:Math.min(5000,options.timeout)});
  return {completed:true, url:page.url(), settled:true,
    settle_predicate:['navigate','click'].includes(action.operation)?'networkidle':'playwright_operation_completed'};
}
async function adopt(p) {
  if (!lease?.orphaned || lease.run_id !== p.previous_run_id || !/^[a-f0-9]{64}$/.test(p.token || '')) throw new Error('Unreconciled lease identity mismatch');
  lease = {...lease,orphaned:false,token:p.token,run_id:p.run_id};
  const handle=await fs.open(marker,'w');
  try {await handle.writeFile(JSON.stringify({run_id:lease.run_id,previous:lease.previous}));await handle.sync();}
  finally {await handle.close();}
  return {owned:true,input_mode:'view_only'};
}
module.exports = {initialize, begin, end, adopt, state, authorized, execute, validate};
