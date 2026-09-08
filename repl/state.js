const fs = require('fs/promises');
async function read(path) { try { return JSON.parse(await fs.readFile(path,'utf8')); } catch(e) { if(e.code==='ENOENT') return null; throw e; } }
async function write(path,data) { await fs.writeFile(path+'.tmp',JSON.stringify(data)); await fs.rename(path+'.tmp',path); }
async function checkpoint(js,py,{label}={}) {
  await js.ensure();
  const tabs=js.ctx.context.pages().filter(p=>p.url()!=='about:blank').map(p=>({url:p.url(),active:p===js.page()}));
  const vars=js.vars(), pyvars=await py.call('dump_vars'), ts=new Date().toISOString();
  await fs.mkdir('/state',{recursive:true});
  await write('/state/tabs.json',{tabs,label,ts}); await write('/state/repl.json',{js:vars,py:pyvars,ts});
  return {tabs:tabs.length,vars:Object.keys(vars),py_vars:Object.keys(pyvars),path:'/state'};
}
async function restore(js,py) {
  await js.ensure();
  const tabs=await read('/state/tabs.json'), state=await read('/state/repl.json');
  const active=tabs?.tabs.find(p=>p.active);
  const page=js.ctx.context.pages().find(p=>p.url()===active?.url);
  if(page) {js.ctx.page=page;await page.bringToFront();}
  if(state) {
    for(const [key,value] of Object.entries(state.js || {})) if(!js.reserved.has(key) && !key.startsWith('__')) js.ctx[key]=value;
    await py.call('load_vars',state.py || {});
  }
  return {tabs_reopened:js.ctx.context.pages().length,vars_restored:Object.keys(state?.js || {}).length+Object.keys(state?.py || {}).length};
}
module.exports={checkpoint,restore};
