// Guard the test harness's known Werift 0.23 zero-retransmit DCEP behavior.
// This is NOT the browser's DCEP behavior or a change to the Node reference.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { RTCPeerConnection } = require('werift');

test('Werift test client advertises reliable unordered for maxRetransmits: 0', async () => {
  const pc = new RTCPeerConnection({ iceServers: [] });
  try {
    const channel = pc.createDataChannel('test', { ordered: false, maxRetransmits: 0 });
    const queued = channel.sctp.dataChannelQueue.find(([candidate]) => candidate === channel);
    assert(queued, 'expected DCEP OPEN to be queued before SCTP establishment');
    const open = queued[2];
    assert.equal(open[0], 3);
    assert.equal(open[1], 0x80, 'Werift zero is treated as false: reliable unordered');
    assert.equal(open.readUInt32BE(4), 0);
    // The diagnostic in rtc-signaling.mjs changes only this byte to 0x81.
    assert.equal(open.length, 12 + 'test'.length);
  } finally {
    await pc.close();
  }
});
