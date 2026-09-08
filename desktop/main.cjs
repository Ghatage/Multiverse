const {
  app,
  BrowserWindow,
  ipcMain,
  desktopCapturer,
  screen,
  globalShortcut,
  systemPreferences,
  shell,
} = require("electron");
const { spawn, execFile } = require("node:child_process");
const { promisify } = require("node:util");
const crypto = require("node:crypto");
const path = require("node:path");
const fs = require("node:fs");
const net = require("node:net");
const { revealCapture } = require("./handoff-motion.cjs");
const run = promisify(execFile);
const root =
  process.env.MULTIVERSE_REPO_ROOT ||
  (app.isPackaged
    ? require("./runtime-config.json").repoRoot
    : path.resolve(__dirname, ".."));
const { WebSocketServer } = require("ws");
const pairToken = crypto.randomBytes(18).toString("hex");
let extensionSocket = null,
  bridge = null;
const pending = new Map();
let taskPrompt = "";
let promptWindow = null;
let currentJob = null;
const taskActive = (task) =>
  task && ["queued", "running"].includes(task.status);
function rememberHandoff(id) {
  if (/^[a-f0-9]{32}$/.test(id))
    fs.writeFileSync(
      path.join(app.getPath("userData"), "last-handoff.json"),
      JSON.stringify({ id }),
    );
}
async function monitorHandoff(id, expectTask = false) {
  let missingTaskPolls = 0;
  for (let n = 0; n < 1800; n++) {
    currentJob = await request("/api/handoffs/" + id);
    emit("handoff", { job: currentJob });
    if (currentJob.status === "failed") return currentJob;
    if (["ready", "partial"].includes(currentJob.status)) {
      if (expectTask && !currentJob.task && ++missingTaskPolls < 10) {
        await sleep(1000);
        continue;
      }
      if (!taskActive(currentJob.task)) return currentJob;
    }
    await sleep(1000);
  }
  throw Error(
    "The workspace is still running. Reopen Multiverse to check its status.",
  );
}
async function resumeHandoff() {
  const saved = path.join(app.getPath("userData"), "last-handoff.json");
  if (!fs.existsSync(saved) || busy) return;
  let id;
  try {
    id = JSON.parse(fs.readFileSync(saved, "utf8")).id;
  } catch {
    return;
  }
  if (!/^[a-f0-9]{32}$/.test(id)) return;
  busy = true;
  try {
    await monitorHandoff(id);
  } catch (e) {
    emit("error", { message: e.message });
  } finally {
    busy = false;
  }
}
async function startTask(prompt) {
  if (busy || !currentJob || !["ready", "partial"].includes(currentJob.status))
    throw Error("Wait for the desktop transfer to finish.");
  if (typeof prompt !== "string" || !prompt.trim() || prompt.length > 16000)
    throw Error("Enter a task (up to 16,000 characters).");
  busy = true;
  emit("working", { message: "Starting your task…" });
  try {
    await request("/api/handoffs/" + currentJob.id + "/task", {
      prompt: prompt.trim(),
    });
    taskPrompt = "";
    emit("prompt-consumed", {});
    return await monitorHandoff(currentJob.id, true);
  } finally {
    busy = false;
  }
}
let shortcutStatus = "Starting shortcut…";
function startBridge() {
  bridge = new WebSocketServer({
    host: "127.0.0.1",
    port: 17866,
    maxPayload: 2 * 1024 * 1024,
  });
  bridge.on("error", (e) =>
    emit("extension", { connected: false, error: e.message, token: pairToken }),
  );
  bridge.on("connection", (socket) => {
    let paired = false;
    const timer = setTimeout(() => {
      if (!paired) socket.close();
    }, 5000);
    socket.on("message", (raw) => {
      try {
        const m = JSON.parse(raw.toString());
        if (!paired) {
          if (
            m.type !== "pair" ||
            typeof m.token !== "string" ||
            m.token.length !== pairToken.length ||
            !crypto.timingSafeEqual(
              Buffer.from(m.token),
              Buffer.from(pairToken),
            )
          ) {
            socket.close();
            return;
          }
          paired = true;
          clearTimeout(timer);
          if (extensionSocket) extensionSocket.close();
          extensionSocket = socket;
          socket.send(JSON.stringify({ type: "paired" }));
          emit("extension", { connected: true, token: pairToken });
          return;
        }
        if (m.type === "ping") socket.send(JSON.stringify({ type: "pong" }));
        if (m.type === "capture_result" && pending.has(m.request_id)) {
          pending.get(m.request_id)(m.payload);
          pending.delete(m.request_id);
        }
      } catch {
        socket.close();
      }
    });
    socket.on("close", () => {
      clearTimeout(timer);
      if (extensionSocket === socket) {
        extensionSocket = null;
        emit("extension", { connected: false, token: pairToken });
      }
    });
  });
}
async function extensionCapture() {
  if (!extensionSocket || extensionSocket.readyState !== 1) return null;
  const request_id = crypto.randomUUID();
  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      pending.delete(request_id);
      resolve(null);
    }, 8000);
    pending.set(request_id, (payload) => {
      clearTimeout(timeout);
      if (payload && Array.isArray(payload.tabs))
        resolve({
          tabs: payload.tabs,
          warnings: Array.isArray(payload.warnings) ? payload.warnings : [],
        });
      else resolve(null);
    });
    extensionSocket.send(JSON.stringify({ type: "capture", request_id }));
  });
}

