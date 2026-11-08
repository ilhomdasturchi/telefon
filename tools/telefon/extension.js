const fsSync = require("fs");
const fs = require("fs/promises");
const os = require("os");
const path = require("path");
const vscode = require("vscode");

const HOOK_COMMAND = "node .cursor/hooks/telefon-stop.cjs";
const pendingPath = path.join(os.homedir(), ".cursor", "telefon-pending.json");
const CALL_CLIPS = [
  "Vazifa bajarildi",
  "Kompyuterga qaravoring",
  "Yuklatilgan vazifa bajarildi",
];

function clipMessage(value) {
  const text = String(value || "").trim();
  return CALL_CLIPS.includes(text) ? text : CALL_CLIPS[0];
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;");
}

function readSettings() {
  const config = vscode.workspace.getConfiguration("telefon");
  const notifyMode = config.get("notifyMode") === "announce" ? "announce" : "ring";
  return {
    url: String(config.get("url") || ""),
    apiKey: String(config.get("apiKey") || ""),
    defaultMessage: String(config.get("defaultMessage") || "Salom"),
    ringSeconds: Number(config.get("ringSeconds") ?? 2),
    idleSeconds: Math.max(1, Number(config.get("idleSeconds") ?? 10)),
    notifyMode,
    notifyMessage: clipMessage(config.get("notifyMessage")),
    notifyRingSeconds: Math.max(1, Number(config.get("notifyRingSeconds") ?? 2)),
  };
}

async function postCall(body) {
  const { url, apiKey } = readSettings();
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Call-Key": apiKey,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(120000),
  });
  if (!response.ok) {
    const detail = (await response.text()).trim().slice(0, 300);
    throw new Error(detail ? `${response.status} ${detail}` : String(response.status));
  }
}

async function runCall(action, customMessage) {
  const { defaultMessage, ringSeconds } = readSettings();
  if (action === "announce") {
    await postCall({ type: "announce", message: defaultMessage });
    return `Spoke “${defaultMessage}”`;
  }
  if (action === "custom") {
    const message = customMessage ?? (await vscode.window.showInputBox({
      title: "Telefon",
      prompt: "Message to speak",
      value: defaultMessage,
      ignoreFocusOut: true,
    }));
    if (message == null || message.trim() === "") {
      return "";
    }
    await postCall({ type: "announce", message });
    return `Spoke “${message}”`;
  }
  await postCall({ type: "ring", ringSeconds });
  return "Ringing";
}

