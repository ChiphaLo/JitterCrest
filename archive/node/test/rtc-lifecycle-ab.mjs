// Real DataChannel boundary comparison: malformed frames, cancel, disconnect, reconnect.
// Only the Node test launcher adapts private ICE candidates in memory; no production listener.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import os from 'node:os';
import path from 'node:path';
import { readFile } from 'node:fs/promises';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const { RTCPeerConnection } = require('werift');
const localIceAddress = Object.entries(os.networkInterfaces())
  .filter(([name]) => !/^(tailscale|docker|br-|tun|lo)/i.test(name))
  .flatMap(([, entries]) => entries || [])
  .find((entry) => entry.family === 'IPv4' && !entry.internal)?.address || '127.0.0.1';
const stressCycles = Number(process.env.RTC_LIFECYCLE_STRESS_CYCLES || 0);
assert(Number.isInteger(stressCycles) && stressCycles >= 0 && stressCycles <= 200);
async function rssMiB(pid) {
  if (process.platform !== 'linux') return null;
  const status = await readFile(`/proc/${pid}/status`, 'utf8');
  const kib = Number(status.match(/^VmRSS:\s+(\d+) kB$/m)?.[1]);
  return Number.isFinite(kib) ? +(kib / 1024).toFixed(2) : null;
}
function hash(value) {
  let result = 0x811c9dc5;
  for (let i = 0; i < value.length; i++) result = Math.imul(result ^ value.charCodeAt(i), 0x01000193);
  return result >>> 0;
}

async function withTimeout(promise, ms, label) {
  return Promise.race([promise, sleep(ms).then(() => { throw new Error(`${label} timed out`); })]);
}

