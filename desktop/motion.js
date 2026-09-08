const snapshot = document.getElementById("desktop");
let target;
window.handoffMotion.onPrepare(async (data) => {
  target = data.target;
  snapshot.src = data.image;
  try {
    await snapshot.decode();
  } catch {
    window.handoffMotion.done();
    return;
  }
  window.handoffMotion.ready();
});
window.handoffMotion.onAnimate(async () => {
  const scale = Math.min(
    target.width / innerWidth,
    target.height / innerHeight,
  );
  const x = target.x + (target.width - innerWidth * scale) / 2;
  const y = target.y + (target.height - innerHeight * scale) / 2;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  try {
    await snapshot.animate(
      reduce
        ? [{ opacity: 1 }, { opacity: 0 }]
        : [
            { transform: "translate(0px,0px) scale(1)", opacity: 1 },
            {
              transform: `translate(${x}px,${y}px) scale(${scale})`,
              opacity: 1,
            },
          ],
      {
        duration: reduce ? 180 : 650,
        easing: "cubic-bezier(0.77,0,0.175,1)",
        fill: "forwards",
      },
    ).finished;
  } finally {
    window.handoffMotion.done();
  }
});
