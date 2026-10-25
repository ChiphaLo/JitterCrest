// Real TCP/HTTP1 Node/Rust A/B: an unread fixed download must release its lease
// and refund the part of its byte-budget reservation never offered to the socket.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createConnection, createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const MiB = 1024 * 1024;

async function freePort() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function unreadDownload(port, size) {
  return new Promise((resolve, reject) => {
    const socket = createConnection({ host: '127.0.0.1', port });
    const timeout = setTimeout(() => socket.destroy(new Error('socket stalled')), 5000);
    socket.on('error', reject);
    socket.on('close', () => clearTimeout(timeout));
    socket.on('connect', () => {
      socket.write(`GET /download?size=${size} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n`);
    });
    socket.once('data', async () => {
      // Confirm the response started, then stop consuming to create backpressure.
      socket.pause();
      await sleep(200);
      socket.destroy();
      resolve();
    });
  });
}

async function run(kind, abort) {
  const port = await freePort();
  const binary = process.platform === 'win32' ? 'speedtest-rust.exe' : 'speedtest-rust';
  const child = spawn(kind === 'node' ? process.execPath
    : (process.env.RUST_BINARY || path.join(root, 'target/release', binary)),
    kind === 'node' ? ['server.js'] : [], { cwd: root,
      env: { ...process.env, SPEEDTEST_HOST: '127.0.0.1', PORT: String(port),
        MAX_ACTIVE_PER_IP: '8', DOWNLOAD_BYTE_BUDGET_PER_IP: String(32 * MiB),
        RATE_MAX: '1000', SPEED_RATE_MAX: '2000' }, stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr += chunk; });
  const base = `http://127.0.0.1:${port}`;
  try {
    let ready = false;
    for (let n = 0; n < 600; n++) {
      if (child.exitCode !== null) throw new Error(`${kind} exited: ${stderr.slice(-1000)}`);
      try { if ((await fetch(`${base}/health`)).ok) { ready = true; break; } } catch { /* startup */ }
      await sleep(50);
    }
    assert(ready, `${kind} startup timeout`);
    if (abort) {
      await unreadDownload(port, 32 * MiB);
      await sleep(400); // allow the peer-close callback/body drop to refund
    }
    const statuses = [];
    const sizes = abort ? [16 * MiB, 8 * MiB, 4 * MiB, 2 * MiB, MiB, MiB, MiB]
      : [16 * MiB, 16 * MiB, 16 * MiB];
    for (const size of sizes) {
      const response = await fetch(`${base}/download?size=${size}`);
      const body = await response.arrayBuffer();
      statuses.push({ status: response.status, bytes: body.byteLength });
    }
    return statuses;
  } finally {
    child.kill();
  }
}

for (const abort of [false, true]) {
  const node = await run('node', abort);
  const rust = await run('rust', abort);
  if (!abort) {
    assert.deepEqual(rust, node, 'fully consumed Node/Rust download budget mismatch');
    assert.deepEqual(node.map((item) => item.status), [200, 200, 429]);
  } else {
    // The amount handed to the socket before backpressure is transport- and
    // OS-dependent; only assert invariants, not exact post-abort status arrays.
    for (const [kind, results] of [['node', node], ['rust', rust]]) {
      assert.equal(results[0].status, 200, `${kind} did not refund the unsent reservation`);
      assert(results.some((item) => item.status === 429), `${kind} never enforced the byte budget`);
      assert(results.filter((item) => item.status === 200)
        .reduce((sum, item) => sum + item.bytes, 0) <= 32 * MiB,
      `${kind} admitted more full responses than its byte budget`);
    }
  }
  console.log(`Node/Rust ${abort ? 'aborted' : 'full'} download budget A/B:`,
    JSON.stringify({ node, rust }));
}
