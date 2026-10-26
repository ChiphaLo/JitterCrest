"""Repeat native remote-close regression with no TLS hooks or packet tracer."""
import argparse
import hashlib
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from rtc_resource_contract import scenario, open_peer, close_wait


def run(binary, browser, output, rounds):
    rows = []
    try:
        for index in range(rounds):
            with scenario(binary, browser, output.with_name(output.stem + f'-{index}.log'),
                          MAX_RTC_INBOUND_BYTES_PER_PEER='262144',
                          MAX_RTC_CONTROL_MESSAGE_BYTES='4096',
                          MAX_RTC_CONTROL_MESSAGES_PER_PEER='512') as case:
                page = case.page()
                peer = open_peer(page)
                page.evaluate('id => window.__rtcResources.controls(id,63,4096)', peer['id'])
                assert page.evaluate('id => window.__rtcResources.probe(id)', peer['id'])
                page.evaluate('id => window.__rtcResources.controls(id,1,4096)', peer['id'])
                case.expected_log.append('inbound-byte-limit')
                assert close_wait(page, peer)['closed']
                assert page.evaluate('id => window.__rtcResources.replay(id)', peer['id'])['status'] == 409
                replacement = open_peer(page)
                assert page.evaluate('id => window.__rtcResources.probe(id)', replacement['id'])
            rows.append(dict(round=index, remote_channels_closed=True, tombstone=True, replacement_ack=True))
            print(f'PASS untraced native close {index + 1}/{rounds}', flush=True)
    except BaseException as error:
        output.write_text(json.dumps(dict(passed=False, rounds=rows, error=str(error)), indent=2) + '\n')
        raise
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 10000:
        parser.error('rounds must be 1..10000')
    inputs = [args.binary, Path(__file__), Path(__file__).with_name('rtc_resource_contract.py'),
              Path(__file__).with_name('rtc_resource_client.py'), Path(__file__).with_name('http_contract.py')]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--disable-gpu', '--disable-features=WebRtcHideLocalIpsWithMdns'])
        try:
            rows = run(args.binary, browser, args.output, args.rounds)
        finally:
            browser.close()
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest for p, digest in hashes.items())
    args.output.write_text(json.dumps(dict(passed=True, rounds=rows, tracer_used=False,
        unchanged_sources_and_binary=True, sha256=hashes), indent=2) + '\n')
