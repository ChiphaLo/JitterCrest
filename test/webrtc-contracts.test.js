const assert = require("node:assert/strict");
const test = require("node:test");

const contracts = require("../webrtc_contracts");

test("60-second stability requests keep their full duration", () => {
  assert.equal(typeof contracts.normalizeMeasurementDurationMs, "function");
  assert.equal(contracts.normalizeMeasurementDurationMs(60_000, 60_000), 60_000);
});

test("WebRTC measurement durations are bounded without changing valid values", () => {
  assert.equal(typeof contracts.normalizeMeasurementDurationMs, "function");
  assert.equal(contracts.normalizeMeasurementDurationMs(undefined, 60_000), 5_000);
  assert.equal(contracts.normalizeMeasurementDurationMs(99, 60_000), 100);
  assert.equal(contracts.normalizeMeasurementDurationMs(75_000, 60_000), 60_000);
  assert.equal(contracts.normalizeMeasurementDurationMs(10_000.9, 60_000), 10_000);
});

test("large uploadComplete control messages round-trip through bounded fragments", () => {
  assert.equal(typeof contracts.serializeFragmentedControlMessage, "function");
  assert.equal(typeof contracts.createControlFragmentReassembler, "function");

  const message = {
    type: "uploadComplete",
    testId: "test",
    sessionId: "session",
    sessionHash: 123,
    testHash: 456,
    planned: 6000,
    scheduled: 5945,
    skipped: 55,
    actualSent: 5945,
    sentSeqs: Array.from({ length: 5945 }, (_, seq) => seq),
  };
  const fragments = contracts.serializeFragmentedControlMessage(message, {
    singleMessageBytes: 900,
    fragmentPayloadBytes: 512,
    maxFragmentCount: 512,
    fragmentId: "fixed-fragment-id",
  });

  assert.ok(fragments.length > 1);
  assert.ok(fragments.every((fragment) => Buffer.byteLength(fragment, "utf8") < 16_384));

  const reassembler = contracts.createControlFragmentReassembler({
    maxFragmentCount: 512,
    maxReassembledBytes: 2 * 1024 * 1024,
  });
  let reassembled = null;
  for (const fragment of fragments) {
    reassembled = reassembler.accept(JSON.parse(fragment)) || reassembled;
  }
  assert.deepEqual(reassembled, message);
});
