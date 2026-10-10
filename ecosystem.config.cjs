/**
 * PM2 process file for the AI desk phone.
 *
 *   npm start          — start (or restart) ai-phone
 *   npm run stop       — stop
 *   npm run logs       — follow logs
 *   npx pm2 status
 */
const path = require("path");

module.exports = {
  apps: [
    {
      name: "ai-phone",
      script: path.join(__dirname, "ai_phone.py"),
      interpreter: "python",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 20,
      min_uptime: "5s",
      watch: false,
      // Keep stdout line-buffered so logs show up in `pm2 logs`
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
      },
    },
  ],
};
