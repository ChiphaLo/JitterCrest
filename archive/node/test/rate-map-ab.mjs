// Offline A/B for the insertion-ordered general rate map at minimum capacity.
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
      RATE_MAP_MAX: '100', RATE_MAX: '10' }, stdio: ['ignore', 'ignore', 'pipe'] });
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
    const request = async (ip) => {
      const response = await fetch(`${base}/webrtc/offer`, {
        method: 'POST', body: '{}', headers: { 'content-type': 'application/json',
          'x-real-ip': ip },
      });
      return response.status;
    };
    const first = '8.8.8.8';
    const second = '8.8.4.4';
    const results = [];
    for (let i = 0; i < 10; i++) results.push(await request(first));
    for (let i = 0; i < 10; i++) results.push(await request(second));
    results.push(await request(first), await request(second));
    // 98 new keys bring the map to 100. The two old keys stay first in order.
    for (let i = 1; i <= 98; i++) results.push(await request(`11.0.0.${i}`));
    results.push(await request(first)); // still limited
    results.push(await request('12.0.0.1')); // evicts first
    results.push(await request(first)); // admitted as new key; evicts second
    results.push(await request(second)); // admitted as new key
    return results;
  } finally { child.kill(); }
}

const node = await run(process.execPath, ['server.js']);
const bin = process.platform === 'win32' ? 'speedtest-rust.exe' : 'speedtest-rust';
const rust = await run(path.join(root, 'target', 'release', bin), []);
assert.deepEqual(node, rust);
assert.deepEqual(node.slice(20, 22), [429, 429]);
assert.deepEqual(node.slice(-4), [429, 400, 400, 400]);
console.log(`${node.length} rate-map requests: exact Node/Rust status match`);
