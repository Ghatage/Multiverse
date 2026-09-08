#!/usr/bin/env node
// Frame-stepped capture for the TV wall. Same idea as ../capture.mjs, but each step awaits every
// <video> element's seek before the screenshot, so the screens show the exact frame for t.
//
//   node capture.mjs --out out.mp4          full film
//   node capture.mjs --at 5.0               single still -> output/still-5_0.png
//   node capture.mjs --frames 30            first 30 frames only
//   node capture.mjs --start 8 --frames 60   sequential end-card smoke
//   node capture.mjs --resume-from 240       keep verified prefix; recapture final 60 frames
import {chromium} from 'playwright';
import {createServer} from 'node:http';
import {readFile, mkdir, rm, writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {join, extname, dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');           // serve video/ so ../vendor/clock.js resolves
const MIME = {'.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.png': 'image/png', '.mp4': 'video/mp4', '.woff2': 'font/woff2'};

const args = process.argv.slice(2);
const flag = (n, d = null) => { const i = args.indexOf(n); return i === -1 ? d : (args[i + 1] ?? true); };
const has = (n) => args.includes(n);
const W = 1920, H = 1080;
const outDir = join(HERE, 'output');
const framesDir = join(HERE, '.frames');
const outFile = resolve(String(flag('--out', join(outDir, 'multiverse-tv-wall-10s.mp4'))));

const srv = createServer(async (req, res) => {
  try {
    let p = decodeURIComponent(req.url.split('?')[0]);
    if (p.endsWith('/')) p += 'index.html';
    const file = join(ROOT, p);
    if (!file.startsWith(ROOT)) return res.writeHead(403).end();
    const body = await readFile(file);
    res.writeHead(200, {'Content-Type': MIME[extname(file)] ?? 'application/octet-stream'});
    res.end(body);
  } catch { console.error('  404', req.url); res.writeHead(404).end(); }
});
await new Promise((ok) => srv.listen(0, '127.0.0.1', ok));
const port = srv.address().port;

const browser = await chromium.launch({args: ['--use-gl=angle', '--enable-gpu-rasterization', '--force-color-profile=srgb', '--disable-lcd-text', '--hide-scrollbars', '--autoplay-policy=no-user-gesture-required']});
const page = await browser.newPage({viewport: {width: W, height: H}, deviceScaleFactor: 1});
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('response', (r) => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
const url = `http://127.0.0.1:${port}/tv-wall/index.html?capture`;
console.log(`  page   ${url}`);
await page.goto(url, {waitUntil: 'load'});
await page.waitForFunction('window.__ready === true', null, {timeout: 180000});
await page.evaluate(() => document.fonts.ready);
const meta = await page.evaluate(() => ({fps: window.hf.fps, total: window.hf.totalFrames, dur: window.hf.duration}));
console.log(`  film   ${meta.dur}s @ ${meta.fps}fps = ${meta.total} frames`);

const seekTo = (t) => page.evaluate((tt) => window.hf.seekAsync(tt), t);
await mkdir(outDir, {recursive: true});

if (has('--at')) {
  const t = Number(flag('--at'));
  await seekTo(t);
  const still = join(outDir, `still-${String(t).replace('.', '_')}.png`);
  await page.screenshot({path: still});
  console.log(`  still  ${still}`);
  await browser.close(); srv.close(); process.exit(0);
}

const total = has('--frames') ? Math.min(Number(flag('--frames')), meta.total) : meta.total;
const resume = Number(flag('--resume-from', 0));
if (resume) {
  for (let n = 0; n < resume; n++) {
    if (!existsSync(join(framesDir, String(n).padStart(5, '0') + '.png'))) throw new Error(`Missing prefix frame ${n}`);
  }
} else if (existsSync(framesDir)) await rm(framesDir, {recursive: true, force: true});
await mkdir(framesDir, {recursive: true});
const t0 = Date.now();
for (let n = resume; n < total; n++) {
  await seekTo(Number(flag('--start', 0)) + n / meta.fps);
  const buf = await page.screenshot({type: 'png'});
  await writeFile(join(framesDir, String(n).padStart(5, '0') + '.png'), buf);
  if (n % 30 === 0 || n === total - 1) process.stdout.write(`\r  render ${String(n + 1).padStart(4)}/${total}  ${((Date.now() - t0) / 1000).toFixed(0)}s   `);
}
console.log(`\n  frames ${((Date.now() - t0) / 1000).toFixed(1)}s -> ${framesDir}`);
await browser.close(); srv.close();
await writeFile(join(outDir, 'capture-check.json'), JSON.stringify({errors, frames: total, retainedPrefixFrames: resume, capturedFrames: total - resume, start: Number(flag('--start', 0)), ...meta}, null, 2));
if (errors.length) throw new Error(errors.join('\n'));

await mkdir(dirname(outFile), {recursive: true});
const ff = spawn('ffmpeg', ['-y', '-framerate', String(meta.fps), '-i', join(framesDir, '%05d.png'),
  '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '16', '-preset', 'slow', '-movflags', '+faststart', '-t', String(meta.dur), outFile],
  {stdio: ['ignore', 'ignore', 'pipe']});
let err = ''; ff.stderr.on('data', (d) => (err += d));
const code = await new Promise((r) => ff.on('close', r));
if (code !== 0) { console.error(err.split('\n').slice(-12).join('\n')); process.exit(1); }
console.log(`  mp4    ${outFile}`);