async function run(kind) {
  const socket = createServer();
  await new Promise((resolve) => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  const executable = kind === 'node' ? process.execPath
    : (process.env.RTC_TEST_RUST_BINARY || path.join(root, 'target', 'debug',
      process.platform === 'win32' ? 'speedtest-rust.exe' : 'speedtest-rust'));
  const child = spawn(executable, kind === 'node' ? ['test/node-loopback-reference.cjs'] : [], {
    cwd: root, env: { ...process.env, PORT: String(port), SPEEDTEST_HOST: '127.0.0.1',
      SPEEDTEST_RUST_RTC_EXPERIMENTAL: '1', SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST: '1',
      SPEEDTEST_RTC_LOCAL_TEST_IP: localIceAddress, MAX_RTC_PEERS_PER_IP: '3',
      MAX_RTC_SESSION_MS: '10000',
      ...(stressCycles ? { RTC_OFFER_MAX_PER_IP: '200', RTC_CANCEL_MAX_PER_IP: '1000' } : {}) },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let logs = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', (data) => { logs += data; });
  const base = `http://127.0.0.1:${port}`;
  const peers = [];
  async function request(attemptId, body) {
    return fetch(`${base}/webrtc/offer`, { method: 'POST',
      headers: { 'content-type': 'application/json', 'x-speedtest-attempt-id': attemptId },
      body: JSON.stringify({ attemptId, ...body }) });
  }
  async function connect(attemptId, allowRejected = false) {
    const pc = new RTCPeerConnection({ iceServers: [], iceUseIpv4: true, iceUseIpv6: false,
      iceInterfaceAddresses: { udp4: localIceAddress } });
    peers.push(pc);
    const control = pc.createDataChannel('control');
    const test = pc.createDataChannel('test', { ordered: false, maxRetransmits: 0 });
    await pc.setLocalDescription(await pc.createOffer());
    const response = await request(attemptId, { type: 'offer', sdp: pc.localDescription.sdp });
    const answer = await response.json();
    if (allowRejected && response.status !== 200) {
      await pc.close();
      return { status: response.status, error: answer.error };
    }
    assert.equal(response.status, 200, `${kind} ${attemptId}: ${JSON.stringify(answer)} ${logs.slice(-1000)}`);
    await pc.setRemoteDescription(answer);
    await withTimeout(test.readyState === 'open' ? Promise.resolve()
      : new Promise((resolve) => { test.onopen = resolve; }), 7000, `${kind} ${attemptId} open`);
    return { pc, control, test, status: response.status };
  }
  async function probe(test, seq) {
    const acks = [];
    test.onMessage.subscribe((raw) => {
      const packet = Buffer.from(raw);
      if (packet.length === 128 && packet[0] === 2 && packet.readUInt32LE(12) === seq) {
        acks.push({ seq: packet.readUInt32LE(12), flags: packet[1], sentAt: packet.readDoubleLE(16) });
      }
    });
    const frame = Buffer.alloc(128);
    frame[0] = 1;
    frame.writeUInt32LE(123, 4);
    frame.writeUInt32LE(456, 8);
    frame.writeUInt32LE(seq, 12);
    frame.writeDoubleLE(500.25, 16);
    async function waitForAcks(count) {
      const deadline = Date.now() + 2000;
      while (acks.length < count && Date.now() < deadline) await sleep(10);
      assert(acks.length >= count, `${kind}: expected ${count} upAck for seq ${seq}`);
    }
    test.send(frame);
    await waitForAcks(1);
    test.send(frame);
    await waitForAcks(2);
    return acks;
  }
  async function rejectMalformed(attemptId, payload) {
    const { test } = await connect(attemptId);
    test.send(payload);
    // werift's local readyState may stay "open" after the remote peer cleans up.
    // Keep the local peer open: with a 3-peer cap, the subsequent fourth offer
    // demonstrates server-side release caused by the malformed frame, not client close.
    await sleep(150);
    return payload.length;
  }
  try {
    let ready = false;
    for (let n = 0; n < 600; n++) {
      if (child.exitCode !== null) throw new Error(`${kind} server exited: ${logs.slice(-2000)}`);
      try { if ((await fetch(`${base}/health`)).ok) { ready = true; break; } } catch { /* startup */ }
      await sleep(50);
    }
    assert(ready, `${kind} server did not start: ${logs.slice(-2000)}`);
    const malformed = [];
    for (const [index, payload] of [Buffer.alloc(1), Buffer.alloc(127), Buffer.alloc(129)].entries()) {
      malformed.push(await rejectMalformed(`lifecycle-malformed-${index}`, payload));
    }
    const first = await connect('lifecycle-1');
    const firstAcks = await probe(first.test, 7);
    const cancelFirst = await request('lifecycle-1', { action: 'cancel' });
    assert.equal(cancelFirst.status, 204);
    await first.pc.close();
    const second = await connect('lifecycle-2');
    const secondAcks = await probe(second.test, 8);
    const measurementReady = new Promise((resolve) => second.control.onMessage.subscribe((raw) => {
      try {
        const msg = JSON.parse(String(raw));
        if (msg.type === 'measurementReady' && msg.testId === 'disconnect-test') resolve();
      } catch { /* Ignore unrelated control traffic. */ }
    }));
    second.control.send(JSON.stringify({ type: 'downStart', sessionId: 'disconnect-session',
      testId: 'disconnect-test', sessionHash: hash('disconnect-session'),
      testHash: hash('disconnect-test'), rate: 20, durationMs: 10000 }));
    await withTimeout(measurementReady, 2000, `${kind} measurementReady`);
    await sleep(250);
    await second.pc.close(); // Abrupt disconnect during an active measurement, no cancel request.
    await sleep(1000);
    const reconnectAcks = [];
    const reconnectStatuses = [];
    const cancels = [];
    for (let i = 0; i < 3; i++) {
      const id = `lifecycle-reconnect-${i}`;
      const reconnected = await connect(id, true);
      reconnectStatuses.push(reconnected.status);
      reconnectAcks.push(reconnected.status === 200 ? await probe(reconnected.test, 9 + i) : null);
    }
    // MAX_RTC_PEERS_PER_IP=3 exposes whether the disconnected measurement
    // has released its server-side slot after one second.
    for (let i = 0; i < 3; i++) {
      if (reconnectStatuses[i] !== 200) continue;
      const cancel = await request(`lifecycle-reconnect-${i}`, { action: 'cancel' });
      assert.equal(cancel.status, 204);
      cancels.push(cancel.status);
    }
    await sleep(11000); // Let the configured session deadline reap the un-cancelled peer.
    const afterTimeoutAcks = [];
    const afterTimeoutCancels = [];
    for (let i = 0; i < 3; i++) {
      const id = `lifecycle-after-timeout-${i}`;
      const peer = await connect(id);
      afterTimeoutAcks.push(await probe(peer.test, 12 + i));
    }
    // Three new active peers require the old, abruptly disconnected attempt's
    // session timer to have released its slot. No other peers remain active.
    for (let i = 0; i < 3; i++) {
      const cancel = await request(`lifecycle-after-timeout-${i}`, { action: 'cancel' });
      assert.equal(cancel.status, 204);
      afterTimeoutCancels.push(cancel.status);
    }
    const stressRss = [];
    const stressStatuses = [];
    for (let i = 0; i < stressCycles; i++) {
      const id = `lifecycle-stress-${i}`;
      const peer = await connect(id);
      await probe(peer.test, 100 + i);
      const cancel = await request(id, { action: 'cancel' });
      assert.equal(cancel.status, 204);
      stressStatuses.push([peer.status, cancel.status]);
      await peer.pc.close();
      if ((i + 1) % 5 === 0) {
        await sleep(50);
        stressRss.push({ cycles: i + 1, rssMiB: await rssMiB(child.pid) });
      }
    }
    return { malformed, firstAcks, cancelFirst: cancelFirst.status, secondAcks,
      inFlightDisconnect: true, reconnectStatuses, reconnectAcks, cancels,
      afterTimeoutAcks, afterTimeoutCancels, stressStatuses, stressRss };
  } finally {
    await Promise.all(peers.map((pc) => pc.close().catch(() => {})));
    child.kill();
  }
}

const baseline = await run('node');
const migrated = await run('rust');
const { stressRss: nodeRss, ...nodeDiscrete } = baseline;
const { stressRss: rustRss, ...rustDiscrete } = migrated;
assert.deepEqual(rustDiscrete, nodeDiscrete);
console.log('Node/Rust real DataChannel malformed/cancel/disconnect/reconnect exact match:',
  JSON.stringify(rustDiscrete));
if (stressCycles) console.log('Stress service-process RSS samples (MiB), Node/Rust:',
  JSON.stringify({ node: nodeRss, rust: rustRss }));
