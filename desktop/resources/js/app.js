/* global Neutralino, NL_PATH, NL_OS, NL_VERSION, NL_APPVERSION, NL_DEFAULT_UPDATE_URL, NL_CALL_API_PORT */

const DEFAULT_UPDATE_URL =
  (typeof NL_DEFAULT_UPDATE_URL === "string" && NL_DEFAULT_UPDATE_URL) ||
  "https://telefon.telepat.uz/soft/telefon.exe";
const CALL_API_PORT =
  (typeof NL_CALL_API_PORT === "string" && NL_CALL_API_PORT) || "8088";

const STORAGE_UPDATE = "updateUrl";
const STORAGE_AUTOSTART = "autostartEnabled";
const STATE_FILE = "update-state.json";
const BACKEND_EXE = "AiPhone.exe";
const LOG_REL = "logs/aiphone.log";
const ENV_REL = ".env";
const UPDATE_POLL_MS = 15 * 60 * 1000;

const $ = (id) => document.getElementById(id);

function appDir() {
  return (typeof NL_PATH === "string" && NL_PATH) || ".";
}

function joinPath(...parts) {
  const sep = NL_OS === "Windows" ? "\\" : "/";
  return parts
    .filter(Boolean)
    .map((p, i) => (i === 0 ? String(p).replace(/[/\\]+$/, "") : String(p).replace(/^[/\\]+/, "")))
    .join(sep);
}

function quoteCmd(path) {
  return `"${String(path).replace(/"/g, "")}"`;
}

async function exec(cmd, opts = {}) {
  return Neutralino.os.execCommand(cmd, {
    background: !!opts.background,
    cwd: opts.cwd || appDir(),
  });
}

async function readText(rel, fallback = "") {
  try {
    return await Neutralino.filesystem.readFile(joinPath(appDir(), rel));
  } catch {
    return fallback;
  }
}

async function writeText(rel, text) {
  const full = joinPath(appDir(), rel);
  const parent = full.replace(/[/\\][^/\\]+$/, "");
  try {
    await Neutralino.filesystem.createDirectory(parent);
  } catch {
    /* exists */
  }
  await Neutralino.filesystem.writeFile(full, text);
}

function toAbs(relOrAbs) {
  if (!relOrAbs) return appDir();
  if (/^[a-zA-Z]:[\\/]/.test(relOrAbs) || relOrAbs.startsWith("\\\\")) return relOrAbs;
  return joinPath(appDir(), relOrAbs);
}

async function pathExists(relOrAbs) {
  try {
    await Neutralino.filesystem.getStats(toAbs(relOrAbs));
    return true;
  } catch {
    return false;
  }
}

function parseEnv(text) {
  const out = {};
  for (const line of String(text || "").split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const eq = t.indexOf("=");
    if (eq < 0) continue;
    out[t.slice(0, eq).trim()] = t.slice(eq + 1).trim();
  }
  return out;
}

function upsertEnv(text, patch) {
  const lines = String(text || "").split(/\r?\n/);
  const seen = new Set();
  const keys = Object.keys(patch);
  const next = lines.map((line) => {
    const t = line.trim();
    if (!t || t.startsWith("#") || !t.includes("=")) return line;
    const eq = t.indexOf("=");
    const key = t.slice(0, eq).trim();
    if (!(key in patch)) return line;
    seen.add(key);
    return `${key}=${patch[key]}`;
  });
  for (const key of keys) {
    if (!seen.has(key)) next.push(`${key}=${patch[key]}`);
  }
  let body = next.join("\n");
  if (!body.endsWith("\n")) body += "\n";
  return body;
}

async function isBackendRunning() {
  if (NL_OS !== "Windows") return false;
  const res = await exec(
    `powershell -NoProfile -Command "if (Get-Process -Name AiPhone -ErrorAction SilentlyContinue) { '1' } else { '0' }"`,
  );
  return String(res.stdOut || "").trim() === "1";
}

async function startBackend() {
  const exe = joinPath(appDir(), BACKEND_EXE);
  if (!(await pathExists(BACKEND_EXE))) {
    throw new Error(`Missing ${BACKEND_EXE} next to telefon.exe`);
  }
  if (await isBackendRunning()) return;
  // start detached so Neutralino does not wait
  await exec(
    `cmd /c start "" /D ${quoteCmd(appDir())} ${quoteCmd(exe)} --no-autostart`,
    { background: true },
  );
}

