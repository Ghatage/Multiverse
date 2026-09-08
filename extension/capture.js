export function transferableUrl(raw) {
  try {
    const url = new URL(raw);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password;
  } catch {
    return false;
  }
}

export function originPattern(raw) {
  if (!transferableUrl(raw)) return null;
  const url = new URL(raw);
  return `${url.protocol}//${url.hostname}/*`;
}

// Chrome serializes this function into the page's isolated world. Keep it self-contained.
export function capturePageState() {
  const state = {scroll: {x: Math.max(0, window.scrollX), y: Math.max(0, window.scrollY)}};
  const videos = [...document.querySelectorAll("video")];
  // Prefer an actively playing player, then the largest visible player.
  const candidates = videos.filter(video => {
    const bounds = video.getBoundingClientRect();
    return bounds.width > 0 && bounds.height > 0 && Number.isFinite(video.currentTime);
  }).sort((a, b) => {
    if (a.paused !== b.paused) return a.paused ? 1 : -1;
    const ar = a.getBoundingClientRect();
    const br = b.getBoundingClientRect();
    return br.width * br.height - ar.width * ar.height;
  });
  if (candidates.length) {
    const video = candidates[0];
    state.video = {
      current_time: Math.max(0, video.currentTime),
      paused: video.paused,
      playback_rate: Number.isFinite(video.playbackRate) ? video.playbackRate : 1,
    };
  }
  return state;
}
