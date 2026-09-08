import {originPattern} from "./capture.js";
const token = document.querySelector("#token");
const status = document.querySelector("#status");
const stored = await chrome.storage.local.get("pairingToken");
token.value = stored.pairingToken || "";
async function renderStatus() {
  const {bridgeStatus} = await chrome.storage.session.get("bridgeStatus");
  status.textContent = bridgeStatus || "Not connected.";
}
await renderStatus();
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.bridgeStatus) void renderStatus();
});
document.querySelector("#pair").addEventListener("submit", async event => {
  event.preventDefault();
  const pairingToken = token.value.trim();
  if (!pairingToken) return;
  await chrome.storage.local.set({pairingToken});
  await chrome.runtime.sendMessage({type: "reconnect"});
});
document.querySelector("#unpair").addEventListener("click", async () => {
  await chrome.storage.local.remove("pairingToken");
  token.value = "";
});
const tabs = (await chrome.tabs.query({})).filter(tab => !tab.incognito);
const patterns = [...new Set(tabs.map(tab => originPattern(tab.url)).filter(Boolean))];
for (const pattern of patterns) {
  if (pattern.startsWith("http://127.0.0.1/")) continue;
  const row = document.createElement("div");
  row.className = "site";
  const label = document.createElement("span");
  label.textContent = pattern.replace(/\/\*$/, "");
  const button = document.createElement("button");
  let granted = await chrome.permissions.contains({origins: [pattern]});
  button.textContent = granted ? "Remove access" : "Allow page state";
  button.addEventListener("click", async () => {
    if (granted) await chrome.permissions.remove({origins: [pattern]});
    else await chrome.permissions.request({origins: [pattern]});
    granted = await chrome.permissions.contains({origins: [pattern]});
    button.textContent = granted ? "Remove access" : "Allow page state";
  });
  row.append(label, button);
  document.querySelector("#sites").append(row);
}
