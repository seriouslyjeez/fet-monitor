// Headless Chrome over the DevTools protocol, no packages (Node 22+ has WebSocket): open a page at a screen size, light
// or dark, run a check script in it and print what it returns (JSON). Used by tests/test_layout.py.
//   node tests/cdp.mjs <chrome> <url> <width> <height> <light|dark> <check.js>
// The check script is the body of an async function run in the page; it returns a JSON-able value.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [chromePath, url, width, height, scheme, checkFile] = process.argv.slice(2);
const check = readFileSync(checkFile, "utf8");
const prof = mkdtempSync(join(tmpdir(), "fet-cdp-"));
const chrome = spawn(chromePath, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
  "--remote-debugging-port=0", "--user-data-dir=" + prof, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
const done = (code, out) => { if (out !== undefined) process.stdout.write(JSON.stringify(out)); try { chrome.kill("SIGKILL"); } catch (e) {} try { rmSync(prof, { recursive: true, force: true }); } catch (e) {} process.exit(code); };
setTimeout(() => done(2, { error: "timed out" }), 60000);

const wsUrl = await new Promise((ok, bad) => {
  let buf = "";
  chrome.stderr.on("data", d => { buf += d; const m = buf.match(/DevTools listening on (ws:\/\/\S+)/); if (m) ok(m[1]); });
  chrome.on("exit", c => bad(new Error("chrome exited " + c)));
});
const ws = new WebSocket(wsUrl);
await new Promise(ok => ws.addEventListener("open", ok));
let id = 0;
const waiting = new Map(), events = [];
ws.addEventListener("message", e => {
  const m = JSON.parse(e.data);
  if (m.id && waiting.has(m.id)) { const [ok, bad] = waiting.get(m.id); waiting.delete(m.id); m.error ? bad(new Error(JSON.stringify(m.error))) : ok(m.result); }
  else if (m.method) events.push(m);
});
const send = (method, params = {}, sessionId) => new Promise((ok, bad) => { const i = ++id; waiting.set(i, [ok, bad]); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
const until = async (test, ms = 15000) => { const end = Date.now() + ms; while (Date.now() < end) { if (events.some(test)) return true; await new Promise(r => setTimeout(r, 25)); } return false; };

try {
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  const w = Number(width), h = Number(height);
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 700 }, sessionId);
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme === "dark" ? "dark" : "light" }] }, sessionId);
  await send("Page.enable", {}, sessionId);
  await send("Runtime.enable", {}, sessionId);
  await send("Page.navigate", { url }, sessionId);
  await until(m => m.method === "Page.loadEventFired" && m.sessionId === sessionId);
  const r = await send("Runtime.evaluate", { expression: "(async () => {" + check + "\n})()", awaitPromise: true, returnByValue: true }, sessionId);
  const errors = events.filter(m => m.method === "Runtime.exceptionThrown").map(m => m.params.exceptionDetails.exception ? m.params.exceptionDetails.exception.description : m.params.exceptionDetails.text);
  if (r.exceptionDetails) done(1, { error: r.exceptionDetails.exception ? r.exceptionDetails.exception.description : r.exceptionDetails.text, pageErrors: errors });
  if (process.env.CDP_SHOT) {                       // also a screenshot (development: compare with the boards)
    const fullH = process.env.CDP_FULL ? (await send("Runtime.evaluate", { expression: "document.documentElement.scrollHeight", returnByValue: true }, sessionId)).result.value : h;
    if (fullH > h) await send("Emulation.setDeviceMetricsOverride", { width: w, height: fullH, deviceScaleFactor: 1, mobile: w < 700 }, sessionId);
    const shot = await send("Page.captureScreenshot", { format: "png" }, sessionId);
    (await import("node:fs")).writeFileSync(process.env.CDP_SHOT, Buffer.from(shot.data, "base64"));
  }
  done(0, { value: r.result.value, pageErrors: errors });
} catch (e) {
  done(1, { error: String(e) });
}
