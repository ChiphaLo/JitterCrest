// Exact discrete transport/accounting comparison against the Node reference.
// Only the in-memory loopback ICE policy changes in the Node test launcher.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
function firstDifference(a, b, at = '$') {
  if (Object.is(a, b)) return null;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return `${at}.length: Node=${a.length} Rust=${b.length}`;
    for (let i = 0; i < a.length; i++) {
      const difference = firstDifference(a[i], b[i], `${at}[${i}]`);
      if (difference) return difference;
    }
    return null;
  } else if (a && b && typeof a === 'object' && typeof b === 'object') {
    for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
      const difference = firstDifference(a[key], b[key], `${at}.${key}`);
      if (difference) return difference;
    }
    return null;
  }
  return `${at}: Node=${JSON.stringify(a)} Rust=${JSON.stringify(b)}`;
}
function assertContractEqual(node, rust, context) {
  const difference = firstDifference(node, rust);
  const uploadSequences = difference && node.uploadReceivedSeqs && rust.uploadReceivedSeqs
    ? `; upload Node=${JSON.stringify(node.uploadReceivedSeqs)} Rust=${JSON.stringify(rust.uploadReceivedSeqs)}`
    : '';
  assert.equal(difference, null, `${context}: ${difference}${uploadSequences}`);
}
assert.equal(firstDifference({ empty: [], nested: { items: [1, 2] } },
  { empty: [], nested: { items: [1, 2] } }), null);