const token = crypto.randomBytes(32).toString("hex");
const port = 17865,
  origin = `http://127.0.0.1:${port}`;
let win,
  child,
  serviceReady = false,
  busy = false,
  lastCapture = null,
  serviceError = "",
  log = "";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function emit(type, data) {
  if (win && !win.isDestroyed())
    win.webContents.send("state", { type, ...data });
}
async function request(route, body) {
  if (!serviceReady)
    throw Error(
      serviceError || "Local service is starting. Try again shortly.",
    );
  const response = await fetch(origin + route, {
    method: body ? "POST" : "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(15000),
  });
  const data = await response.json();
  if (!response.ok)
    throw Error(
      typeof (data.detail || data.error) === "string"
        ? data.detail || data.error
        : JSON.stringify(
            data.detail || data.error || `Service returned ${response.status}`,
          ),
    );
  return data;
}
async function startService() {
  const free = await new Promise((resolve) => {
    const s = net.createServer();
    s.once("error", () => resolve(false));
    s.listen(port, "127.0.0.1", () => s.close(() => resolve(true)));
  });
  if (!free)
    throw Error(
      `Port ${port} is already in use. Close the other Multiverse service and reopen this app.`,
    );
  const python = path.join(root, ".venv", "bin", "python");
  if (!fs.existsSync(python))
    throw Error(
      "Python environment missing. Run uv sync in the Multiverse repository.",
    );
  child = spawn(python, ["-m", "fork.handoff.server", "--port", String(port)], {
    cwd: root,
    env: { ...process.env, MULTIVERSE_HANDOFF_TOKEN: token },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.on("data", (b) => {
    log = (log + b).slice(-8000);
  });
  child.stderr.on("data", (b) => {
    log = (log + b).slice(-8000);
  });
  child.on("error", (e) => {
    serviceError = e.message;
  });
  child.on("exit", (code) => {
    serviceReady = false;
    serviceError = `Local service stopped (${code}). ${log.slice(-800)}`;
    emit("service", { ready: false, error: serviceError });
  });
  for (let n = 0; n < 120; n++) {
    if (child.exitCode !== null) throw Error(serviceError || log);
    try {
      const r = await fetch(origin + "/health", {
        headers: { Authorization: `Bearer ${token}` },
        signal: AbortSignal.timeout(500),
      });
      const d = await r.json();
      if (r.ok && d.service === "multiverse-handoff") {
        const auth = await fetch(
          origin + "/api/handoffs/00000000000000000000000000000000",
          {
            headers: { Authorization: `Bearer ${token}` },
            signal: AbortSignal.timeout(1000),
          },
        );
        if (auth.status !== 404)
          throw Error("Service ownership could not be verified");
        serviceReady = true;
        emit("service", { ready: true });
        resumeHandoff();
        return;
      }
    } catch {}
    await sleep(250);
  }
  throw Error("Local service did not start. " + log.slice(-800));
}
async function chromeTabs() {
  if (process.platform !== "darwin")
    return {
      tabs: [],
      warnings: [
        "Automatic browser capture currently supports Chrome on macOS.",
      ],
    };
  const script = `const chrome=Application('Google Chrome');if(!chrome.running()){JSON.stringify({tabs:[],warnings:['Chrome is not running.']});}else{const result=[];const wins=chrome.windows();for(let w=0;w<wins.length;w++){const tabs=wins[w].tabs();for(let i=0;i<tabs.length;i++)result.push({url:tabs[i].url(),title:tabs[i].title(),active:w===0&&i+1===wins[w].activeTabIndex(),window_id:wins[w].id(),index:i});}JSON.stringify({tabs:result,warnings:['Video position and scroll require the optional Chrome extension. Logins and unsaved edits are not transferred.']});}`;
  try {
    const { stdout } = await run(
      "/usr/bin/osascript",
      ["-l", "JavaScript", "-e", script],
      { timeout: 15000, maxBuffer: 2 * 1024 * 1024 },
    );
    return JSON.parse(stdout);
  } catch (e) {
    return {
      tabs: [],
      warnings: [
        "Chrome tab access was unavailable. Allow Multiverse/Electron under System Settings → Privacy & Security → Automation, then retry.",
      ],
    };
  }
}
async function capture(showAfter = true, animate = false) {
  if (busy) throw Error("A transfer is already running.");
  busy = true;
  emit("working", { message: "Capturing this desktop…" });
  try {
    const wasVisible = win.isVisible();
    win.hide();
    await sleep(220);
    const display = screen.getDisplayNearestPoint(
      screen.getCursorScreenPoint(),
    );
    let screenshot,
      warnings = [];
    try {
      if (
        process.platform === "darwin" &&
        systemPreferences.getMediaAccessStatus("screen") === "denied"
      )
        throw Error("Screen recording denied");
      const sources = await desktopCapturer.getSources({
        types: ["screen"],
        thumbnailSize: { width: 1920, height: 1200 },
      });
      if (
        process.platform === "darwin" &&
        systemPreferences.getMediaAccessStatus("screen") !== "granted"
      )
        throw Error("Screen recording permission is required");
      const source =
        sources.find((s) => s.display_id === String(display.id)) || sources[0];
      if (!source || source.thumbnail.isEmpty()) throw Error("empty");
      const size = source.thumbnail.getSize();
      screenshot = { data_url: source.thumbnail.toDataURL(), ...size };
    } catch {
      warnings.push(
        "Desktop preview unavailable. Enable Screen Recording for this app in System Settings, then restart it.",
      );
    }
    const browser = (await extensionCapture()) || (await chromeTabs());
    warnings.push(...browser.warnings);
    const validTabs = browser.tabs
      .filter((t) => {
        try {
          const u = new URL(t.url);
          return (
            ["http:", "https:"].includes(u.protocol) &&
            !u.username &&
            !u.password &&
            t.url.length <= 16384 &&
            !/[\x00-\x1f]/.test(t.url)
          );
        } catch {
          return false;
        }
      })
      .map((t) => ({
        ...t,
        title: typeof t.title === "string" ? t.title.slice(0, 4096) : undefined,
      }));
    const tabs = validTabs.slice(0, 50);
    if (validTabs.length > 50)
      warnings.push("Only the first 50 tabs will be transferred.");
    if (tabs.length !== browser.tabs.length)
      warnings.push("Chrome internal pages and local-file tabs were skipped.");
    lastCapture = {
      version: 1,
      captured_at: new Date().toISOString(),
      source: {
        platform: process.platform,
        display: {
          id: String(display.id),
          width: display.size.width,
          height: display.size.height,
        },
      },
      ...(screenshot ? { screenshot } : {}),
      tabs,
    };
    emit("capture", { capture: lastCapture, warnings, transferring: animate });
    if (animate) {
      try {
        await revealCapture(win, screenshot, display);
      } catch {
        win.show();
        win.focus();
      }
    } else if (showAfter || wasVisible) {
      win.show();
      win.focus();
    }
    return { capture: lastCapture, warnings };
  } finally {
    busy = false;
    if (showAfter) win.show();
  }
}
async function transfer() {
  if (busy) throw Error("A transfer is already running.");
  const prompt = taskPrompt.trim();
  await capture(true, true);
  if (!lastCapture.tabs.length && !lastCapture.screenshot)
    throw Error(
      "No desktop or Chrome tabs could be captured. Check permissions and retry.",
    );
  busy = true;
  emit("working", { message: "Creating your virtual desktop…" });
  try {
    const job = await request("/api/handoffs", {
      ...lastCapture,
      ...(prompt ? { prompt } : {}),
    });
    currentJob = job;
    rememberHandoff(job.id);
    if (prompt) {
      taskPrompt = "";
      emit("prompt-consumed", {});
    }
    emit("handoff", { job });
    return await monitorHandoff(job.id, !!prompt);
  } finally {
    busy = false;
  }
}
function openPrompt() {
  if (busy || !serviceReady) {
    win.show();
    win.focus();
    return;
  }
  if (promptWindow && !promptWindow.isDestroyed()) {
    promptWindow.close();
    return;
  }
  const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
  promptWindow = new BrowserWindow({
    width: 640,
    height: 168,
    frame: false,
    transparent: true,
    hasShadow: true,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    title: "Give your desktop a task",
    backgroundColor: "#00000000",
    x: Math.round(display.workArea.x + (display.workArea.width - 640) / 2),
    y: Math.round(display.workArea.y + display.workArea.height * 0.25),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  const composer = promptWindow;
  composer.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  composer.webContents.on("will-navigate", (event) => event.preventDefault());
  composer.once("ready-to-show", () => {
    composer.show();
    composer.focus();
  });
  const escapeRegistered = globalShortcut.register("Escape", () => {
    if (!composer.isDestroyed()) composer.close();
  });
  composer.on("closed", () => {
    if (escapeRegistered) globalShortcut.unregister("Escape");
    if (promptWindow === composer) promptWindow = null;
  });
  composer.loadFile("prompt.html");
}
function trusted(event) {
  return (
    event.sender === win.webContents &&
    event.senderFrame === win.webContents.mainFrame
  );
}
app.whenReady().then(() => {
  win = new BrowserWindow({
    width: 1440,
    height: 940,
    minWidth: 960,
    minHeight: 700,
    backgroundColor: "#0b0e13",
    title: "Multiverse",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.webContents.session.setPermissionRequestHandler(
    (_contents, _permission, callback) => callback(false),
  );
  win.webContents.session.setPermissionCheckHandler(() => false);
  win.loadFile("index.html");
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith("file://")) e.preventDefault();
  });
  ipcMain.handle("action", async (event, action, payload) => {
    if (
      promptWindow &&
      event.sender === promptWindow.webContents &&
      event.senderFrame === promptWindow.webContents.mainFrame
    ) {
      if (action === "cancel-prompt") {
        promptWindow.close();
        return;
      }
      if (action !== "submit-prompt") throw Error("Unsupported prompt action");
      if (busy || !serviceReady)
        throw Error("Wait for the current operation to finish.");
      if (
        typeof payload !== "string" ||
        !payload.trim() ||
        payload.length > 16000
      )
        throw Error("Enter a task first.");
      taskPrompt = payload.trim();
      promptWindow.close();
      transfer().catch((error) => emit("error", { message: error.message }));
      return;
    }
    if (!trusted(event)) throw Error("Untrusted frame");
    if (action === "open-prompt") return openPrompt();
    if (action === "capture") return capture();
    if (action === "transfer") return transfer();
    if (action === "set-prompt") {
      if (typeof payload !== "string" || payload.length > 16000)
        throw Error("Invalid prompt");
      taskPrompt = payload;
      return;
    }
    if (action === "start-task") return startTask(payload);
    if (action === "stop-task") {
      if (!currentJob || !taskActive(currentJob.task))
        throw Error("No active task");
      return request("/api/handoffs/" + currentJob.id + "/task/cancel", {});
    }
    if (action === "status")
      return {
        ready: serviceReady,
        error: serviceError,
        pairToken,
        extensionConnected: !!extensionSocket,
        shortcutStatus,
        currentJob,
      };
    if (action === "permissions") {
      await shell.openExternal(
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
      );
      return;
    }
    throw Error("Unknown action");
  });
  const primaryShortcut = globalShortcut.register(
    "Control+Shift+Space",
    openPrompt,
  );
  const fallbackShortcut = globalShortcut.register(
    "CommandOrControl+Shift+M",
    openPrompt,
  );
  shortcutStatus = primaryShortcut
    ? "⌃⇧Space opens the prompt" + (fallbackShortcut ? " · ⌘⇧M also works" : "")
    : fallbackShortcut
      ? "⌃⇧Space is unavailable · use ⌘⇧M"
      : "Shortcuts unavailable · use Give desktop a task";
  startBridge();
  startService().catch((e) => {
    serviceError = e.message;
    emit("service", { ready: false, error: e.message });
  });
});
app.on("activate", () => {
  if (win) win.show();
});
app.on("window-all-closed", () => app.quit());
app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  if (bridge) bridge.close();
  if (extensionSocket) extensionSocket.terminate();
  if (child) child.kill("SIGTERM");
});
