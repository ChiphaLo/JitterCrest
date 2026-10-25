// Local raw-socket baseline for Node's maxHeadersCount versus Hyper parsing.
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';
import { createConnection, createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const maxHeaders = Number(process.env.HEADER_COUNT_TEST_MAX || 16);
async function port() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const selected = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return selected;
}
function raw(portNumber, request) {
  return new Promise((resolve, reject) => {
    const socket = createConnection(portNumber, '127.0.0.1');
    let response = '';
    const timer = setTimeout(() => socket.destroy(new Error('response timeout')), 3000);
    socket.on('error', reject);
    socket.on('data', (chunk) => { response += chunk.toString('latin1'); });
    socket.on('close', () => {
      clearTimeout(timer);
      resolve({ status: Number(response.match(/^HTTP\/1\.1 (\d+)/)?.[1] || 0),
        bytes: response.length, headers: response.split('\r\n\r\n')[0],
        body: response.split('\r\n\r\n')[1]?.slice(0, 80) });
    });
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
      HTTP_MAX_HEADERS_COUNT: String(maxHeaders), HTTP_KEEP_ALIVE_TIMEOUT_MS: '1000' },
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
    const results = [];
    for (const count of [maxHeaders - 1, maxHeaders, maxHeaders + 1]) {
      const headers = ['Host: localhost', ...Array.from({ length: count - 1 }, (_, i) => `X-Test-${i}: a`)];
      const request = `GET /health HTTP/1.1\r\n${headers.join('\r\n')}\r\nConnection: close\r\n\r\n`;
      results.push({ count, ...(await raw(listenPort, request)) });
    }
    const headers = ['Host: localhost', ...Array.from({ length: maxHeaders }, (_, i) => `X-Test-${i}: a`),
      'Origin: https://disallowed.example'];
    const lateOrigin = await raw(listenPort,
      `GET /download?size=1024 HTTP/1.1\r\n${headers.join('\r\n')}\r\nConnection: close\r\n\r\n`);
    return { kind, results, lateOrigin };
  } finally { child.kill(); }
}
const results = [await run('node'), await run('rust')];
for (const result of results) {
  assert.deepEqual(result.results.map(({ status }) => status), [200, 431, 431]);
  assert.equal(result.lateOrigin.status, 431);
}
console.log(JSON.stringify(results));
