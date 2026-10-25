// SIGTERM behavior in a private Linux container; never point at a production service.
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';
import { createConnection, createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
async function port() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const selected = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return selected;
}
async function run(kind, scenario) {
  const listenPort = await port();
  const binary = kind === 'node' ? process.execPath : path.resolve(root,
    process.env.RUST_BINARY || 'target/release/speedtest-rust');
  const child = spawn(binary, kind === 'node' ? ['server.js'] : [], {
    cwd: root, stdio: ['ignore', 'ignore', 'pipe'],
    env: { ...process.env, SPEEDTEST_HOST: '127.0.0.1', PORT: String(listenPort),
      HTTP_HEADERS_TIMEOUT_MS: '1000', HTTP_KEEP_ALIVE_TIMEOUT_MS: '1000' },
  });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr = (stderr + chunk).slice(-1500); });
  const exited = new Promise((resolve) => child.once('exit', (code, signal) => resolve({ code, signal })));
  let socket;
  let response = '';
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (child.exitCode !== null) throw new Error(`${kind} exited: ${stderr}`);
      try { if ((await fetch(`http://127.0.0.1:${listenPort}/health`)).ok) { ready = true; break; } }
      catch { /* startup */ }
      await sleep(50);
    }
    if (!ready) throw new Error(`${kind} startup timeout: ${stderr}`);
    const request = scenario === 'idle' ? 'GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n'
      : scenario === 'partialHeaders' ? 'GET /health HTTP/1.1\r\nHost: '
        : scenario === 'partialBody' ? 'POST /webrtc/offer HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\nx'
          : `POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1024\r\n\r\n${'x'.repeat(512)}`;
    socket = createConnection(listenPort, '127.0.0.1');
    socket.on('error', () => {});
    socket.on('data', (chunk) => { response += chunk.toString('latin1'); });
    await new Promise((resolve) => socket.once('connect', resolve));
    socket.write(request);
    await sleep(150);
    const started = Date.now();
    child.kill('SIGTERM');
    if (scenario === 'finishingBody' && kind === 'rust') {
      await sleep(150);
      socket.write('x'.repeat(512));
    }
    const result = await Promise.race([exited, sleep(8000).then(() => null)]);
    if (!result) { child.kill('SIGKILL'); await exited; }
    return { kind, scenario, exit: result, elapsedMs: Date.now() - started,
      forcedKill: result === null, statuses: [...response.matchAll(/HTTP\/1\.1 (\d+)/g)]
        .map((match) => Number(match[1])), stderr: stderr.slice(-300) };
  } finally {
    socket?.destroy();
    if (child.exitCode === null) child.kill('SIGKILL');
  }
}
for (const scenario of ['idle', 'partialHeaders', 'partialBody', 'finishingBody']) {
  const results = await Promise.all([run('node', scenario), run('rust', scenario)]);
  for (const result of results) {
    assert.equal(result.forcedKill, false, `${result.kind} hung on ${scenario}`);
    assert(result.elapsedMs < 7500, `${result.kind} slow shutdown on ${scenario}`);
  }
  if (process.platform === 'win32') {
    // Windows has no catchable SIGTERM: child.kill forces termination and
    // reports a signal, so the exit-code proof only applies to POSIX runs.
    assert.equal(results[1].exit.signal, 'SIGTERM', `Rust forced exit on ${scenario}`);
  } else {
    assert.equal(results[1].exit.code, 0, `Rust exit on ${scenario}`);
  }
  // Completing an in-flight request needs a deliverable SIGTERM; Windows
  // force-terminates the child instead, so this is enforced on POSIX only.
  if (scenario === 'finishingBody' && process.platform !== 'win32') {
    assert.deepEqual(results[1].statuses, [200]);
  }
  console.log(JSON.stringify(results));
}
