const vm = require('vm');
const { chromium } = require('/opt/fork/node_modules/playwright-core');
class JsContext {
  constructor() {
    this.browser=null; this.ctx=vm.createContext({}); this.connected=false;
    this.reserved=new Set(['browser','context','page','console','display','sleep','Buffer','setTimeout','URL']);
  }
  async ensure() {
    if (this.browser?.isConnected()) {
      if (!this.ctx.page || this.ctx.page.isClosed()) this.ctx.page=this.ctx.context.pages().at(-1) || await this.ctx.context.newPage();
      return;
    }
    try { this.browser=await chromium.connectOverCDP('http://127.0.0.1:9222'); }
    catch(e) {e.code=-32003;throw e;}
    const context=this.browser.contexts()[0] || await this.browser.newContext();
    const page=context.pages()[0] || await context.newPage();
    Object.assign(this.ctx,{browser:this.browser,context,page,Buffer,setTimeout,URL,
      console:{log:(...a)=>this.ctx.__out.push(a.map(String).join(' '))},
      display:b=>this.ctx.__images.push(typeof b==='string'?b:b.toString('base64')),
      sleep:ms=>new Promise(r=>setTimeout(r,ms)),__images:[],__out:[]});
    this.connected=true;
    this.browser.on('disconnected',()=>{this.connected=false;});
  }
  page() {return this.ctx.page;}
  url() {try{return this.page().url();}catch{return null;}}
  async screenshot(){await this.ensure();return (await this.page().screenshot({type:'png'})).toString('base64');}
  async exec(code,timeoutMs){
    await this.ensure(); this.ctx.__images=[];this.ctx.__out=[];
    const ctx=this.ctx, start=Date.now(), oldPages=ctx.context.pages(); let timer;
    try {
      const script=new vm.Script(`(async()=>{${code}\n})()`,{filename:'exec_js'});
      const result=await Promise.race([
        script.runInContext(ctx,{timeout:timeoutMs}),
        new Promise((_,reject)=>{timer=setTimeout(()=>reject(Object.assign(new Error('JavaScript execution timed out'),{code:-32002})),timeoutMs);})
      ]);
      const pages=ctx.context.pages(); if(pages.length && (!ctx.page || ctx.page.isClosed() || !oldPages.includes(pages.at(-1)))) ctx.page=pages.at(-1);
      let value;try{value=JSON.parse(JSON.stringify(result??null));}catch{value=String(result);}
      return {stdout:ctx.__out.join('\n'),value,images:ctx.__images,duration_ms:Date.now()-start,url:this.url(),title:await this.page().title().catch(()=>null)};
    }catch(e){
      if(e.code==='ERR_SCRIPT_EXECUTION_TIMEOUT' || e.code===-32002){
        e.code=-32002;e.partial_stdout=ctx.__out.join('\n');
        // Disconnect pending Playwright operations and replace the VM before accepting another request.
        const vars=this.vars(); await this.browser.close().catch(()=>{});
        this.browser=null;this.connected=false;this.ctx=vm.createContext(vars);
      }
      throw e;
    }finally{clearTimeout(timer);}
  }
  vars(){
    const out={};
    for(const [k,v] of Object.entries(this.ctx)){
      if(k.startsWith('__')||this.reserved.has(k))continue;
      try{const encoded=JSON.stringify(v);if(encoded!==undefined)out[k]=JSON.parse(encoded);}catch{}
    }
    return out;
  }
}
module.exports={JsContext};
