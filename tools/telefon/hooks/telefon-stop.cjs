const fs = require("fs");
const os = require("os");
const path = require("path");

function finish() {
  process.stdout.write("{}\n");
  process.exit(0);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

async function drainStdin() {
  return new Promise((resolve) => {
    const chunks = [];
    process.stdin.on("data", (chunk) => chunks.push(chunk));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", () => resolve(""));
    setTimeout(() => resolve(""), 300);
  });
}

async function main() {
  await drainStdin();
  const configPath = path.join(__dirname, "..", "telefon.json");
  if (!fs.existsSync(configPath)) {
    finish();
  }

  const config = readJson(configPath);
  const pending = {
    at: Date.now(),
    idleSeconds: Math.max(1, Number(config.idleSeconds ?? 10)),
    url: String(config.url || ""),
    apiKey: String(config.apiKey || ""),
    mode: config.mode === "announce" ? "announce" : "ring",
    message: String(config.message || "Vazifa bajarildi"),
    ringSeconds: Math.max(1, Number(config.ringSeconds ?? 2)),
  };
  const dir = path.join(os.homedir(), ".cursor");
  fs.mkdirSync(dir, { recursive: true });
  const dest = path.join(dir, "telefon-pending.json");
  const tmp = `${dest}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(pending)}\n`);
  fs.renameSync(tmp, dest);
  finish();
}

main().catch(() => finish());
