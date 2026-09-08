// Audited one-action RPC. No arbitrary evaluation, callbacks, shell, or imports.
const {execFile} = require('child_process');
const {promisify} = require('util');
const run = promisify(execFile);
const fs = require('fs/promises');
const operations = new Set(['goto','click','fill','press','select','check','uncheck','scroll','new_tab','close_tab','activate_tab','desktop_click','desktop_type','desktop_press','desktop_hotkey','desktop_scroll']);
function validate(a) {
  if (!a || !operations.has(a.operation) || typeof a.arguments !== 'object' || !a.arguments || Array.isArray(a.arguments)) throw new Error('Unsupported action');
  const p=a.arguments;
  const allowed={goto:['url'],click:['selector'],fill:['selector','text'],press:['selector','key'],select:['selector','value'],check:['selector'],uncheck:['selector'],scroll:['x','y'],new_tab:[],close_tab:[],activate_tab:['index'],desktop_click:['x','y'],desktop_type:['text'],desktop_press:['key'],desktop_hotkey:['keys'],desktop_scroll:['amount']}[a.operation];
  if(Object.keys(p).sort().join()!==allowed.sort().join())throw new Error('Invalid action arguments');
  for(const [k,v] of Object.entries(p)){
    if(['x','y','index','amount'].includes(k)){if(!Number.isInteger(v)||Math.abs(v)>10000)throw new Error('Invalid numeric argument');}
    else if(k==='keys'){if(!Array.isArray(v)||!v.length||v.length>5||v.some(x=>typeof x!=='string'||x.length>30))throw new Error('Invalid keys');}
    else if(typeof v!=='string'||v.length>16000)throw new Error('Invalid string argument');
  }
  if(a.operation==='desktop_type'&&/[^\x00-\x7f]/.test(p.text))throw new Error('Desktop typing supports ASCII only');
  if(p.url&&!/^(https?:\/\/|about:blank$|file:\/\/)/.test(p.url))throw new Error('Unsupported navigation protocol');
}
async function action(js,py,a){
  validate(a);const p=a.arguments;await js.ensure();const page=js.page();
  if(a.operation.startsWith('desktop_'))return py.call('action',a);
  const loc=p.selector ? page.locator(p.selector) : null;
  switch(a.operation){
    case 'goto': await page.goto(p.url,{waitUntil:'domcontentloaded',timeout:15000});break;
    case 'click':await loc.click({timeout:10000});break;
    case 'fill':await loc.fill(p.text,{timeout:10000});break;
    case 'press':await loc.press(p.key,{timeout:10000});break;
    case 'select':await loc.selectOption(p.value,{timeout:10000});break;
    case 'check':await loc.check({timeout:10000});break;
    case 'uncheck':await loc.uncheck({timeout:10000});break;
    case 'scroll':await page.mouse.wheel(p.x,p.y);break;
    case 'new_tab':js.ctx.page=await js.ctx.context.newPage();break;
    case 'close_tab':if(js.ctx.context.pages().length<2)throw new Error('Cannot close the last tab');await page.close();js.ctx.page=js.ctx.context.pages().at(-1);break;
    case 'activate_tab':{const next=js.ctx.context.pages()[p.index];if(!next)throw new Error('Unknown tab');await next.bringToFront();js.ctx.page=next;break;}
  }
  let settled=false;
  try{await js.page().waitForLoadState('domcontentloaded',{timeout:2000});settled=true;}catch{}
  return {operation:a.operation,settle:{predicate:'browser_domcontentloaded',matched:settled,atomic:false}};
}
async function evidence(js,py){
  await js.ensure(); const tabs=[];
  for(const page of js.ctx.context.pages()){
    const tab={url:page.url(),active:page===js.page()};
    try{tab.title=await page.title();tab.tree=await page.locator('body').ariaSnapshot({timeout:5000});tab.complete=true;}
    catch(e){tab.complete=false;tab.error=e.message;}
    tabs.push(tab);
  }
  const native=await py.call('inventory');
  return {ts:new Date().toISOString(),desktop:await py.call('screenshot'),browser_tree:{tabs},native_tree:native.tree,inventory:{...native.inventory,tabs,atomic:false,unsaved_memory:'unsupported'}};
}
async function viewOnly(){
  for(const name of ['AcceptPointerEvents','AcceptKeyEvents','AcceptCutText','AcceptSetDesktopSize']){
    await run('vncconfig',['-display',':1','-set',name+'=0'],{timeout:5000});
    const check=await run('vncconfig',['-display',':1','-get',name],{timeout:5000});
    if(check.stdout.trim()!=='0')throw new Error('Could not disable raw VNC input: '+name);
  }
}
module.exports={action,evidence,validate,viewOnly};
