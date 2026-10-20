// A disposable headless Edge/Chromium profile tests the actual browser Fetch
// streaming API over local Caddy HTTP/2. Pass the origin printed by
// `node test/caddy-h2-upload.mjs --serve`, or run that test with --browser to
// manage both processes automatically. No CA is installed system-wide.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const origin = process.argv[2];
if (!origin || !/^https:\/\/127\.0\.0\.1:\d+$/.test(origin)) {
  throw new Error('pass a local Caddy HTTPS origin, e.g. https://127.0.0.1:12345');
}
const browser = process.env.BROWSER_BIN ||
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const profile = await mkdtemp(path.join(tmpdir(), 'speedtest-edge-h2-'));
const child = spawn(browser, ['--headless=new', '--disable-gpu', '--no-first-run',
  '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
  '--disable-backgrounding-occluded-windows',
  '--ignore-certificate-errors', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
let ws;
try {
  let port;
  for (let i = 0; i < 200; i++) {
    if (child.exitCode !== null) throw new Error(`browser exited: ${child.exitCode}`);
    try {
      port = Number((await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]);
      if (port) break;
    } catch { /* profile not ready */ }
    await sleep(100);
  }
  if (!port) throw new Error('headless browser did not expose DevTools');
  const target = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(origin + '/?singleProtocol=h2')}`,
    { method: 'PUT' }).then((res) => res.json());
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', reject, { once: true });
  });
  let next = 0;
  const pending = new Map();
  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    const callback = pending.get(message.id);
    if (callback) { pending.delete(message.id); callback(message); }
  });
  const command = (method, params) => new Promise((resolve, reject) => {
    const id = ++next;
    pending.set(id, (result) => result.error ? reject(new Error(result.error.message)) : resolve(result.result));
    ws.send(JSON.stringify({ id, method, params }));
  });
  let ready = false;
  for (let i = 0; i < 100; i++) {
    const reply = await command('Runtime.evaluate', { expression: 'document.readyState + "|" + location.origin',
      returnByValue: true });
    if (reply.result.value === `complete|${origin}`) { ready = true; break; }
    await sleep(100);
  }
  if (!ready) throw new Error('test page did not load');
  const expression = `
    (async () => {
      const protocol = performance.getEntriesByType('navigation')[0]?.nextHopProtocol;
      const size = 1024 * 1024;
      let sent = 0;
      const body = new ReadableStream({
        pull(controller) {
          if (sent >= size) { controller.close(); return; }
          const n = Math.min(32768, size - sent);
          controller.enqueue(new Uint8Array(n));
          sent += n;
        }
      });
      const response = await fetch('/upload-stream?warmupMs=0',
        { method: 'POST', body, duplex: 'half' });
      return { protocol, status: response.status, body: await response.json() };
    })()
  `;
  const result = await command('Runtime.evaluate', { expression, awaitPromise: true,
    returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  assert.equal(result.result.value.protocol, 'h2');
  assert.equal(result.result.value.status, 200);
  assert.equal(result.result.value.body.bytes, 1024 * 1024);
  console.log('Chromium Fetch ReadableStream 1 MiB upload over Caddy HTTP/2 passed');
  if (process.argv.includes('--ui')) {
    // The original page automatically runs its WebRTC RTT and object waterfall.
    // Then use its real Start button for the timed single-stream worker path.
    let phase;
    for (let i = 0; i < 300; i++) {
      const reply = await command('Runtime.evaluate', { expression:
        '({ ready: !document.getElementById("run").disabled, phase: document.getElementById("phase").textContent })',
      returnByValue: true });
      phase = reply.result.value.phase;
      if (reply.result.value.ready) break;
      await sleep(100);
    }
    if (phase !== 'RTT complete') throw new Error(`initial page flow did not complete: ${phase}`);
    await command('Runtime.evaluate', { expression: 'document.getElementById("run").click()' });
    let evidence;
    for (let i = 0; i < 600; i++) {
      const reply = await command('Runtime.evaluate', { expression:
        '({ phase: document.getElementById("phase").textContent, download: document.getElementById("downloadMeta").textContent, upload: document.getElementById("uploadMeta").textContent, log: document.getElementById("log").textContent.slice(-800) })',
      returnByValue: true });
      evidence = reply.result.value;
      if (evidence.phase === 'Speed complete' || evidence.phase === 'Speed error') break;
      await sleep(100);
    }
    console.log(JSON.stringify(evidence, null, 2));
    assert.equal(evidence.phase, 'Speed complete');
    assert.match(evidence.download, /Final; 1 stream; HTTP\/2/);
    assert.match(evidence.upload, /1 stream; steady/);
    console.log('Original UI single-stream download and upload over Caddy HTTP/2 passed');
    if (process.argv.includes('--stability')) {
      // Exercise the actual page's stability button and report validator,
      // rather than a synthetic protocol client or a test hook.
      await command('Runtime.evaluate', { expression: `
        document.getElementById('stabilityRate').value = '20';
        document.getElementById('stabilityDuration').value = '20';
        document.getElementById('runStability').click();
      ` });
      let stability;
      for (let i = 0; i < 450; i++) {
        const reply = await command('Runtime.evaluate', { expression: `(() => {
          const report = globalThis.__speedtestEvidence?.stability;
          return {
            status: document.getElementById('stabilityStatus').textContent,
            error: document.getElementById('stabilityErrorDetails').textContent.slice(0, 500),
            phase: globalThis.__speedtestEvidence?.phase,
            validation: report?.validation?.valid,
            upSent: report?.upload?.sent, upReceived: report?.upload?.received,
            downSent: report?.download?.sent, downReceived: report?.download?.received,
          };
        })()` , returnByValue: true });
        stability = reply.result.value;
        if (stability.status === 'Stability Test Complete'
          || stability.status.startsWith('Stability Test Error:')
          || stability.status === 'Stability Test (Incomplete)') break;
        await sleep(100);
      }
      console.log(`ORIGINAL_UI_STABILITY:${JSON.stringify(stability)}`);
      assert.equal(stability.status, 'Stability Test Complete');
      assert.equal(stability.phase, 'stability-complete');
      assert.equal(stability.validation, true);
      assert.equal(stability.upSent, 400);
      assert.equal(stability.downSent, 400);
      assert(stability.upReceived >= 396 && stability.upReceived <= 400);
      assert(stability.downReceived >= 396 && stability.downReceived <= 400);
    }
  }
} finally {
  ws?.close();
  child.kill();
  await sleep(500);
  await rm(profile, { recursive: true, force: true });
}
