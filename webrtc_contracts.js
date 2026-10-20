(function (root, factory) {
  "use strict";
  const contracts = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = contracts;
  }
  if (root) {
    root.SpeedtestContracts = contracts;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const CONTRACT_VERSION = 1;

  function parseBoundedNumber(raw, options) {
    const { name, defaultValue, min, max } = options;
    const value = raw === undefined || raw === null || raw === ""
      ? defaultValue
      : Number(raw);
    if (!Number.isFinite(value) || value < min || value > max) {
      throw new Error(`${name} must be a finite number between ${min} and ${max}`);
    }
    return value;
  }

  function parseDensity(raw, options) {
    return parseBoundedNumber(raw, options);
  }

  function parseRate(raw, options) {
    return parseBoundedNumber(raw, options);
  }

  function normalizeMeasurementDurationMs(raw, maxDurationMs) {
    if (!Number.isSafeInteger(maxDurationMs) || maxDurationMs < 100) {
      throw new Error("maxDurationMs must be an integer of at least 100");
    }
    const parsed = raw === undefined || raw === null || raw === ""
      ? 5000
      : Number(raw);
    const durationMs = Number.isFinite(parsed) ? Math.floor(parsed) : 5000;
    return Math.max(100, Math.min(durationMs, maxDurationMs));
  }

  function requiredSampleCount(planned, minimumDensity) {
    if (!Number.isInteger(planned) || planned <= 0) {
      throw new Error("planned must be a positive integer");
    }
    return Math.ceil(planned * minimumDensity);
  }

  function sampleDensity(actualSent, planned) {
    return planned > 0 ? actualSent / planned : null;
  }

  function createCancellableScheduler(options) {
    const {
      planned,
      intervalMs,
      send,
      now = () => performance.now(),
      setTimer = (fn, delay) => setTimeout(fn, delay),
      clearTimer = (handle) => clearTimeout(handle),
    } = options;
    let scheduled = 0;
    let actualSent = 0;
    let skipped = 0;
    let nextSlot = 0;
    let timer = null;
    let terminal = false;
    let cancelled = false;
    let cancelReason = null;
    const sentTimes = [];
    const sentSlots = [];
    const invalidReasons = [];
    const startedAt = now();
    let resolveDone;
    const done = new Promise((resolve) => { resolveDone = resolve; });

    function snapshot() {
      return Object.freeze({
        planned,
        scheduled,
        actualSent,
        skipped,
        sentTimes: sentTimes.slice(),
        sentSlots: sentSlots.slice(),
        cancelled,
        cancelReason,
        invalidReasons: invalidReasons.slice(),
      });
    }

    function finish() {
      if (terminal) return;
      terminal = true;
      timer = null;
      resolveDone(snapshot());
    }

    function skipRemaining() {
      skipped += planned - scheduled - skipped;
    }

    function cancel(reason) {
      if (terminal) return done;
      cancelled = true;
      cancelReason = String(reason || "cancelled");
      if (timer !== null) clearTimer(timer);
      skipRemaining();
      finish();
      return done;
    }

    function tick() {
      if (terminal) return;
      const elapsed = Math.max(0, now() - startedAt);
      const due = Math.min(planned - 1, Math.floor(elapsed / intervalMs));
      while (nextSlot < due) {
        skipped += 1;
        nextSlot += 1;
      }
      if (nextSlot < planned) {
        const slot = nextSlot++;
        scheduled += 1;
        try {
          const sent = send(slot, startedAt + slot * intervalMs);
          if (sent === true) {
            actualSent += 1;
            sentTimes.push(now());
            sentSlots.push(slot);
          } else if (sent !== false) {
            invalidReasons.push({code: "SEND_CALLBACK_RESULT_INVALID", slot});
            skipRemaining();
            finish();
            return;
          }
        } catch (error) {
          invalidReasons.push({
            code: "SEND_CALLBACK_THROW",
            slot,
            errorClass: error && error.name ? error.name : "Error",
          });
          skipRemaining();
          finish();
          return;
        }
      }
      if (scheduled + skipped >= planned) {
        finish();
        return;
      }
      if (terminal) return;
      timer = setTimer(tick, Math.max(0, startedAt + nextSlot * intervalMs - now()));
    }

    timer = setTimer(tick, 0);
    return Object.freeze({
      cancel,
      done,
      getPlanned: () => planned,
      getScheduled: () => scheduled,
      getActualSent: () => actualSent,
      getSkipped: () => skipped,
      getSentTimes: () => sentTimes.slice(),
      getSentSlots: () => sentSlots.slice(),
    });
  }

  function validateAppProbeResult(result) {
    const hasRtt = Boolean(
      result
      && result.rttMs !== null
      && result.rttMs !== undefined
      && result.rttMs !== "",
    );
    const rtt = hasRtt ? Number(result.rttMs) : NaN;
    const valid = Boolean(result && result.sent > 0 && result.acked > 0 && Number.isFinite(rtt) && rtt >= 0);
    return {valid, invalidReasons: valid ? [] : ["APP_PROBE_INVALID"]};
  }

  function validateSchedulerAccounting(label, result, invalidReasons) {
    const initialReasonCount = invalidReasons.length;
    if (!result || result.complete !== true) {
      invalidReasons.push(`${label.toUpperCase()}_INCOMPLETE`);
      return false;
    }
    const fields = ["planned", "scheduled", "skipped", "actualSent"];
    for (const field of fields) {
      if (!Number.isInteger(result[field]) || result[field] < 0) {
        invalidReasons.push(`${label.toUpperCase()}_${field.toUpperCase()}_INVALID`);
      }
    }
    if (invalidReasons.length !== initialReasonCount) return false;
    if (result.planned <= 0) invalidReasons.push(`${label.toUpperCase()}_PLANNED_INVALID`);
    if (result.scheduled + result.skipped !== result.planned) {
      invalidReasons.push(`${label.toUpperCase()}_SCHEDULER_ACCOUNTING`);
    }
    if (result.actualSent > result.scheduled || result.scheduled > result.planned) {
      invalidReasons.push(`${label.toUpperCase()}_SCHEDULER_BOUNDS`);
    }
    return invalidReasons.length === initialReasonCount;
  }

  function finiteSamples(samples) {
    return Array.isArray(samples)
      && samples.every((value) => {
        const numeric = value && typeof value === "object" ? value.rtt : value;
        return Number.isFinite(numeric) && numeric >= 0;
      });
  }

  function validateDirectionalCounts(upload, download, invalidReasons) {
    if (!Number.isInteger(upload.uploadReceived)
        || !Number.isInteger(upload.uploadAcked)
        || upload.uploadAcked < 0
        || upload.uploadReceived < upload.uploadAcked
        || upload.uploadReceived > upload.actualSent) {
      invalidReasons.push("UPLOAD_RECEIPT_ACCOUNTING");
    }
    if (!Number.isInteger(download.received)
        || !Number.isInteger(download.acked)
        || download.acked < 0
        || download.received < download.acked
        || download.received > download.actualSent) {
      invalidReasons.push("DOWNLOAD_RECEIPT_ACCOUNTING");
    }
  }

  function validatorOptions(options, label, invalidReasons) {
    if (options === null || options === undefined) return {};
    if (typeof options !== "object") {
      invalidReasons.push(`${label}_OPTIONS_INVALID`);
      return {};
    }
    return options;
  }

  function boundedValidationOption(options, field, defaultValue, min, max, code, invalidReasons) {
    if (options[field] === undefined) return defaultValue;
    let value;
    try {
      value = Number(options[field]);
    } catch {
      invalidReasons.push(code);
      return defaultValue;
    }
    if (!Number.isFinite(value) || value < min || value > max) {
      invalidReasons.push(code);
      return defaultValue;
    }
    return value;
  }

  function positiveIntegerValidationOption(options, field, defaultValue, code, invalidReasons) {
    const value = options[field];
    if (value === undefined) return defaultValue;
    if (!Number.isInteger(value) || value <= 0) {
      invalidReasons.push(code);
      return defaultValue;
    }
    return value;
  }

  function validMeasurementSamples(measurement, ackField, minimumSamples) {
    if (!measurement || !finiteSamples(measurement.samples)) return false;
    const acked = measurement[ackField];
    return Number.isInteger(acked)
      && acked >= 0
      && measurement.samples.length >= minimumSamples
      && measurement.samples.length <= acked;
  }

  const MEASUREMENT_ID_FIELDS = Object.freeze([
    "sessionId", "sessionHash", "testId", "testHash",
  ]);

  function encodeUtf8(value) {
    return new TextEncoder().encode(String(value));
  }

  function decodeUtf8(value) {
    return new TextDecoder().decode(value);
  }

  function encodeBase64(bytes) {
    if (typeof Buffer !== "undefined") return Buffer.from(bytes).toString("base64");
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    }
    return btoa(binary);
  }

  function decodeBase64(value) {
    if (typeof Buffer !== "undefined") return new Uint8Array(Buffer.from(value, "base64"));
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function positiveIntegerOption(value, name) {
    if (!Number.isSafeInteger(value) || value < 1) {
      throw new Error(`${name} must be a positive integer`);
    }
    return value;
  }

  function serializeFragmentedControlMessage(message, options) {
    const singleMessageBytes = positiveIntegerOption(options.singleMessageBytes, "singleMessageBytes");
    const fragmentPayloadBytes = positiveIntegerOption(options.fragmentPayloadBytes, "fragmentPayloadBytes");
    const maxFragmentCount = positiveIntegerOption(options.maxFragmentCount, "maxFragmentCount");
    const serialized = JSON.stringify(message);
    const payload = encodeUtf8(serialized);
    if (payload.byteLength <= singleMessageBytes) return [serialized];
    if (!message || typeof message.type !== "string" || !message.type) {
      throw new Error("fragmented control messages require a type");
    }
    if (typeof options.fragmentId !== "string" || !options.fragmentId) {
      throw new Error("fragmentId is required for fragmented control messages");
    }
    const count = Math.ceil(payload.byteLength / fragmentPayloadBytes);
    if (count > maxFragmentCount) throw new Error("control message exceeds the fragment limit");
    const identity = {};
    for (const field of MEASUREMENT_ID_FIELDS) identity[field] = message[field];
    const fragments = [];
    for (let index = 0; index < count; index += 1) {
      const start = index * fragmentPayloadBytes;
      fragments.push(JSON.stringify({
        type: "controlFragment",
        messageType: message.type,
        fragmentId: options.fragmentId,
        index,
        count,
        encoding: "base64",
        ...identity,
        data: encodeBase64(payload.subarray(start, start + fragmentPayloadBytes)),
      }));
    }
    return fragments;
  }

  function createControlFragmentReassembler(options) {
    const maxFragmentCount = positiveIntegerOption(options.maxFragmentCount, "maxFragmentCount");
    const maxReassembledBytes = positiveIntegerOption(options.maxReassembledBytes, "maxReassembledBytes");
    const maxConcurrentAssemblies = positiveIntegerOption(options.maxConcurrentAssemblies || 4, "maxConcurrentAssemblies");
    const assemblies = new Map();

    function reject(message) {
      throw new Error(message);
    }

    function accept(fragment) {
      if (!fragment || fragment.type !== "controlFragment"
          || typeof fragment.messageType !== "string" || !fragment.messageType
          || typeof fragment.fragmentId !== "string" || !fragment.fragmentId
          || !Number.isInteger(fragment.index) || !Number.isInteger(fragment.count)
          || fragment.count < 1 || fragment.count > maxFragmentCount
          || fragment.index < 0 || fragment.index >= fragment.count
          || fragment.encoding !== "base64" || typeof fragment.data !== "string") {
        reject("invalid control fragment");
      }
      let bytes;
      try {
        bytes = decodeBase64(fragment.data);
      } catch {
        reject("invalid control fragment encoding");
      }
      let assembly = assemblies.get(fragment.fragmentId);
      if (!assembly) {
        if (assemblies.size >= maxConcurrentAssemblies) reject("too many control fragment assemblies");
        const identity = {};
        for (const field of MEASUREMENT_ID_FIELDS) identity[field] = fragment[field];
        assembly = {
          messageType: fragment.messageType,
          count: fragment.count,
          identity,
          parts: new Array(fragment.count),
          received: 0,
          totalBytes: 0,
        };
        assemblies.set(fragment.fragmentId, assembly);
      } else {
        if (assembly.messageType !== fragment.messageType || assembly.count !== fragment.count) {
          reject("inconsistent control fragment metadata");
        }
        for (const field of MEASUREMENT_ID_FIELDS) {
          if (assembly.identity[field] !== fragment[field]) reject("inconsistent control fragment identity");
        }
      }
      if (assembly.parts[fragment.index]) return null;
      assembly.parts[fragment.index] = bytes;
      assembly.received += 1;
      assembly.totalBytes += bytes.byteLength;
      if (assembly.totalBytes > maxReassembledBytes) {
        assemblies.delete(fragment.fragmentId);
        reject("reassembled control message exceeds the byte limit");
      }
      if (assembly.received !== assembly.count) return null;
      const payload = new Uint8Array(assembly.totalBytes);
      let offset = 0;
      for (const part of assembly.parts) {
        if (!part) reject("control fragment missing");
        payload.set(part, offset);
        offset += part.byteLength;
      }
      assemblies.delete(fragment.fragmentId);
      let message;
      try {
        message = JSON.parse(decodeUtf8(payload));
      } catch {
        reject("invalid reassembled control message");
      }
      if (!message || message.type !== assembly.messageType) reject("reassembled control message type mismatch");
      for (const field of MEASUREMENT_ID_FIELDS) {
        if (message[field] !== assembly.identity[field]) reject("reassembled control message identity mismatch");
      }
      return message;
    }

    return Object.freeze({ accept, clear: () => assemblies.clear() });
  }

  function validateMatchingMeasurementIds(upload, download, invalidReasons) {
    for (const field of MEASUREMENT_ID_FIELDS) {
      if (!upload || !download || upload[field] === undefined
          || download[field] === undefined || upload[field] !== download[field]) {
        invalidReasons.push(`MEASUREMENT_${field.toUpperCase()}_MISMATCH`);
      }
    }
  }

  function validateSequenceSet(label, values, expectedCount, planned, invalidReasons) {
    if (!Array.isArray(values) || !Number.isInteger(expectedCount)
        || values.length !== expectedCount) {
      invalidReasons.push(`${label}_COUNT_MISMATCH`);
      return null;
    }
    const unique = new Set();
    for (const value of values) {
      if (!Number.isInteger(value) || value < 0 || value >= planned || unique.has(value)) {
        invalidReasons.push(`${label}_INVALID`);
        return null;
      }
      unique.add(value);
    }
    return unique;
  }

  function validatePairedEvidence(upload, download, invalidReasons) {
    validateMatchingMeasurementIds(upload, download, invalidReasons);
    if (!download || download.downResultReceived !== true || download.final !== true) {
      invalidReasons.push("FINAL_DOWN_RESULT_MISSING");
    }
    const sent = validateSequenceSet(
      "UPLOAD_SENT_SEQS",
      upload && upload.sentSeqs,
      upload && upload.actualSent,
      upload && upload.planned,
      invalidReasons,
    );
    const received = validateSequenceSet(
      "UPLOAD_RECEIVED_SEQS",
      download && download.uploadReceivedSeqs,
      download && download.uploadReceived,
      upload && upload.planned,
      invalidReasons,
    );
    if (sent && received) {
      for (const seq of received) {
        if (!sent.has(seq)) invalidReasons.push("UPLOAD_RECEIVED_NOT_SENT");
      }
    }
    if (!upload || !download || upload.uploadReceived !== download.uploadReceived
        || upload.planned !== download.planned
        || upload.actualSent !== download.clientUploadActualSent
        || upload.scheduled !== download.clientUploadScheduled
        || upload.skipped !== download.clientUploadSkipped) {
      invalidReasons.push("UPLOAD_COMPLETE_ECHO_MISMATCH");
    }
  }

  function validateRttMeasurement(upload, download, options = {}) {
    const invalidReasons = [];
    const normalizedOptions = validatorOptions(options, "RTT", invalidReasons);
    const minimumDensity = boundedValidationOption(
      normalizedOptions,
      "minimumDensity",
      0.90,
      0,
      1,
      "RTT_MINIMUM_DENSITY_INVALID",
      invalidReasons,
    );
    const minimumSamples = positiveIntegerValidationOption(
      normalizedOptions,
      "minimumSamples",
      10,
      "RTT_MINIMUM_SAMPLES_INVALID",
      invalidReasons,
    );
    validateSchedulerAccounting("upload", upload, invalidReasons);
    validateSchedulerAccounting("download", download, invalidReasons);
    if (!invalidReasons.length) {
      validateDirectionalCounts(upload, download, invalidReasons);
      if (upload.actualSent < requiredSampleCount(upload.planned, minimumDensity)) {
        invalidReasons.push("UPLOAD_DENSITY");
      }
      if (download.actualSent < requiredSampleCount(download.planned, minimumDensity)) {
        invalidReasons.push("DOWNLOAD_DENSITY");
      }
    }
    validatePairedEvidence(upload, download, invalidReasons);
    if (!validMeasurementSamples(upload, "uploadAcked", minimumSamples)) {
      invalidReasons.push("UPLOAD_RTT_SAMPLES");
    }
    if (!validMeasurementSamples(download, "acked", minimumSamples)) {
      invalidReasons.push("DOWNLOAD_RTT_SAMPLES");
    }
    return Object.freeze({
      valid: invalidReasons.length === 0,
      invalidReasons,
      minimumDensity,
      minimumSamples,
      uploadRequiredSent: upload && Number.isInteger(upload.planned) && upload.planned > 0
        ? requiredSampleCount(upload.planned, minimumDensity)
        : null,
      downloadRequiredSent: download && Number.isInteger(download.planned) && download.planned > 0
        ? requiredSampleCount(download.planned, minimumDensity)
        : null,
    });
  }

  function validateStabilityMeasurement(upload, download, options = {}) {
    const invalidReasons = [];
    const normalizedOptions = validatorOptions(options, "STABILITY", invalidReasons);
    const minimumDensity = boundedValidationOption(
      normalizedOptions,
      "minimumDensity",
      0.99,
      0,
      1,
      "STABILITY_MINIMUM_DENSITY_INVALID",
      invalidReasons,
    );
    validateSchedulerAccounting("upload", upload, invalidReasons);
    validateSchedulerAccounting("download", download, invalidReasons);
    if (!invalidReasons.length) {
      validateDirectionalCounts(upload, download, invalidReasons);
      if (upload.actualSent < requiredSampleCount(upload.planned, minimumDensity)) {
        invalidReasons.push("UPLOAD_DENSITY");
      }
      if (download.actualSent < requiredSampleCount(download.planned, minimumDensity)) {
        invalidReasons.push("DOWNLOAD_DENSITY");
      }
    }
    validatePairedEvidence(upload, download, invalidReasons);
    const accountingValid = invalidReasons.length === 0;
    const uploadNetworkLost = accountingValid ? upload.actualSent - upload.uploadReceived : null;
    const uploadAckReturnLost = accountingValid ? upload.uploadReceived - upload.uploadAcked : null;
    const downloadNetworkLost = accountingValid ? download.actualSent - download.received : null;
    const downloadAckReturnLost = accountingValid ? download.received - download.acked : null;
    return Object.freeze({
      valid: invalidReasons.length === 0,
      invalidReasons,
      minimumSampleDensity: minimumDensity,
      uploadRequiredSent: upload && Number.isInteger(upload.planned) && upload.planned > 0
        ? requiredSampleCount(upload.planned, minimumDensity)
        : null,
      downloadRequiredSent: download && Number.isInteger(download.planned) && download.planned > 0
        ? requiredSampleCount(download.planned, minimumDensity)
        : null,
      uploadDensity: accountingValid ? sampleDensity(upload.actualSent, upload.planned) : null,
      downloadDensity: accountingValid ? sampleDensity(download.actualSent, download.planned) : null,
      uploadNetworkLost,
      uploadAckReturnLost,
      downloadNetworkLost,
      downloadAckReturnLost,
      uploadLossRate: accountingValid && upload.actualSent > 0
        ? uploadNetworkLost / upload.actualSent
        : null,
      downloadLossRate: accountingValid && download.actualSent > 0
        ? downloadNetworkLost / download.actualSent
        : null,
    });
  }

  function normalizeCandidatePair(report, selectedPairId) {
    let records;
    try {
      records = Array.from(report || []);
    } catch {
      return null;
    }
    const pairs = records.filter((item) => item && item.type === "candidate-pair");
    return pairs.find((item) => item.id === selectedPairId)
      || pairs.find((item) => item.selected === true)
      || pairs.find((item) => item.nominated === true && item.state === "succeeded")
      || null;
  }

  return Object.freeze({
    CONTRACT_VERSION,
    createControlFragmentReassembler,
    createCancellableScheduler,
    requiredSampleCount,
    sampleDensity,
    serializeFragmentedControlMessage,
    validateAppProbeResult,
    validateRttMeasurement,
    validateStabilityMeasurement,
    normalizeCandidatePair,
    normalizeMeasurementDurationMs,
    parseDensity,
    parseRate,
  });
});
