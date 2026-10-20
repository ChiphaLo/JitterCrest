// Run the actual Node budget helpers with an explicit clock to pin wall-second
// and refund-expiry semantics. The corresponding Rust cases live in limits.rs.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = readFileSync(path.join(root, 'server.js'), 'utf8');
const start = source.indexOf('function reserveRollingByteBudget(');
const end = source.indexOf('function consumeRollingByteBudget(', start);
assert.ok(start >= 0 && end > start, 'locate the original Node budget helpers');
const sandbox = { Map, Math, Number, Date };
vm.createContext(sandbox);
vm.runInContext(`const BYTE_BUDGET_WINDOW_MS = 60000; const RATE_MAP_MAX = 100;\n${source.slice(start, end)}\nthis.reserve = reserveRollingByteBudget; this.refund = refundRollingByteBudget;`, sandbox);

const map = new Map();
const first = sandbox.reserve(map, 'ip', 80, 100, 1999);
const second = sandbox.reserve(map, 'ip', 20, 100, 2000);
assert.equal(first.bucketAt, 1000);
assert.equal(second.bucketAt, 2000);
assert.equal(sandbox.refund(map, first, 30, 60999), 30);
assert.equal(map.get('ip').total, 70);
assert.equal(sandbox.refund(map, first, 50, 61000), 0);
assert.equal(map.get('ip').total, 70);
assert.ok(sandbox.reserve(map, 'ip', 80, 100, 61000));
assert.deepEqual([...map.get('ip').buckets], [[2000, 20], [61000, 80]]);
console.log('Node budget buckets/refund match the Rust boundary fixtures');
