// Compare the Node HTTP/1.1 per-socket request limit to the Rust candidate.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import http from 'node:http';
import { createConnection, createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const runs = [];

async function availablePort() {
  const socket = createServer();
  await new Promise((resolve) => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  return port;
}

async function run(kind, limit, count) {
  const port = await availablePort();
  const binary = kind === 'node' ? process.execPath : path.resolve(root,
    process.env.RUST_BINARY || 'target/release/speedtest-rust.exe');
  const child = spawn(binary, kind === 'node' ? ['server.js'] : [], {
    cwd: root, stdio: ['ignore', 'ignore', 'pipe'],
    env: { ...process.env, PORT: String(port), SPEEDTEST_HOST: '127.0.0.1',
      HTTP_MAX_REQUESTS_PER_SOCKET: String(limit) },
  });
  const agent = new http.Agent({ keepAlive: true, maxSockets: 1, maxFreeSockets: 1 });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr = (stderr + chunk.toString()).slice(-1024); });
  function request() {
    return new Promise((resolve, reject) => {
      const req = http.get({ host: '127.0.0.1', port, path: '/health', agent }, (response) => {
        const localPort = response.socket.localPort;
        const parts = [];
        response.on('data', (part) => parts.push(part));
        response.on('end', () => resolve({ status: response.statusCode,
          body: Buffer.concat(parts).toString(),
          connection: response.headers.connection, keepAlive: response.headers['keep-alive'],
          localPort, reused: req.reusedSocket }));
        response.on('error', reject);
      });
      req.on('error', reject);
    });
  }
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (child.exitCode !== null) throw new Error(`${kind} exited: ${stderr}`);
      try {
        const response = await fetch(`http://127.0.0.1:${port}/health`);
        if (response.ok) { ready = true; break; }
      } catch { /* waiting for startup */ }
      await sleep(50);
    }
    assert(ready, `${kind} startup timeout: ${stderr}`);
    const observed = [];
    for (let i = 0; i < count; i++) observed.push(await request());
    assert(observed.every((r) => r.status === 200 && JSON.parse(r.body).ok));
    return observed.map(({ status, connection, keepAlive, localPort, reused }) =>
      ({ status, connection, keepAlive, localPort, reused }));
  } finally {
    agent.destroy();
    child.kill();
  }
}

for (const [limit, count] of [[1, 4], [3, 8], [100, 102]]) {
  const node = await run('node', limit, count);
  const rust = await run('rust', limit, count);
  function semantics(requests) {
    return requests.map((response, index) => ({
      status: response.status,
      close: response.connection === 'close',
      reused: response.reused,
      samePortAsPrevious: index > 0 && response.localPort === requests[index - 1].localPort,
      keepAlive: response.keepAlive ?? null,
    }));
  }
  const baseline = semantics(node);
  assert.deepEqual(semantics(rust), baseline, `connection lifetime differs at limit ${limit}`);
  for (let i = 0; i < count; i++) {
    assert.equal(baseline[i].close, (i + 1) % limit === 0);
    assert.equal(baseline[i].reused, i % limit !== 0);
  }
  runs.push({ limit, count, connectionCloses: baseline.filter((r) => r.close).length,
    sameBehavior: true });
}
console.log(JSON.stringify(runs));

async function pipeline(kind) {
  const port = await availablePort();
  const binary = kind === 'node' ? process.execPath : path.resolve(root,
    process.env.RUST_BINARY || 'target/release/speedtest-rust.exe');
  const child = spawn(binary, kind === 'node' ? ['server.js'] : [], {
    cwd: root, stdio: ['ignore', 'ignore', 'pipe'],
    env: { ...process.env, PORT: String(port), SPEEDTEST_HOST: '127.0.0.1',
      HTTP_MAX_REQUESTS_PER_SOCKET: '3' },
  });
  try {
    for (let i = 0; i < 100; i++) {
      try {
        if ((await fetch(`http://127.0.0.1:${port}/health`)).ok) break;
      } catch { /* startup */ }
      await sleep(50);
    }
    const socket = createConnection(port, '127.0.0.1');
    socket.setTimeout(1500, () => socket.end());
    const result = await new Promise((resolve, reject) => {
      let output = '';
      socket.on('connect', () => socket.write('GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n'.repeat(5)));
      socket.on('data', (chunk) => { output += chunk.toString(); });
      socket.on('error', reject);
      socket.on('close', () => resolve(output));
    });
    return [...result.matchAll(/HTTP\/1\.1 (\d+) ([^\r]+)/g)].map((match) => Number(match[1]));
  } finally {
    child.kill();
  }
}

const nodePipeline = await pipeline('node');
const rustPipeline = await pipeline('rust');
assert.deepEqual(nodePipeline, [200, 200, 200, 503, 503]);
assert.deepEqual(rustPipeline, nodePipeline);
console.log(JSON.stringify({ pipeline: { node: nodePipeline, rust: rustPipeline,
  sameBehavior: true } }));