assert.match(firstDifference({ items: [1, 2] }, { items: [1, 3] }), /\$\.items\[1\]/);
async function run(server, mode, rate, durationMs) {
  const child = spawn(process.execPath, ['test/rtc-signaling.mjs'], {
    cwd: root, env: { ...process.env, RTC_TEST_SERVER: server,
      RTC_TEST_COMPLETION_MODE: mode.startsWith('ack-') || mode.startsWith('upload-') ? 'valid' : mode,
      RTC_TEST_ACK_MODE: mode.startsWith('ack-') ? mode : 'normal',
      RTC_TEST_UPLOAD_MODE: mode.startsWith('upload-') ? mode : 'normal',
      RTC_TEST_NETWORK_LOSS: process.env.RTC_AB_LOSS === '1' ? '1' : '0',
      RTC_TEST_DEFERRED_NETEM: process.env.RTC_AB_DEFERRED_NETEM || '0',
      RTC_TEST_ZERO_REXMIT_DCEP: process.env.RTC_AB_ZERO_REXMIT_DCEP || '0',
      RTC_TEST_RATE: String(rate),
      RTC_TEST_DURATION_MS: String(durationMs), RTC_TEST_AB_JSON: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', (chunk) => {
    output += chunk;
    if (process.env.RTC_AB_DEFERRED_NETEM === '1' && chunk.toString().includes('RTC_NETEM_READY:')) {
      process.stdout.write(chunk);
    }
  });
  const code = await new Promise((resolve) => child.on('exit', resolve));
  assert.equal(code, 0, `${server} ${mode}: ${output}`);
  const line = output.split(/\r?\n/).find((item) => item.startsWith('AB_RESULT:'));
  assert(line, `${server} ${mode}: missing AB result`);
  if (process.env.RTC_AB_DEFERRED_NETEM === '1') {
    for (const diagnostic of output.split(/\r?\n/).filter((item) =>
      item.startsWith('RTC_SEND_DIAGNOSTIC:') || item.startsWith('RTC_BUFFER_DIAGNOSTIC:')
        || item.startsWith('RTC_CHANNEL_POLICY:')
        || item.startsWith('RTC_SCTP_FORWARD:')
        || item.startsWith('RTC_NODE_CHANNEL_POLICY:')
        || item.startsWith('RTC_DEBUG_ACTIVE:'))) {
      console.log(diagnostic);
    }
  }
  return JSON.parse(line.slice('AB_RESULT:'.length));
}

const cases = [
  ['valid', 20, 2000], ['invalid', 20, 2000], ['missing', 20, 2000],
  ['duplicate', 20, 2000],
  ['planned-null', 20, 2000], ['scheduled-negative', 20, 2000],
  ['planned-mismatch', 20, 2000], ['skipped-negative', 20, 2000],
  ['actual-negative', 20, 2000],
  ['scheduled-fractional', 20, 2000], ['actual-over', 20, 2000],
  ['seq-duplicate', 20, 2000], ['seq-out-of-range', 20, 2000],
  ['seq-fractional', 20, 2000], ['seq-count-short', 20, 2000],
  ['seq-missing', 20, 2000], ['id-mismatch', 20, 2000],
  ['hash-mismatch', 20, 2000],
  ['ack-drop-one', 20, 2000], ['ack-duplicate', 20, 2000],
  ['ack-out-of-range', 20, 2000], ['ack-wrong-identity', 20, 2000],
  ['ack-reorder-one', 20, 2000],
  ['upload-omit-one', 20, 2000],
  ['valid', 60, 10000],
];
if (process.env.RTC_AB_HIGH === '1') {
  cases.length = 0;
  cases.push(['valid', 100, 10000], ['valid', 200, 10000]);
}
if (process.env.RTC_AB_LOSS === '1') {
  cases.length = 0;
  cases.push(['valid', 100, 10000]);
}
if (process.env.RTC_AB_SMOKE_ONLY === '1') cases.splice(1);
if (process.env.RTC_AB_MODE) {
  const selected = cases.find(([mode]) => mode === process.env.RTC_AB_MODE);
  assert(selected, `unknown RTC_AB_MODE=${process.env.RTC_AB_MODE}`);
  cases.length = 0;
  cases.push(selected);
}
if (process.env.RTC_AB_LONG === '1') {
  if (process.env.RTC_AB_LONG_ONLY === '1') cases.length = 0;
  cases.push(['valid', 20, 60000]);
}
for (const [mode, rate, durationMs] of cases) {
  if (process.env.RTC_AB_SERVER_ONLY) {
    assert(process.env.RTC_AB_DEFERRED_NETEM === '1' && process.env.RTC_AB_LOSS === '1');
    assert(['node', 'rust'].includes(process.env.RTC_AB_SERVER_ONLY));
    const single = await run(process.env.RTC_AB_SERVER_ONLY, mode, rate, durationMs);
    assert.equal(single.scheduled + single.skipped, single.planned);
    assert.equal(single.actualSent, single.sentSeqs.length);
    assert.equal(single.received, single.sampleSeqs.length);
    assert.equal(single.uploadReceived, single.uploadReceivedSeqs.length);
    assert(single.actualSent >= Math.ceil(single.planned * .99));
    console.log(`DEFERRED_LOSS_RESULT:${JSON.stringify({ server: process.env.RTC_AB_SERVER_ONLY,
      mode, rate, durationMs, result: single })}`);
    continue;
  }
  const baseline = await run('node', mode, rate, durationMs);
  const migrated = await run('rust', mode, rate, durationMs);
  const identity = (value) => ({ type: value.type, final: value.final,
    complete: value.complete, invalidReasons: value.invalidReasons,
    sessionId: value.sessionId, testId: value.testId,
    sessionHash: value.sessionHash, testHash: value.testHash,
    planned: value.planned, clientUploadScheduled: value.clientUploadScheduled,
    clientUploadSkipped: value.clientUploadSkipped,
    clientUploadActualSent: value.clientUploadActualSent,
    hasPacing: value.hasPacing, hasDelay: value.hasDelay,
    hasUploadCompleteTime: value.hasUploadCompleteTime });
  assertContractEqual(identity(baseline), identity(migrated), `${mode} Node/Rust contract mismatch`);
  for (const [name, value] of [['Node', baseline], ['Rust', migrated]]) {
    assert.equal(value.scheduled + value.skipped, value.planned, `${name} scheduler accounting`);
    assert.equal(value.actualSent, value.sentSeqs.length, `${name} sent-sequence count`);
    assert.equal(value.received, value.sampleSeqs.length, `${name} sample count`);
    assert.equal(value.uploadReceived, value.uploadReceivedSeqs.length, `${name} upload count`);
  }
  if (durationMs === 2000) {
    const discrete = ({ rttStats, pacingP95, delayP95, ...rest }) => rest;
    assertContractEqual(discrete(baseline), discrete(migrated), `${mode} Node/Rust 20/s discrete mismatch`);
    console.log(`Node/Rust ${mode} ${rate} pkt/s × ${durationMs} ms: exact discrete result match`);
  } else {
    assert(baseline.actualSent >= Math.ceil(baseline.planned * .99));
    assert(migrated.actualSent >= Math.ceil(migrated.planned * .99));
    console.log(`Node/Rust ${mode} ${rate} pkt/s × ${durationMs} ms: matching schema/reasons/accounting; `
      + `time-dependent sent/ack: Node ${baseline.actualSent}/${baseline.acked}, Rust ${migrated.actualSent}/${migrated.acked}`);
    if (process.env.RTC_AB_LOSS === '1') console.log(`  loss probe: Node downDelivered=${baseline.received}/sent=${baseline.actualSent}, `
      + `uploadReceived=${baseline.uploadReceived}/${baseline.clientUploadActualSent}; `
      + `Rust downDelivered=${migrated.received}/sent=${migrated.actualSent}, `
      + `uploadReceived=${migrated.uploadReceived}/${migrated.clientUploadActualSent}`);
  }
  console.log(`  RTT mean/P95/P99 ms: Node ${JSON.stringify(baseline.rttStats)}, `
    + `Rust ${JSON.stringify(migrated.rttStats)}; pacing P95 ms: `
    + `${baseline.pacingP95}/${migrated.pacingP95}; timer delay P95 ms: `
    + `${baseline.delayP95}/${migrated.delayP95}`);
}
