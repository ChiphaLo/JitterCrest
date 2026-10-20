// A response may finish before its advertised request body is complete.
// Compare Node and Rust raw TCP reuse and idle closure without HTTP helpers.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createConnection, createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const rust = process.env.RUST_BINARY || resolve(root, 'target/x86_64-pc-windows-gnu/release/speedtest-rust.exe');
const nextRequest = 'GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n';
const first = 'GET /health HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\nx';

async function freePort() {
  const socket = createServer();
  await new Promise((accept) => socket.listen(0, '127.0.0.1', accept));
  const port = socket.address().port;
  await new Promise((accept) => socket.close(accept));
  return port;
}

async function observe(port, finishAfterMs, sendNext = true) {
  return new Promise((accept, reject) => {
    const socket = createConnection(port, '127.0.0.1');
    const began = Date.now();
    let response = '';
    let localTimeout = false;
    const timeout = setTimeout(() => { localTimeout = true; socket.destroy(); }, 3600);
    socket.on('connect', async () => {
      socket.write(first);
      if (finishAfterMs !== null) {
        await sleep(finishAfterMs);
        if (!socket.destroyed) socket.write('x'.repeat(99) + (sendNext ? nextRequest : ''));
      }
    });
    socket.on('data', (chunk) => { response += chunk.toString('latin1'); });
    socket.on('error', (error) => {
      if (error.code !== 'ECONNRESET' && error.code !== 'EPIPE') reject(error);
    });
    socket.on('close', () => {
      clearTimeout(timeout);
      accept({ finishAfterMs, sendNext, statuses: [...response.matchAll(/HTTP\/1\.1 (\d+)/g)]
        .map((match) => Number(match[1])), closeMs: Date.now() - began, localTimeout });
    });
  });
}

async function run(kind) {
  const port = await freePort();
  const child = spawn(kind === 'node' ? process.execPath : rust,
    kind === 'node' ? ['server.js'] : [], { cwd: root,
      stdio: process.env.TRACE_EARLY === '1' ? ['ignore', 'pipe', 'pipe'] : 'ignore',
      env: { ...process.env, SPEEDTEST_HOST: '127.0.0.1', PORT: String(port),
        HTTP_KEEP_ALIVE_TIMEOUT_MS: '1000' } });
  let logs = '';
  child.stdout?.on('data', (chunk) => { logs += chunk.toString(); });
  child.stderr?.on('data', (chunk) => { logs += chunk.toString(); });
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (child.exitCode !== null) throw new Error(`${kind} exited during startup`);
      try { if ((await fetch(`http://127.0.0.1:${port}/health`)).ok) { ready = true; break; } }
      catch { /* startup */ }
      await sleep(50);
    }
    assert(ready, `${kind} startup`);
    const cases = await Promise.all([
      observe(port, 0), observe(port, 500), observe(port, 1500), observe(port, 500, false),
      observe(port, null),
    ]);
    return { kind, cases, ...(process.env.TRACE_EARLY === '1' ? { logBytes: logs.length,
      logs: logs.split(/\r?\n/).filter((line) => line.includes('ignored HTTP body')) } : {}) };
  } finally { child.kill(); }
}

const observed = await Promise.all(['node', 'rust'].map(run));
for (let i = 0; i < observed[0].cases.length; i++) {
  const node = observed[0].cases[i], rust = observed[1].cases[i];
  assert.deepEqual(rust.statuses, node.statuses, `early-body statuses case ${i}`);
  assert.equal(node.localTimeout, false, `Node close case ${i}`);
  assert.equal(rust.localTimeout, false, `Rust close case ${i}`);
  assert(Math.abs(rust.closeMs - node.closeMs) < 300,
    `early-body close case ${i}: ${node.closeMs}/${rust.closeMs} ms`);
}
console.log(JSON.stringify(observed));
