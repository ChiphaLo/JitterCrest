// Real-socket Node/Rust A/B for stalled streaming downloads and disconnect cleanup.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import http from 'node:http';
import { createConnection, createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function freePort() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

function stalledStream(port, fixed) {
  return new Promise((resolve, reject) => {
    const socket = createConnection({ host: '127.0.0.1', port });
    const timeout = setTimeout(() => socket.destroy(new Error('download did not start')), 5000);
    socket.on('error', reject);
    socket.on('close', () => clearTimeout(timeout));
    socket.on('connect', () => socket.write(`GET ${fixed ? '/download?size=100663296'
      : '/download-stream?maxMs=30000'} HTTP/1.1\r\nHost: localhost\r\n\r\n`));
    socket.once('data', (part) => {
      assert.match(part.toString('latin1', 0, 64), /^HTTP\/1\.1 200/);
      socket.pause();
      clearTimeout(timeout);
      resolve(socket);
    });
  });
}

async function reusableAfterDeadline(port) {
  const agent = new http.Agent({ keepAlive: true, maxSockets: 1 });
  const get = (uri) => new Promise((resolve, reject) => {
    const request = http.get({ host: '127.0.0.1', port, path: uri, agent }, (response) => {
      let bytes = 0;
      response.on('data', (part) => { bytes += part.length; });
      response.on('end', () => resolve({ status: response.statusCode, bytes,
        reused: request.reusedSocket }));
      response.on('error', reject);
    });
    request.on('error', reject);
  });
  try {
    const first = await get('/download-100k?size=1024');
    await sleep(1300);
    const second = await get('/health');
    return { first, second };
  } finally {
    agent.destroy();
  }
}

async function run(kind, fixed) {
  const port = await freePort();
  const binary = process.platform === 'win32' ? 'speedtest-rust.exe' : 'speedtest-rust';
  const child = spawn(kind === 'node' ? process.execPath
    : (process.env.RUST_BINARY || path.join(root, 'target/release', binary)),
  kind === 'node' ? ['server.js'] : [], {
    cwd: root, env: { ...process.env, SPEEDTEST_HOST: '127.0.0.1', PORT: String(port),
      MAX_ACTIVE_PER_IP: '8', MAX_FIXED_DOWNLOAD_MS: '1000',
      RATE_MAX: '1000', SPEED_RATE_MAX: '2000' },
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  let stderr = '';
  child.stderr.on('data', (part) => { stderr += part; });
  const sockets = [];
  const base = `http://127.0.0.1:${port}`;
  try {
    let ready = false;
    for (let i = 0; i < 600; i++) {
      if (child.exitCode !== null) throw new Error(`${kind} exited: ${stderr.slice(-1000)}`);
      try { if ((await fetch(`${base}/health`)).ok) { ready = true; break; } } catch { /* startup */ }
      await sleep(50);
    }
    assert(ready, `${kind} startup timeout: ${stderr.slice(-1000)}`);
    sockets.push(...await Promise.all(Array.from({ length: 8 }, () => stalledStream(port, fixed))));
    await sleep(150);
    const saturated = await fetch(`${base}/download-100k?size=1024`);
    await saturated.arrayBuffer();
    let afterDeadline = null;
    if (fixed) {
      await sleep(1300);
      const response = await fetch(`${base}/download-100k?size=1024`);
      afterDeadline = response.status;
      await response.arrayBuffer();
    }
    for (const socket of sockets) socket.destroy();
    await sleep(300);
    const followup = await fetch(`${base}/download-100k?size=1024`);
    const bytes = (await followup.arrayBuffer()).byteLength;
    const keepalive = fixed ? await reusableAfterDeadline(port) : null;
    return { saturated: saturated.status, afterDeadline, followup: followup.status, bytes,
      keepalive };
  } finally {
    for (const socket of sockets) socket.destroy();
    child.kill();
  }
}

for (const fixed of process.env.SLOW_FIXED_ONLY === '1' ? [true] : [false, true]) {
  const node = await run('node', fixed);
  const rust = await run('rust', fixed);
  assert.deepEqual(node, { saturated: 429, afterDeadline: fixed ? 200 : null,
    followup: 200, bytes: 1024, keepalive: fixed ? {
      first: { status: 200, bytes: 1024, reused: false },
      second: { status: 200, bytes: 35, reused: true },
    } : null });
  assert.deepEqual(rust, node);
  console.log(`Node/Rust stalled ${fixed ? 'fixed' : 'stream'} download lease A/B:`, JSON.stringify(rust));
}
