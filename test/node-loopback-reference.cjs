// In-memory test-only adaptation of the archived Node reference. The original
// server.js and its production ICE policy remain untouched. Never run this
// launcher on a non-loopback listener.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const os = require('node:os');

assert.equal(process.env.SPEEDTEST_HOST, '127.0.0.1');
const localIp = process.env.SPEEDTEST_RTC_LOCAL_TEST_IP;
assert(localIp && Object.values(os.networkInterfaces()).flat().some((entry) =>
  entry && entry.family === 'IPv4' && !entry.internal && entry.address === localIp));
const filename = path.resolve(__dirname, '..', 'server.js');
const original = fs.readFileSync(filename, 'utf8');
const policy = 'return canonical !== null && !isPrivateIp(canonical);';
assert.equal(original.split(policy).length, 2, 'Node ICE policy changed; review this test harness');
let source = original.replace(policy,
  'return canonical !== null && (canonical === process.env.SPEEDTEST_RTC_LOCAL_TEST_IP || !isPrivateIp(canonical));');
if (process.env.RTC_REFERENCE_REPORT_CHANNEL === '1') {
  const marker = 'pc.onDataChannel.subscribe((channel) => {';
  assert.equal(source.split(marker).length, 2, 'Node DataChannel handler changed');
  source = source.replace(marker, `${marker}
    if (channel.label === 'test') console.error('RTC_NODE_CHANNEL_POLICY:' + JSON.stringify({
      ordered: channel.ordered, maxRetransmits: channel.maxRetransmits }));`);
}
const reference = new Module(filename, module);
reference.filename = filename;
reference.paths = Module._nodeModulePaths(path.dirname(filename));
reference._compile(source, filename);
