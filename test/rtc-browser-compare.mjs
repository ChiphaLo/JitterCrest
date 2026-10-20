import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const [nodePath, rustPath] = process.argv.slice(2);
assert(nodePath && rustPath, 'expected native-browser result logs');
const parse = (file) => {
  const line = readFileSync(file, 'utf8').split(/\r?\n/)
    .find((entry) => entry.startsWith('NATIVE_RTC_RESULT:'));
  assert(line, `${file}: missing browser result`);
  return JSON.parse(line.slice('NATIVE_RTC_RESULT:'.length));
};
const node = parse(nodePath), rust = parse(rustPath);
assert.equal(node.implementation, 'node');
assert.equal(rust.implementation, 'rust');
const identity = ({ type, planned, scheduled, skipped, actualSent,
  ordered, maxRetransmits, cancelStatus }) =>
  ({ type, planned, scheduled, skipped, actualSent,
    ordered, maxRetransmits, cancelStatus });
assert.deepEqual(identity(node), identity(rust));
for (const result of [node, rust]) {
  assert(result.acked <= result.browserDownFrames && result.browserDownFrames <= result.actualSent);
  if (Number.isInteger(result.clientUploadActualSent)) {
    assert(result.uploadReceived <= result.clientUploadActualSent);
  }
  assert.equal(result.acked, result.received);
  assert.equal(result.maxRetransmits, 0);
}
if (process.env.RTC_BROWSER_LOSS !== '1') {
  assert.equal(node.complete, true);
  assert.equal(rust.complete, true);
  assert.deepEqual(node.invalidReasons, rust.invalidReasons);
  assert.equal(node.uploadReceived, node.clientUploadActualSent);
  assert.equal(rust.uploadReceived, rust.clientUploadActualSent);
}
console.log(`NATIVE_RTC_COMPARE_OBSERVED: Node complete=${node.complete} `
  + `reasons=${JSON.stringify(node.invalidReasons)}, down ${node.acked}/${node.actualSent}, `
  + `up ${node.uploadReceived}/${node.clientUploadActualSent}, RTT P99 ${node.rttP99}; `
  + `Rust complete=${rust.complete} reasons=${JSON.stringify(rust.invalidReasons)}, `
  + `down ${rust.acked}/${rust.actualSent}, `
  + `up ${rust.uploadReceived}/${rust.clientUploadActualSent}, RTT P99 ${rust.rttP99}`);
