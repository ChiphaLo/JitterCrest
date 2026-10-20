// Isolated Linux Chromium native-DataChannel probe. The parent shaper gates
// downStart only after ICE/DTLS/SCTP and a 128 B probe ACK have succeeded.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { access, mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir, networkInterfaces } from 'node:os';
import { join, resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const implementation = process.env.RTC_BROWSER_SERVER;
assert(['node', 'rust'].includes(implementation));
const root = resolve(import.meta.dirname, '..');
const localIp = Object.entries(networkInterfaces())
  .filter(([name]) => !/^(tailscale|docker|br-|tun|lo)/i.test(name))
  .flatMap(([, entries]) => entries || [])
  .find((entry) => entry.family === 'IPv4' && !entry.internal)?.address;
assert(localIp, 'isolated bridge IPv4 is required');
const reserve = createServer();
await new Promise((resolve) => reserve.listen(0, '127.0.0.1', resolve));
const port = reserve.address().port;
await new Promise((resolve) => reserve.close(resolve));
const base = `http://127.0.0.1:${port}`;
const executable = implementation === 'node' ? process.execPath
  : process.env.RTC_TEST_RUST_BINARY;
assert(executable);
const server = spawn(executable, implementation === 'node' ? ['test/node-loopback-reference.cjs'] : [], {
  cwd: root, env: { ...process.env, SPEEDTEST_HOST: '127.0.0.1', PORT: String(port),
    ALLOWED_ORIGINS: base, RUST_LOG: process.env.RTC_BROWSER_DIAGNOSTICS === '1'
      ? 'info,speedtest_rust::rtc=debug' : (process.env.RUST_LOG || 'info'),
    SPEEDTEST_RUST_RTC_EXPERIMENTAL: '1',
    SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST: '1', SPEEDTEST_RTC_LOCAL_TEST_IP: localIp },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let serverLog = '';
server.stdout.on('data', (data) => { serverLog += data; });
server.stderr.on('data', (data) => { serverLog += data; });
let browser, ws;
const profile = await mkdtemp(join(tmpdir(), 'rtc-chromium-'));
try {
  let ready = false;
  for (let i = 0; i < 300; i++) {
    if (server.exitCode !== null) throw new Error(`backend exited: ${serverLog.slice(-2000)}`);
    try { if ((await fetch(`${base}/health`)).ok) { ready = true; break; } } catch { /* startup */ }
    await sleep(50);
  }
  assert(ready, `backend health timeout: ${serverLog.slice(-2000)}`);
  const browserRoot = '/ms-playwright';
  const versions = (await readdir(browserRoot)).filter((name) => name.startsWith('chromium-'));
  assert.equal(versions.length, 1, 'expected exactly one Chromium distribution');
  const browserBinary = join(browserRoot, versions[0], 'chrome-linux', 'chrome');
  await access(browserBinary);
  browser = spawn(browserBinary, ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
    '--disable-gpu', '--no-first-run', '--disable-features=WebRtcHideLocalIpsWithMdns',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
  { env: { ...process.env, HOME: '/tmp', XDG_CACHE_HOME: '/tmp' }, stdio: 'ignore' });
  let debugPort;
  for (let i = 0; i < 200; i++) {
    if (browser.exitCode !== null) throw new Error(`Chromium exited: ${browser.exitCode}`);
    try {
      debugPort = Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]);
      if (debugPort) break;
    } catch { /* browser startup */ }
    await sleep(100);
  }
  assert(debugPort, 'Chromium DevTools did not start');
  const target = await fetch(`http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(`${base}/health`)}`,
    { method: 'PUT' }).then((response) => response.json());
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', reject, { once: true });
  });
  let nextId = 0;
  const pending = new Map();
  ws.addEventListener('message', (event) => {
    const response = JSON.parse(event.data);
    const callback = pending.get(response.id);
    if (callback) { pending.delete(response.id); callback(response); }
  });
  const command = (method, params) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, (response) => response.error ? reject(new Error(response.error.message))
      : resolve(response.result));
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (fn, arg) => {
    const response = await command('Runtime.evaluate', {
      expression: `(${fn.toString()})(${JSON.stringify(arg)})`, awaitPromise: true, returnByValue: true,
    });
    if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails).slice(0, 2400));
    return response.result.value;
  };
  let pageReady = false;
  for (let i = 0; i < 100; i++) {
    const response = await command('Runtime.evaluate', { expression: 'location.origin', returnByValue: true });
    if (response.result.value === base) { pageReady = true; break; }
    await sleep(100);
  }
  assert(pageReady, 'browser did not navigate to local origin');

  const setup = await evaluate(async () => {
    const pc = new RTCPeerConnection({ iceServers: [] });
    const control = pc.createDataChannel('control');
    const test = pc.createDataChannel('test', { ordered: false, maxRetransmits: 0 });
    test.binaryType = 'arraybuffer';
    const attemptId = 'native-browser-test';
    const post = async (payload) => fetch('/webrtc/offer', { method: 'POST',
      headers: { 'content-type': 'application/json', 'x-speedtest-attempt-id': attemptId },
      body: JSON.stringify({ attemptId, ...payload }) });
    const opened = new Promise((resolve) => test.addEventListener('open', resolve, { once: true }));
    await pc.setLocalDescription(await pc.createOffer());
    if (pc.iceGatheringState !== 'complete') await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('ICE gathering timeout')), 7000);
      pc.addEventListener('icegatheringstatechange', () => {
        if (pc.iceGatheringState === 'complete') { clearTimeout(timeout); resolve(); }
      });
    });
    const answer = await post({ type: 'offer', sdp: pc.localDescription.sdp });
    const body = await answer.json();
    if (answer.status !== 200) throw new Error(`offer ${answer.status}: ${JSON.stringify(body)}`);
    await pc.setRemoteDescription(body);
    await Promise.race([opened, new Promise((_, reject) =>
      setTimeout(() => reject(new Error(`DataChannel open timeout: ${pc.connectionState}`)), 12000))]);
    const probeAck = new Promise((resolve) => test.addEventListener('message', resolve, { once: true }));
    const probe = new Uint8Array(128);
    const view = new DataView(probe.buffer);
    probe[0] = 1;
    view.setUint32(4, 123, true); view.setUint32(8, 456, true);
    view.setUint32(12, 7, true); view.setFloat64(16, 500.25, true);
    test.send(probe);
    const ack = await Promise.race([probeAck, new Promise((_, reject) =>
      setTimeout(() => reject(new Error('probe ACK timeout')), 3000))]);
    const reply = new DataView(ack.data);
    if (reply.byteLength !== 128 || reply.getUint8(0) !== 2 || reply.getUint32(12, true) !== 7) {
      throw new Error('invalid probe ACK');
    }
    window.__nativeRtc = { pc, control, test, post };
    return { ordered: test.ordered, maxRetransmits: test.maxRetransmits,
      controlOrdered: control.ordered, controlMaxRetransmits: control.maxRetransmits,
      candidateCount: pc.localDescription.sdp.split(/\r?\n/).filter((line) => line.startsWith('a=candidate:')).length };
  });
  assert.equal(setup.ordered, false);
  assert.equal(setup.maxRetransmits, 0);
  assert.equal(setup.controlOrdered, true);
  assert.equal(setup.controlMaxRetransmits, null);
  // The successful DataChannel probe is authoritative; candidate lines can
  // be absent from this browser's post-answer localDescription representation.
  console.log(`RTC_BROWSER_READY:${implementation}:${JSON.stringify(setup)}`);
  if (process.env.RTC_BROWSER_DEFERRED_NETEM === '1') {
    console.log(`RTC_NETEM_READY:${implementation}`);
    let released = false;
    for (let i = 0; i < 300; i++) {
      try { await access('/tmp/rtc-netem-go'); released = true; break; } catch { await sleep(100); }
    }
    assert(released, 'netem gate timeout');
  }
  const rate = Number(process.env.RTC_BROWSER_RATE || 100);
  const durationMs = Number(process.env.RTC_BROWSER_DURATION_MS || 10000);
  assert([[20, 2000], [100, 10000]].some(([r, ms]) => rate === r && durationMs === ms));
  const planned = rate * durationMs / 1000;
  const result = await evaluate(async ({ rate, durationMs, diagnostics }) => {
    const { pc, control, test, post } = window.__nativeRtc;
    const sessionId = 'native-session', testId = 'native-test';
    const hash = (value) => {
      let result = 0x811c9dc5;
      for (let i = 0; i < value.length; i++) result = Math.imul(result ^ value.charCodeAt(i), 0x01000193);
      return result >>> 0;
    };
    const sessionHash = hash(sessionId), testHash = hash(testId);
    const planned = rate * durationMs / 1000;
    let readyResolve, resultResolve, downCount = 0;
    const ready = new Promise((resolve) => { readyResolve = resolve; });
    const downResult = new Promise((resolve) => { resultResolve = resolve; });
    const fragments = new Map();
    const upAcked = new Set();
    control.addEventListener('message', (event) => {
      let value;
      try { value = JSON.parse(event.data); } catch { return; }
      if (value.type === 'measurementReady' && value.testId === testId) readyResolve();
      if (value.type === 'controlFragment' && value.messageType === 'downResult') {
        const parts = fragments.get(value.fragmentId) || Array(value.count);
        parts[value.index] = Uint8Array.from(atob(value.data), (char) => char.charCodeAt(0));
        fragments.set(value.fragmentId, parts);
        if (parts.filter(Boolean).length === value.count) {
          const size = parts.reduce((sum, part) => sum + part.length, 0);
          const bytes = new Uint8Array(size);
          let offset = 0;
          for (const part of parts) { bytes.set(part, offset); offset += part.length; }
          value = JSON.parse(new TextDecoder().decode(bytes));
        }
      }
      if (value.type === 'downResult' && value.testId === testId) resultResolve(value);
    });
    test.addEventListener('message', (event) => {
      const frame = new Uint8Array(event.data);
      if (frame.byteLength !== 128) return;
      const view = new DataView(frame.buffer);
      if (view.getUint32(4, true) !== sessionHash || view.getUint32(8, true) !== testHash) return;
      if (frame[0] === 2 && view.getFloat64(16, true) > 0) upAcked.add(view.getUint32(12, true));
      if (frame[0] === 3) { downCount++; frame[0] = 4; test.send(frame); }
    });
    control.send(JSON.stringify({ type: 'downStart', sessionId, testId, sessionHash, testHash,
      rate, durationMs }));
    await Promise.race([ready, new Promise((_, reject) =>
      setTimeout(() => reject(new Error('measurementReady timeout')), 12000))]);
    const started = performance.now();
    const sentSeqs = [];
    for (let seq = 0; seq < planned; seq++) {
      const delay = started + seq * 1000 / rate - performance.now();
      if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
      const frame = new Uint8Array(128);
      const view = new DataView(frame.buffer);
      frame[0] = 1;
      view.setUint32(4, sessionHash, true); view.setUint32(8, testHash, true);
      view.setUint32(12, seq, true); view.setFloat64(16, performance.now(), true);
      test.send(frame);
      sentSeqs.push(seq);
    }
    const completion = new TextEncoder().encode(JSON.stringify({ type: 'uploadComplete',
      sessionId, testId, sessionHash, testHash, planned, scheduled: planned, skipped: 0,
      actualSent: sentSeqs.length, sentSeqs }));
    let completionFragments = 1;
    const completionQueuedAt = performance.now();
    if (completion.length <= 900) {
      control.send(new TextDecoder().decode(completion));
    } else {
      const fragmentId = 'native-upload-1';
      const count = Math.ceil(completion.length / 512);
      completionFragments = count;
      for (let index = 0; index < count; index++) {
        const slice = completion.subarray(index * 512, (index + 1) * 512);
        control.send(JSON.stringify({ type: 'controlFragment', messageType: 'uploadComplete',
          fragmentId, count, index, encoding: 'base64', sessionId, testId, sessionHash, testHash,
          data: btoa(String.fromCharCode(...slice)) }));
      }
    }
    const controlBufferedAfterQueue = control.bufferedAmount;
    const down = await Promise.race([downResult, new Promise((_, reject) =>
      setTimeout(() => reject(new Error('downResult timeout')), 18000))]);
    const completionToResultMs = performance.now() - completionQueuedAt;
    const rtts = down.samples.map((item) => item.rtt).filter(Number.isFinite).sort((a, b) => a - b);
    const summary = { type: down.type, complete: down.complete,
      invalidReasons: down.invalidReasons, planned: down.planned, scheduled: down.scheduled,
      skipped: down.skipped, actualSent: down.actualSent, acked: down.acked,
      received: down.received, uploadReceived: down.uploadReceived,
      clientUploadActualSent: down.clientUploadActualSent,
      browserDownFrames: downCount, browserUpAcked: upAcked.size,
      completionFragments, controlBufferedAfterQueue,
      controlBufferedAtResult: control.bufferedAmount, completionToResultMs,
      rttP95: rtts.length ? rtts[Math.floor((rtts.length - 1) * .95)] : null,
      rttP99: rtts.length ? rtts[Math.floor((rtts.length - 1) * .99)] : null,
      ordered: test.ordered, maxRetransmits: test.maxRetransmits };
    // Diagnostic only: keep the peer alive briefly after an incomplete result
    // to distinguish late reliable control delivery from permanent loss.
    if (diagnostics && !down.complete) {
      await new Promise((resolve) => setTimeout(resolve, 3000));
      summary.controlBufferedAfterWait = control.bufferedAmount;
      summary.controlStateAfterWait = control.readyState;
    }
    const cancelled = await post({ action: 'cancel' });
    summary.cancelStatus = cancelled.status;
    await pc.close();
    return summary;
  }, { rate, durationMs, diagnostics: process.env.RTC_BROWSER_DIAGNOSTICS === '1' });
  console.log(`NATIVE_RTC_RESULT:${JSON.stringify({ implementation, ...result })}`);
  if (process.env.RTC_BROWSER_DIAGNOSTICS === '1' && implementation === 'rust') {
    for (const line of serverLog.split(/\r?\n/)
      .filter((entry) => entry.includes('WebRTC uploadComplete fragment')
        || entry.includes('RTC_APP_PROBE_SUMMARY')
        || entry.includes('RTC_SLOW_DOWN_ACK'))) {
      console.log(`RTC_SERVER_DIAG:${line}`);
    }
    const parseWarnings = serverLog.split(/\r?\n/)
      .filter((entry) => entry.includes('unable to parse SCTP packet'));
    const warningKinds = new Map();
    for (const warning of parseWarnings) {
      const message = JSON.parse(warning).fields.message;
      warningKinds.set(message, (warningKinds.get(message) || 0) + 1);
    }
    console.log(`RTC_SCTP_PARSE_WARNINGS:${JSON.stringify([...warningKinds])}`);
    if (process.env.RTC_BROWSER_SCTP_TRACE === '1') {
      const trace = await readFile(process.env.SCTP_TRACE_FILE, 'utf8');
      assert(/SCTP_TRACE (?:unix_ms=\d+ )?role=/.test(trace), 'SCTP trace was not written');
      for (const line of trace.trim().split(/\r?\n/)) console.log(`RTC_SERVER_DIAG:${line}`);
    }
  }
  assert.equal(result.type, 'downResult');
  assert.equal(result.planned, planned);
  assert.equal(result.scheduled + result.skipped, result.planned);
  assert.equal(result.actualSent, planned);
  assert(result.acked <= result.received && result.received <= result.actualSent);
  if (Number.isInteger(result.clientUploadActualSent)) {
    assert(result.uploadReceived <= result.clientUploadActualSent);
  }
  assert.equal(result.cancelStatus, 204);
  assert.equal(result.maxRetransmits, 0);
} finally {
  ws?.close();
  browser?.kill();
  server.kill();
  // Chromium can still create files for a moment after SIGTERM. The entire
  // profile is in container-private tmpfs, so cleanup must not mask the
  // actual browser/protocol failure if it races process termination.
  await sleep(300);
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
    .catch(() => {});
}
