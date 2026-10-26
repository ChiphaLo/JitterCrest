"""Golden arithmetic checks on the actual HTML served by independent backends.

Browser-side statistics stay in the unchanged shared frontend. This offline
arithmetic test is not native RTC, network-loss, rendering or timing acceptance.
No Node runtime, fake network peer or frontend source edit is involved.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random

from http_contract import Server, request
from playwright.sync_api import sync_playwright


def section(source, first, last):
    start = source.index(first)
    return source[start:source.index(last, start)]


def helpers(source):
    return '\n'.join([
        section(source, '    function avg(', '    function fmtStat('),
        section(source, '    function stats(', '    function analyzePacing('),
        section(source, '    function packetTimingStats(', '    function formatGameLoss('),
        section(source, '      function stableRate(', '      function publishThroughputSample('),
    ])


def finite(values):
    values = [v.get('rtt') if isinstance(v, dict) else v for v in values]
    return [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]


def pct(values, p):
    return sorted(values)[math.floor((len(values)-1)*p)] if values else None


def expected(values):
    vals = finite(values)
    average = sum(vals)/len(vals) if vals else None
    stats = dict(min=min(vals) if vals else None, avg=average, max=max(vals) if vals else None)
    for name, p in [('p10', .1), ('p25', .25), ('p50', .5), ('p75', .75), ('p90', .9), ('p95', .95), ('p99', .99)]:
        stats[name] = pct(vals, p)
    median = pct(vals, .5)
    mad = 1.4826*pct([abs(v-median) for v in vals], .5) if vals else None
    delta = sum(abs(b-a) for a, b in zip(vals, vals[1:]))/(len(vals)-1) if len(vals) > 1 else None
    positive = sorted(v for v in vals if v > 0)
    cut = min(math.floor(len(positive)*.1), math.floor((len(positive)-1)/2)) if positive else 0
    trimmed = positive[cut:len(positive)-cut] if positive else []
    stable = sum(trimmed)/len(trimmed) if trimmed else None
    return dict(stats=stats, jitter=dict(mad=mad, meanDelta=delta,
                stddev=math.sqrt(sum((v-average)**2 for v in vals)/len(vals)) if len(vals) > 1 else 0,
                p95MinusP50=pct(vals,.95)-median if vals else None,
                p99MinusP50=pct(vals,.99)-median if vals else None, sampleCount=len(vals)),
                stable=stable, workerStable=stable,
                timing=dict(over100=sum(v>100 for v in vals),over200=sum(v>200 for v in vals),
                    over100Rate=sum(v>100 for v in vals)/len(vals) if vals else 0,
                    over200Rate=sum(v>200 for v in vals)/len(vals) if vals else 0))


def close(actual, target, where='value'):
    if isinstance(target, dict):
        assert actual.keys() == target.keys(), where
        for key in target:
            close(actual[key], target[key], where+'.'+key)
    elif isinstance(target, (float, int)):
        assert isinstance(actual, (float, int)) and math.isclose(actual, target, rel_tol=1e-12, abs_tol=1e-12), (where, actual, target)
    else:
        assert actual == target, (where, actual, target)


def vectors():
    rows = [[], [0], [17], [0, 1], [1, 2, 3, 1000], [100, 200, 200.0001],
            [None, math.nan, math.inf, -math.inf, True, False],
            [{'rtt': 1}, {'rtt': 5}, {'rtt': 9}, {'rtt': None}],
            [-5, 0, 1, 2, 3, 4, 5, 6, 7, 900], [3]*40]
    rng = random.Random(20261027)
    for _ in range(500):
        rows.append([rng.uniform(-1000, 10000) for _ in range(rng.randrange(513))])
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpp', type=Path, required=True)
    p.add_argument('--rust', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    report = dict(passed=False,purpose='shared_frontend_offline_arithmetic',native_rtc_acceptance=False,
                  relative_tolerance=1e-12,absolute_tolerance=1e-12,backends={})
    cases = vectors()
    scripts = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True,args=['--disable-gpu'])
            try:
                page = browser.new_page()
                for name,binary in [('rust',a.rust),('cpp',a.cpp)]:
                    server = Server(binary,{})
                    try:
                        response = request(server.port,'GET','/')
                        assert response['status'] == 200
                        script = helpers(response['body'].decode('utf-8'))
                    finally:
                        server.close()
                    scripts.append(script)
                    # Stable throughput consumes numeric Mbps, RTT functions also
                    # accept {rtt: ...} records. Preserve each actual filter/order.
                    actual = page.evaluate("""({script,cases})=>new Function('cases',script+`
                      return cases.map(v=>({stats:stats(v),jitter:jitterMetrics(v),
                       stable:trimmedStableRate(rttValues(v)),workerStable:stableRate(rttValues(v)),
                       timing:packetTimingStats(v)}));`)(cases)""",dict(script=script,cases=cases))
                    for i,(value,data) in enumerate(zip(actual,cases)):
                        close(value,expected(data),str(i))
                    assert len(actual) == len(cases)
                    report['backends'][name] = dict(passed=True,vectors=len(cases),served_helpers_sha256=hashlib.sha256(script.encode()).hexdigest())
                    print(name+': '+str(len(cases))+' actual-served frontend arithmetic vectors passed',flush=True)
            finally:
                browser.close()
        assert scripts[0] == scripts[1], 'served frontend statistic helpers differ'
        report.update(passed=True,served_helpers_identical=True)
    except BaseException as e:
        report.update(error_type=type(e).__name__,error=str(e))
        raise
    finally:
        a.output.write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()
