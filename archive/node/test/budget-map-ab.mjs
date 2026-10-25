// Offline A/B for upload byte-budget map eviction at the minimum capacity.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
async function freePort() {
  const listener = createServer();
  await new Promise((resolve) => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise((resolve) => listener.close(resolve));
  return port;
}
async function run(command, args) {
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const child = spawn(command, args, { cwd: root,
    env: { ...process.env, SPEEDTEST_HOST: '127.0.0.1', PORT: String(port),
      TRUST_PROXY_HEADERS: '1', TRUSTED_PROXY_IPS: '127.0.0.1',
      RATE_MAP_MAX: '100', UPLOAD_BYTE_BUDGET_PER_IP: String(1024 * 1024) },
    stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
  try {
    let ready = false;
    for (let i = 0; i < 600; i++) {
      if (child.exitCode !== null) throw new Error(`server exited: ${stderr}`);
      try { if ((await fetch(`${base}/health`)).ok) { ready = true; break; } } catch { /* startup */ }
      await sleep(50);
    }
    if (!ready) throw new Error(`server did not start: ${stderr}`);
    const send = async (ip, body) => {
      const response = await fetch(`${base}/upload`, { method: 'POST', body,
        headers: { 'x-real-ip': ip } });
      await response.arrayBuffer();
      return response.status;
    };
    const results = [await send('8.8.8.8', Buffer.alloc(1024 * 1024))];
    results.push(await send('8.8.8.8', Buffer.alloc(1)));
    for (let i = 1; i <= 99; i++) results.push(await send(`11.0.0.${i}`, Buffer.alloc(1)));
    results.push(await send('8.8.8.8', Buffer.alloc(1))); // still at budget
    results.push(await send('12.0.0.1', Buffer.alloc(1))); // evicts oldest
    results.push(await send('8.8.8.8', Buffer.alloc(1))); // new budget entry
    return results;
  } finally { child.kill(); }
}

const node = await run(process.execPath, ['server.js']);
const bin = process.platform === 'win32' ? 'speedtest-rust.exe' : 'speedtest-rust';
const rust = await run(path.join(root, 'target', 'release', bin), []);
assert.deepEqual(node, rust);
assert.deepEqual(node.slice(-3), [429, 200, 200]);
console.log(`${node.length} byte-budget requests: exact Node/Rust status match`);
