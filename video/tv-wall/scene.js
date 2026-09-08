// Multiverse TV wall: a ten-second dolly back from one screen to a wall of hundreds.
// Every visual is a pure function of t through the shared hf clock (../vendor/clock.js).
import {Clock, clamp01, easeStd, lerp} from '../vendor/clock.js';

const DURATION = 10, FPS = 30;
const PW = 2080, PH = 1220;          // TV pitch in wall units (screen 1920x1080 + bezel + gap)
const COLS = 30, ROWS = 24;          // 720 TVs
const HERO = {c: 15, r: 12};
const BLOCK = 6;                     // one mosaic video covers a 6x6 block of TVs
const NEAR = {c0: 13, c1: 17, r0: 11, r1: 13};  // individually driven TVs around the hero
const P = 1200;                      // CSS perspective, matches styles.css

// Real footage. hero.mp4 is the screen we start on; n01..n14 are its neighbours; m0..m3 are
// 6x6 mosaics of the same pool at different offsets. prep.sh writes all of them to assets/.
const GLOWS = ['rgba(90,170,220,.30)', 'rgba(255,150,70,.28)', 'rgba(130,200,140,.26)', 'rgba(170,130,240,.30)',
  'rgba(230,90,90,.26)', 'rgba(120,180,255,.28)', 'rgba(240,200,120,.26)', 'rgba(150,230,220,.26)'];
const near = [{src: 'assets/near/hero.mp4', off: 0.0}];
for (let i = 1; i <= 14; i++) near.push({src: `assets/near/n${String(i).padStart(2, '0')}.mp4`, off: (i * 0.73) % 2.5});
const mosaics = [0, 1, 2, 3].map((i) => `assets/mosaic/m${i}.mp4`);

// Deterministic pseudo-random for layout jitter.
let seed = 7;
const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;

const wall = document.getElementById('wall');
const videos = [];   // {el, off}

const cellLeft = (c) => (c - HERO.c) * PW - PW / 2;
const cellTop = (r) => (r - HERO.r) * PH - PH / 2;

function makeTV({left, top, width, height, z, src, off, glow, mosaic, hero}) {
  const tv = document.createElement('div');
  tv.className = 'tv' + (mosaic ? ' mosaic' : '');
  tv.style.left = left + 'px'; tv.style.top = top + 'px';
  tv.style.width = width + 'px'; tv.style.height = height + 'px';
  tv.style.transform = `translateZ(${z}px)`;
  tv.style.setProperty('--glow', glow);
  const screen = document.createElement('div');
  screen.className = 'screen';
  const v = document.createElement('video');
  v.muted = true; v.playsInline = true; v.preload = 'auto'; v.dataset.src = src;
  rnd();
  screen.append(v);
  const gloss = document.createElement('div'); gloss.className = 'gloss'; screen.append(gloss);
  if (hero) screen.append(buildChrome());
  tv.append(screen);
  if (!mosaic) { const led = document.createElement('div'); led.className = 'led'; tv.append(led); }
  wall.append(tv);
  videos.push({el: v, off});
  return tv;
}

// Mosaic blocks first (behind), then the individually driven near TVs on top.
for (let by = 0; by < ROWS / BLOCK; by++) {
  for (let bx = 0; bx < COLS / BLOCK; bx++) {
    const central = bx >= 1 && bx <= 3;   // the M lives here: keep flat
    const z = central ? 0 : lerp(-110, 70, rnd());
    const variant = (bx * 3 + by * 5 + Math.floor(rnd() * 2)) % mosaics.length;
    makeTV({
      left: cellLeft(bx * BLOCK), top: cellTop(by * BLOCK),
      width: BLOCK * PW, height: BLOCK * PH, z,
      src: mosaics[variant], off: rnd() * 1.5, glow: GLOWS[(bx + by * 2) % GLOWS.length], mosaic: true,
    });
  }
}
let n = 1;
for (let r = NEAR.r0; r <= NEAR.r1; r++) {
  for (let c = NEAR.c0; c <= NEAR.c1; c++) {
    const hero = c === HERO.c && r === HERO.r;
    const clip = hero ? near[0] : near[n++];
    if (c === 14 && r === 12) { clip.src = 'assets/near/vector.mp4'; clip.off = 0; }
    if (c === 16 && r === 12) { clip.src = 'assets/near/n02.mp4'; clip.off = 0; }
    makeTV({
      left: cellLeft(c) + 20, top: cellTop(r) + 10, width: 2040, height: 1200,
      z: hero ? 40 : lerp(6, 30, rnd()),
      src: clip.src, off: clip.off, glow: GLOWS[(c * 5 + r * 3) % GLOWS.length], hero,
    });
  }
}


