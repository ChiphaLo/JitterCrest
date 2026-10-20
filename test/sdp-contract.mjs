import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { createServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const portServer = createServer();
await new Promise((resolve) => portServer.listen(0, '127.0.0.1', resolve));
process.env.PORT = String(portServer.address().port);
await new Promise((resolve) => portServer.close(resolve));
process.env.NODE_ENV = 'test';
const require = createRequire(import.meta.url);
const reference = require('../server.js');
const good = 'v=0\r\nm=application 9 UDP/DTLS/SCTP webrtc-data\r\n';
const candidate = (foundation, address, extra = '') =>
  `a=candidate:${foundation} 1 UDP 123 ${address} 9999 typ host${extra}\r\n`;
const cases = [
  '', good, good + candidate('1', '8.8.8.8'),
  good + candidate('1', '8.8.8.8') + candidate('2', '8.8.8.8'),
  good + candidate('1', '192.168.1.1') + candidate('2', 'peer.local'),
  good + candidate('1', '2606:4700:4700::1111', ' generation 0'),
  good + candidate('1', '2001:db8::1'),
  good + 'a=candidate:1 2 udp 1 8.8.8.8 9999 typ host\r\n',
  'x'.repeat(4097), '\n'.repeat(1024),
  good + candidate('1', '8.8.8.8').repeat(65),
];
const node = cases.map((value) => {
  try { return reference.sanitizeRemoteOfferSdp(value); }
  catch (error) { return { error: error.code }; }
});
reference.server.once('listening', () => reference.server.close());
const binary = process.platform === 'win32' ? 'sdp_contract.exe' : 'sdp_contract';
const rust = spawnSync(path.join(root, 'target', 'debug', 'examples', binary), [], {
  input: JSON.stringify(cases), cwd: root, encoding: 'utf8',
});
assert.equal(rust.status, 0, rust.stderr);
const actual = JSON.parse(rust.stdout);
for (let i = 0; i < cases.length; i++) {
  assert.deepEqual(actual[i], node[i], `SDP case ${i}`);
}
console.log(`Node/Rust SDP sanitizer: ${cases.length} matching cases`);
