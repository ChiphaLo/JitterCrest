const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "server.js"), "utf8");
const chartSource = source.slice(source.indexOf("    const speedCharts = {"), source.indexOf("    function addPoint("));

function harness() {
  const containers = {
    downloadSpeedChart: { clientWidth: 480, innerHTML: "" },
    uploadSpeedChart: { clientWidth: 480, innerHTML: "" },
  };
  const frames = [];
  const sandbox = { $: (id) => containers[id], requestAnimationFrame: (callback) => { frames.push(callback); return frames.length; } };
  vm.runInNewContext(chartSource + "\nglobalThis.api = {speedCharts, addSpeedSample, finishSpeedChart, resetSpeedCharts, drawSpeedCharts};", sandbox);
  return { ...sandbox.api, containers, frames,
    flush: () => { for (const frame of frames.splice(0)) frame(); },
    html: (kind) => containers[kind + "SpeedChart"].innerHTML,
  };
}

test("both speed charts initially show time and Mbps axes without a fabricated result", () => {
  const chart = harness();
  chart.drawSpeedCharts();
  for (const kind of ["download", "upload"]) {
    assert.match(chart.html(kind), /Time \(s\)/);
    assert.match(chart.html(kind), />Mbps<\/text>/);
    assert.match(chart.html(kind), /data-sample-count="0"/);
    assert.doesNotMatch(chart.html(kind), /speed-result-line|speed-sample-line/);
  }
});

test("speed samples use real worker timestamps, not equally spaced sample indexes", () => {
  const chart = harness();
  for (const atMs of [100, 900, 1000]) chart.addSpeedSample("download", { value: 20, atMs });
  assert.equal(chart.frames.length, 1, "a burst schedules only one render frame");
  chart.flush();
  const trace = chart.html("download").match(/class="speed-sample-line" d="([^"]+)"/)[1];
  const xs = Array.from(trace.matchAll(/[ML]([\d.]+)/g), (match) => Number(match[1]));
  assert.ok(Math.abs((xs[1] - xs[0]) / (xs[2] - xs[1]) - 8) < 0.01);
  assert.match(chart.html("upload"), /data-sample-count="0"/);
  assert.doesNotMatch(chart.html("download"), /speed-result-line/);
});

test("each final horizontal dashed line uses the exact final result, not the sample average", () => {
  const chart = harness();
  for (const [kind, stable] of [["download", 123.456789], ["upload", 41.25]]) {
    chart.addSpeedSample(kind, { value: 20, atMs: 300 });
    chart.finishSpeedChart(kind, { stable, sampledStable: 999, ms: 10000 });
    const line = chart.html(kind).match(/<line class="speed-result-line"[^>]+>/)[0];
    const attr = (name) => line.match(new RegExp(name + '="([^"]+)"'))[1];
    assert.equal(attr("y1"), attr("y2"));
    assert.equal(attr("stroke-dasharray"), "6 4");
    assert.equal(Number(attr("data-result-mbps")), stable);
    assert.ok(Number(attr("y1")) >= 28 && Number(attr("y1")) <= 178);
    assert.ok(Number(attr("x2")) > Number(attr("x1")));
    assert.ok(chart.html(kind).includes(stable.toFixed(2) + " Mbps"));
  }
  chart.addSpeedSample("upload", { value: 60, atMs: 600 });
  chart.flush();
  assert.match(chart.html("download"), /data-result-mbps="123.456789"/);
});

test("charts reject invalid or non-monotonic samples but retain real zero speeds", () => {
  const chart = harness();
  for (const sample of [
    { value: NaN, atMs: 0 }, { value: Infinity, atMs: 0 }, { value: -1, atMs: 0 },
    { value: 10, atMs: null }, { value: 10, atMs: -1 }, { value: 10, atMs: Infinity },
  ]) chart.addSpeedSample("download", sample);
  chart.addSpeedSample("small-download", { value: 50, atMs: 100 });
  assert.equal(chart.speedCharts.download.points.length, 0);
  chart.addSpeedSample("download", { value: 0, atMs: 100 });
  chart.addSpeedSample("download", { value: 20, atMs: 100 });
  chart.addSpeedSample("download", { value: 20, atMs: 99 });
  assert.equal(chart.speedCharts.download.points.length, 1);
  chart.finishSpeedChart("download", { stable: 0, ms: 1000 });
  assert.match(chart.html("download"), /data-result-mbps="0"/);
  chart.finishSpeedChart("upload", { stable: NaN });
  assert.doesNotMatch(chart.html("upload"), /speed-result-line/);
});

test("reset clears both charts and queued frames cannot restore old results", () => {
  const chart = harness();
  chart.addSpeedSample("download", { value: 70, atMs: 9000 });
  chart.finishSpeedChart("download", { stable: 65, ms: 10000 });
  chart.addSpeedSample("upload", { value: 30, atMs: 300 });
  chart.resetSpeedCharts();
  chart.flush();
  for (const kind of ["download", "upload"]) {
    assert.match(chart.html(kind), /data-sample-count="0"/);
    assert.doesNotMatch(chart.html(kind), /speed-result-line|speed-sample-line/);
    assert.equal(chart.speedCharts[kind].durationMs, 0);
  }
});

test("sample storage stays bounded and resizing redraws at the actual card width", () => {
  const chart = harness();
  for (let i = 0; i < 600; i += 1) chart.addSpeedSample("download", { value: i, atMs: i * 300 });
  assert.equal(chart.speedCharts.download.points.length, 512);
  chart.containers.downloadSpeedChart.clientWidth = 280;
  chart.flush();
  assert.match(chart.html("download"), /viewBox="0 0 280 220"/);
  chart.containers.downloadSpeedChart.clientWidth = 600;
  chart.drawSpeedCharts();
  assert.match(chart.html("download"), /viewBox="0 0 600 220"/);
});

test("throughput worker adds a timeline timestamp without altering the raw speed", () => {
  const start = source.indexOf("      function publishThroughputSample(");
  const end = source.indexOf("      function percentile(", start);
  const messages = [];
  const sandbox = { self: { postMessage: (message) => messages.push(message) }, fmtMbps: (bytes, ms) => bytes * 8 / ms / 1000 };
  vm.runInNewContext(source.slice(start, end) + "\npublishThroughputSample([], 1000, 1300, 750000, 1, 300);", sandbox);
  assert.equal(messages[0].value, 20);
  assert.equal(messages[0].atMs, 300);
  assert.match(source, /concurrency, now - started\)/);
  assert.match(source, /sampleBytes, 1, now - measurementStartAt\)/);
  assert.match(source, /sampleBytes, 1, now - started\)/);
});
