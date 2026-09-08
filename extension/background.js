import {capturePageState, originPattern, transferableUrl} from "./capture.js";

const BRIDGE = "ws://127.0.0.1:17866/extension";
let socket;
let heartbeat;
let connecting = false;
let paired = false;
let capturing = false;

async function status(message) {
  await chrome.storage.session.set({bridgeStatus: message});
}

async function captureWorkspace() {
  const warnings = [];
  const captured_at = new Date().toISOString();
  const allTabs = (await chrome.tabs.query({})).filter(tab => !tab.incognito);
  const tabs = [];
  const deadline = performance.now() + 3000;
  // Sequential injection avoids a resource spike across many open tabs.
  for (const tab of allTabs) {
    if (!transferableUrl(tab.url)) {
      warnings.push(`Skipped non-web or unavailable tab: ${tab.title || "untitled"}`);
      continue;
    }
    const entry = {
      url: tab.url, title: tab.title || "", active: Boolean(tab.active),
      window_id: tab.windowId, index: tab.index,
    };
    const pattern = originPattern(tab.url);
    const allowed = await chrome.permissions.contains({origins: [pattern]});
    if (allowed && !tab.discarded && performance.now() < deadline) {
      try {
        const result = await Promise.race([
          chrome.scripting.executeScript({target: {tabId: tab.id}, func: capturePageState}),
          new Promise((_, reject) => setTimeout(() => reject(new Error("Page capture timed out")), Math.min(500, deadline - performance.now()))),
        ]);
        // Navigation during capture must not attach a different page's state to this URL.
        const latest = await chrome.tabs.get(tab.id);
        if (latest.url === tab.url && result[0]?.result) Object.assign(entry, result[0].result);
        else warnings.push(`Page changed during capture; URL only: ${tab.title || tab.url}`);
      } catch {
        warnings.push(`Page state unavailable; URL only: ${tab.title || tab.url}`);
      }
    } else {
      const reason = !allowed ? "Site access not granted" : tab.discarded ? "Suspended tab" : "Page-state capture time budget reached";
      warnings.push(`${reason}; URL only: ${tab.title || tab.url}`);
    }
    tabs.push(entry);
  }
  return {captured_at, tabs, warnings};
}

function disconnect() {
  clearInterval(heartbeat);
  heartbeat = undefined;
  paired = false;
  socket?.close();
  socket = undefined;
}

async function connect() {
  if (connecting || socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return;
  connecting = true;
  try {
    const {pairingToken} = await chrome.storage.local.get("pairingToken");
    if (!pairingToken) { await status("Not paired. Enter the token from Multiverse."); return; }
    const current = new WebSocket(BRIDGE);
    socket = current;
    current.onopen = () => current.send(JSON.stringify({type: "pair", token: pairingToken}));
    current.onmessage = async event => {
      if (socket !== current) return;
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === "paired") {
        paired = true;
        await status("Connected. Capture starts only when you transfer in Multiverse.");
        clearInterval(heartbeat);
        heartbeat = setInterval(() => {
          if (current.readyState === WebSocket.OPEN) current.send(JSON.stringify({type: "ping"}));
        }, 20000);
      } else if (message.type === "capture" && paired && typeof message.request_id === "string" && !capturing) {
        capturing = true;
        try {
          const payload = await captureWorkspace();
          if (current.readyState === WebSocket.OPEN) {
            current.send(JSON.stringify({type: "capture_result", request_id: message.request_id, payload}));
          }
        } catch (error) {
          if (current.readyState === WebSocket.OPEN) {
            current.send(JSON.stringify({type: "capture_error", request_id: message.request_id, error: String(error.message || error)}));
          }
        } finally { capturing = false; }
      }
    };
    current.onclose = () => {
      if (socket !== current) return;
      disconnect();
      void status("Disconnected. Open Multiverse; reconnects within 30 seconds.");
    };
    current.onerror = () => current.close();
  } finally { connecting = false; }
}

chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "bridge-reconnect") void connect(); });
chrome.runtime.onStartup.addListener(() => void connect());
chrome.runtime.onInstalled.addListener(() => {
  void chrome.alarms.create("bridge-reconnect", {periodInMinutes: 0.5});
  void connect();
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.pairingToken) { disconnect(); void connect(); }
});
chrome.runtime.onMessage.addListener(message => {
  if (message.type === "reconnect") { disconnect(); void connect(); }
});
void chrome.alarms.create("bridge-reconnect", {periodInMinutes: 0.5});
void connect();
