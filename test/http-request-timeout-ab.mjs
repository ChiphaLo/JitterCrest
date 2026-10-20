// Observe requestTimeout on a complete head with an incomplete body.
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';
import { createConnection, createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
async function port() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const selected = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return selected;
}
function observe(portNumber, request, waitMs) {
  return new Promise((resolve, reject) => {
    const socket = createConnection(portNumber, '127.0.0.1');
    const started = Date.now();
    let response = '';
    let closedAt = null;
    let timedOutLocally = false;
    const timeout = setTimeout(() => { timedOutLocally = true; socket.destroy(); }, waitMs);
    socket.on('data', (chunk) => { response += chunk.toString('latin1'); });
    socket.on('error', reject);
    socket.on('close', () => {
      clearTimeout(timeout);
      resolve({ statuses: [...response.matchAll(/HTTP\/1\.1 (\d+)/g)].map((m) => Number(m[1])),
        closedAt: closedAt ?? Date.now() - started, timedOutLocally,
        firstHead: response.split('\r\n\r\n')[0].slice(0, 220) });
    });
    socket.on('end', () => { closedAt = Date.now() - started; });
    socket.on('connect', () => socket.write(request));
  });
}
async function run(kind) {
  const listenPort = await port();
  const binary = kind === 'node' ? process.execPath : path.resolve(root,
    process.env.RUST_BINARY || 'target/release/speedtest-rust.exe');
  const child = spawn(binary, kind === 'node' ? ['server.js'] : [], {
    cwd: root, stdio: ['ignore', 'ignore', 'pipe'],
    env: { ...process.env, SPEEDTEST_HOST: '127.0.0.1', PORT: String(listenPort),
      HTTP_HEADERS_TIMEOUT_MS: process.env.HEADERS_TEST_MS || '60000',
      HTTP_REQUEST_TIMEOUT_MS: process.env.REQUEST_TEST_MS || '1000',
      HTTP_KEEP_ALIVE_TIMEOUT_MS: '1000', MAX_UPLOAD_MS: '1000' },
  });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr = (stderr + chunk).slice(-1000); });
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (child.exitCode !== null) throw new Error(`${kind} exited: ${stderr}`);
      try { if ((await fetch(`http://127.0.0.1:${listenPort}/health`)).ok) { ready = true; break; } }
      catch { /* startup */ }
      await sleep(50);
    }
    if (!ready) throw new Error(`${kind} startup timeout: ${stderr}`);
    const waitMs = Number(process.env.PARTIAL_BODY_WAIT_MS || 40000);
    const paths = process.env.TIMEOUT_ONLY_RTC === '1' ? ['/webrtc/offer']
      : ['/health', '/not-found', '/upload', '/webrtc/offer'];
    const results = await Promise.all(paths.map(async (uri) => {
      if (uri === '/webrtc/offer') await sleep(Number(process.env.BODY_START_DELAY_MS || 0));
      return { uri, ...await observe(listenPort,
        `${uri === '/upload' || uri === '/webrtc/offer' ? 'POST' : 'GET'} ${uri} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n${uri === '/webrtc/offer' ? 'Content-Type: application/json\r\n' : ''}\r\nx`, waitMs) };
    }));
    return { kind, results };
  } finally { child.kill(); }
}
const kinds = process.env.TIMEOUT_NODE_ONLY === '1' ? ['node'] : ['node', 'rust'];
const observed = await Promise.all(kinds.map(run));
if (process.env.TIMEOUT_EXPECT_MS) {
  const expected = Number(process.env.TIMEOUT_EXPECT_MS);
  for (const result of observed) {
    const rtc = result.results.find(({ uri }) => uri === '/webrtc/offer');
    assert.deepEqual(rtc.statuses, [408], `${result.kind} RTC request timeout`);
    assert.equal(rtc.timedOutLocally, false);
    assert(Math.abs(rtc.closedAt - expected) < 2500,
      `${result.kind} RTC request timeout at ${rtc.closedAt} instead of ${expected}`);
  }
}
console.log(JSON.stringify(observed));
