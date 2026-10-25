// Raw HTTP/1 pipelining beyond Node maxRequestsPerSocket; local-only A/B.
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';
import { createConnection, createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const request = 'GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n';
async function port() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const selected = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return selected;
}
async function run(kind, first, second, delayMs, limit, payload = null) {
  const listenPort = await port();
  const binary = kind === 'node' ? process.execPath : path.resolve(root,
    process.env.RUST_BINARY || 'target/release/speedtest-rust.exe');
  const child = spawn(binary, kind === 'node' ? ['server.js'] : [], {
    cwd: root, stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, SPEEDTEST_HOST: '127.0.0.1', PORT: String(listenPort),
      HTTP_MAX_REQUESTS_PER_SOCKET: String(limit), HTTP_KEEP_ALIVE_TIMEOUT_MS: '1000' },
  });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr = (stderr + chunk).slice(-1000); });
  child.stdout.on('data', () => {});
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (child.exitCode !== null) throw new Error(`${kind} exited: ${stderr}`);
      try { if ((await fetch(`http://127.0.0.1:${listenPort}/health`)).ok) { ready = true; break; } }
      catch { /* startup */ }
      await sleep(50);
    }
    if (!ready) throw new Error(`${kind} startup timeout: ${stderr}`);
    const socket = createConnection(listenPort, '127.0.0.1');
    let response = '';
    const result = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => socket.destroy(), 3500);
      socket.on('connect', () => {
        socket.write(payload ?? request.repeat(first));
        if (second) setTimeout(() => { if (!socket.destroyed) socket.write(request.repeat(second)); }, delayMs);
      });
      socket.on('data', (chunk) => { response += chunk.toString('latin1'); });
      socket.on('error', (error) => { if (error.code !== 'ECONNRESET') reject(error); });
      socket.on('close', () => { clearTimeout(timer); resolve(response); });
    });
    const statuses = [...result.matchAll(/HTTP\/1\.1 (\d+) ([^\r]+)/g)]
      .map((match) => Number(match[1]));
    const tail = result.slice(result.lastIndexOf('HTTP/1.1 503')).slice(0, 160);
    return { kind, first, second, delayMs, limit, statuses, tail };
  } finally { child.kill(); }
}
for (const [first, second, delayMs, limit] of [
  [5, 0, 0, 1], [5, 0, 0, 3], [30, 0, 0, 3], [300, 0, 0, 3],
  [102, 0, 0, 100], [3, 2, 0, 3], [3, 2, 10, 3], [3, 2, 100, 3],
  [3, 2, 1000, 3], [3, 2, 1500, 3], [3, 2, 2600, 3],
]) {
  const results = await Promise.all(['node', 'rust']
    .map((kind) => run(kind, first, second, delayMs, limit)));
  const expected = delayMs === 2600 ? Array(limit).fill(200)
    : [...Array(limit).fill(200), ...Array(first + second - limit).fill(503)];
  for (const result of results) assert.deepEqual(result.statuses, expected);
  console.log(JSON.stringify(results));
}
for (const [label, third] of [
  ['consumed-post', 'POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: 3\r\n\r\nabc'],
  ['ignored-get-body', 'GET /health HTTP/1.1\r\nHost: localhost\r\nContent-Length: 3\r\n\r\nabc'],
]) {
  const payload = request.repeat(2) + third + request;
  const results = await Promise.all(['node', 'rust'].map((kind) => run(kind, 0, 0, 0, 3, payload)));
  for (const result of results) assert.deepEqual(result.statuses, [200, 200, 200, 503]);
  console.log(JSON.stringify({ label, results }));
}
