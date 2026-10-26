-- Same native wrk client and cached requests for both independent servers.
-- Synthetic benchmark-range identities are trusted ONLY on the loopback test
-- listener. Real production rate/budget code stays enabled and unchanged.
local next_worker = 0
function setup(thread)
  thread:set("worker_id", next_worker)
  next_worker = next_worker + 1
end

function init(args)
  local method, path = args[1], args[2]
  local bytes, variants = tonumber(args[3]), tonumber(args[4])
  cached, index = {}, 0
  local body = bytes > 0 and string.rep("x", bytes) or nil
  for i = 1, variants do
    local identity = worker_id * variants + i - 1
    local ip = "198.18." .. math.floor(identity / 254) .. "." .. (identity % 254 + 1)
    local headers = { ["X-Forwarded-For"] = ip, ["X-Real-IP"] = ip,
                      ["Accept-Encoding"] = "identity", ["Content-Type"] = "application/octet-stream" }
    cached[i] = wrk.format(method == "MIX" and (i % 2 == 0 and "POST" or "GET") or method,
      method == "MIX" and (i % 2 == 0 and "/upload" or "/download?size=" .. bytes) or path,
      headers, (method == "POST" or (method == "MIX" and i % 2 == 0)) and body or nil)
  end
end

function request()
  index = index % #cached + 1
  return cached[index]
end

function done(summary, latency, requests)
  local e = summary.errors
  io.write(string.format('BENCHMARK_JSON:{"requests":%d,"response_bytes":%d,"duration_us":%d,' ..
    '"p50_ms":%.6f,"p95_ms":%.6f,"p99_ms":%.6f,' ..
    '"errors":{"connect":%d,"read":%d,"write":%d,"status":%d,"timeout":%d}}\n',
    summary.requests, summary.bytes, summary.duration,
    latency:percentile(50)/1000, latency:percentile(95)/1000, latency:percentile(99)/1000,
    e.connect, e.read, e.write, e.status, e.timeout))
end
