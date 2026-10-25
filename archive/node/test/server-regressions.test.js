const assert = require("node:assert/strict");
const { after, before, test } = require("node:test");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const SERVER_SOURCE = fs.readFileSync(path.join(ROOT, "server.js"), "utf8");

let child;
let baseUrl;
let indexResponse;
let indexHtml;

async function reservePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

async function waitForServer(url) {
  let lastError;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(`${url}/health`);
      if (response.ok) return;
      lastError = new Error(`health returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw lastError || new Error("server did not become ready");
}

before(async () => {
  const port = await reservePort();
  baseUrl = `http://127.0.0.1:${port}`;
  child = spawn(process.execPath, [path.join(ROOT, "server.js")], {
    cwd: ROOT,
    env: {
      ...process.env,
      SPEEDTEST_HOST: "127.0.0.1",
      PORT: String(port),
      SPEEDTEST_NODE_NAME: "regression-test",
      SPEEDTEST_NODES: "",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  await waitForServer(baseUrl);
  indexResponse = await fetch(`${baseUrl}/`);
  indexHtml = await indexResponse.text();
});

after(async () => {
  if (!child || child.exitCode !== null) return;
  child.kill();
  await new Promise((resolve) => {
    const timer = setTimeout(resolve, 2000);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
});

test("favicon is linked and served as a cacheable SVG, including HEAD and /speed alias", async () => {
  assert.match(indexHtml, /<link rel="icon" type="image\/svg\+xml" href="\/favicon\.svg\?v=1">/);
  for (const route of ["/favicon.svg", "/speed/favicon.svg"]) {
    const response = await fetch(`${baseUrl}${route}?v=1`);
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type"), /image\/svg\+xml/);
    assert.match(response.headers.get("cache-control"), /max-age=86400/);
    const svg = await response.text();
    assert.match(svg, /viewBox="0 0 64 64"/);
    assert.doesNotMatch(svg, /<script|<image|<foreignObject/);
    const head = await fetch(`${baseUrl}${route}`, { method: "HEAD" });
    assert.equal(head.status, 200);
    assert.equal(Number(head.headers.get("content-length")), Buffer.byteLength(svg));
    assert.equal(await head.text(), "");
  }
});

test("the server accepts the UI's 60-second WebRTC measurement duration", () => {
  assert.match(SERVER_SOURCE, /MAX_RTC_MEASUREMENT_MS/);
  assert.match(
    SERVER_SOURCE,
    /normalizeMeasurementDurationMs\(msg\.durationMs,\s*MAX_RTC_MEASUREMENT_MS\)/,
  );
});

test("large upload completion evidence is fragmented by the client and reassembled by the server", () => {
  assert.match(SERVER_SOURCE, /serializeFragmentedControlMessage\(complete,/);
  assert.match(SERVER_SOURCE, /createControlFragmentReassembler\(/);
  assert.match(SERVER_SOURCE, /messageType !== "uploadComplete"/);
});

test("stability charts render incrementally while text updates remain lightweight", () => {
  assert.match(SERVER_SOURCE, /const STABILITY_TEXT_UPDATE_MS = 200;/);
  assert.match(SERVER_SOURCE, /sentCount = scheduler \? scheduler\.getScheduled\(\) : 0;/);
  assert.doesNotMatch(SERVER_SOURCE, /getSentSlots\(\)\.filter/);
  assert.match(SERVER_SOURCE, /Math\.max\(0, liveSnapshot\.totalSent - liveSnapshot\.totalReceived\)/);
  assert.match(SERVER_SOURCE, /function createLiveStabilityRttRenderer\(planned\)/);
  assert.match(SERVER_SOURCE, /window\.requestAnimationFrame\(flush\)/);
  assert.match(SERVER_SOURCE, /ctx\.fillRect\(x - 1, y - 1, 2, 2\)/);
  assert.match(SERVER_SOURCE, /onUploadSample: function\(rtt, seq\)/);
  assert.match(SERVER_SOURCE, /onDownloadSample: function\(rtt, seq\)/);
  assert.doesNotMatch(
    SERVER_SOURCE,
    /liveTimer = setInterval[\s\S]{0,800}buildLiveStabilityAnalysis/,
  );
});

test("the RTT incomplete badge obeys the hidden attribute", () => {
  assert.match(
    indexHtml,
    /\.rtt-incomplete-badge\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}/,
  );
});

test("the default node list contains only the same-origin local server", () => {
  const config = indexHtml.match(/<script[^>]*id="nodeConfig"[^>]*>([\s\S]*?)<\/script>/);
  assert.ok(config, "embedded node configuration is present");
  assert.deepEqual(JSON.parse(config[1]), [
    { id: "local", label: "Local server", base: "", rtcTargetMs: 25 },
  ]);
});

test("changing nodes synchronizes the selected node into the page URL", () => {
  assert.match(indexHtml, /function syncSelectedNodeUrl\(\)/);
  assert.match(
    indexHtml,
    /if \(navigateToProbeOnlyDirectPage\(\) \|\| navigateFromProbeOnlyToSharedPage\(\)\) return;\s*syncSelectedNodeUrl\(\);/,
  );
});
