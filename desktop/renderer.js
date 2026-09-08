const $ = (id) => document.getElementById(id);
let ready = false,
  working = false,
  hasTabs = false;
let availableJob = null;
let activeTask = false;
let viewerURL = "";
let motionGeneration = 0;
let flight = null;
function clearFlight() {
  if (flight) {
    flight.getAnimations().forEach((a) => a.cancel());
    flight.remove();
    flight = null;
  }
}
async function animateTransfer() {
  const generation = ++motionGeneration;
  clearFlight();
  const screenshot = $("screenshot");
  if (screenshot.hidden || !screenshot.src) return;
  const ghost = $("transfer-ghost");
  $("transfer-image").src = screenshot.src;
  ghost.hidden = false;
  ghost.style.opacity = "0";
  $("destination-empty").hidden = true;
  const from = screenshot.getBoundingClientRect();
  const to = ghost.getBoundingClientRect();
  const clone = document.createElement("img");
  clone.className = "flying-snapshot";
  clone.src = screenshot.src;
  Object.assign(clone.style, {
    left: from.left + "px",
    top: from.top + "px",
    width: from.width + "px",
    height: from.height + "px",
  });
  document.body.append(clone);
  flight = clone;
  const scale = Math.min(to.width / from.width, to.height / from.height);
  const dx = to.left + (to.width - from.width * scale) / 2 - from.left;
  const dy = to.top + (to.height - from.height * scale) / 2 - from.top;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  try {
    await clone.animate(
      reduced
        ? [{ opacity: 1 }, { opacity: 0 }]
        : [
            { transform: "translate(0px,0px) scale(1)", opacity: 1 },
            {
              transform: `translate(${dx}px,${dy}px) scale(${scale})`,
              opacity: 1,
            },
          ],
      {
        duration: reduced ? 180 : 450,
        easing: "cubic-bezier(0.77,0,0.175,1)",
        fill: "forwards",
      },
    ).finished;
  } catch {
    /* A newer capture can cancel this transfer. */
  }
  if (generation !== motionGeneration) return;
  ghost.style.opacity = "1";
  clearFlight();
}
$("viewer").addEventListener("load", () => {
  if ($("viewer").hidden) return;
  const ghost = $("transfer-ghost");
  ghost
    .animate([{ opacity: 1 }, { opacity: 0 }], {
      duration: 220,
      easing: "cubic-bezier(0.23,1,0.32,1)",
    })
    .finished.then(() => {
      ghost.hidden = true;
    });
  $("viewer").animate([{ opacity: 0 }, { opacity: 1 }], {
    duration: 220,
    easing: "cubic-bezier(0.23,1,0.32,1)",
  });
});
function buttons() {
  $("capture").disabled = working;
  $("transfer").disabled = working || !ready;
  $("task-prompt").disabled = working;
  $("transfer-label").textContent = $("task-prompt").value.trim()
    ? "Transfer & start"
    : "Give desktop a task";
  $("run-task").hidden = !availableJob;
  $("run-task").disabled = working || !$("task-prompt").value.trim();
  $("stop-task").hidden = !activeTask;
}
function warnings(items) {
  $("warnings").replaceChildren(
    ...(items || []).map((text) => {
      const d = document.createElement("div");
      d.textContent = "• " + text;
      return d;
    }),
  );
}
function state(s) {
  if (s.type === "prompt-consumed") $("task-prompt").value = "";
  if (s.type === "shortcut") $("shortcut-status").textContent = s.message;
  if (s.type === "service") {
    ready = s.ready;
    $("service").textContent = s.ready
      ? "● Local service ready"
      : s.error || "Starting local service…";
    if (s.error) $("status").textContent = s.error;
  }
  if (s.type === "extension") {
    $("extension-status").textContent = s.connected
      ? "connected"
      : "not connected";
    if (s.token) $("pair-token").textContent = s.token;
  }
  if (s.type === "working") {
    working = true;
    $("status").textContent = s.message;
    if (s.message === "Creating your virtual desktop…") animateTransfer();
  }
  if (s.type === "capture") {
    ++motionGeneration;
    clearFlight();
    if (s.transferring) {
      availableJob = null;
      viewerURL = "";
      $("viewer").hidden = true;
      $("transfer-ghost").hidden = true;
      $("destination-empty").hidden = false;
      $("branch").textContent = "No workspace yet";
      $("checkpoint").textContent = "";
      $("task-result").hidden = true;
      $("task-progress").hidden = true;
    }
    working = !!s.transferring;
    hasTabs = s.capture.tabs.length > 0;
    $("tab-count").textContent = `${s.capture.tabs.length} tabs captured`;
    $("source-empty").hidden = !!s.capture.screenshot;
    $("screenshot").hidden = !s.capture.screenshot;
    if (s.capture.screenshot)
      $("screenshot").src = s.capture.screenshot.data_url;
    $("tabs").replaceChildren(
      ...s.capture.tabs.map((t) => {
        const d = document.createElement("div");
        d.className = "tab";
        d.textContent = (t.active ? "● " : "") + (t.title || t.url);
        const u = document.createElement("small");
        u.textContent = t.url;
        d.append(u);
        return d;
      }),
    );
    warnings(s.warnings);
    $("status").textContent = s.transferring
      ? "Moving your desktop…"
      : "Captured. Ready to transfer.";
  }
  if (s.type === "handoff") {
    const j = s.job;
    const task = j.task;
    activeTask = !!task && ["queued", "running"].includes(task.status);
    availableJob = ["ready", "partial"].includes(j.status) ? j : null;
    $("stop-task").disabled = !!task?.cancellation_requested;
    if (task) {
      $("task-progress").hidden = false;
      const labels = {
        queued: "Task queued",
        running: "Agent working on this desktop",
        completed: "Agent finished",
        stopped: "Task stopped",
        failed: "Task could not finish",
      };
      $("task-progress").textContent =
        (task.cancellation_requested && activeTask
          ? "Stopping after the current action…"
          : labels[task.status] || task.status) +
        (typeof task.cost_usd === "number"
          ? ` · $${task.cost_usd.toFixed(3)}`
          : "");
      $("task-result").textContent = task.final_text || task.error || "";
      $("task-result").hidden = !$("task-result").textContent;
      $("task-hint").textContent = activeTask
        ? "The agent is using the virtual desktop. You can keep working on your Mac."
        : "Enter another prompt to continue on this desktop, or transfer a new workspace.";
    }
    if (j.status === "failed") {
      ++motionGeneration;
      clearFlight();
      $("transfer-ghost").hidden = true;
      $("destination-empty").hidden = false;
    }
    working = !["ready", "partial", "failed"].includes(j.status) || activeTask;
    $("status").textContent =
      j.error ||
      {
        queued: "Transfer queued…",
        restoring: "Restoring tabs in your virtual desktop…",
        ready: "Your workspace is ready.",
        partial: "Workspace opened with some limitations.",
        failed: "Transfer failed.",
      }[j.status] ||
      j.status;
    const detailWarnings = [
      ...(j.warnings || []),
      ...(j.tabs || []).flatMap((t) => [
        ...(t.warnings || []).map((w) => (t.url || "Tab") + ": " + w),
        ...(t.error ? [(t.url || "Tab") + ": " + t.error] : []),
      ]),
    ];
    if (detailWarnings.length) warnings(detailWarnings);
    if (j.branch)
      $("branch").textContent =
        typeof j.branch === "string" ? j.branch : JSON.stringify(j.branch);
    if (j.checkpoint_id)
      $("checkpoint").textContent = "Checkpoint " + j.checkpoint_id;
    if (j.viewer_url && ["ready", "partial"].includes(j.status)) {
      try {
        const u = new URL(j.viewer_url);
        if (
          !["127.0.0.1", "localhost"].includes(u.hostname) ||
          u.protocol !== "http:"
        )
          throw Error("Viewer URL is not local");
        if (viewerURL !== u.href) {
          viewerURL = u.href;
          $("viewer").src = u.href;
        }
        $("viewer").hidden = false;
        $("destination-empty").hidden = true;
      } catch (e) {
        $("status").textContent = e.message;
      }
    }
  }
  if (s.type === "error") {
    working = false;
    $("status").textContent = s.message;
  }
  buttons();
}
window.multiverse.onState(state);
async function action(name, payload) {
  try {
    await window.multiverse.action(name, payload);
  } catch (e) {
    state({
      type: "error",
      message: e.message.replace(
        /^Error invoking remote method 'action': Error: /,
        "",
      ),
    });
  }
}
$("capture").onclick = () => action("capture");
$("transfer").onclick = () =>
  action($("task-prompt").value.trim() ? "transfer" : "open-prompt");
$("permissions").onclick = () => action("permissions");
$("task-prompt").addEventListener("input", () => {
  action("set-prompt", $("task-prompt").value);
  buttons();
});
$("run-task").onclick = () => action("start-task", $("task-prompt").value);
$("stop-task").onclick = () => action("stop-task");
window.multiverse.action("status").then((s) => {
  state({ type: "service", ...s });
  state({ type: "shortcut", message: s.shortcutStatus });
  if (s.currentJob) state({ type: "handoff", job: s.currentJob });
  if (s.pairToken)
    state({
      type: "extension",
      token: s.pairToken,
      connected: s.extensionConnected,
    });
});