// ---- final mark: screens outside a 13x15 M go dark; exact phrase holds below ----
const M_ROWS = [
  'XXXX.....XXXX', 'XXXX.....XXXX', 'XXXXX...XXXXX', 'XXXXX...XXXXX', 'XXX.XX.XX.XXX', 'XXX.XX.XX.XXX',
  'XXX..XXX..XXX', 'XXX...X...XXX', 'XXX.......XXX', 'XXX.......XXX', 'XXX.......XXX', 'XXX.......XXX',
  'XXX.......XXX', 'XXX.......XXX', 'XXX.......XXX'];
const M_C0 = HERO.c - 6, M_R0 = HERO.r - 7;
const inM = (c, r) => (M_ROWS[r - M_R0] ?? '').charAt(c - M_C0) === 'X';
// Screen-sized mask avoids rasterizing a 62400 x 29280 SVG in the 3D layer tree.
// Project each cell through the same camera as the CSS wall.
const mask = document.createElement('canvas');
mask.width = 1920; mask.height = 1080; mask.id = 'mask';
document.getElementById('room').append(mask);
const ctx = mask.getContext('2d');
function paintMask(dx, dy, d, rx, ry, opacity) {
  ctx.clearRect(0, 0, 1920, 1080);
  if (opacity <= 0) return;
  const ax = rx * Math.PI / 180, ay = ry * Math.PI / 180;
  function project(x, y) {
    const yy = y * Math.cos(ax) - 80 * Math.sin(ax);
    const zz = y * Math.sin(ax) + 80 * Math.cos(ax);
    const xx = x * Math.cos(ay) + zz * Math.sin(ay) + dx;
    const z = -x * Math.sin(ay) + zz * Math.cos(ay) - d;
    const scale = P / (P - z);
    return [960 + xx * scale, 540 + (yy + dy) * scale];
  }
  ctx.fillStyle = `rgba(5,5,7,${opacity})`;
  // One compound path prevents doubled alpha along shared cell boundaries.
  ctx.beginPath();
  for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) {
    if (inM(c, r)) continue;
    const x = cellLeft(c), y = cellTop(r);
    const points = [[x,y],[x+PW,y],[x+PW,y+PH],[x,y+PH]].map(([x,y]) => project(x,y));
    ctx.moveTo(...points[0]);
    for (const point of points.slice(1)) ctx.lineTo(...point);
    ctx.closePath();
  }
  ctx.fill();
}

function buildChrome() {
  const el = document.createElement('div');
  el.id = 'chrome';
  el.innerHTML = `
    <div class="top"><div class="title"><div class="avatar"></div>Tears of Steel — Blender Foundation (2012)</div></div>
    <div class="bottom">
      <div class="bar"><div class="buffered"></div><div class="played"></div><div class="knob"></div></div>
      <div class="controls">
        <svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>
        <svg viewBox="0 0 24 24"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
        <svg viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4z"/></svg>
        <div class="vol"></div>
        <div class="time">8:10 / 12:14</div>
        <div class="spacer"></div>
        <svg viewBox="0 0 24 24"><path d="M19 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zm-8 7H9.5v-.5h-2v3h2V13H11v1a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1zm7 0h-1.5v-.5h-2v3h2V13H18v1a1 1 0 0 1-1 1h-3a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1z"/></svg>
        <svg viewBox="0 0 24 24"><path d="M19.4 13a7.6 7.6 0 0 0 0-2l2.1-1.6-2-3.5-2.5 1a7.4 7.4 0 0 0-1.7-1l-.4-2.7h-4l-.4 2.7a7.4 7.4 0 0 0-1.7 1l-2.5-1-2 3.5L6.6 11a7.6 7.6 0 0 0 0 2l-2.1 1.6 2 3.5 2.5-1a7.4 7.4 0 0 0 1.7 1l.4 2.7h4l.4-2.7a7.4 7.4 0 0 0 1.7-1l2.5 1 2-3.5L19.4 13zM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7z"/></svg>
        <svg viewBox="0 0 24 24"><path d="M21 3H3a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h18a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm0 16H3V5h18v14zm-2-8h-6v5h6v-5z"/></svg>
        <svg viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
      </div>
    </div>`;
  return el;
}

