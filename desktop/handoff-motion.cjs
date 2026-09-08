const { BrowserWindow, ipcMain, systemPreferences } = require("electron");
const path = require("node:path");

// One temporary compositor surface; destroyed after the handoff, never polled.
async function revealCapture(win, screenshot, display) {
  if (
    !screenshot ||
    systemPreferences.getAnimationSettings().prefersReducedMotion
  ) {
    win.show();
    win.focus();
    return;
  }
  const area = display.workArea;
  const width = Math.min(
    area.width,
    Math.max(960, Math.round(area.width * 0.86)),
  );
  const height = Math.min(area.height, 940);
  win.setBounds({ x: area.x + area.width - width, y: area.y, width, height });
  const content = win.getContentBounds();
  const target = await win.webContents.executeJavaScript(`(() => {
    const r = document.querySelector('.preview').getBoundingClientRect();
    return {x:r.x,y:r.y,width:r.width,height:r.height};
  })()`);
  const overlay = new BrowserWindow({
    ...display.bounds,
    frame: false,
    transparent: true,
    show: false,
    focusable: false,
    alwaysOnTop: true,
    hasShadow: false,
    skipTaskbar: true,
    enableLargerThanScreen: true,
    webPreferences: {
      preload: path.join(__dirname, "motion-preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  overlay.setIgnoreMouseEvents(true);
  overlay.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  overlay.webContents.on("will-navigate", (e) => e.preventDefault());
  await new Promise((resolve) => {
    let timeout;
    const cleanup = () => {
      clearTimeout(timeout);
      ipcMain.removeListener("motion:ready", ready);
      ipcMain.removeListener("motion:done", done);
      if (!overlay.isDestroyed()) overlay.destroy();
      if (!win.isDestroyed()) {
        win.show();
        win.focus();
      }
      resolve();
    };
    const ready = (event) => {
      if (event.sender !== overlay.webContents) return;
      overlay.showInactive();
      win.show();
      overlay.webContents.send("motion:animate");
    };
    const done = (event) => {
      if (event.sender === overlay.webContents) cleanup();
    };
    ipcMain.on("motion:ready", ready);
    ipcMain.on("motion:done", done);
    timeout = setTimeout(cleanup, 2200);
    overlay.webContents.once("did-finish-load", () =>
      overlay.webContents.send("motion:prepare", {
        image: screenshot.data_url,
        target: {
          x: content.x + target.x - display.bounds.x,
          y: content.y + target.y - display.bounds.y,
          width: target.width,
          height: target.height,
        },
      }),
    );
    overlay.loadFile(path.join(__dirname, "motion.html")).catch(cleanup);
  });
}
module.exports = { revealCapture };
