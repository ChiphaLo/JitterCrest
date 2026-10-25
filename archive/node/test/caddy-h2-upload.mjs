// Isolated local Caddy HTTP/2 -> Rust HTTP/1.1 upload path. No production
// configuration, system trust store, or network listener beyond loopback.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os, { tmpdir, homedir } from 'node:os';
import path from 'node:path';
import http2 from 'node:http2';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const rust = path.join(root, 'target', 'release', process.platform === 'win32'
  ? 'speedtest-rust.exe' : 'speedtest-rust');
const nodeBaseline = process.argv.includes('--node-backend');
const localIceAddress = Object.values(os.networkInterfaces()).flat()
  .find((entry) => entry?.family === 'IPv4' && !entry.internal)?.address;
if (nodeBaseline && !localIceAddress) throw new Error('Node reference needs a local non-loopback ICE address');
const caddy = process.env.CADDY_BIN || path.join(homedir(), 'go', 'bin',
  process.platform === 'win32' ? 'caddy.exe' : 'caddy');

async function port() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const selected = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return selected;
}

function upload(origin, size, uri) {
  return new Promise((resolve, reject) => {
    const client = http2.connect(origin, { rejectUnauthorized: false });
    client.once('error', reject);
    client.once('connect', () => {
      if (client.alpnProtocol !== 'h2') {
        client.close();
        reject(new Error(`expected HTTP/2 ALPN, got ${client.alpnProtocol}`));
        return;
      }
      const request = client.request({ ':method': 'POST', ':path': uri,
        'content-type': 'application/octet-stream', origin });
      let status;
      const chunks = [];
      request.on('response', (headers) => { status = headers[':status']; });
      request.on('data', (chunk) => { chunks.push(chunk); });
      request.once('error', reject);
      request.once('end', () => {
        client.close();
        try {
          const body = JSON.parse(Buffer.concat(chunks).toString());
          assert.equal(status, 200);
          assert.equal(body.bytes, size);
          resolve({ status, bytes: body.bytes });
        } catch (error) { reject(error); }
      });
      const data = Buffer.alloc(32 * 1024, 7);
      let sent = 0;
      const pump = () => {
        while (sent < size) {
          const n = Math.min(data.length, size - sent);
          if (!request.write(data.subarray(0, n))) {
            sent += n;
            request.once('drain', pump);
            return;
          }
          sent += n;
        }
        request.end();
      };
      pump();
    });
  });
}

const backendPort = await port();
const proxyPort = await port();
const origin = `https://127.0.0.1:${proxyPort}`;
const directory = await mkdtemp(path.join(tmpdir(), 'speedtest-caddy-h2-'));
const config = path.join(directory, 'Caddyfile');
await writeFile(config, `{
 admin off
 auto_https disable_redirects
}
https://127.0.0.1:${proxyPort} {
 bind 127.0.0.1
 tls internal
 reverse_proxy 127.0.0.1:${backendPort}
}
`);
const backend = spawn(nodeBaseline ? process.execPath : rust,
  nodeBaseline ? ['test/node-loopback-reference.cjs'] : [], { cwd: root, env: { ...process.env,
  SPEEDTEST_HOST: '127.0.0.1', PORT: String(backendPort), ALLOWED_ORIGINS: origin,
  SPEEDTEST_RUST_RTC_EXPERIMENTAL: '1', SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST: '1',
  SPEEDTEST_RTC_LOCAL_TEST_IP: localIceAddress || '',
  DOWNLOAD_BYTE_BUDGET_PER_IP: String(32 * 1024 ** 3),
  UPLOAD_BYTE_BUDGET_PER_IP: String(32 * 1024 ** 3) },
stdio: ['ignore', 'ignore', 'pipe'] });
const proxy = spawn(caddy, ['run', '--config', config, '--adapter', 'caddyfile'], {
  cwd: root, env: { ...process.env, APPDATA: directory, XDG_DATA_HOME: directory,
    XDG_CONFIG_HOME: directory }, stdio: ['ignore', 'ignore', 'pipe'] });
let backendLog = '', proxyLog = '';
backend.stderr.on('data', (chunk) => { backendLog += chunk.toString(); });
proxy.stderr.on('data', (chunk) => { proxyLog += chunk.toString(); });
try {
  let ready = false;
  for (let i = 0; i < 300; i++) {
    if (backend.exitCode !== null || proxy.exitCode !== null) {
      throw new Error(`server exited: ${backendLog}\n${proxyLog}`);
    }
    try {
      const result = await upload(origin, 10 * 1024, '/upload');
      if (result.status === 200) { ready = true; break; }
    } catch { /* startup, TLS certificate generation */ }
    await sleep(100);
  }
  if (!ready) throw new Error(`Caddy/Rust not ready: ${backendLog}\n${proxyLog}`);
  for (const size of [100 * 1024, 1024 * 1024]) {
    assert.deepEqual(await upload(origin, size, '/upload'), { status: 200, bytes: size });
  }
  assert.deepEqual(await upload(origin, 1024 * 1024, '/upload-stream?warmupMs=0'),
    { status: 200, bytes: 1024 * 1024 });
  console.log(`Caddy HTTP/2 -> ${nodeBaseline ? 'Node reference' : 'Rust'}: 10 KiB, 100 KiB, 1 MiB uploads and 1 MiB streaming upload passed`);
  if (process.argv.includes('--browser')) {
    const script = path.join(root, 'test', 'browser-h2-upload.mjs');
    const browser = spawn(process.execPath,
      [script, origin, '--ui', ...(process.argv.includes('--stability') ? ['--stability'] : [])], {
      cwd: root, stdio: 'inherit', env: process.env,
    });
    const code = await new Promise((resolve, reject) => {
      browser.once('error', reject);
      browser.once('exit', resolve);
    });
    if (code !== 0) throw new Error(`browser UI test exited ${code}`);
  }
  if (process.argv.includes('--serve')) {
    console.log(`Local test page: ${origin}/?singleProtocol=h2`);
    await sleep(120_000); // automatic cleanup even without interactive stdin
  }
} finally {
  proxy.kill();
  backend.kill();
  await sleep(250);
  await rm(directory, { recursive: true, force: true });
}
