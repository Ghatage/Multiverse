#!/usr/bin/env node
// Play the encoded film from start to end in Chromium; never substitute source seeks for playback.
import {chromium} from 'playwright';
import {createServer} from 'node:http';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const here = dirname(fileURLToPath(import.meta.url));
const file = resolve(process.argv[2] || join(here, 'output/multiverse-tv-wall-astra-rescue-10s.mp4'));
const output = join(here, 'output');
await mkdir(output, {recursive:true});
const movie = await readFile(file);
const server = createServer((req,res) => {
  if (req.url === '/film.mp4') { res.writeHead(200, {'Content-Type':'video/mp4'}); res.end(movie); }
  else { res.writeHead(200, {'Content-Type':'text/html'}); res.end('<style>body{margin:0;background:#000}video{width:100vw;height:100vh}</style><video muted playsinline></video>'); }
});
await new Promise(ok => server.listen(0,'127.0.0.1',ok));
const browser = await chromium.launch({args:['--autoplay-policy=no-user-gesture-required']});
const page = await browser.newPage({viewport:{width:1920,height:1080}});
const errors = [];
page.on('pageerror',e=>errors.push(e.message));
page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
page.on('response',r=>{if(r.status()>=400)errors.push(`${r.status()} ${r.url()}`);});
try {
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.evaluate(async () => {
    const video = document.querySelector('video');
    video.src = URL.createObjectURL(await (await fetch('/film.mp4')).blob());
    window.presented = 0;
    function frame(){window.presented++;video.requestVideoFrameCallback(frame);}
    video.requestVideoFrameCallback(frame);
    await video.play();
  });
  for (const time of [0.2,5,9.6]) {
    await page.waitForFunction(t=>document.querySelector('video').currentTime>=t,time,{timeout:20000});
    await page.screenshot({path:join(output,`playback-${time}.png`)});
  }
  await page.waitForFunction(()=>document.querySelector('video').ended,null,{timeout:15000});
  const result = await page.evaluate(()=>{
    const v=document.querySelector('video');
    return {duration:v.duration,width:v.videoWidth,height:v.videoHeight,currentTime:v.currentTime,ended:v.ended,presented:window.presented,decoded:v.getVideoPlaybackQuality().totalVideoFrames,error:v.error};
  });
  assert.equal(result.duration,10);
  assert.equal(result.width,1920);assert.equal(result.height,1080);
  assert.equal(result.ended,true);assert.equal(result.error,null);
  assert.ok(result.presented>200,'Playback must advance through the full film');
  assert.deepEqual(errors,[]);
  await writeFile(join(output,'playback-check.json'),JSON.stringify({file,...result,errors},null,2));
  console.log(JSON.stringify({file,...result,errors}));
} finally { await browser.close();server.close(); }
