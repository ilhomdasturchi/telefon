const vscode = require("vscode");
const fs = require("fs");
const path = require("path");

const RULE_REL = path.join(".cursor", "rules", "git-commit-message.mdc");
const CURSOR_RULES = ".cursorrules";
const BEGIN = "# BEGIN git-commit-short";
const END = "# END git-commit-short";

const RULE_BODY = `---
description: Short imperative Git commit subjects only (~50 chars)
alwaysApply: true
---

# Git commit messages

When generating a Git commit message for this repository:

- Output exactly ONE short subject line
- Max ~50 characters (hard cap 72)
- Imperative mood: Fix..., Add..., Scale...
- No body, no bullets, no file-by-file summary
- Never start with "This commit..."
`;

const LEGACY_BLOCK = `${BEGIN}
When generating a Git commit message for this repository:
- Output exactly ONE short subject line
- Max ~50 characters (hard cap 72)
- Imperative mood: Fix..., Add..., Scale...
- No body, no bullets, no file-by-file summary
- Never start with "This commit..."
${END}
`;

function workspaceRoot() {
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
  return folder ? folder.uri.fsPath : null;
}

function rulePath(root) {
  return path.join(root, RULE_REL);
}

function isEnabled(root) {
  return !!root && fs.existsSync(rulePath(root));
}

function removeLegacyBlock(text) {
  if (!text) {
    return "";
  }
  const pattern = new RegExp(
    `\\s*${escapeRegExp(BEGIN)}\\s*[\\s\\S]*?${escapeRegExp(END)}\\s*`,
    "g"
  );
  return text.replace(pattern, "").trimEnd() + "\n";
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function addLegacyBlock(text) {
  const clean = removeLegacyBlock(text || "");
  if (!clean.trim()) {
    return `${LEGACY_BLOCK.trimEnd()}\n`;
  }
  return `${clean.trimEnd()}\n\n${LEGACY_BLOCK.trimEnd()}\n`;
}

function toggle(root) {
  const file = rulePath(root);
  const cursorRules = path.join(root, CURSOR_RULES);
  const enabled = fs.existsSync(file);

  if (enabled) {
    fs.unlinkSync(file);
    if (fs.existsSync(cursorRules)) {
      const next = removeLegacyBlock(fs.readFileSync(cursorRules, "utf8"));
      if (!next.trim()) {
        fs.unlinkSync(cursorRules);
      } else {
        fs.writeFileSync(cursorRules, `${next.trimEnd()}\n`, "utf8");
      }
    }
    return "Git commit rule: OFF (uninstalled)";
  }

  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${RULE_BODY.trimEnd()}\n`, "utf8");

  const existing = fs.existsSync(cursorRules)
    ? fs.readFileSync(cursorRules, "utf8")
    : "";
  fs.writeFileSync(cursorRules, addLegacyBlock(existing), "utf8");
  return "Git commit rule: ON (installed)";
}

function refresh(item, root) {
  const on = isEnabled(root);
  item.text = on ? "$(sparkle) Commit rule ON" : "$(sparkle) Commit rule OFF";
  item.tooltip = on
    ? "Click to uninstall short Git commit rule in this project"
    : "Click to install short Git commit rule in this project";
  item.backgroundColor = on
    ? new vscode.ThemeColor("statusBarItem.prominentBackground")
    : undefined;
}

function activate(context) {
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  item.command = "telefon.toggleGitCommitRule";
  item.show();

  const bump = () => refresh(item, workspaceRoot());
  bump();

  const cmd = vscode.commands.registerCommand("telefon.toggleGitCommitRule", () => {
    const ws = workspaceRoot();
    if (!ws) {
      vscode.window.showWarningMessage("Open a folder as the workspace root first.");
      return;
    }
    try {
      const message = toggle(ws);
      vscode.window.showInformationMessage(message);
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      vscode.window.showErrorMessage(`Commit rule toggle failed: ${text}`);
    }
    bump();
  });

  const watchers = [];
  const rewatch = () => {
    while (watchers.length) {
      watchers.pop().dispose();
    }
    const root = workspaceRoot();
    if (!root) {
      return;
    }
    const watcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(root, RULE_REL.replace(/\\/g, "/"))
    );
    watcher.onDidCreate(bump);
    watcher.onDidDelete(bump);
    watchers.push(watcher);
  };
  rewatch();

  context.subscriptions.push(
    item,
    cmd,
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      rewatch();
      bump();
    }),
    { dispose: () => watchers.forEach((w) => w.dispose()) }
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
