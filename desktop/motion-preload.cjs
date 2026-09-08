const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("handoffMotion", {
  onPrepare: (callback) =>
    ipcRenderer.on("motion:prepare", (_e, data) => callback(data)),
  onAnimate: (callback) => ipcRenderer.on("motion:animate", () => callback()),
  ready: () => ipcRenderer.send("motion:ready"),
  done: () => ipcRenderer.send("motion:done"),
});
