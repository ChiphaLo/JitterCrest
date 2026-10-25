// Compare deterministic contract fields while keeping each random loss sample separate.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const [nodePath, rustPath] = process.argv.slice(2);
assert(nodePath && rustPath, 'expected Node and Rust logs');
const read = (file) => {
  const lines = readFileSync(file, 'utf8').split(/\r?\n/);
  const line = lines.find((item) => item.startsWith('DEFERRED_LOSS_RESULT:'));
  assert(line, `${file}: missing result`);
  return JSON.parse(line.slice('DEFERRED_LOSS_RESULT:'.length));
};
const node = read(nodePath);
const rust = read(rustPath);
assert.equal(node.server, 'node');
assert.equal(rust.server, 'rust');
for (const key of ['mode', 'rate', 'durationMs']) assert.deepEqual(node[key], rust[key]);
const deterministic = (v) => {
  const r = v.result;
  return { type: r.type, final: r.final, complete: r.complete,
    invalidReasons: r.invalidReasons, sessionId: r.sessionId, testId: r.testId,
    sessionHash: r.sessionHash, testHash: r.testHash, planned: r.planned,
    clientUploadScheduled: r.clientUploadScheduled,
    clientUploadSkipped: r.clientUploadSkipped,
    clientUploadActualSent: r.clientUploadActualSent,
    hasPacing: r.hasPacing, hasDelay: r.hasDelay,
    hasUploadCompleteTime: r.hasUploadCompleteTime };
};
assert.deepEqual(deterministic(node), deterministic(rust));
for (const { server, result: r } of [node, rust]) {
  assert.equal(r.actualSent, r.sentSeqs.length, server);
  assert.equal(r.received, r.sampleSeqs.length, server);
  assert.equal(r.uploadReceived, r.uploadReceivedSeqs.length, server);
  assert(r.acked <= r.received && r.received <= r.actualSent, server);
  assert(r.uploadReceived <= r.clientUploadActualSent, server);
}
console.log(`DEFERRED_LOSS_COMPARE_PASS: Node down ${node.result.acked}/${node.result.actualSent}, `
  + `up ${node.result.uploadReceived}/${node.result.clientUploadActualSent}; `
  + `Rust down ${rust.result.acked}/${rust.result.actualSent}, `
  + `up ${rust.result.uploadReceived}/${rust.result.clientUploadActualSent}`);
