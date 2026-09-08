import {chromium} from 'playwright';
import {createServer} from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {resolve,dirname,extname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
const here=dirname(fileURLToPath(import.meta.url)),root=resolve(here,'..');
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2','.mp4':'video/mp4','.webm':'video/webm'};
const server=createServer(async(req,res)=>{try{const path=resolve(root,'.'+decodeURIComponent(req.url.split('?')[0]));if(!path.startsWith(root+'/'))throw Error('path');const body=await readFile(path);const headers={'Content-Type':mime[extname(path)]||'application/octet-stream','Accept-Ranges':'bytes'};if(req.headers.range){const [a,b]=req.headers.range.replace('bytes=','').split('-');const start=Number(a),end=b?Math.min(Number(b),body.length-1):body.length-1;res.writeHead(206,{...headers,'Content-Range':`bytes ${start}-${end}/${body.length}`,'Content-Length':end-start+1});res.end(body.subarray(start,end+1));}else{res.writeHead(200,{...headers,'Content-Length':body.length});res.end(body);}}catch{res.writeHead(404);res.end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({args:['--autoplay-policy=no-user-gesture-required','--force-color-profile=srgb']});
const page=await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=400)errors.push(`${r.status()} ${r.url()}`)});
const playback=process.argv.includes('--playback'),live=process.argv.includes('--live');
await page.goto(`http://127.0.0.1:${server.address().port}/tv-wall-astra/${playback?'preview.html':live?'index.html':'index.html?capture'}`);
if(playback||live){
 if(playback){await page.waitForFunction(()=>document.querySelector('video').readyState>=2);await page.evaluate(()=>document.querySelector('video').play());}
 else await page.waitForFunction(()=>window.__ready===true);
 await page.waitForTimeout(1200);
 const first=await page.screenshot();await page.waitForTimeout(1000);const second=await page.screenshot();
 const details=await page.evaluate(()=>{const v=document.querySelector('video');return v?{time:v.currentTime,width:v.videoWidth,height:v.videoHeight,paused:v.paused,duration:v.duration}:{ready:window.__ready};});
 if(first.equals(second)||errors.length||(playback&&(details.paused||details.time<.5)))throw Error('Playback failed: '+JSON.stringify({details,errors}));
 console.log(JSON.stringify({mode:playback?'mp4 playback':'live scene',details,changedFrames:true,errors}));await browser.close();server.close();process.exit(0);
}
await page.waitForFunction(()=>window.__ready===true,null,{timeout:60000});
await mkdir(join(here,'output'),{recursive:true});await mkdir(join(here,'.frames'),{recursive:true});
const stills=process.argv.includes('--stills');
for(const n of stills?[0,90,150,255,299]:Array.from({length:300},(_,i)=>i)){
 await page.evaluate(t=>window.hf.seekAsync(t),n/30);
 await page.screenshot({path:join(here,stills?'output':'.frames',`${String(n).padStart(5,'0')}.png`)});
 if(n%30===0)console.log(`Captured ${n}/300`);
}
const verification=await page.evaluate(()=>({ready:window.__ready,canvas:[document.querySelector('canvas').width,document.querySelector('canvas').height],duration:window.hf.duration,frames:window.hf.totalFrames}));
await writeFile(join(here,'output/browser-check.json'),JSON.stringify({verification,errors},null,2));
await browser.close();server.close();if(errors.length)throw Error(errors.join('\n'));
if(!stills){const result=spawnSync('ffmpeg',['-v','error','-y','-framerate','30','-i',join(here,'.frames/%05d.png'),'-c:v','libx264','-crf','17','-preset','fast','-pix_fmt','yuv420p','-movflags','+faststart',join(here,'output/silent.mp4')],{stdio:'inherit'});if(result.status!==0)process.exit(result.status||1);}
console.log('Browser verification passed.');
