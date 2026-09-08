#!/usr/bin/env node
// Deterministic frame capture.
//
// Drives the page's clock frame by frame instead of letting it play, so the output does not depend on
// render speed, machine load, or how long the model took to warm up. The same page served to a person
// plays on wall clock; served to this harness it is stepped.
//
//   node capture.mjs bonsai                     full film -> media/bonsai.mp4
//   node capture.mjs bonsai --frames 60         first 60 frames only
//   node capture.mjs bonsai --hash              print a digest of every frame
//   node capture.mjs bonsai --at 12.5           single still at t=12.5s
//
// --hash twice, compared, is the determinism check.

import {chromium} from 'playwright';
import {createServer} from 'node:http';
import {createHash} from 'node:crypto';
import {readFile, mkdir, rm, readdir} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {join, extname, dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, '..');

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.json': 'application/json', '.png': 'image/png',
  '.woff2': 'font/woff2', '.svg': 'image/svg+xml',
};

function serve(root) {
  return new Promise((ok) => {
    const srv = createServer(async (req, res) => {
      try {
        let p = decodeURIComponent(req.url.split('?')[0]);
        if (p.endsWith('/')) p += 'index.html';
        const file = join(root, p);
        if (!file.startsWith(root)) return res.writeHead(403).end();
        const body = await readFile(file);
        res.writeHead(200, {'Content-Type': MIME[extname(file)] ?? 'application/octet-stream'});
        res.end(body);
      } catch {
        console.error('  404', req.url);
        res.writeHead(404).end('not found');
      }
    });
    srv.listen(0, '127.0.0.1', () => ok({srv, port: srv.address().port}));
  });
}

const args = process.argv.slice(2);
const demo = args[0];
if (!demo) {
  console.error('usage: node capture.mjs <demo> [--frames N] [--hash] [--at SECONDS] [--out FILE]');
  process.exit(1);
}
const flag = (n, d = null) => {
  const i = args.indexOf(n);
  return i === -1 ? d : (args[i + 1] ?? true);
};
const has = (n) => args.includes(n);

const W = 1920, H = 1080;
// Spec-rendered films are named for the film, not the renderer, so many can coexist.
const slug = flag('--film') ? String(flag('--film')) : demo;
const framesDir = join(HERE, '.frames', slug);
const outFile = flag('--out', join(REPO, 'media', `${slug}.mp4`));

const {srv, port} = await serve(HERE);
const browser = await chromium.launch({
  args: [
    '--use-gl=angle',
    '--enable-gpu-rasterization',
    '--force-color-profile=srgb',
    '--disable-lcd-text',                 // subpixel AA is host-dependent; kills reproducibility
    '--hide-scrollbars',
  ],
});
const page = await browser.newPage({
  viewport: {width: W, height: H},
  deviceScaleFactor: 1,
});

page.on('pageerror', (e) => console.error('  page error:', e.message));
page.on('console', (m) => m.type() === 'error' && console.error('  console:', m.text()));

// --film selects a spec for the generic renderer: `capture.mjs _spec --film gpt-oss`.
const film = flag('--film');
const url = `http://127.0.0.1:${port}/${demo}/index.html?capture${film ? `&film=${film}` : ''}`;
console.log(`  page   ${url}`);
await page.goto(url, {waitUntil: 'load'});
await page.waitForFunction('window.__ready === true', null, {timeout: 30000});

const meta = await page.evaluate(() => ({fps: window.hf.fps, total: window.hf.totalFrames, dur: window.hf.duration}));
console.log(`  film   ${meta.dur}s @ ${meta.fps}fps = ${meta.total} frames`);

/** Seek to a frame and wait for the paint that follows it. */
async function seekFrame(n) {
  await page.evaluate(
    (i) => {
      window.hf.frame(i);
      return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    },
    n,
  );
}

// single still
if (has('--at')) {
  const t = Number(flag('--at'));
  await page.evaluate((tt) => {
    window.hf.seek(tt);
    return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  }, t);
  const still = join(REPO, 'media', `${demo}-t${String(t).replace('.', '_')}.png`);
  await mkdir(dirname(still), {recursive: true});
  await page.screenshot({path: still});
  console.log(`  still  ${still}`);
  await browser.close(); srv.close();
  process.exit(0);
}

const total = has('--frames') ? Math.min(Number(flag('--frames')), meta.total) : meta.total;

if (existsSync(framesDir)) await rm(framesDir, {recursive: true, force: true});
await mkdir(framesDir, {recursive: true});

const hashes = [];
const t0 = Date.now();
for (let n = 0; n < total; n++) {
  await seekFrame(n);
  const buf = await page.screenshot({type: 'png'});
  const file = join(framesDir, String(n).padStart(5, '0') + '.png');
  await (await import('node:fs/promises')).writeFile(file, buf);
  if (has('--hash')) hashes.push(createHash('sha256').update(buf).digest('hex').slice(0, 12));
  if (n % 30 === 0 || n === total - 1) {
    const pct = (((n + 1) / total) * 100).toFixed(0);
    process.stdout.write(`\r  render ${String(n + 1).padStart(4)}/${total}  ${pct}%   `);
  }
}
console.log(`\n  frames ${((Date.now() - t0) / 1000).toFixed(1)}s -> ${framesDir}`);

await browser.close();
srv.close();

if (has('--hash')) {
  const digest = createHash('sha256').update(hashes.join('')).digest('hex');
  console.log(`  digest ${digest}`);
  console.log(`  sample ${hashes.slice(0, 4).join(' ')} ...`);
}

// mux
await mkdir(dirname(outFile), {recursive: true});
const ff = spawn('ffmpeg', [
  '-y', '-framerate', String(meta.fps),
  '-i', join(framesDir, '%05d.png'),
  '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '16', '-preset', 'slow',
  '-movflags', '+faststart',
  outFile,
], {stdio: ['ignore', 'ignore', 'pipe']});
let ffErr = '';
ff.stderr.on('data', (d) => (ffErr += d));
const code = await new Promise((r) => ff.on('close', r));
if (code !== 0) {
  console.error(ffErr.split('\n').slice(-12).join('\n'));
  process.exit(1);
}
const {size} = await (await import('node:fs/promises')).stat(outFile);
console.log(`  mp4    ${outFile}  ${(size / 1e6).toFixed(1)} MB`);
