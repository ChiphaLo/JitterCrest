// Local real-socket Node/Rust A/B for slow and abruptly disconnected uploads.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createConnection, createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function freePort() {
  const socket = createServer();
  await new Promise((resolve) => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  return port;
}

async function partialUpload(port, uri, abort, onSent = () => {}, abortDelay = 100) {
  return new Promise((resolve, reject) => {
    const socket = createConnection({ host: '127.0.0.1', port });
    let response = '';
    const timer = setTimeout(() => socket.destroy(new Error('partial upload timed out')), 5000);
    socket.on('error', (error) => { clearTimeout(timer); reject(error); });
    socket.on('data', (part) => { response += part.toString('latin1'); });
    socket.on('close', () => {
      clearTimeout(timer);
      if (!abort) resolve({ status: Number(response.match(/^HTTP\/1\.1 (\d+)/)?.[1] || 0),
        closed: true });
    });
    socket.on('connect', async () => {
      const streaming = uri.startsWith('/upload-stream');
      const headers = streaming
        ? `POST ${uri} HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n400\r\n`
        : `POST ${uri} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 102400\r\nConnection: close\r\n\r\n`;
      socket.write(headers);
      socket.write(Buffer.alloc(1024, 7));
      if (streaming) socket.write('\r\n');
      onSent();
      if (abort) {
        await sleep(abortDelay);
        socket.destroy();
        resolve({ aborted: true });
      }
    });
  });
}

async function run(kind) {
  const port = await freePort();
  const executable = kind === 'node' ? process.execPath
    : (process.env.RUST_BINARY || path.join(root, 'target', 'release',
      process.platform === 'win32' ? 'speedtest-rust.exe' : 'speedtest-rust'));
  const child = spawn(executable, kind === 'node' ? ['server.js'] : [], {
    cwd: root, env: { ...process.env, SPEEDTEST_HOST: '127.0.0.1', PORT: String(port),
      MAX_UPLOAD_MS: '1000', MAX_ACTIVE_PER_IP: '8', SPEED_RATE_MAX: '2000', RATE_MAX: '1000' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let logs = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', (data) => { logs += data; });
  const base = `http://127.0.0.1:${port}`;
  try {
    let ready = false;
    for (let n = 0; n < 600; n++) {
      if (child.exitCode !== null) throw new Error(`${kind} exited: ${logs.slice(-1000)}`);
      try { if ((await fetch(`${base}/health`)).ok) { ready = true; break; } } catch { /* startup */ }
      await sleep(50);
    }
    assert(ready, `${kind} startup timeout: ${logs.slice(-1000)}`);
    const fixedTimeout = await partialUpload(port, '/upload', false);
    const streamTimeout = await partialUpload(port, '/upload-stream?warmupMs=0', false);
    const sent = [];
    const held = Array.from({ length: 8 }, () => {
      let markSent;
      sent.push(new Promise((resolve) => { markSent = resolve; }));
      return partialUpload(port, '/upload', false, markSent);
    });
    await Promise.all(sent);
    await sleep(100);
    const saturated = await fetch(`${base}/upload`, { method: 'POST', body: Buffer.alloc(1024, 7) });
    await saturated.arrayBuffer();
    const heldResults = await Promise.all(held);
    assert(heldResults.every((result) => result.status === 408));
    const abortSent = [];
    const aborted = Array.from({ length: 8 }, () => {
      let markSent;
      abortSent.push(new Promise((resolve) => { markSent = resolve; }));
      return partialUpload(port, '/upload', true, markSent, 300);
    });
    await Promise.all(abortSent);
    await sleep(50);
    const abortSaturated = await fetch(`${base}/upload`, { method: 'POST', body: Buffer.alloc(1024, 7) });
    await abortSaturated.arrayBuffer();
    const abortedResults = await Promise.all(aborted);
    await sleep(200);
    const followup = await fetch(`${base}/upload`, { method: 'POST', body: Buffer.alloc(10240, 7) });
    const body = await followup.json();
    return { fixedTimeout, streamTimeout, saturatedStatus: saturated.status,
      heldTimeouts: heldResults.length, abortSaturatedStatus: abortSaturated.status,
      abortedCount: abortedResults.length, followupStatus: followup.status,
      followupBytes: body.bytes };
  } finally {
    child.kill();
  }
}

const node = await run('node');
const rust = await run('rust');
assert.deepEqual(rust, node);
console.log('Node/Rust slow and abort upload A/B:', JSON.stringify(rust));
