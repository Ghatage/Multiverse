// Browser viewport input only. No script evaluation or canvas injection.
async function execute(page, action) {
  if (action.operation === 'press_key') {
    await page.keyboard.press(action.key);
    return;
  }
  const size = await page.locator('html').boundingBox();
  // viewportSize is null for CDP-attached desktop pages. The layout viewport is
  // available through a read-only CDP query, without evaluating page programs.
  const session = await page.context().newCDPSession(page);
  let viewport;
  try { viewport = (await session.send('Page.getLayoutMetrics')).cssLayoutViewport; }
  finally { await session.detach(); }
  if (!size || !viewport) throw new Error('Browser viewport unavailable');
  const points = action.operation === 'pointer_drag' ? action.points : [action];
  if (points.some(({x,y}) => x >= viewport.clientWidth || y >= viewport.clientHeight)) throw new Error('Pointer outside browser viewport');
  if (action.operation === 'pointer_click') {
    await page.mouse.click(action.x, action.y);
    return;
  }
  await page.mouse.move(points[0].x, points[0].y);
  try {
    await page.mouse.down();
    for (const point of points.slice(1)) await page.mouse.move(point.x, point.y, {steps: 3});
  } finally {
    // An interrupted stroke must not leave a held button in the next action.
    await page.mouse.up();
  }
}
module.exports = {execute};
