// Local HTTP/1 connection lifecycle baseline; no production node changes.
import { spawn } from 'node:child_process';
import { createServer, createConnection } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const keepAliveMs = Number(process.env.KEEP_ALIVE_TEST_MS || 1000);

async function port() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const value = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return value;
}

async function observe(kind, request, timeoutMs) {
  const listenPort = await port();
  const child = spawn(kind === 'node' ? process.execPath
    : path.resolve(root, process.env.RUST_BINARY || 'target/release/speedtest-rust.exe'),
  kind === 'node' ? ['server.js'] : [], {
    cwd: root, stdio: ['ignore', 'ignore', 'pipe'],
    env: { ...process.env, PORT: String(listenPort), SPEEDTEST_HOST: '127.0.0.1',
      HTTP_KEEP_ALIVE_TIMEOUT_MS: String(keepAliveMs),
      HTTP_HEADERS_TIMEOUT_MS: process.env.HEADER_TIMEOUT_TEST_MS || '1000',
      HTTP_REQUEST_TIMEOUT_MS: process.env.REQUEST_TIMEOUT_TEST_MS || '35000',
      MAX_UPLOAD_MS: process.env.MAX_UPLOAD_MS || '1000' },
  });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr = (stderr + chunk).slice(-1024); });
  try {
    let ready = false;
    for (let i = 0; i < 200; i++) {
      if (child.exitCode !== null) throw new Error(`${kind} exited: ${stderr}`);
      try {
        if ((await fetch(`http://127.0.0.1:${listenPort}/health`)).ok) {
          ready = true;
          break;
        }
      } catch { /* startup */ }
      await sleep(50);
    }
    assert(ready, `${kind} startup timeout: ${stderr}`);
    if (request.endsWith('Host: ')) await sleep(Number(process.env.PARTIAL_START_DELAY_MS || 0));
    const socket = createConnection(listenPort, '127.0.0.1');
    let data = '';
    const started = Date.now();
    let closedAt = null;
    socket.on('data', (chunk) => { data += chunk; });
    socket.on('close', () => { closedAt = Date.now() - started; });
    await new Promise((resolve, reject) => {
      socket.once('connect', resolve);
      socket.once('error', reject);
    });
    socket.write(request);
    await sleep(timeoutMs);
    socket.destroy();
    const responseHeaders = new Map(data.split('\r\n\r\n')[0].split('\r\n').slice(1)
      .filter((line) => line.includes(':')).map((line) => {
        const at = line.indexOf(':');
        return [line.slice(0, at).toLowerCase(), line.slice(at + 1).trim()];
      }));
    return { kind, responseStatuses: [...data.matchAll(/HTTP\/1\.1 (\d+)/g)]
      .map((match) => Number(match[1])), connection: responseHeaders.get('connection') || null,
    keepAlive: responseHeaders.get('keep-alive') || null, closedAt };
  } finally {
    child.kill();
  }
}

const keepalive = 'GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n';
const partial = 'GET /health HTTP/1.1\r\nHost: ';
const idleWaitMs = Math.max(4000, keepAliveMs + 2000);
const idle = [await observe('node', keepalive, idleWaitMs),
  await observe('rust', keepalive, idleWaitMs)];
for (const result of idle) {
  assert.deepEqual(result.responseStatuses, [200]);
  assert.equal(result.connection, 'keep-alive');
  assert.equal(result.keepAlive, `timeout=${Math.floor(keepAliveMs / 1000)}, max=100`);
  assert(Math.abs(result.closedAt - (keepAliveMs + 1000)) <= 500,
    `${result.kind} idle close outside Node 24 timeout+buffer: ${result.closedAt}`);
}
assert(Math.abs(idle[0].closedAt - idle[1].closedAt) <= 500);
console.log(JSON.stringify({ keepalive: idle }));

// Node checks incomplete headers on a longer cadence; four seconds of silence
// is not a proof that the configured headersTimeout has been implemented.
const partialWaitMs = Number(process.env.PARTIAL_HEADERS_WAIT_MS || 4000);
const startDelayMs = Number(process.env.PARTIAL_START_DELAY_MS || 0);
const incomplete = await Promise.all([observe('node', partial, partialWaitMs),
  observe('rust', partial, partialWaitMs)]);
if (partialWaitMs <= 4000) {
  for (const result of incomplete) {
    assert.deepEqual(result.responseStatuses, []);
    assert.equal(result.closedAt, null);
  }
} else if (partialWaitMs >= 40000 || (startDelayMs && partialWaitMs >= 20000)) {
  for (const result of incomplete) {
    assert.deepEqual(result.responseStatuses, [408], `${result.kind} incomplete headers`);
    assert.equal(result.connection, 'close');
    assert(result.closedAt !== null && Math.abs(result.closedAt - (30000 - startDelayMs)) <= 2000,
      `${result.kind} incomplete headers closed at ${result.closedAt}`);
  }
}
console.log(JSON.stringify({ partialHeaders: incomplete }));