async function stopBackend() {
  if (NL_OS !== "Windows") return;
  await exec(`taskkill /IM AiPhone.exe /F`, { background: false });
}

async function fetchStatus() {
  const urls = [
    `http://127.0.0.1:${CALL_API_PORT}/api/status`,
    `http://127.0.0.1:${CALL_API_PORT}/api/health`,
  ];
  for (const url of urls) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) continue;
      return await res.json();
    } catch {
      /* try next */
    }
  }
  return null;
}

function setBadge(running) {
  const el = $("backendBadge");
  el.textContent = running ? "Backend on" : "Backend off";
  el.className = `badge ${running ? "badge-on" : "badge-off"}`;
}

async function refreshStatus() {
  const running = await isBackendRunning();
  setBadge(running);
  const st = running ? await fetchStatus() : null;
  $("stBind").textContent = (st && st.bindIp) || $("inpBind").value || "—";
  $("stAdvertise").textContent = (st && st.advertiseIp) || $("inpAdvertise").value || "—";
  $("stSipPort").textContent =
    (st && st.sipPort != null ? String(st.sipPort) : null) || $("inpSipPort").value || "—";
  if (!running) {
    $("stPhone").textContent = "backend stopped";
    $("stCallApi").textContent = "—";
  } else if (!st) {
    $("stPhone").textContent = "starting…";
    $("stCallApi").textContent = `http://127.0.0.1:${CALL_API_PORT}`;
  } else {
    $("stPhone").textContent = st.registered
      ? `registered (${st.extension || "101"})`
      : `waiting (${st.extension || "101"})`;
    $("stCallApi").textContent = st.callApiEnabled
      ? `enabled :${st.callApiPort || CALL_API_PORT}`
      : `status only :${st.callApiPort || CALL_API_PORT}`;
  }
}

async function loadEnvIntoForm() {
  const env = parseEnv(await readText(ENV_REL, ""));
  $("inpBind").value = env.BIND_IP || "0.0.0.0";
  $("inpAdvertise").value = env.SIP_ADVERTISE_IP || "";
  $("inpSipPort").value = env.SIP_PORT || "5060";
}

async function saveIpAndRestart() {
  const patch = {
    BIND_IP: $("inpBind").value.trim() || "0.0.0.0",
    SIP_ADVERTISE_IP: $("inpAdvertise").value.trim(),
    SIP_PORT: $("inpSipPort").value.trim() || "5060",
  };
  const cur = await readText(ENV_REL, "");
  await writeText(ENV_REL, upsertEnv(cur, patch));
  if (await isBackendRunning()) {
    await stopBackend();
    await new Promise((r) => setTimeout(r, 800));
  }
  await startBackend();
  await refreshStatus();
}

async function refreshLogs() {
  const text = await readText(LOG_REL, "(no log file yet)");
  const view = $("logView");
  view.textContent = text || "(empty)";
  view.scrollTop = view.scrollHeight;
}

async function clearLogs() {
  await writeText(LOG_REL, "");
  await refreshLogs();
}

async function getAutostartEnabled() {
  if (NL_OS !== "Windows") return false;
  const res = await exec(
    `powershell -NoProfile -Command "(Get-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' -Name 'Telefon' -ErrorAction SilentlyContinue).Telefon"`,
  );
  return String(res.stdOut || "").trim().length > 0;
}

async function setAutostart(enabled) {
  if (NL_OS !== "Windows") return;
  const exe = joinPath(appDir(), "telefon.exe");
  if (enabled) {
    await exec(`reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v Telefon /t REG_SZ /d ${quoteCmd(exe)} /f`);
  } else {
    await exec(`reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v Telefon /f`);
  }
  await Neutralino.storage.setData(STORAGE_AUTOSTART, enabled ? "1" : "0");
}

function manifestUrlFromUpdateUrl(updateUrl) {
  try {
    const u = new URL(updateUrl);
    const parts = u.pathname.split("/");
    parts[parts.length - 1] = "update.json";
    u.pathname = parts.join("/");
    u.search = "";
    u.hash = "";
    return u.toString();
  } catch {
    return "https://telefon.telepat.uz/soft/update.json";
  }
}

async function readUpdateState() {
  try {
    return JSON.parse(await readText(STATE_FILE, "{}")) || {};
  } catch {
    return {};
  }
}

async function writeUpdateState(state) {
  await writeText(STATE_FILE, JSON.stringify(state, null, 2));
}

