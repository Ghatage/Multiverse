import test from "node:test";
import assert from "node:assert/strict";
import {capturePageState, originPattern, transferableUrl} from "./capture.js";

test("transfer excludes privileged pages and embedded URL credentials", () => {
  for (const raw of ["chrome://settings", "file:///tmp/private", "javascript:alert(1)", "not a URL", "https://user:secret@example.com/"]) {
    assert.equal(transferableUrl(raw), false);
    assert.equal(originPattern(raw), null);
  }
  assert.equal(transferableUrl("https://example.com/watch?v=1&t=25"), true);
  assert.equal(originPattern("https://example.com:8443/watch?v=1"), "https://example.com/*");
});

test("capture prefers playing video and returns only permitted state fields", () => {
  globalThis.window = {scrollX: 12, scrollY: 35};
  const video = (paused, size, time) => ({paused, currentTime: time, playbackRate: 1.5, getBoundingClientRect: () => ({width: size, height: size})});
  globalThis.document = {querySelectorAll: () => [video(true, 800, 5), video(false, 300, 42), video(false, 0, 99)]};
  assert.deepEqual(capturePageState(), {scroll: {x: 12, y: 35}, video: {current_time: 42, paused: false, playback_rate: 1.5}});
});

test("no main-frame video produces scroll-only state", () => {
  globalThis.window = {scrollX: -1, scrollY: 0};
  globalThis.document = {querySelectorAll: () => []};
  assert.deepEqual(capturePageState(), {scroll: {x: 0, y: 0}});
});
