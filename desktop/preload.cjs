const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("multiverse", {
  action: (name, payload) => ipcRenderer.invoke("action", name, payload),
  onState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on("state", listener);
    return () => ipcRenderer.removeListener("state", listener);
  },
});
