// Record the real dashboard while actual local browser actions update its data.
import {chromium} from 'playwright';
import {mkdir,writeFile} from 'node:fs/promises';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {dirname,join} from 'node:path';

const root=dirname(dirname(fileURLToPath(import.meta.url)));
const out=join(root,'video/assets/live-graph-v2');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({args:['--use-gl=angle','--enable-gpu-rasterization']});
const ctx=await browser.newContext({viewport:{width:1920,height:1080},recordVideo:{dir:join(out,'raw'),size:{width:1920,height:1080}}});
const page=await ctx.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://127.0.0.1:8010',{waitUntil:'domcontentloaded'});
await page.waitForFunction(()=>window.forkDashboard?.graphCounts?.links>0);
await page.locator('#fit').click();
await page.waitForTimeout(1500);
const read=()=>page.evaluate(()=>({counts:window.forkDashboard.graphCounts,revision:window.forkDashboard.snapshot.revision,summary:document.getElementById('graph-summary').textContent}));
const baseline=await read();
await page.screenshot({path:join(out,'before.png')});
const started=Date.now();
const samples=[{seconds:0,...baseline}];
const worker=spawn(join(root,'.venv/bin/python'),['video/live-graph-run.py'],{cwd:root,env:{...process.env,PYTHONPATH:root}});
let log='',finished=false,exitCode=null;
worker.stdout.on('data',b=>{log+=b;process.stdout.write(b);});
worker.stderr.on('data',b=>{log+=b;process.stderr.write(b);});
worker.on('close',code=>{finished=true;exitCode=code;});
// Pointer input orbits the existing graph; no graph data is injected.
const area=await page.locator('#graph').boundingBox();
await page.mouse.move(area.x+area.width*.6,area.y+area.height*.6);
await page.mouse.down();await page.mouse.move(area.x+area.width*.6+90,area.y+area.height*.6+20,{steps:35});await page.mouse.up();
let last=baseline.counts.links;
while(Date.now()-started<180000){
  await page.waitForTimeout(350);
  const sample={seconds:(Date.now()-started)/1000,...await read()};
  if(sample.counts.links!==last){samples.push(sample);last=sample.counts.links;await page.locator('#fit').click();console.log('Live graph update',JSON.stringify(sample));}
  if(finished&&last>=baseline.counts.links+4)break;
  if(finished&&exitCode!==0)break;
}
await page.waitForTimeout(2200);
const final=await read();
await page.screenshot({path:join(out,'after.png')});
const video=page.video();await ctx.close();await video.saveAs(join(out,'dashboard-live.webm'));await browser.close();
await writeFile(join(out,'evidence.json'),JSON.stringify({baseline,final,samples,exitCode,errors,log,recording_started_before_run:true},null,2));
if(exitCode!==0||final.counts.links<baseline.counts.links+4)throw Error('Live action run or dashboard update verification failed; inspect evidence.json');
console.log('Live capture verified:',join(out,'dashboard-live.webm'));
