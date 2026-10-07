// Dev builds must not require a frozen sidecar; release retains externalBin.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const args = process.argv.slice(2);
if (args[0] === "dev") args.push("--config", "tauri.dev.json");
const result = spawnSync(process.execPath, [require.resolve("@tauri-apps/cli/tauri.js"), ...args], {
  stdio: "inherit",
});
process.exit(result.status ?? 1);