function setUpdateMsg(text) {
  $("updateMsg").textContent = text || "";
  $("stUpdate").textContent = text || "—";
}

async function fileSha256(absPath) {
  const res = await exec(
    `powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -Path ${quoteCmd(absPath)}).Hash.ToLower()"`,
  );
  const hash = String(res.stdOut || "").trim().toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(hash)) {
    throw new Error("Could not hash downloaded package");
  }
  return hash;
}

async function downloadToFile(url, destAbs) {
  // Prefer PowerShell for large packages (Neutralino binary write base64s over WS).
  const dl = await exec(
    `powershell -NoProfile -Command "Invoke-WebRequest -Uri ${quoteCmd(url)} -OutFile ${quoteCmd(destAbs)}; if (-not (Test-Path ${quoteCmd(destAbs)})) { exit 1 }"`,
  );
  if (dl.exitCode && dl.exitCode !== 0) {
    throw new Error(`Download failed (exit ${dl.exitCode})`);
  }
  if (!(await pathExists(destAbs))) {
    throw new Error("Download failed: file missing");
  }
}

async function checkForUpdates({ apply = true } = {}) {
  const updateUrl = $("inpUpdateUrl").value.trim() || DEFAULT_UPDATE_URL;
  await Neutralino.storage.setData(STORAGE_UPDATE, updateUrl);
  const manifestUrl = manifestUrlFromUpdateUrl(updateUrl);
  setUpdateMsg(`Checking ${manifestUrl}…`);

  let manifest;
  try {
    const res = await fetch(manifestUrl, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    manifest = await res.json();
  } catch (err) {
    setUpdateMsg(`Update check failed: ${err.message || err}`);
    return false;
  }

  const remoteHash = String(manifest.sha256 || "").trim().toLowerCase();
  const packageUrl = String(manifest.url || "").trim();
  if (!remoteHash || !packageUrl) {
    setUpdateMsg("Invalid update.json (need sha256 + url)");
    return false;
  }

  const state = await readUpdateState();
  const localHash = String(state.sha256 || "").trim().toLowerCase();
  if (localHash && localHash === remoteHash) {
    setUpdateMsg(`Up to date (${manifest.version || remoteHash.slice(0, 12)})`);
    return false;
  }

  setUpdateMsg(`Update available: ${manifest.version || "new"} — downloading…`);
  if (!apply) return true;

  const staging = joinPath(appDir(), "_update_staging");
  const zipPath = joinPath(appDir(), "_update_package.zip");

  try {
    await exec(`powershell -NoProfile -Command "Remove-Item -Recurse -Force ${quoteCmd(staging)} -ErrorAction SilentlyContinue"`);
    await downloadToFile(packageUrl, zipPath);
    const got = await fileSha256(zipPath);
    if (got !== remoteHash) {
      throw new Error(`Hash mismatch (got ${got.slice(0, 12)}…)`);
    }
    await exec(
      `powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path ${quoteCmd(staging)} | Out-Null; Expand-Archive -Path ${quoteCmd(zipPath)} -DestinationPath ${quoteCmd(staging)} -Force"`,
    );

    // If zip contains a single top folder, use its contents
    const list = await exec(
      `powershell -NoProfile -Command "$i=Get-ChildItem -Force ${quoteCmd(staging)}; if ($i.Count -eq 1 -and $i[0].PSIsContainer) { $i[0].FullName } else { '' }"`,
    );
    const inner = String(list.stdOut || "").trim();
    const source = inner || staging;

    const newState = {
      sha256: remoteHash,
      version: manifest.version || "",
      url: packageUrl,
      checkedAt: new Date().toISOString(),
    };
    // Persist into staged package so xcopy installs the new hash marker.
    await Neutralino.filesystem.writeFile(
      joinPath(source, STATE_FILE),
      JSON.stringify(newState, null, 2),
    );
    await writeUpdateState(newState);

    const applyCmd = joinPath(appDir(), "update-apply.cmd");
    const script = [
      "@echo off",
      "setlocal",
      `set "APPDIR=${appDir()}"`,
      `set "SRC=${source}"`,
      "cd /d \"%APPDIR%\"",
      "timeout /t 2 /nobreak >nul",
      "taskkill /IM telefon.exe /F >nul 2>&1",
      "taskkill /IM AiPhone.exe /F >nul 2>&1",
      "timeout /t 1 /nobreak >nul",
      "xcopy /E /Y /I \"%SRC%\\*\" \"%APPDIR%\\\" >nul",
      "del /f /q \"%APPDIR%\\_update_package.zip\" >nul 2>&1",
      "rmdir /s /q \"%APPDIR%\\_update_staging\" >nul 2>&1",
      "start \"\" \"%APPDIR%\\telefon.exe\"",
      "del \"%~f0\"",
    ].join("\r\n");
    await Neutralino.filesystem.writeFile(applyCmd, script);
    setUpdateMsg("Installing update and restarting…");
    await exec(`cmd /c start "" ${quoteCmd(applyCmd)}`, { background: true });
    await Neutralino.app.exit();
    return true;
  } catch (err) {
    setUpdateMsg(`Update failed: ${err.message || err}`);
    return false;
  }
}

async function initWindowCloseBehavior() {
  // Keep backend running when UI closes; exitProcessOnClose is false in config.
  try {
    await Neutralino.os.setTray({
      icon: "/resources/icons/appIcon.png",
      menuItems: [
        { id: "show", text: "Show Telefon" },
        { id: "sep", text: "-" },
        { id: "quit", text: "Quit shell (keep backend)" },
        { id: "quitAll", text: "Quit shell + backend" },
      ],
    });
  } catch (err) {
    console.warn("tray unavailable", err);
  }

  await Neutralino.events.on("trayMenuItemClicked", async (ev) => {
    const id = ev && ev.detail && ev.detail.id;
    if (id === "show") {
      await Neutralino.window.show();
      await Neutralino.window.focus();
    } else if (id === "quit") {
      await Neutralino.app.exit();
    } else if (id === "quitAll") {
      await stopBackend();
      await Neutralino.app.exit();
    }
  });

  await Neutralino.events.on("windowClose", async () => {
    await Neutralino.window.hide();
  });
}

async function init() {
  await Neutralino.init();

  let updateUrl = DEFAULT_UPDATE_URL;
  try {
    updateUrl = (await Neutralino.storage.getData(STORAGE_UPDATE)) || DEFAULT_UPDATE_URL;
  } catch {
    /* first run */
  }
  $("inpUpdateUrl").value = updateUrl;

  await loadEnvIntoForm();
  $("chkAutostart").checked = await getAutostartEnabled();

  $("btnEnable").addEventListener("click", async () => {
    try {
      await startBackend();
      setUpdateMsg("Backend started");
    } catch (err) {
      setUpdateMsg(String(err.message || err));
    }
    await refreshStatus();
  });
  $("btnDisable").addEventListener("click", async () => {
    await stopBackend();
    await refreshStatus();
  });
  $("btnSaveIp").addEventListener("click", () => saveIpAndRestart().catch((e) => setUpdateMsg(String(e))));
  $("btnReloadIp").addEventListener("click", () => loadEnvIntoForm());
  $("btnRefreshLogs").addEventListener("click", () => refreshLogs());
  $("btnClearLogs").addEventListener("click", () => clearLogs());
  $("chkAutostart").addEventListener("change", async (ev) => {
    await setAutostart(!!ev.target.checked);
  });
  $("btnSaveUpdateUrl").addEventListener("click", async () => {
    await Neutralino.storage.setData(STORAGE_UPDATE, $("inpUpdateUrl").value.trim());
    setUpdateMsg("Update URL saved");
  });
  $("btnCheckUpdate").addEventListener("click", () => checkForUpdates({ apply: true }));

  await initWindowCloseBehavior();
  await refreshStatus();
  await refreshLogs();

  // Auto-start backend if present and not running
  if (await pathExists(BACKEND_EXE)) {
    if (!(await isBackendRunning())) {
      try {
        await startBackend();
      } catch (err) {
        setUpdateMsg(String(err.message || err));
      }
    }
  } else {
    setUpdateMsg(`Place ${BACKEND_EXE} next to telefon.exe`);
  }

  setInterval(() => {
    refreshStatus().catch(() => {});
  }, 3000);
  setInterval(() => {
    refreshLogs().catch(() => {});
  }, 5000);
  setTimeout(() => {
    checkForUpdates({ apply: true }).catch(() => {});
  }, 4000);
  setInterval(() => {
    checkForUpdates({ apply: true }).catch(() => {});
  }, UPDATE_POLL_MS);
}

Neutralino.events.on("ready", init);
