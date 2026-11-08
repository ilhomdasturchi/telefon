const vscode = require("vscode");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const SCRIPT_REL = path.join("scripts", "toggle-git-commit-rule.ps1");
const RULE_REL = path.join(".cursor", "rules", "git-commit-message.mdc");

function workspaceRoot() {
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
  return folder ? folder.uri.fsPath : null;
}

function isEnabled(root) {
  return !!root && fs.existsSync(path.join(root, RULE_REL));
}

function refresh(item, root) {
  const on = isEnabled(root);
  item.text = on ? "$(sparkle) Commit rule ON" : "$(sparkle) Commit rule OFF";
  item.tooltip = on
    ? "Click to uninstall short Git commit rule"
    : "Click to install short Git commit rule";
  item.backgroundColor = on
    ? new vscode.ThemeColor("statusBarItem.prominentBackground")
    : undefined;
}

function toggle(root) {
  const script = path.join(root, SCRIPT_REL);
  if (!fs.existsSync(script)) {
    vscode.window.showErrorMessage(`Missing ${SCRIPT_REL}`);
    return;
  }
  const result = spawnSync(
    "powershell",
    ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
    { cwd: root, encoding: "utf8" }
  );
  const out = `${result.stdout || ""}${result.stderr || ""}`.trim();
  if (result.status !== 0) {
    vscode.window.showErrorMessage(out || "Toggle failed");
    return;
  }
  vscode.window.showInformationMessage(out || "Toggled");
}

function activate(context) {
  const root = workspaceRoot();
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  item.command = "telefon.toggleGitCommitRule";
  item.show();
  refresh(item, root);

  const cmd = vscode.commands.registerCommand("telefon.toggleGitCommitRule", () => {
    const ws = workspaceRoot();
    if (!ws) {
      vscode.window.showWarningMessage("Open the telefon folder as the workspace root.");
      return;
    }
    toggle(ws);
    refresh(item, ws);
  });

  const watcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(root || ".", RULE_REL)
  );
  const bump = () => refresh(item, workspaceRoot());
  watcher.onDidCreate(bump);
  watcher.onDidDelete(bump);

  context.subscriptions.push(item, cmd, watcher);
}

function deactivate() {}

module.exports = { activate, deactivate };