function workspaceRoot() {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

async function projectStatus(root) {
  if (!root) {
    return { installed: false };
  }
  try {
    const hooks = JSON.parse(await fs.readFile(path.join(root, ".cursor", "hooks.json"), "utf8"));
    const stop = Array.isArray(hooks.hooks?.stop) ? hooks.hooks.stop : [];
    const hooked = stop.some((hook) => String(hook.command || "").includes("telefon-stop"));
    await fs.access(path.join(root, ".cursor", "telefon.json"));
    return { installed: hooked };
  } catch {
    return { installed: false };
  }
}

async function saveNotifySettings(values) {
  const config = vscode.workspace.getConfiguration("telefon");
  const idleSeconds = Math.max(1, Number(values.idleSeconds) || 10);
  const notifyMode = values.notifyMode === "announce" ? "announce" : "ring";
  const notifyRingSeconds = Math.max(1, Number(values.notifyRingSeconds) || 2);
  const notifyMessage = clipMessage(values.notifyMessage);
  await config.update("idleSeconds", idleSeconds, vscode.ConfigurationTarget.Global);
  await config.update("notifyMode", notifyMode, vscode.ConfigurationTarget.Global);
  await config.update("notifyMessage", notifyMessage, vscode.ConfigurationTarget.Global);
  await config.update("notifyRingSeconds", notifyRingSeconds, vscode.ConfigurationTarget.Global);
}

async function installProject(context, values) {
  const root = workspaceRoot();
  if (!root) {
    throw new Error("Open a folder first");
  }
  await saveNotifySettings(values);
  const settings = readSettings();
  const cursorDir = path.join(root, ".cursor");
  const hooksDir = path.join(cursorDir, "hooks");
  await fs.mkdir(hooksDir, { recursive: true });

  const projectConfig = {
    url: settings.url,
    apiKey: settings.apiKey,
    idleSeconds: settings.idleSeconds,
    mode: settings.notifyMode,
    message: settings.notifyMessage,
    ringSeconds: settings.notifyRingSeconds,
  };
  await fs.writeFile(path.join(cursorDir, "telefon.json"), `${JSON.stringify(projectConfig, null, 2)}\n`);

  const script = await fs.readFile(path.join(context.extensionPath, "hooks", "telefon-stop.cjs"));
  await fs.writeFile(path.join(hooksDir, "telefon-stop.cjs"), script);

  const hooksPath = path.join(cursorDir, "hooks.json");
  let hooks = { version: 1, hooks: {} };
  try {
    const existing = JSON.parse(await fs.readFile(hooksPath, "utf8"));
    if (existing && typeof existing === "object") {
      hooks = existing;
    }
  } catch (error) {
    if (error.code !== "ENOENT") {
      throw new Error("Could not read .cursor/hooks.json");
    }
  }
  if (!hooks.hooks || typeof hooks.hooks !== "object") {
    hooks.hooks = {};
  }
  hooks.version = hooks.version || 1;
  const stop = Array.isArray(hooks.hooks.stop) ? hooks.hooks.stop : [];
  hooks.hooks.stop = stop.filter((hook) => !String(hook.command || "").includes("telefon-stop"));
  hooks.hooks.stop.push({ command: HOOK_COMMAND, failClosed: false });
  await fs.writeFile(hooksPath, `${JSON.stringify(hooks, null, 2)}\n`);
}

async function removeProject() {
  const root = workspaceRoot();
  if (!root) {
    throw new Error("Open a folder first");
  }
  const cursorDir = path.join(root, ".cursor");
  const hooksPath = path.join(cursorDir, "hooks.json");
  try {
    const existing = JSON.parse(await fs.readFile(hooksPath, "utf8"));
    if (existing && typeof existing === "object") {
      if (!existing.hooks || typeof existing.hooks !== "object") {
        existing.hooks = {};
      }
      const stop = Array.isArray(existing.hooks.stop) ? existing.hooks.stop : [];
      const kept = stop.filter((hook) => !String(hook.command || "").includes("telefon-stop"));
      if (kept.length) {
        existing.hooks.stop = kept;
      } else {
        delete existing.hooks.stop;
      }
      const stillUsed = Object.values(existing.hooks).some((value) => {
        if (Array.isArray(value)) {
          return value.length > 0;
        }
        return value && typeof value === "object" && Object.keys(value).length > 0;
      });
      const extraKeys = Object.keys(existing).filter((key) => key !== "version" && key !== "hooks");
      if (stillUsed || extraKeys.length) {
        await fs.writeFile(hooksPath, `${JSON.stringify(existing, null, 2)}\n`);
      } else {
        await fs.unlink(hooksPath);
      }
    }
  } catch (error) {
    if (error.code !== "ENOENT") {
      throw new Error("Could not read .cursor/hooks.json");
    }
  }
  await fs.rm(path.join(cursorDir, "telefon.json"), { force: true });
  await fs.rm(path.join(cursorDir, "hooks", "telefon-stop.cjs"), { force: true });
}

function viewHtml(state) {
  const nonce = String(Math.random()).slice(2);
  const message = escapeHtml(state.defaultMessage);
  const installLabel = state.installed ? "Update" : "Install";
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}'">
  <style>
    body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 12px; font-size: 13px; }
    h2 { margin: 0; font-size: 13px; font-weight: 600; }
    .section { margin: 14px 0 6px; font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; opacity: 0.65; }
    label { display: block; margin: 0 0 4px; font-size: 12px; }
    button, input { font: inherit; box-sizing: border-box; }
    button { border: 0; border-radius: 6px; background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); cursor: pointer; }
    button:focus { outline: none; }
    button:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 1px; }
    input { width: 100%; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--vscode-input-border, transparent); background: var(--vscode-input-background); color: var(--vscode-input-foreground); }
    button.primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
    #watch { margin: 4px 0 0; font-size: 12px; line-height: 1.35; opacity: 0.85; }
    .fields { display: flex; gap: 8px; margin-top: 8px; }
    .field { flex: 1; min-width: 0; }
    .choices { display: flex; gap: 2px; margin: 8px 0; padding: 2px; border-radius: 6px; background: var(--vscode-input-background); }
    .choices button { flex: 1; margin: 0; padding: 6px 8px; background: transparent; color: var(--vscode-foreground); }
    .choices button.selected { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
    .phrases { display: flex; flex-direction: column; gap: 4px; margin: 0 0 8px; }
    .phrases button { margin: 0; padding: 6px 8px; text-align: left; font-size: 12px; background: transparent; color: var(--vscode-foreground); }
    .phrases button.selected { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
    .actions { display: flex; gap: 8px; }
    .actions button { flex: 1; margin: 0; padding: 8px 10px; }
    .call { display: flex; gap: 8px; }
    .call input { margin: 0; }
    .call button, #ring { margin: 0; padding: 6px 12px; }
    #ring { width: 100%; margin-top: 8px; }
    #status { min-height: 1.2em; margin: 8px 0 0; font-size: 12px; opacity: 0.85; }
  </style>
</head>
<body>
  <h2>Telefon</h2>
  <p id="watch"></p>
  <p class="section">After a task</p>
  <div class="fields">
    <div class="field">
      <label for="idle">Idle seconds</label>
      <input id="idle" type="number" min="1" value="${state.idleSeconds}">
    </div>
    <div class="field" id="ringFields" style="display:${state.notifyMode === "ring" ? "block" : "none"}">
      <label for="notifyRing">Ring seconds</label>
      <input id="notifyRing" type="number" min="1" value="${state.notifyRingSeconds}">
    </div>
  </div>
  <div class="choices">
    <button type="button" id="mode-ring" class="${state.notifyMode === "ring" ? "selected" : ""}">Ring</button>
    <button type="button" id="mode-announce" class="${state.notifyMode === "announce" ? "selected" : ""}">Speak</button>
  </div>
  <div id="announceFields" style="display:${state.notifyMode === "announce" ? "block" : "none"}">
    <div class="phrases">
      ${CALL_CLIPS.map((speak) => `<button type="button" class="phrase${speak === state.notifyMessage ? " selected" : ""}" data-speak="${escapeHtml(speak)}">${escapeHtml(speak)}</button>`).join("")}
    </div>
  </div>
  <div class="actions">
    <button class="primary" id="install">${installLabel}</button>
    ${state.installed ? `<button id="remove">Turn off</button>` : ""}
  </div>
  <p class="section">Call now</p>
  <div class="call">
    <input id="message" value="${message}" aria-label="Message">
    <button id="custom">Speak</button>
  </div>
  <button id="ring">Ring ${state.ringSeconds}s</button>
  <p id="status"></p>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const status = document.getElementById("status");
    const watch = document.getElementById("watch");
    const installed = ${state.installed ? "true" : "false"};
    let notifyMode = "${state.notifyMode}";
    let notifyMessage = ${JSON.stringify(state.notifyMessage)};
    const refresh = () => {
      const idle = document.getElementById("idle").value || "10";
      const detail = notifyMode === "announce"
        ? "speaks “" + notifyMessage + "”"
        : "rings " + (document.getElementById("notifyRing").value || "2") + "s";
      watch.textContent = (installed ? "On" : "Off") + " in this project. After a task ends, waits " + idle + "s while you are not busy in Cursor, then " + detail + ". Busy = Cursor focused and you are using it. Outside Cursor always calls.";
    };
    const setMode = (mode) => {
      notifyMode = mode;
      document.getElementById("mode-ring").classList.toggle("selected", mode === "ring");
      document.getElementById("mode-announce").classList.toggle("selected", mode === "announce");
      document.getElementById("announceFields").style.display = mode === "announce" ? "block" : "none";
      document.getElementById("ringFields").style.display = mode === "ring" ? "block" : "none";
      refresh();
    };
    document.getElementById("mode-ring").onclick = () => setMode("ring");
    document.getElementById("mode-announce").onclick = () => setMode("announce");
    document.getElementById("idle").oninput = refresh;
    document.getElementById("notifyRing").oninput = refresh;
    document.querySelectorAll(".phrase").forEach((button) => {
      button.onclick = () => {
        notifyMessage = button.getAttribute("data-speak");
        document.querySelectorAll(".phrase").forEach((el) => el.classList.toggle("selected", el === button));
        refresh();
      };
    });
    const payload = () => ({
      idleSeconds: document.getElementById("idle").value,
      notifyMode,
      notifyMessage,
      notifyRingSeconds: document.getElementById("notifyRing").value
    });
    document.getElementById("install").onclick = () => vscode.postMessage({ type: "install", ...payload() });
    const remove = document.getElementById("remove");
    if (remove) {
      remove.onclick = () => vscode.postMessage({ type: "remove" });
    }
    document.getElementById("custom").onclick = () => vscode.postMessage({ type: "custom", message: document.getElementById("message").value });
    document.getElementById("ring").onclick = () => vscode.postMessage({ type: "ring" });
    window.addEventListener("message", (event) => { status.textContent = event.data.text || ""; });
    refresh();
  </script>
</body>
</html>`;
}

class TelefonViewProvider {
  constructor(context, onStatus) {
    this.context = context;
    this.onStatus = onStatus;
  }

  resolveWebviewView(webviewView) {
    const webview = webviewView.webview;
    webview.options = { enableScripts: true };
    const render = async () => {
      const settings = readSettings();
      const status = await projectStatus(workspaceRoot());
      webview.html = viewHtml({ ...settings, installed: status.installed });
      this.onStatus(status.installed);
    };
    render();
    this.context.subscriptions.push(
      vscode.workspace.onDidChangeWorkspaceFolders(render),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration("telefon")) {
          render();
        }
      })
    );
    webview.onDidReceiveMessage(async (message) => {
      try {
        if (message.type === "install") {
          await installProject(this.context, message);
          await render();
          webview.postMessage({ text: "Installed in this project" });
          return;
        }
        if (message.type === "remove") {
          await removeProject();
          await render();
          webview.postMessage({ text: "Turned off in this project" });
          return;
        }
        const text = await runCall(message.type, message.message);
        if (text) {
          webview.postMessage({ text });
        }
      } catch (error) {
        const text = error instanceof Error ? error.message : String(error);
        webview.postMessage({ text: `Failed: ${text}` });
        vscode.window.showErrorMessage(`Telefon failed: ${text}`);
      }
    });
  }
}

const armedPath = path.join(os.homedir(), ".cursor", "telefon-armed.json");
const presenceDir = path.join(os.homedir(), ".cursor", "telefon-presence");
const presencePath = path.join(presenceDir, `${process.pid}.json`);
const FALLBACK_ACTIVE_MS = 10000;
const PRESENCE_FRESH_MS = 3000;

let log = null;
let notifyTimer = null;
let placingCall = false;
let lastEventAt = 0;

function trace(message) {
  if (log) {
    log.appendLine(`[${new Date().toLocaleTimeString()}] ${message}`);
  }
}

function markEvent() {
  lastEventAt = Date.now();
}

/** This window: focused AND recently used (keyboard/mouse anywhere in it, including chat). */
function isBusyHere() {
  const state = vscode.window.state;
  if (!state.focused) {
    return false;
  }
  if (typeof state.active === "boolean") {
    return state.active;
  }
  return Date.now() - lastEventAt < FALLBACK_ACTIVE_MS;
}

function writePresence() {
  try {
    fsSync.mkdirSync(presenceDir, { recursive: true });
    fsSync.writeFileSync(presencePath, JSON.stringify({ busy: isBusyHere(), at: Date.now() }));
  } catch {}
}

/** Busy in any Cursor window. Activity in other apps never counts. */
function isBusyInCursor() {
  if (isBusyHere()) {
    return true;
  }
  let names = [];
  try {
    names = fsSync.readdirSync(presenceDir);
  } catch {
    return false;
  }
  const now = Date.now();
  return names.some((name) => {
    try {
      const entry = JSON.parse(fsSync.readFileSync(path.join(presenceDir, name), "utf8"));
      return entry.busy && now - Number(entry.at || 0) < PRESENCE_FRESH_MS;
    } catch {
      return false;
    }
  });
}

function describeState() {
  const state = vscode.window.state;
  return `focused=${state.focused} active=${state.active} anyWindowBusy=${isBusyInCursor()}`;
}


async function placePending(pending) {
  const mode = pending.mode === "announce" ? "announce" : "ring";
  const body = mode === "announce"
    ? { type: "announce", message: String(pending.message || "Vazifa bajarildi") }
    : { type: "ring", ringSeconds: Number(pending.ringSeconds ?? 2) };
  const response = await fetch(pending.url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Call-Key": String(pending.apiKey ?? ""),
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(120000),
  });
  if (!response.ok) {
    const detail = (await response.text()).trim().slice(0, 300);
    throw new Error(detail ? `${response.status} ${detail}` : String(response.status));
  }
}

function clearNotifyTimer() {
  if (notifyTimer) {
    clearInterval(notifyTimer);
    notifyTimer = null;
  }
}

function readArmedAt() {
  try {
    return Number(JSON.parse(fsSync.readFileSync(armedPath, "utf8")).at || 0);
  } catch {
    return 0;
  }
}

/** Take ownership of the pending notify so only one Cursor window handles it. */
function claimPending() {
  const claimPath = `${pendingPath}.${process.pid}.claim`;
  try {
    fsSync.renameSync(pendingPath, claimPath);
  } catch {
    return null;
  }
  try {
    return JSON.parse(fsSync.readFileSync(claimPath, "utf8"));
  } catch {
    return null;
  } finally {
    fsSync.rmSync(claimPath, { force: true });
  }
}

function armPending() {
  const pending = claimPending();
  if (!pending) {
    return;
  }
  const at = Number(pending.at || 0);
  if (!at) {
    return;
  }
  const idleMs = Math.max(1, Number(pending.idleSeconds ?? 10)) * 1000;
  if (Date.now() - at > idleMs + 60000) {
    trace("stale task-finished signal ignored");
    return;
  }
  try {
    fsSync.mkdirSync(path.dirname(armedPath), { recursive: true });
    fsSync.writeFileSync(armedPath, `${JSON.stringify({ at, pid: process.pid })}\n`);
  } catch {}
  clearNotifyTimer();
  trace(`task finished — waiting ${idleMs / 1000}s (${describeState()})`);

  notifyTimer = setInterval(() => {
    if (placingCall) {
      return;
    }
    if (readArmedAt() !== at) {
      clearNotifyTimer();
      trace("newer task finished — this notify replaced");
      return;
    }
    // Busy in Cursor at any moment after the task ended = you saw it; no call.
    if (isBusyInCursor()) {
      clearNotifyTimer();
      trace(`busy in Cursor — call cancelled (${describeState()})`);
      return;
    }
    if (Date.now() - at < idleMs) {
      return;
    }
    clearNotifyTimer();
    placingCall = true;
    trace(`not busy in Cursor — calling (${describeState()})`);
    placePending(pending)
      .catch((error) => {
        const message = error instanceof Error ? error.message : String(error);
        trace(`call failed: ${message}`);
        vscode.window.showErrorMessage(`Telefon failed: ${message}`);
      })
      .finally(() => {
        placingCall = false;
      });
  }, 400);
}

function watchActivity(context) {
  markEvent();
  writePresence();
  const pendingPoll = setInterval(armPending, 400);
  const presencePoll = setInterval(writePresence, 1000);
  context.subscriptions.push(
    { dispose: () => clearInterval(pendingPoll) },
    { dispose: () => clearInterval(presencePoll) },
    { dispose: () => clearNotifyTimer() },
    { dispose: () => fsSync.rmSync(presencePath, { force: true }) },
    vscode.window.onDidChangeWindowState(() => {
      markEvent();
      writePresence();
    }),
    vscode.workspace.onDidChangeTextDocument(markEvent),
    vscode.window.onDidChangeTextEditorSelection(markEvent),
    vscode.window.onDidChangeActiveTextEditor(markEvent),
    vscode.window.onDidChangeTextEditorVisibleRanges(markEvent),
    vscode.window.onDidChangeActiveTerminal(markEvent)
  );
}

function activate(context) {
  log = vscode.window.createOutputChannel("Telefon");
  context.subscriptions.push(log);
  const version = context.extension?.packageJSON?.version || "?";
  trace(`Telefon ${version} active`);
  const button = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100000);
  button.command = "telefon.show";
  button.tooltip = `Open Telefon (v${version})`;
  const watchingColor = new vscode.ThemeColor("statusBarItem.warningBackground");
  const setWatching = (installed) => {
    button.text = installed ? "Telefon on" : "Telefon";
    button.backgroundColor = installed ? watchingColor : undefined;
  };
  setWatching(false);
  button.show();
  projectStatus(workspaceRoot()).then((status) => setWatching(status.installed));

  const command = vscode.commands.registerCommand("telefon.show", async () => {
    await vscode.commands.executeCommand("telefon.call.focus");
  });

  watchActivity(context);
  context.subscriptions.push(
    command,
    button,
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      projectStatus(workspaceRoot()).then((status) => setWatching(status.installed));
    }),
    vscode.window.registerWebviewViewProvider(
      "telefon.call",
      new TelefonViewProvider(context, setWatching)
    )
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
