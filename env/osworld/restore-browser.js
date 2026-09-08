const fs = require('fs');
const {chromium} = require('playwright-core');
(async () => {
  const storage = JSON.parse(fs.readFileSync('/state/browser-storage.json', 'utf8'));
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const context = browser.contexts()[0];
  await context.addCookies(storage.cookies || []);
  await context.addInitScript(({origins, sessions}) => {
    const marker = '__fork_storage_restored';
    if (sessionStorage.getItem(marker)) return;
    const origin = origins.find(x => x.origin === location.origin);
    for (const entry of origin?.localStorage || []) localStorage.setItem(entry.name, entry.value);
    const session = sessions.find(x => x.url === location.href);
    for (const [key, value] of Object.entries(session?.values || {})) sessionStorage.setItem(key, value);
    sessionStorage.setItem(marker, '1');
  }, {origins:storage.origins || [], sessions:storage.sessions || []});
  const initial = context.pages();
  const tabs = JSON.parse(fs.readFileSync('/state/tabs.json', 'utf8')).tabs;
  let active;
  for (const tab of tabs) {
    const page = await context.newPage();
    await page.goto(tab.url, {waitUntil:'domcontentloaded'});
    if (tab.active) active = page;
  }
  if (tabs.length) for (const page of initial) await page.close();
  if (active) await active.bringToFront();
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
