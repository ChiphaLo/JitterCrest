"""Deterministic browser-only worker regressions. Run with Python + Playwright."""
from pathlib import Path
from playwright.sync_api import sync_playwright

page_source = (Path(__file__).resolve().parent.parent / 'public/index.html').read_text(encoding='utf8')
helpers = page_source[page_source.index('      function createTimedUploadBody('):page_source.index('      async function uploadStream(')]
helpers += page_source[page_source.index('      function publishThroughputSample('):page_source.index('      function percentile(', page_source.index('      function publishThroughputSample('))]
helpers += page_source[page_source.index('      function createChunkPayload('):page_source.index('      async function uploadChunkLoop(')]
checks = r'''
return (async () => {
  function check(value, message) { if (!value) throw new Error(message); }
  const valid = {bytes: 1000, elapsedMs: 1000, measuredBytes: 900, measuredElapsedMs: 900};
  check(validateUploadAccounting(valid, 1000n, true) === valid, 'valid accounting changed');
  const bad = [null, {}, {...valid, bytes: 0}, {...valid, bytes: -1}, {...valid, bytes: 999},
    {...valid, bytes: 1.5}, {...valid, elapsedMs: 0}, {...valid, elapsedMs: Infinity},
    {...valid, measuredBytes: 0}, {...valid, measuredBytes: -1}, {...valid, measuredBytes: 1001},
    {...valid, measuredElapsedMs: 0}, {...valid, measuredElapsedMs: NaN}];
  for (const value of bad) {
    let rejected = false;
    try { validateUploadAccounting(value, 1000n, true); } catch { rejected = true; }
    check(rejected, 'invalid/zero server accounting was accepted');
  }
  let invalidJson = false;
  try { await readUploadAccounting({json: async () => {throw new Error('bad JSON');}}, 1000n); }
  catch (error) { invalidJson = error.message.includes('invalid accounting JSON'); }
  check(invalidJson, 'invalid JSON must not become a zero-speed result');
  let closed = 0;
  const transfer = createTimedUploadBody(1000, () => {}, () => closed++);
  await Promise.resolve();
  await Promise.resolve();
  check(!transfer.closed && transfer.bytes <= 262144n, 'upload queue is not backpressure bounded');
  clock.now = 1000;
  [...clock.timers.values()].find(item => item.ms === 1000).callback();
  check(transfer.closed && !transfer.cancelled && closed === 1, 'deadline must close without a new pull');
  const reader = transfer.body.getReader();
  while (!(await reader.read()).done) {}
  transfer.dispose();
  check(closed === 1, 'producer closed twice');
  const cancelled = createTimedUploadBody(1000, () => {}, () => closed++);
  await cancelled.body.cancel();
  check(cancelled.cancelled && cancelled.closed, 'stream cancel must release producer timer');
  clock.timers.clear();
  clock.now = 0;
  let bytes = 0n;
  const sampler = createUploadSampler(0, 500, () => bytes, 1);
  clock.now = 500;
  [...clock.timers.values()].find(item => item.ms === 500).callback();
  const tick = [...clock.timers.values()].find(item => item.interval).callback;
  bytes = 30000n;
  clock.now = 800;
  tick();
  check(Math.abs(sampler.samples[0] - 0.8) < 1e-9, 'raw sample rate changed');
  clock.now = 1100;
  tick();
  check(sampler.samples[1] === 0, 'a genuine stall must not be concealed');
  clock.now = 1110;
  sampler.stop();
  check(sampler.samples.length === 2, 'a tiny EOF interval created a false zero');
  clock.now = 2000;
  tick();
  check(sampler.samples.length === 2, 'sampling continued during server-accounting wait');
  const controller = new AbortController();
  let progressBytes = 0;
  const request = uploadChunkRequest('http://test/upload', {size: 1000}, controller.signal, delta => { progressBytes += delta; });
  const xhr = clock.requests.at(-1);
  xhr.upload.onprogress({loaded: 400});
  check(progressBytes === 400, 'chunk progress must not wait for a whole-file ACK');
  xhr.upload.onprogress({loaded: 700});
  xhr.status = 200;
  xhr.responseText = JSON.stringify(valid);
  xhr.onload();
  await request;
  check(progressBytes === 1000, 'progress/ACK double-counted bytes');
  check(xhr.onload === null && xhr.upload.onprogress === null, 'XHR listeners leaked');
  const failure = uploadChunkRequest('http://test/upload', {size: 1000}, controller.signal, () => {});
  const badXhr = clock.requests.at(-1);
  badXhr.status = 200;
  badXhr.responseText = JSON.stringify({...valid, bytes: 0});
  badXhr.onload();
  let rejectedAck = false;
  try { await failure; } catch { rejectedAck = true; }
  check(rejectedAck, 'XHR zero ACK became a zero-speed success');
  const abortController = new AbortController();
  const aborting = uploadChunkRequest('http://test/upload', {size: 1000}, abortController.signal, () => {});
  abortController.abort();
  let rejectedAbort = false;
  try { await aborting; } catch (error) { rejectedAbort = error.name === 'AbortError'; }
  check(rejectedAbort, 'XHR did not cancel with its owning upload');
  return {invalidAccountingCases: bad.length, invalidJsonRejected: true, boundedProducer: true,
    deadlineWithoutPull: true, cancellation: true, realZeroPreserved: true, noEofZero: true, noDrainSamples: true,
    chunkProgressBeforeAck: true, noProgressDoubleCount: true, zeroChunkAckRejected: true, chunkCancellation: true};
})();
'''
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    # Use a separate blank test document; production CSP stays unchanged.
    result = page.evaluate(r'''({helpers, checks}) => {
      const clock = {now: 0, timers: new Map(), id: 0, requests: []};
      class MockXHR {
        constructor() { this.upload = {}; clock.requests.push(this); }
        open() {}
        send() {}
        abort() { if (this.onabort) this.onabort(); }
      }
      const schedule = (callback, ms, interval = false) => {
        const id = ++clock.id; clock.timers.set(id, {callback, ms, interval}); return id;
      };
      const run = new Function('clock', 'performance', 'self', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'XMLHttpRequest',
        'function fmtMbps(bytes, ms) { return bytes * 8 / ms / 1000; }\n' + helpers + '\n' + checks);
      return run(clock, {now: () => clock.now}, {postMessage() {}},
        (callback, ms) => schedule(callback, ms), id => clock.timers.delete(id),
        (callback, ms) => schedule(callback, ms, true), id => clock.timers.delete(id), MockXHR);
    }''', {'helpers': helpers, 'checks': checks})
    print(result)
    browser.close()
