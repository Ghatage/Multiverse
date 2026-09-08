const http=require('http');
const fs=require('fs');
const {JsContext}=require('./js_context');
const {PyWorker}=require('./py_worker_proxy');
const {observe}=require('./observe');
const {checkpoint,restore}=require('./state');
const js=new JsContext(),py=new PyWorker();
let busy=false,ready=false;const started=Date.now();
const health=()=>({ok:ready,cdp:js.connected,py:py.alive,uptime_s:(Date.now()-started)/1000,busy});
const methods={exec_js:p=>js.exec(p.code,p.timeout_ms??30000),exec_py:p=>py.exec(p.code,p.timeout_ms??30000),
  observe:p=>observe(js,py,p),checkpoint:p=>checkpoint(js,py,p),restore:()=>restore(js,py),health};
function reply(res,status,body,id){res.writeHead(status,{'content-type':'application/json','x-fork-request-id':id==null?'':String(id)});res.end(JSON.stringify(body));}
function error(res,id,code,message,data={}){reply(res,200,{jsonrpc:'2.0',id,error:{code,message,data}},id);}
http.createServer(async(req,res)=>{
  if(req.method==='GET'&&req.url==='/health')return reply(res,ready?200:503,health());
  if(req.method!=='POST'||req.url!=='/rpc')return reply(res,404,{error:'not found'});
  let msg,raw='';
  try{for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>131072)return error(res,null,-32600,'Request too large');}msg=JSON.parse(raw);}catch{return error(res,null,-32600,'Bad JSON');}
  if(!msg||msg.jsonrpc!=='2.0'||!['string','number'].includes(typeof msg.id)||typeof msg.method!=='string'||(msg.params!==undefined&&(!msg.params||Array.isArray(msg.params)||typeof msg.params!=='object')))return error(res,msg?.id??null,-32600,'Bad request');
  const {id,method}=msg,p=msg.params||{};
  if(!Object.hasOwn(methods,method))return error(res,id,-32601,'Unknown method');
  if(method==='health')return reply(res,200,{jsonrpc:'2.0',id,result:health()},id);
  if(busy||!ready)return error(res,id,-32001,'busy');
  if(method.startsWith('exec_')&&(typeof p.code!=='string'||Buffer.byteLength(p.code)>65536||!Number.isInteger(p.timeout_ms??30000)||(p.timeout_ms??30000)<=0||(p.timeout_ms??30000)>120000))return error(res,id,-32600,'Invalid code or timeout');
  busy=true;
  try{reply(res,200,{jsonrpc:'2.0',id,result:await methods[method](p)},id);}
  catch(e){
    const code=Number.isInteger(e.code)?e.code:-32000;
    const data={...e.data,name:e.name,message:e.message,stack:(e.stack||'').split('\n').slice(0,8).join('\n'),url:js.url()};
    if(code===-32000){try{data.last_screenshot=await js.screenshot();}catch{}}
    if(code===-32002)data.partial_stdout=e.partial_stdout||'';
    error(res,id,code,e.message,data);
  }finally{busy=false;}
}).listen(7000,'0.0.0.0',async()=>{
  try{
    while(!fs.existsSync('/tmp/ready'))await new Promise(r=>setTimeout(r,50));
    await restore(js,py);ready=true;console.log('REPL ready :7000');
  }catch(e){console.error('REPL startup failed:',e.message);process.exit(1);}
});