// ---- camera: monotone cubic on ln(scale) so the dolly never stalls or overshoots ----
const KEYS = [[0, 1], [1.0, 1], [2.0, 0.90], [3.5, 0.50], [5.0, 0.30], [6.8, 0.090], [8.0, 0.048], [8.6, 0.0425], [10, 0.042]];
const xs = KEYS.map((k) => k[0]), ys = KEYS.map((k) => Math.log(k[1]));
const ms = (() => {                       // Fritsch–Carlson tangents
  const d = [], m = [];
  for (let i = 0; i < xs.length - 1; i++) d.push((ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]));
  m[0] = d[0]; m[xs.length - 1] = d[d.length - 1];
  for (let i = 1; i < xs.length - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < d.length; i++) {
    if (d[i] === 0) { m[i] = m[i + 1] = 0; continue; }
    const a = m[i] / d[i], b = m[i + 1] / d[i], s = a * a + b * b;
    if (s > 9) { const tau = 3 / Math.sqrt(s); m[i] = tau * a * d[i]; m[i + 1] = tau * b * d[i]; }
  }
  return m;
})();
function scaleAt(t) {
  t = Math.max(xs[0], Math.min(xs[xs.length - 1], t));
  let i = 0; while (i < xs.length - 2 && t > xs[i + 1]) i++;
  const h = xs[i + 1] - xs[i], u = (t - xs[i]) / h;
  const h00 = 2 * u ** 3 - 3 * u ** 2 + 1, h10 = u ** 3 - 2 * u ** 2 + u, h01 = -2 * u ** 3 + 3 * u ** 2, h11 = u ** 3 - u ** 2;
  return Math.exp(h00 * ys[i] + h10 * h * ms[i] + h01 * ys[i + 1] + h11 * h * ms[i + 1]);
}

const chrome = document.getElementById('chrome');
const haze = document.getElementById('haze');
const dim = document.getElementById('dim');
const vignette = document.getElementById('vignette');
const clock = new Clock({duration: DURATION, fps: FPS, onSeek: paint}).expose();

function paint(t) {
  const s = scaleAt(t);
  const d = P * (1 / s - 1);
  const drift = easeStd(clamp01((t - 1) / 9));
  const dx = -40 * drift / s, dy = (14 * drift - 94 * easeStd(clamp01((t - 7) / 1.5))) / s;              // slow lateral glide, in screen px
  const ry = -2.0 * drift, rx = 1.0 * drift;
  wall.style.transform = `translate3d(${dx}px, ${dy}px, ${-d}px) rotateY(${ry}deg) rotateX(${rx}deg)`;
  chrome.style.opacity = String(1 - easeStd(clamp01((t - 1.3) / 0.6)));
  haze.style.opacity = String(0.55 * easeStd(clamp01((t - 4) / 5)));
  dim.style.opacity = String(0.28 * easeStd(clamp01((t - 3) / 6)));
  const off = 0.995 * easeStd(clamp01((t - 7.8) / 0.7));
  paintMask(dx, dy, d, rx, ry, off);
  // Rasterize the lockup with the mask: no late-visible DOM text layer to go stale.
  ctx.save();
  ctx.globalAlpha = easeStd(clamp01((t - 8.0) / 0.5));
  ctx.textAlign = 'center';
  ctx.fillStyle = '#ece6f6';
  ctx.shadowColor = 'rgba(190,170,255,.35)'; ctx.shadowBlur = 22;
  ctx.font = '600 64px Geist';
  ctx.fillText('Multiverse:', 960, 949);
  ctx.font = '450 44px Geist';
  ctx.fillText('Your Desktop. Forkable', 960, 1016);
  ctx.restore();
  vignette.style.opacity = String(lerp(0.25, 1, easeStd(clamp01((t - 2) / 6))));
  if (clock.mode === 'capture') {
    for (const {el, off} of videos) {
      const target = Math.min(off + t, el.duration - 1 / FPS);
      if (Math.abs(el.currentTime - target) > 1e-4) el.currentTime = target;
    }
  }
}

const seeked = (v) => new Promise((ok) => {
  if (!v.seeking) return ok();
  v.addEventListener('seeked', () => ok(), {once: true});
});
window.hf.seekAsync = async (t) => {
  clock.seek(t);
  await Promise.all(videos.map(({el}) => seeked(el)));
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
};

const fit = () => (document.getElementById('stage').style.transform = `scale(${Math.min(innerWidth / 1920, innerHeight / 1080)})`);
addEventListener('resize', fit); fit();

// Load every clip fully into memory first so per-frame seeks never wait on the network.
const blobs = new Map();
await Promise.all([...new Set(videos.map((v) => v.el.dataset.src))].map(async (src) => {
  const b = await (await fetch(src)).blob();
  blobs.set(src, URL.createObjectURL(b));
}));
await Promise.all(videos.map(({el}) => new Promise((ok, fail) => {
  el.addEventListener('loadeddata', () => ok(), {once: true});
  el.addEventListener('error', () => fail(new Error('video failed: ' + el.dataset.src)), {once: true});
  el.src = blobs.get(el.dataset.src);
})));
await document.fonts.load('600 64px Geist');
await document.fonts.load('450 44px Geist');
if (clock.mode === 'capture') {
  await window.hf.seekAsync(0);
} else {
  for (const {el, off} of videos) { el.currentTime = off; el.loop = true; el.play().catch(() => {}); }
  clock.play();
}
window.__ready = true;
