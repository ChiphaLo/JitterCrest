"""Bounded HTTPS load client, not a backend or production deployment tool.

Targets must be authorized isolated services. Resource snapshots are supplied by
an independent operator callback; this client cannot inspect a remote process.
Results include client/network/proxy effects, not language-only performance.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import threading
import time


class PayloadBudget:
    """Aggregate attempted application payload, including warmup and failures."""
    def __init__(self, limit):
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError('positive byte budget required')
        self.limit, self.used = limit, 0
        self.lock = threading.Lock()

    def reserve(self, size):
        with self.lock:
            if self.used + size > self.limit:
                raise RuntimeError('aggregate payload budget exhausted; run incomplete')
            self.used += size


def percentile(values, fraction):
    if not values or not 0 < fraction <= 1:
        raise ValueError('nonempty values and fraction in (0,1] required')
    return sorted(values)[math.ceil(len(values)*fraction)-1]


def run_workload(target, method, size, concurrency, duration, warmup, budget,
                 observe=lambda phase: None):
    """Keepalive closed-loop requests for a common measured admission window.

    Each worker drains its last admitted request. Drain time remains in the
    throughput denominator. P50/95/99 are observed request completion latencies,
    not coordinated-omission corrected or open-loop SLA estimates. Preconnect
    and warmup are outside measurement. Never count status/error response bytes
    as successful payload. An exhausted budget is a failure, not early success.
    """
    if method not in ['GET', 'POST', 'LATENCY'] or size not in [0, 10240, 102400, 1048576]:
        raise ValueError('unsupported workload')
    if (method == 'LATENCY') != (size == 0):
        raise ValueError('latency alone has zero payload')
    if not 1 <= concurrency <= 16 or not 0 < duration <= 30 or not 0 <= warmup <= 10:
        raise ValueError('bounded workload parameters required')
    ready = threading.Barrier(concurrency+1, timeout=30)
    start = threading.Event()
    abort = threading.Event()
    timing = {}
    path = '/latency' if method == 'LATENCY' else '/upload' if method == 'POST' else '/download?size='+str(size)
    body = b'x'*size if method == 'POST' else None

    def worker():
        connection = None
        samples, failures, warmed = [], [], 0
        def one():
            budget.reserve(size)
            before = time.perf_counter()
            connection.request('POST' if method == 'POST' else 'GET', path, body=body, headers=target.headers)
            response = connection.getresponse()
            maximum = size if method == 'GET' else 4096
            data = response.read(maximum+1)
            if len(data) > maximum:
                raise RuntimeError('oversized response')
            expected = 204 if method == 'LATENCY' else 200
            if response.status != expected:
                raise RuntimeError('unexpected HTTP status '+str(response.status))
            if method == 'GET' and len(data) != size:
                raise RuntimeError('download byte accounting mismatch')
            if method == 'POST' and json.loads(data).get('bytes') != size:
                raise RuntimeError('upload byte accounting mismatch')
            return (time.perf_counter()-before)*1000
        try:
            connection = target.connection()
            connection.connect()
            warmup_end = time.perf_counter()+warmup
            while time.perf_counter() < warmup_end and not abort.is_set():
                one()
                warmed += 1
            ready.wait()
            start.wait(timeout=30)
            if not start.is_set():
                raise RuntimeError('measurement did not start')
            while time.perf_counter() < timing['deadline'] and not abort.is_set():
                samples.append(one())
        except Exception as error:
            failures.append(type(error).__name__+': '+str(error))
            abort.set()
            ready.abort()
        finally:
            if connection is not None:
                connection.close()
        return dict(latencies=samples, errors=failures, warmup_requests=warmed)

    before = after = None
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker) for _ in range(concurrency)]
        try:
            ready.wait()
            before = observe('before')
            timing['start'] = time.perf_counter()
            timing['deadline'] = timing['start']+duration
            start.set()
            rows = [future.result() for future in futures]
            elapsed = time.perf_counter()-timing['start']
            after = observe('after')
        except threading.BrokenBarrierError as error:
            abort.set()
            start.set()
            rows = [future.result() for future in futures]
            messages = [value for row in rows for value in row['errors']]
            raise RuntimeError('warmup/preconnect failed: '+'; '.join(messages)) from error
        except BaseException:
            abort.set()
            start.set()
            raise
    latencies = [value for row in rows for value in row['latencies']]
    errors = [value for row in rows for value in row['errors']]
    return dict(passed=bool(latencies) and not errors, method=method, payload_bytes=size,
                concurrency=concurrency, admission_seconds=duration, warmup_seconds=warmup,
                observed_seconds=elapsed, requests=len(latencies), errors=errors,
                warmup_requests=sum(r['warmup_requests'] for r in rows),
                payload_mbps=len(latencies)*size*8/elapsed/1e6,
                requests_per_second=len(latencies)/elapsed,
                p50_ms=percentile(latencies,.5) if latencies else None,
                p95_ms=percentile(latencies,.95) if latencies else None,
                p99_ms=percentile(latencies,.99) if latencies else None,
                resource_before=before, resource_after=after)


def main():
    import argparse
    from pathlib import Path
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
    from remote_rtc_contract import Target
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['rust-url','cpp-url','ca','output']:
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--token-file',type=Path)
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--duration',type=float,default=2)
    parser.add_argument('--warmup',type=float,default=.5)
    parser.add_argument('--max-payload-mib',type=int,default=512)
    args=parser.parse_args()
    if not 1<=args.rounds<=5 or not 0<args.duration<=30 or not 0<=args.warmup<=10 or not 1<=args.max_payload_mib<=4096:
        parser.error('choose bounded rounds/durations/payload budget')
    output=Path(args.output)
    if output.exists():
        parser.error('choose a new output; never overwrite an earlier experiment')
    # Validate both HTTPS origins/CA before creating the evidence file.
    token=args.token_file.read_text().strip() if args.token_file else ''
    targets={label:Target(getattr(args,label+'_url'),args.ca,token) for label in ['rust','cpp']}
    budget=PayloadBudget(args.max_payload_mib<<20)
    report=dict(passed=False,rows=[],resource_measurements_available=False,
                client='Python stdlib HTTPS keepalive, closed loop',
                maximum_payload_bytes=budget.limit,rounds=args.rounds)
    output.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation precedes network traffic, including readiness/warmup.
    with output.open('x') as evidence:
        try:
            for round_index in range(args.rounds):
                for method,size in [('LATENCY',0),('GET',1048576),('POST',1048576)]:
                    for concurrency in [1,4]:
                        order=['rust','cpp'] if round_index%2==0 else ['cpp','rust']
                        for label in order:
                            row=run_workload(targets[label],method,size,concurrency,args.duration,args.warmup,budget)
                            row.update(backend=label,round=round_index)
                            report['rows'].append(row)
                            print(f'{label} {method} c{concurrency}: {row["requests"]} requests, passed={row["passed"]}',flush=True)
                            if not row['passed']:
                                raise RuntimeError('non-successful workload; inspect retained errors')
            report['passed']=True
        except BaseException as error:
            message=str(error).replace(token,'[redacted]') if token else str(error)
            report.update(error_type=type(error).__name__,error=message)
            raise
        finally:
            report['attempted_payload_bytes_including_warmup']=budget.used
            json.dump(report,evidence,indent=2)
            evidence.write('\n')
    print('PASS bounded authorized HTTPS comparison; no resource or language-only claim',flush=True)


if __name__=='__main__':main()
