// Local, deterministic HTTP comparison. Run with `node test/http-contract.mjs` after
// building the Rust binary. Neither process contacts production nodes.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer } from 'node:net';
import { get as httpGet } from 'node:http';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function requestStatus(url, headers) {
  return new Promise((resolve, reject) => {
    const request = httpGet(url, { headers }, (response) => {
      response.on('error', reject);
      response.on('end', () => resolve(response.statusCode));
      response.resume();
    });
    request.on('error', reject);
  });
}

async function port() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const selected = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return selected;
}

async function run(command, args) {
  const selected = await port();
  const base = `http://127.0.0.1:${selected}`;
  const child = spawn(command, args, {
    cwd: root,
    env: { ...process.env, SPEEDTEST_HOST: '127.0.0.1', PORT: String(selected),
      SPEEDTEST_NODE_NAME: 'contract-test', ALLOWED_ORIGINS: 'https://allowed.example',
      SPEEDTEST_NODES: '',
      MAX_RTC_OFFER_BODY_BYTES: '16384' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
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
    const cases = [
      ['GET', '/health'], ['GET', '/latency'], ['HEAD', '/latency'], ['GET', '/favicon.svg'],
      ['HEAD', '/speed/favicon.svg'], ['HEAD', '/'], ['GET', '/'], ['GET', '/client-info'],
      ['GET', '/download?size=10240'], ['GET', '/download-stream?maxMs=1000'], ['GET', '/download-100k?size=102400'],
      ['GET', '/download-100k?size=1048576'], ['GET', '/download?size=bad'],
      ['GET', '/no-such-route'], ['OPTIONS', '/upload'],
      ['POST', '/upload', Buffer.alloc(10240, 7)],
      ['POST', '/upload-stream?warmupMs=0', Buffer.alloc(102400, 7)],
      ['POST', '/webrtc/offer', '{}'],
      ['POST', '/webrtc/offer', Buffer.alloc(20 * 1024, 120)],
    ];
    const results = [];
    for (const [method, uri, body] of cases) {
      const response = await fetch(base + uri, {
        method, body, headers: { origin: 'https://allowed.example',
          ...(uri === '/webrtc/offer' ? { 'content-type': 'application/json' } : {}) },
      });
      let bytes;
      if (uri.startsWith('/download-stream')) {
        const reader = response.body.getReader();
        bytes = Buffer.from((await reader.read()).value);
        await reader.cancel();
      } else {
        bytes = Buffer.from(await response.arrayBuffer());
      }
      if (uri === '/' && method === 'GET') {
        const config = bytes.toString().match(/<script[^>]*id="nodeConfig"[^>]*>([\s\S]*?)<\/script>/);
        assert.ok(config, 'embedded node configuration is present');
        assert.deepEqual(JSON.parse(config[1]), [
          { id: 'local', label: 'Local server', base: '', rtcTargetMs: 25 },
        ]);
        assert.match(response.headers.get('content-security-policy'), /https:\/\/speed\.example\.com/);
      }
      results.push({ method, uri, status: response.status, length: bytes.length,
        contentType: response.headers.get('content-type'),
        cacheControl: response.headers.get('cache-control'),
        contentLength: response.headers.get('content-length'),
        encoding: response.headers.get('content-encoding'),
        cors: response.headers.get('access-control-allow-origin'),
        sha256: createHash('sha256').update(bytes).digest('hex'),
        normalizedPageSha256: uri === '/' && method === 'GET'
          ? createHash('sha256').update(bytes.toString().replaceAll(/nonce="[^"]+"/g, 'nonce="__"')).digest('hex')
          : undefined,
        normalizedPage: uri === '/' && method === 'GET' ? bytes.toString().replaceAll(/nonce="[^"]+"/g, 'nonce="__"') : undefined,
        body: bytes.length < 1024 && !uri.startsWith('/upload') ? bytes.toString() : undefined });
    }
    // Generic probe-only selectors retain the legacy restricted HTTP behavior.
    for (const selector of [{ 'x-speedtest-node': 'probe-only' }, { host: '192.0.2.10' }]) {
      // Use node:http because fetch can discard an explicit Host header.
      assert.equal(await requestStatus(base + '/download?size=10240', {
        origin: 'https://allowed.example', ...selector,
      }), 404);
      assert.equal(await requestStatus(base + '/latency', selector), 204);
    }
    return results;
  } finally {
    child.kill();
  }
}

const node = await run(process.execPath, ['server.js']);
if (process.argv.includes('--baseline')) {
  console.log(JSON.stringify(node, null, 2));
} else {
  const bin = process.platform === 'win32' ? 'speedtest-rust.exe' : 'speedtest-rust';
  const rust = await run(process.env.RUST_BINARY || path.join(root, 'target', 'release', bin), []);
  const dynamic = new Set(['/', '/upload', '/upload-stream?warmupMs=0', '/download-stream?maxMs=1000']);
  const differences = [];
  for (let i = 0; i < node.length; i++) {
    const a = node[i], b = rust[i];
    for (const key of ['status', 'length', 'contentType', 'cacheControl', 'contentLength', 'encoding', 'cors']) {
      if (dynamic.has(a.uri) && ['length', 'contentLength'].includes(key)) continue;
      // Axum/Hyper emits Content-Length: 0 for HEAD 204; Node omits it.
      if (a.method === 'HEAD' && a.uri === '/latency' && key === 'contentLength') continue;
      if (b[key] !== a[key]) differences.push(`${a.method} ${a.uri}: ${key} ${JSON.stringify(a[key])} -> ${JSON.stringify(b[key])}`);
    }
    if (!dynamic.has(a.uri) && a.uri !== '/download?size=bad' && !a.uri.includes('download')) {
      if (b.sha256 !== a.sha256) differences.push(`${a.method} ${a.uri}: body differs`);
    }
    if (a.normalizedPageSha256 && a.normalizedPageSha256 !== b.normalizedPageSha256) {
      const at = [...a.normalizedPage].findIndex((c, j) => c !== b.normalizedPage[j]);
      differences.push(`GET /: normalized frontend differs at ${at}: ${JSON.stringify(a.normalizedPage.slice(at, at + 100))} -> ${JSON.stringify(b.normalizedPage.slice(at, at + 100))}`);
    }
  }
  console.log(`Compared ${node.length} HTTP cases against Node`);
  console.log(differences.join('\n') || 'No differences');
  assert.deepEqual(differences, []);
}
