// Local experimental DataChannel smoke and scheduler test; never contacts production.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { performance } from 'node:perf_hooks';
import { createRequire } from 'node:module';
import { randomBytes } from 'node:crypto';
import os from 'node:os';
import { access } from 'node:fs/promises';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const { RTCPeerConnection } = require('werift');
const socket = createServer();
await new Promise((resolve) => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise((resolve) => socket.close(resolve));
const nodeBaseline = process.env.RTC_TEST_SERVER === 'node';
const localIceAddress = Object.entries(os.networkInterfaces())
  .filter(([name]) => !/^(tailscale|docker|br-|tun|lo)/i.test(name))
  .flatMap(([, entries]) => entries || [])
  .find((entry) => entry.family === 'IPv4' && !entry.internal)?.address || '127.0.0.1';
const executable = nodeBaseline ? process.execPath : (process.env.RTC_TEST_RUST_BINARY
  || path.join(root, 'target', 'debug', process.platform === 'win32' ? 'speedtest-rust.exe' : 'speedtest-rust'));
const server = spawn(executable, nodeBaseline ? ['test/node-loopback-reference.cjs'] : [], {
  cwd: root, env: { ...process.env, PORT: String(port), SPEEDTEST_RUST_RTC_EXPERIMENTAL: '1',
    SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST: '1', SPEEDTEST_HOST: '127.0.0.1',
    SPEEDTEST_RTC_LOCAL_TEST_IP: localIceAddress,
    RTC_REFERENCE_REPORT_CHANNEL: process.env.RTC_TEST_ZERO_REXMIT_DCEP || '0',
    ...(process.env.RTC_TEST_DEFERRED_NETEM === '1' && !nodeBaseline
      ? { RUST_LOG: 'speedtest_rust::rtc=debug,webrtc_sctp::association::association_internal=debug,warn' }
      : {}) }, stdio: ['ignore', 'pipe', 'pipe'],
});
let stderr = '';
let stdout = '';
server.stderr.on('data', (part) => { stderr += part.toString(); });
server.stdout.on('data', (part) => { stdout += part.toString(); });
const pc = new RTCPeerConnection({ iceServers: [], iceUseIpv4: true, iceUseIpv6: false,
  iceInterfaceAddresses: { udp4: localIceAddress } });
try {
  let serverReady = false;
  for (let n = 0; n < 300; n++) {
    try { if ((await fetch(`http://127.0.0.1:${port}/health`)).ok) { serverReady = true; break; } }
    catch { await sleep(50); }
  }
  assert(serverReady, `Rust server did not start: ${stderr}`);
  const control = pc.createDataChannel('control');
  const test = pc.createDataChannel('test', { ordered: false, maxRetransmits: 0 });
  if (process.env.RTC_TEST_ZERO_REXMIT_DCEP === '1') {
    // Werift 0.23 treats zero as false and incorrectly advertises reliable
    // unordered (0x80). Patch ONLY the queued test-client DCEP OPEN, before
    // SCTP is established, to advertise unordered zero-retransmit (0x81).
    // Keep the actual client send behavior unchanged; this is a diagnostic,
    // not a claim to emulate a native browser's outbound reliability.
    const entry = test.sctp.dataChannelQueue.find(([channel]) => channel === test);
    assert(entry, 'missing queued test DataChannel OPEN');
    assert.equal(entry[2][0], 3, 'unexpected DCEP message type');
    assert.equal(entry[2][1], 0x80, 'unexpected Werift channel type');
    assert.equal(entry[2].readUInt32BE(4), 0, 'unexpected reliability value');
    entry[2][1] = 0x81;
  }
  await pc.setLocalDescription(await pc.createOffer());
  const attemptId = 'local-test-1';
  const post = (body) => fetch(`http://127.0.0.1:${port}/webrtc/offer`, {
    method: 'POST', headers: { 'content-type': 'application/json', 'x-speedtest-attempt-id': attemptId },
    body: JSON.stringify({ attemptId, ...body }),
  });
  const answer = await post({ type: 'offer', sdp: pc.localDescription.sdp });
  const parsed = await answer.json();
  assert.equal(answer.status, 200, JSON.stringify({ parsed, stderr }));
  assert.equal(parsed.type, 'answer');
  assert.match(parsed.sdp, /a=fingerprint:/);
  await pc.setRemoteDescription(parsed);
  await Promise.race([
    test.readyState === 'open' ? Promise.resolve() : new Promise((resolve) => { test.onopen = resolve; }),
    sleep(process.env.RTC_TEST_NETWORK_LOSS === '1' ? 12000 : 7000).then(() => { throw new Error(`DataChannel did not open: ${stderr}\n${stdout.slice(-3000)}\n${pc.connectionState}\n${pc.localDescription.sdp.split(/\r?\n/).filter((line) => line.startsWith('a=candidate:')).join('\n')}`); }),
  ]);
  const ack = new Promise((resolve) => test.onMessage.subscribe(resolve));
  const frame = Buffer.alloc(128);
  frame[0] = 1;
  frame.writeUInt32LE(123, 4);
  frame.writeUInt32LE(456, 8);
  frame.writeUInt32LE(7, 12);
  frame.writeDoubleLE(500.25, 16);
  test.send(frame);
  const rawAck = Buffer.from(await Promise.race([ack, sleep(2000).then(() => { throw new Error('upAck timeout'); })]));
  assert.equal(rawAck.length, 128);
  assert.equal(rawAck[0], 2);
  assert.equal(rawAck.readUInt32LE(12), 7);
  assert.equal(rawAck.readDoubleLE(16), 500.25);
  const hash = (value) => {
    let result = 0x811c9dc5;
    for (let i = 0; i < value.length; i++) result = Math.imul(result ^ value.charCodeAt(i), 0x01000193);
    return result >>> 0;
  };
  const sessionId = 'local-session', testId = 'paired-test';
  const sessionHash = hash(sessionId), testHash = hash(testId);
  const fragments = new Map();
  let readyResolve, resultResolve;
  const ready = new Promise((resolve) => { readyResolve = resolve; });
  const result = new Promise((resolve) => { resultResolve = resolve; });
  let downCount = 0;
  const downArrivedAt = new Map();
  const downSentAt = new Map();
  const downSampleReturnedAt = new Map();
  const downInterarrivalMs = [];
  let lastDownAt = null;
  const upSentAt = new Map();
  const upAckLatencyMs = [];
  const ackMode = process.env.RTC_TEST_ACK_MODE || 'normal';
  const networkLoss = process.env.RTC_TEST_NETWORK_LOSS === '1';
  let heldAck = null;
  let reorderAckSixSent = false;
  let reorderConfirmed = false;
  test.onMessage.subscribe((payload) => {
    const packet = Buffer.from(payload);
    if (packet.length !== 128 || packet.readUInt32LE(4) !== sessionHash || packet.readUInt32LE(8) !== testHash) return;
    if (packet[0] === 5 && ackMode === 'ack-reorder-one'
      && packet.readUInt32LE(12) === 6 && reorderAckSixSent) {
      assert(heldAck, 'expected held sequence 5 ACK after sequence 6 sample');
      test.send(heldAck);
      heldAck = null;
      reorderConfirmed = true;
    }
    if (packet[0] === 3) {
      downCount++;
      if (process.env.RTC_TEST_DEFERRED_NETEM === '1') {
        const now = performance.now();
        const seq = packet.readUInt32LE(12);
        downArrivedAt.set(seq, now);
        downSentAt.set(seq, packet.readDoubleLE(16));
        if (lastDownAt !== null) downInterarrivalMs.push(now - lastDownAt);
        lastDownAt = now;
      }
      if (packet.readUInt32LE(12) === 5 && ackMode === 'ack-drop-one') return;
      packet[0] = 4;
      if (packet.readUInt32LE(12) === 5 && ackMode === 'ack-out-of-range') {
        packet.writeUInt32LE(Number(process.env.RTC_TEST_RATE || 20)
          * Number(process.env.RTC_TEST_DURATION_MS || 2000) / 1000, 12);
      }
      if (packet.readUInt32LE(12) === 5 && ackMode === 'ack-wrong-identity') {
        packet.writeUInt32LE((testHash + 1) >>> 0, 8);
      }
      if (ackMode === 'ack-reorder-one') {
        const seq = packet.readUInt32LE(12);
        if (seq === 5) { heldAck = packet; return; }
        if (seq === 6) {
          assert(heldAck, 'expected the preceding ACK before sequence 6');
          reorderAckSixSent = true;
          test.send(packet);
          return;
        }
      }
      test.send(packet);
      if (packet.readUInt32LE(12) === 5 && ackMode === 'ack-duplicate') test.send(packet);
    }
    if (packet[0] === 5 && process.env.RTC_TEST_DEFERRED_NETEM === '1') {
      downSampleReturnedAt.set(packet.readUInt32LE(12), performance.now());
    }
  });
  control.onMessage.subscribe((payload) => {
    let value;
    try { value = JSON.parse(String(payload)); } catch { return; }
    if (value.type === 'measurementReady' && value.testId === testId) readyResolve(value);
    if (value.type === 'controlFragment' && value.messageType === 'downResult') {
      const parts = fragments.get(value.fragmentId) || Array(value.count);
      parts[value.index] = Buffer.from(value.data, 'base64');
      fragments.set(value.fragmentId, parts);
      if (parts.filter(Boolean).length === value.count) value = JSON.parse(Buffer.concat(parts).toString());
    }
    if (value.type === 'downResult' && value.testId === testId) resultResolve(value);
  });
  const rate = Number(process.env.RTC_TEST_RATE || 20);
  const durationMs = Number(process.env.RTC_TEST_DURATION_MS || 2000);
  assert(Number.isInteger(rate) && rate >= 1 && rate <= 500);
  assert(Number.isInteger(durationMs) && durationMs >= 100 && durationMs <= 60000);
  const planned = Math.floor(rate * durationMs / 1000);
  const uploadMode = process.env.RTC_TEST_UPLOAD_MODE || 'normal';
  assert(['normal', 'upload-omit-one'].includes(uploadMode));
  const uploadSentSeqs = Array.from({ length: planned }, (_, i) => i)
    .filter((seq) => uploadMode !== 'upload-omit-one' || seq !== 5);
  assert(uploadMode === 'normal' || planned > 6);
  // Arm the namespace shaper after the probe ACK but BEFORE downStart. The
  // server begins its downlink scheduler immediately after measurementReady;
  // a gate after that message leaves early samples unshaped and biases tails.
  if (process.env.RTC_TEST_DEFERRED_NETEM === '1') {
    assert(networkLoss, 'deferred netem requires loss-tolerant assertions');
    console.log(`RTC_NETEM_READY:${process.env.RTC_TEST_SERVER}`);
    const gate = '/tmp/rtc-netem-go';
    let released = false;
    for (let i = 0; i < 300; i++) {
      try { await access(gate); released = true; break; } catch { await sleep(100); }
    }
    assert(released, 'deferred netem gate timeout');
  }
  control.send(JSON.stringify({ type: 'downStart', sessionId, testId, sessionHash, testHash,
    rate, durationMs }));
  await Promise.race([ready, sleep(process.env.RTC_TEST_DEFERRED_NETEM === '1' ? 12000
    : networkLoss ? 6000 : 2000).then(() => { throw new Error(`measurementReady timeout: ${stderr}`); })]);
  const reorderedAcks = new Map();
  const uploadAckedSeqs = new Set();
  let uploadAcksResolve;
  const uploadAcks = new Promise((resolve) => { uploadAcksResolve = resolve; });
  const reordered = new Promise((resolve) => test.onMessage.subscribe((raw) => {
    const frame = Buffer.from(raw);
    if (frame.length === 128 && frame[0] === 2 && frame.readUInt32LE(4) === sessionHash
      && frame.readUInt32LE(8) === testHash) {
      const seq = frame.readUInt32LE(12);
      if ([3, 2].includes(seq) && frame.readDoubleLE(16) === 0) {
        reorderedAcks.set(seq, frame[1]);
        if (reorderedAcks.size === 2) resolve();
      }
      if (seq < planned && frame.readDoubleLE(16) > 0) {
        if (process.env.RTC_TEST_DEFERRED_NETEM === '1' && upSentAt.has(seq)) {
          upAckLatencyMs.push(performance.now() - upSentAt.get(seq));
        }
        uploadAckedSeqs.add(seq);
        if (uploadAckedSeqs.size === uploadSentSeqs.length) uploadAcksResolve();
      }
    }
  }));
  if (!networkLoss) {
    for (const seq of [3, 2]) {
      const probe = Buffer.alloc(128);
      probe[0] = 1;
      probe.writeUInt32LE(sessionHash, 4);
      probe.writeUInt32LE(testHash, 8);
      probe.writeUInt32LE(seq, 12);
      test.send(probe);
      await sleep(5);
    }
    await Promise.race([reordered, sleep(2000).then(() => { throw new Error('reordered upAck timeout'); })]);
    assert.equal(reorderedAcks.get(3) & 1, 0);
    assert.equal(reorderedAcks.get(2) & 1, 1);
  }
  const started = performance.now();
  for (let seq = 0; seq < planned; seq++) {
    const delay = started + seq * 1000 / rate - performance.now();
    if (delay > 0) await sleep(delay);
    if (uploadMode === 'upload-omit-one' && seq === 5) continue;
    const up = Buffer.alloc(128);
    up[0] = 1;
    up.writeUInt32LE(sessionHash, 4);
    up.writeUInt32LE(testHash, 8);
    up.writeUInt32LE(seq, 12);
    up.writeDoubleLE(performance.now(), 16);
    if (process.env.RTC_TEST_DEFERRED_NETEM === '1') upSentAt.set(seq, performance.now());
    test.send(up);
  }
  const completionMode = process.env.RTC_TEST_COMPLETION_MODE || 'valid';
  // Invalid completion can finalize immediately; synchronize the preceding
  // unordered test-channel frames before comparing its discrete snapshot.
  if ((completionMode !== 'valid' && completionMode !== 'missing') || uploadMode !== 'normal') {
    await Promise.race([uploadAcks, sleep(2000).then(() => {
      throw new Error(`upload ACK barrier timeout: ${uploadAckedSeqs.size}/${uploadSentSeqs.length}`);
    })]);
  }
  const completion = { type: 'uploadComplete', sessionId, testId, sessionHash, testHash,
    planned, scheduled: planned, skipped: 0, actualSent: uploadSentSeqs.length,
    sentSeqs: uploadSentSeqs.slice() };
  if (completionMode === 'invalid') completion.skipped = 1;
  if (completionMode === 'planned-null') completion.planned = null;
  if (completionMode === 'planned-mismatch') completion.planned = planned + 1;
  if (completionMode === 'scheduled-negative') completion.scheduled = -1;
  if (completionMode === 'scheduled-fractional') completion.scheduled = 1.5;
  if (completionMode === 'skipped-negative') completion.skipped = -1;
  if (completionMode === 'actual-negative') completion.actualSent = -1;
  if (completionMode === 'actual-over') completion.actualSent = planned + 1;
  if (completionMode === 'seq-duplicate') completion.sentSeqs[1] = completion.sentSeqs[0];
  if (completionMode === 'seq-out-of-range') completion.sentSeqs[0] = planned;
  if (completionMode === 'seq-fractional') completion.sentSeqs[0] = 0.5;
  if (completionMode === 'seq-count-short') completion.sentSeqs.pop();
  if (completionMode === 'seq-missing') delete completion.sentSeqs;
  if (completionMode === 'id-mismatch') completion.testId = 'wrong-test-id';
  if (completionMode === 'hash-mismatch') completion.testHash++;
  const bytes = Buffer.from(JSON.stringify(completion));
  if (completionMode === 'missing') { /* Deliberately wait for the missing-upload deadline. */ }
  else if (bytes.length <= 900) control.send(bytes.toString());
  else {
    const fragmentId = randomBytes(8).toString('hex');
    const count = Math.ceil(bytes.length / 512);
    for (let index = 0; index < count; index++) {
      control.send(JSON.stringify({ type: 'controlFragment', messageType: 'uploadComplete',
        fragmentId, count, index, encoding: 'base64', sessionId, testId, sessionHash, testHash,
        data: bytes.subarray(index * 512, (index + 1) * 512).toString('base64') }));
    }
  }
  if (completionMode === 'duplicate') control.send(bytes.toString());
  const down = await Promise.race([result, sleep(durationMs + (networkLoss ? 8000 : 4000)).then(() => { throw new Error(`downResult timeout: ${stderr}`); })]);
  if (process.env.RTC_TEST_ZERO_REXMIT_DCEP === '1' && nodeBaseline) {
    console.log(stderr.split(/\r?\n/).find((line) => line.startsWith('RTC_NODE_CHANNEL_POLICY:'))
      || 'RTC_NODE_CHANNEL_POLICY:MISSING');
  }
  if (process.env.RTC_TEST_DEFERRED_NETEM === '1' && !nodeBaseline) {
    const diagnostics = `${stdout}\n${stderr}`;
    console.log(`RTC_DEBUG_ACTIVE:${diagnostics.includes('WebRTC downlink diagnostics active')}`);
    const channelPolicy = diagnostics.split(/\r?\n/).flatMap((line) => {
      try {
        const event = JSON.parse(line).fields;
        return event?.message === 'WebRTC test channel policy' ? [event] : [];
      } catch { return []; }
    });
    console.log(`RTC_CHANNEL_POLICY:${JSON.stringify(channelPolicy)}`);
    const forwardTsnLogs = diagnostics.split(/\r?\n/).flatMap((line) => {
      try {
        const message = JSON.parse(line).fields?.message;
        return message?.includes('ForwardTSN') ? [message] : [];
      } catch { return []; }
    });
    console.log(`RTC_SCTP_FORWARD:${JSON.stringify({ count: forwardTsnLogs.length,
      first: forwardTsnLogs.slice(0, 4) })}`);
    const events = diagnostics.split(/\r?\n/).flatMap((line) => {
      try {
        const event = JSON.parse(line).fields;
        return event?.message?.startsWith('WebRTC downlink') || event?.message?.endsWith('send delayed')
          ? [event] : [];
      } catch { return []; }
    });
    const delayed = events.filter((event) => event.message.endsWith('send delayed'));
    const byType = Object.groupBy(delayed, (event) => event.message);
    const sendDiagnostic = Object.fromEntries(
      Object.entries(byType).map(([name, events]) => [name, {
        count: events.length,
        maxMs: Math.max(...events.map((event) => event.elapsed_ms)),
        firstSeqs: events.slice(0, 8).map((event) => event.seq),
      }]));
    console.log(`RTC_SEND_DIAGNOSTIC:${JSON.stringify(sendDiagnostic)}`);
    const buffered = events.filter((event) => event.message === 'WebRTC downlink buffered data');
    const maximum = events.find((event) => event.message === 'WebRTC downlink maximum buffered data');
    console.log(`RTC_BUFFER_DIAGNOSTIC:${JSON.stringify({ maxBytes: maximum?.max_buffered_bytes ?? null,
      above1280Count: buffered.length, first: buffered.slice(0, 8).map(({ seq, buffered_bytes }) =>
        ({ seq, buffered_bytes })) })}`);
  }
  const summary = { planned, scheduled: down.scheduled, skipped: down.skipped,
    sent: down.actualSent, acked: down.acked, receivedByClient: downCount,
    invalidReasons: down.invalidReasons, pacing: down.pacing,
    executorDelay: down.serverEventLoopDelay };
  if (process.env.RTC_TEST_AB_JSON === '1') {
    const rtts = down.samples.map((item) => item.rtt).filter(Number.isFinite).sort((a, b) => a - b);
    const percentile = (values, fraction) => {
      if (!values.length) return null;
      const sorted = values.toSorted((a, b) => a - b);
      return sorted[Math.floor((sorted.length - 1) * fraction)];
    };
    const returned = [...downSampleReturnedAt].flatMap(([seq, at]) =>
      downArrivedAt.has(seq) ? [at - downArrivedAt.get(seq)] : []);
    // Different process.timeOrigins cancel in the difference from the fastest
    // frame; this measures excess first-leg residence, not absolute one-way RTT.
    const offsets = [...downArrivedAt].map(([seq, at]) => at - downSentAt.get(seq));
    const minimumOffset = Math.min(...offsets);
    const excess = offsets.map((offset) => offset - minimumOffset);
    const clientMetrics = process.env.RTC_TEST_DEFERRED_NETEM === '1' ? {
      downInterarrivalP99: percentile(downInterarrivalMs, .99),
      downInterarrivalMax: percentile(downInterarrivalMs, 1),
      downExcessP99: percentile(excess, .99),
      downExcessMax: percentile(excess, 1),
      downSampleReturnP99: percentile(returned, .99),
      downSampleReturnMax: percentile(returned, 1),
      upAckP99: percentile(upAckLatencyMs, .99),
      upAckMax: percentile(upAckLatencyMs, 1),
      rttOver200: down.samples.filter(({ rtt }) => rtt > 200).slice(0, 50).map(({ seq, rtt }) => ({
        seq, rtt,
        downSampleReturnMs: downSampleReturnedAt.has(seq) && downArrivedAt.has(seq)
          ? downSampleReturnedAt.get(seq) - downArrivedAt.get(seq) : null,
        downExcessMs: downArrivedAt.has(seq)
          ? downArrivedAt.get(seq) - downSentAt.get(seq) - minimumOffset : null,
      })),
    } : null;
    console.log(`AB_RESULT:${JSON.stringify({
      type: down.type, final: down.final, complete: down.complete,
      invalidReasons: down.invalidReasons,
      sessionId: down.sessionId, testId: down.testId,
      sessionHash: down.sessionHash, testHash: down.testHash,
      planned: down.planned, scheduled: down.scheduled, skipped: down.skipped,
      actualSent: down.actualSent, sent: down.sent, sentSeqs: down.sentSeqs,
      received: down.received, acked: down.acked,
      uploadReceived: down.uploadReceived, uploadReceivedSeqs: down.uploadReceivedSeqs,
      clientUploadScheduled: down.clientUploadScheduled,
      clientUploadSkipped: down.clientUploadSkipped,
      clientUploadActualSent: down.clientUploadActualSent,
      sampleSeqs: down.samples.map((item) => item.seq),
      rttStats: rtts.length ? {
        mean: rtts.reduce((a, b) => a + b, 0) / rtts.length,
        p95: rtts[Math.floor((rtts.length - 1) * .95)],
        p99: rtts[Math.floor((rtts.length - 1) * .99)],
      } : null,
      ...(clientMetrics ? { clientMetrics } : {}),
      pacingP95: down.pacing?.p95Interval ?? null,
      delayP95: down.serverEventLoopDelay?.p95Ms ?? null,
      hasPacing: down.pacing !== null,
      hasDelay: down.serverEventLoopDelay !== null,
      hasUploadCompleteTime: down.uploadCompleteAtMs !== null,
    })}`);
  }
  if (completionMode !== 'valid') {
    assert.equal(down.complete, false, JSON.stringify(summary));
    const expected = {
      missing: 'UPLOAD_COMPLETE_MISSING', duplicate: 'UPLOAD_COMPLETE_DUPLICATE',
      invalid: 'UPLOAD_SCHEDULER_ACCOUNTING', 'planned-null': 'UPLOAD_PLANNED_INVALID',
      'planned-mismatch': 'UPLOAD_PLANNED_MISMATCH',
      'scheduled-negative': 'UPLOAD_SCHEDULED_INVALID',
      'scheduled-fractional': 'UPLOAD_SCHEDULED_INVALID',
      'skipped-negative': 'UPLOAD_SKIPPED_INVALID',
      'actual-negative': 'UPLOAD_ACTUALSENT_INVALID',
      'actual-over': 'UPLOAD_SCHEDULER_BOUNDS',
      'seq-duplicate': 'UPLOAD_SENT_SEQS_INVALID',
      'seq-out-of-range': 'UPLOAD_SENT_SEQS_INVALID',
      'seq-fractional': 'UPLOAD_SENT_SEQS_INVALID',
      'seq-count-short': 'UPLOAD_SENT_SEQS_COUNT_MISMATCH',
      'seq-missing': 'UPLOAD_SENT_SEQS_COUNT_MISMATCH',
      'id-mismatch': 'UPLOAD_COMPLETE_ID_MISMATCH',
      // Bad outer identity is discarded before completion validation.
      'hash-mismatch': 'UPLOAD_COMPLETE_MISSING',
    }[completionMode];
    assert(expected, `unknown completion mode: ${completionMode}`);
    assert(down.invalidReasons.includes(expected), JSON.stringify(summary));
    assert.equal((await post({ action: 'cancel' })).status, 204);
    console.log(`${nodeBaseline ? 'Node' : 'Rust'} WebRTC: ${completionMode} uploadComplete, ${JSON.stringify(summary)}`);
  } else {
  assert.equal(down.complete, true, JSON.stringify(summary));
  assert.equal(down.planned, planned);
  assert.equal(down.scheduled + down.skipped, planned);
  const missingAck = ['ack-drop-one', 'ack-out-of-range', 'ack-wrong-identity'].includes(ackMode) ? 1 : 0;
  if (networkLoss) {
    assert(down.acked <= downCount && downCount <= down.actualSent, JSON.stringify(summary));
    assert(down.uploadReceived <= planned, JSON.stringify(summary));
  } else {
    assert.equal(down.actualSent - missingAck, down.acked, JSON.stringify(summary));
    assert.equal(downCount, down.actualSent, JSON.stringify(summary));
  }
  assert.equal(down.received, down.acked, JSON.stringify(summary));
  if (uploadMode === 'upload-omit-one') {
    assert.equal(down.uploadReceived, planned - 1, JSON.stringify(summary));
    assert(!down.uploadReceivedSeqs.includes(5), JSON.stringify(summary));
  }
  if (ackMode === 'ack-reorder-one') {
    assert.equal(heldAck, null, 'reordered ACK was not sent');
    assert(reorderConfirmed, 'server did not confirm sequence 6 before sequence 5 ACK');
  }
  assert.equal(down.invalidReasons.length, 0);
  assert(Number.isFinite(down.serverEventLoopDelay?.p95Ms), JSON.stringify(down));
  assert.equal((await post({ action: 'cancel' })).status, 204);
  console.log(`${nodeBaseline ? 'Node' : 'Rust'} WebRTC: ${rate} pkt/s x ${durationMs} ms, ${JSON.stringify(summary)}, cancellation passed`);
  assert(down.actualSent >= Math.ceil(planned * .99), `99% density gate FAILED: ${JSON.stringify(summary)}`);
  }
} finally {
  await pc.close().catch(() => {});
  server.kill();
}
