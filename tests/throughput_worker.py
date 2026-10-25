"""Deterministic browser-only worker regressions. Run with Python + Playwright."""
from pathlib import Path
from playwright.sync_api import sync_playwright

page_source = (Path(__file__).resolve().parent.parent / 'public/index.html').read_text(encoding='utf8')
helpers = page_source[page_source.index('      function createTimedUploadBody('):page_source.index('      async function uploadStream(')]
helpers += page_source[page_source.index('      function publishThroughputSample('):page_source.index('      function percentile(', page_source.index('      function publishThroughputSample('))]
helpers += page_source[page_source.index('      function createChunkPayload('):page_source.index('      async function uploadChunkLoop(')]
helpers += page_source[page_source.index('      function buildUrl('):page_source.index('      async function downloadLane(')]
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
  const receipts = createServerUploadSampler(1);
  const snap = (bytes, ms, done=false) => ({bytes,measuredBytes:bytes,elapsedMs:ms,measuredElapsedMs:ms,done,failed:false,expired:false});
  receipts.accept(snap(1000000,100));
  receipts.accept(snap(2000000,200));
  check(receipts.samples[0] === 80 && receipts.samples[1] === 80, 'server receipts depended on the browser queue counter');
  receipts.accept(snap(2000000,300));
  check(receipts.samples[2] === 0, 'a genuine server receipt stall was hidden');
  receipts.accept(snap(2000000,300,true));
  check(receipts.samples.length === 3, 'completed telemetry manufactured a zero EOF sample');
  let counterRejected=false;
  try {receipts.accept(snap(1,400));} catch {counterRejected=true;}
  check(counterRejected,'receipt counters regressed silently');
  receipts.stop();receipts.accept(snap(5000000,500));
  check(receipts.samples.length === 3,'receipt sampling survived cancellation');
  const stallHistory=[];
  for(let i=0;i<7;i++) publishThroughputSample(stallHistory,i*100,(i+1)*100,i===0?1000000:0,1,(i+1)*100);
  check(clock.messages.at(-1).displayValue===0,'smoothing hid a sustained genuine stall');
  const warmedReceipts = createServerUploadSampler(1);
  warmedReceipts.accept({...snap(1000000,100),elapsedMs:2100});
  check(clock.messages.at(-1).atMs === 100, 'upload chart included the two-second warmup offset');
  check(warmedReceipts.sampleDetails[0].atMs === 100 && warmedReceipts.sampleDetails[0].transferAtMs === 2100,
    'measurement and transfer clocks were confused');
  check(warmedReceipts.samples[0] === 80, 'normalizing the chart changed raw speed');
  warmedReceipts.stop();
  const originalFetch = globalThis.fetch;
  try {
    const rows = Array.from({length:500},(_,i)=>({bytes:(i+1)*1000,measuredBytes:(i+1)*1000,
      elapsedMs:(i+1)*100,measuredElapsedMs:(i+1)*100,done:i===499,failed:false,expired:false}));
    globalThis.fetch = async () => new Response(rows.map(row=>JSON.stringify(row)).join('\n')+'\n');
    let accepted = 0;
    await readUploadProgress('https://test/',{id:'a'.repeat(32)},new AbortController().signal,()=>accepted++);
    check(accepted===500,'batched receipt frames were rejected or dropped');
    globalThis.fetch = async () => new Response(Array.from({length:2049},()=>JSON.stringify(rows[0])).join('\n')+'\n');
    let tooMany=false;
    try {await readUploadProgress('https://test/',{id:'a'.repeat(32)},new AbortController().signal,()=>{});}
    catch {tooMany=true;}
    check(tooMany,'receipt stream sample count was unbounded');
    globalThis.fetch = async () => new Response('x'.repeat(9000));
    let tooLarge=false;
    try {await readUploadProgress('https://test/',{id:'a'.repeat(32)},new AbortController().signal,()=>{});}
    catch {tooLarge=true;}
    check(tooLarge,'unbounded incomplete receipt frame was accepted');
    globalThis.fetch = async () => new Response(JSON.stringify(rows[0])+'\n');
    let truncated=false;
    try {await readUploadProgress('https://test/',{id:'a'.repeat(32)},new AbortController().signal,()=>{});}
    catch {truncated=true;}
    check(truncated,'truncated receipt stream became successful completion');
  } finally {globalThis.fetch=originalFetch;}
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
  return {boundedReceiptCount:true,sustainedZeroVisible:true,batchedReceipts:true,boundedReceiptFrames:true,truncatedReceiptsRejected:true,serverReceiptQueueIndependence:true, serverRealZeroPreserved:true, receiptRegressionRejected:true, invalidAccountingCases: bad.length, invalidJsonRejected: true, boundedProducer: true,
    deadlineWithoutPull: true, cancellation: true, realZeroPreserved: true, noEofZero: true, noDrainSamples: true,
    chunkProgressBeforeAck: true, noProgressDoubleCount: true, zeroChunkAckRejected: true, chunkCancellation: true};
})();
'''
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    # Use a separate blank test document; production CSP stays unchanged.
    result = page.evaluate(r'''({helpers, checks}) => {
      const clock = {now: 0, timers: new Map(), id: 0, requests: [], messages: []};
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
      return run(clock, {now: () => clock.now}, {postMessage(message) {clock.messages.push(message);}},
        (callback, ms) => schedule(callback, ms), id => clock.timers.delete(id),
        (callback, ms) => schedule(callback, ms, true), id => clock.timers.delete(id), MockXHR);
    }''', {'helpers': helpers, 'checks': checks})
    print(result)
    browser.close()
