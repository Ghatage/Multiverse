// The deterministic frame clock.
//
// Every visual on the page is a pure function of t (seconds). Nothing reads wall time except the
// live-mode driver below, and nothing may animate via CSS keyframes that affects clock-owned state.
// That constraint is what lets the same file be a live page and a reproducible film source.
//
//   live     — rAF advances t from wall clock. This is the page you hand someone.
//   capture  — the harness calls seek(t) per frame and screenshots. Wall clock never advances t.

export class Clock {
  constructor({duration, fps = 30, onSeek}) {
    this.duration = duration;
    this.fps = fps;
    this.onSeek = onSeek;
    this.t = 0;
    this.mode = new URLSearchParams(location.search).get('capture') !== null ? 'capture' : 'live';
    this._raf = null;
    this._t0 = null;
  }

  seek(t) {
    this.t = Math.max(0, Math.min(this.duration, t));
    this.onSeek(this.t);
    return this.t;
  }

  frame(n) {
    return this.seek(n / this.fps);
  }

  play() {
    if (this.mode === 'capture') return;      // capture drives seek() itself
    const step = (now) => {
      if (this._t0 === null) this._t0 = now;
      const t = (now - this._t0) / 1000;
      this.seek(t % this.duration);           // loop, so a handed-out page never dead-ends
      this._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
  }

  pause() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
  }

  /**
   * Walk the whole timeline once before anything is captured.
   *
   * An element that sits at opacity 0 until mid-film has never been painted, so the frame where it
   * first becomes visible races the compositor: sometimes its text layer is rasterized in time,
   * sometimes a frame later. That showed up as exactly one frame in ninety differing between runs —
   * enough to break frame-hash equality while being invisible to the eye.
   *
   * Visiting every part of the timeline forces each element to be painted at least once, so the
   * glyph atlases and compositor layers all exist before frame 0 is taken. Costs ~250ms at load.
   */
  async warmup(samples = 16) {
    for (let i = 0; i <= samples; i++) {
      this.seek((i / samples) * this.duration);
      await new Promise((r) => requestAnimationFrame(r));
    }
    this.seek(0);
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  }

  // Exposed to the capture harness on window.hf
  expose() {
    window.hf = {
      duration: this.duration,
      fps: this.fps,
      mode: this.mode,
      seek: (t) => this.seek(t),
      frame: (n) => this.frame(n),
      totalFrames: Math.round(this.duration * this.fps),
    };
    return this;
  }
}

// ---- timing helpers: all pure, all f(t) ----

export const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);

/** Normalized progress through [start, start+dur], clamped. */
export const at = (t, start, dur) => clamp01((t - start) / dur);

/** Staggered progress for item i (entrance system). */
export const stagger = (t, start, dur, i, step) => at(t, start + i * step, dur);

export const easeOut = (x) => 1 - Math.pow(1 - x, 3);
export const easeInOut = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
export const easeStd = (x) => {
  // approximates cubic-bezier(.25,.1,.25,1) closely enough for JS-driven values
  return x * x * (3 - 2 * x);
};

export const lerp = (a, b, x) => a + (b - a) * x;

/** Which entry of a keyed timeline is active at t, plus local progress. */
export function phase(t, marks) {
  let i = 0;
  for (let k = 0; k < marks.length; k++) if (t >= marks[k].at) i = k;
  const cur = marks[i];
  const next = marks[i + 1];
  const span = (next ? next.at : cur.at + (cur.dur ?? 1)) - cur.at;
  return {index: i, mark: cur, p: clamp01((t - cur.at) / span)};
}
