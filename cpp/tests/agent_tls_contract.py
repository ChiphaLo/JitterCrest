"""Real central UI -> separate measurement-only agent over cross-origin TLS.

All listeners, certificates, Caddy state and child processes are test-local.
No route mocks, response interception, Rust wrappers or production changes.
Probe-only RTC without a configured public mapping must fail closed; this local
suite does not claim successful public-NAT probe-only connectivity.
"""
import argparse
import base64
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time

from playwright.sync_api import sync_playwright
from http_contract import Server, allocate_port
from proxy_tls_contract import request


def certificate(folder):
    key, cert = folder / "key.pem", folder / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
                    "-days", "1", "-subj", "/CN=localhost", "-addext",
                    "subjectAltName=DNS:localhost,IP:127.0.0.1", "-keyout", str(key), "-out", str(cert)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    public = subprocess.check_output(["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"])
    der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "DER"], input=public,
                         check=True, stdout=subprocess.PIPE).stdout
    return cert, key, base64.b64encode(hashlib.sha256(der).digest()).decode()


@contextmanager
def backend(binary, env, log_path):
    server = Server(binary, env)
    try:
        yield server
    finally:
        try:
            server.close()
        finally:
            log_path.write_text(server.output)
        assert "rtc_native_cleanup_delayed" not in server.output


@contextmanager
def proxy(caddy, config, folder):
    env = dict(os.environ, XDG_CONFIG_HOME=str(folder / "caddy-config"),
               XDG_DATA_HOME=str(folder / "caddy-data"))
    with tempfile.TemporaryFile() as log:
        validated = subprocess.run([str(caddy), "validate", "--adapter", "caddyfile", "--config", str(config)],
                                   env=env, stdout=log, stderr=log)
        if validated.returncode:
            log.seek(0)
            raise AssertionError(log.read().decode(errors="replace"))
        process = subprocess.Popen([str(caddy), "run", "--adapter", "caddyfile", "--config", str(config)],
                                   env=env, stdout=log, stderr=log)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
                raise AssertionError("isolated proxy shutdown exceeded grace")
            assert process.returncode == 0, process.returncode


BROWSER_PAYLOADS = r"""async base => {
  const results={};
  for(const size of [10240,102400,1048576]) {
    const r=await fetch(base+'/download?size='+size,{cache:'no-store'});
    const bytes=(await r.arrayBuffer()).byteLength;
    if(r.status!==200 || bytes!==size)throw Error('download '+r.status+' '+bytes);
    results['download-'+size]={status:r.status,bytes};
  }
  const upload=await fetch(base+'/upload',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:new Uint8Array(1048576)});
  const received=await upload.json();
  if(upload.status!==200 || received.bytes!==1048576)throw Error('upload '+JSON.stringify(received));
  results.upload={status:upload.status,bytes:received.bytes};
  const created=await fetch(base+'/upload-progress?lanes=1&warmupMs=0',{method:'POST'});
  if(created.status!==200)throw Error('create progress '+created.status);
  const {id}=await created.json(),stream=await fetch(base+'/upload-progress?id='+id,{signal:AbortSignal.timeout(10000)});
  if(stream.status!==200)throw Error('progress stream '+stream.status);
  const reader=stream.body.getReader(),decoder=new TextDecoder();let pending='';
  const line=async()=>{for(;;){const at=pending.indexOf('\n');if(at>=0){const v=pending.slice(0,at);pending=pending.slice(at+1);return JSON.parse(v);}const r=await reader.read();if(r.done)throw Error('early progress EOF');pending+=decoder.decode(r.value,{stream:true});}};
  try {
    const initial=await line();
    if(initial.bytes!==0 || initial.done)throw Error('initial progress');
    const sent=await fetch(base+'/upload-stream?progressId='+id+'&lane=0&warmupMs=0',{
      method:'POST',headers:{'Content-Type':'application/octet-stream'},body:new Uint8Array(1048576)});
    const accounting=await sent.json();
    if(sent.status!==200 || accounting.bytes!==1048576)throw Error('upload stream accounting');
    let final=initial;
    for(let i=0;i<100&&!final.done;i++)final=await line();
    if(!final.done || final.failed || final.expired || final.bytes!==1048576 || final.measuredBytes!==1048576)throw Error('final progress '+JSON.stringify(final));
    results.progress={initial_before_upload:true,final_bytes:final.bytes,done:true};
  } finally {await reader.cancel();}
  return results;
}"""

PROBE_ONLY = r"""async base => {
  const statuses={};
  for(const [method,path] of [['GET','/download?size=10240'],['GET','/download-stream'],
      ['GET','/download-100k'],['POST','/upload'],['POST','/upload-stream'],
      ['POST','/upload-progress'],['GET','/speed/download?size=10240']]) {
    const r=await fetch(base+path,{method});await r.arrayBuffer();
    if(r.status!==404)throw Error('probe-only '+method+' '+path+' '+r.status);
    statuses[method+' '+path]=r.status;
  }
  const latency=await fetch(base+'/latency');
  if(latency.status!==204)throw Error('probe-only latency '+latency.status);
  let preflightBlocked=false;
  try{await fetch(base+'/upload',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:new Uint8Array(1)});}
  catch(error){if(!(error instanceof TypeError))throw error;preflightBlocked=true;}
  if(!preflightBlocked)throw Error('probe-only upload preflight unexpectedly allowed');
  const pc=new RTCPeerConnection({iceServers:[]}),attempt='probe-'+crypto.randomUUID();
  const headers={'Content-Type':'application/json','X-Speedtest-Attempt-Id':attempt};
  pc.createDataChannel('control');pc.createDataChannel('test',{ordered:false,maxRetransmits:0});
  try {
    await pc.setLocalDescription(await pc.createOffer());
    const until=performance.now()+5000;
    while(pc.iceGatheringState!=='complete'){if(performance.now()>until)throw Error('probe gather');await new Promise(r=>setTimeout(r,5));}
    const r=await fetch(base+'/webrtc/offer',{method:'POST',headers,
      body:JSON.stringify({attemptId:attempt,type:'offer',sdp:pc.localDescription.sdp})});
    const body=await r.json();
    if(r.status!==500 || body.error!=='WebRTC offer failed')throw Error('mapping guard '+r.status+' '+JSON.stringify(body));
    return {blocked_payloads:statuses,upload_preflight_blocked:preflightBlocked,
      latency_status:latency.status,missing_public_mapping_status:r.status};
  } finally {
    pc.close();
    const r=await fetch(base+'/webrtc/offer',{method:'POST',headers,body:JSON.stringify({attemptId:attempt,action:'cancel'})});
    if(r.status!==204)throw Error('probe cancel '+r.status);
  }
}"""


def run(ui_binary, agent_binary, caddy, browser_type, directory, output, label, probe_node_id):
    directory.mkdir()
    cert, key, pin = certificate(directory)
    ports = {name: allocate_port() for name in ["ui", "agent", "probe"]}
    hosts = {"ui": "localhost", "agent": "127.0.0.1", "probe": "127.0.0.1"}
    origins = {name: f"https://{hosts[name]}:{port}" for name, port in ports.items()}
    common = {"SPEEDTEST_RUST_RTC_EXPERIMENTAL": "1", "SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST": "1",
              "TRUST_PROXY_HEADERS": "1", "TRUSTED_PROXY_IPS": "127.0.0.1,::1", "RATE_MAX": "1000",
              "SPEEDTEST_ALLOW_CROSS_SITE": "1", "ALLOWED_ORIGINS": origins["ui"]}
    nodes = [dict(id="fixture-agent", label="Independent agent", base=origins["agent"]),
             dict(id="probe-only", label="Probe only", base=origins["probe"])]
    with ExitStack() as stack:
        servers = {}
        for name, binary, extra in [("agent", agent_binary, {}),
                                     ("probe", agent_binary, {"SPEEDTEST_PROBE_ONLY": "1"}),
                                     ("ui", ui_binary, {"SPEEDTEST_NODES": json.dumps(nodes)})]:
            servers[name] = stack.enter_context(backend(binary, {**common, **extra},
                output.with_name(output.stem + "-" + label + "-" + name + ".log")))
        config = directory / "Caddyfile"
        text = "{\n admin off\n auto_https off\n}\n"
        for name, server in servers.items():
            role_header = f"  header_up X-Speedtest-Node {probe_node_id}\n" if name == "probe" else ""
            text += (f'{origins[name]} {{\n bind 127.0.0.1\n tls "{cert}" "{key}"\n'
                     f' reverse_proxy 127.0.0.1:{server.port} {{\n'
                     '  header_up X-Real-IP {http.request.remote.host}\n'
                     '  header_up X-Forwarded-For {http.request.remote.host}\n' + role_header +
                     '  flush_interval -1\n }\n' +
                     f' log {{\n  output file "{directory / (name + "-access.jsonl")}"\n  format json\n }}\n}}\n')
        config.write_text(text)
        process = stack.enter_context(proxy(caddy, config, directory))
        context = ssl.create_default_context(cafile=str(cert))
        for name, port in ports.items():
            for _ in range(100):
                assert process.poll() is None, "proxy exited before readiness"
                try:
                    if request(port, "GET", "/health", context=context, host=hosts[name])[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(.05)
            else:
                raise AssertionError("TLS readiness timeout")
        # This must fail without the explicit test CA, before using a browser
        # scoped to its SPKI. Never accept arbitrary TLS certificates.
        try:
            request(ports["agent"], "GET", "/health", context=ssl.create_default_context(), host=hosts["agent"])
        except ssl.SSLCertVerificationError:
            pass
        else:
            raise AssertionError("untrusted ephemeral certificate accepted")
        for name in ["agent", "probe"]:
            for path in ["/", "/favicon.svg"]:
                assert request(ports[name], "GET", path, context=context, host=hosts[name])[0] == 404, (name, path)
        _, fields, _ = request(ports["ui"], "GET", "/", context=context)
        fields = {k.lower(): v for k, v in fields.items()}
        assert all(origin in fields["content-security-policy"] for origin in [origins["agent"], origins["probe"]])
        status, fields, _ = request(ports["agent"], "OPTIONS", "/upload", headers={
            "Origin": "https://forbidden.invalid", "Access-Control-Request-Method": "POST"}, context=context, host=hosts["agent"])
        assert status == 403 and not any(k.lower() == "access-control-allow-origin" for k in fields)
        assert request(ports["probe"], "OPTIONS", "/upload", headers={"Origin": origins["ui"],
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}, context=context, host=hosts["probe"])[0] == 404
        browser = browser_type.launch(headless=True, args=["--disable-gpu",
            "--disable-features=WebRtcHideLocalIpsWithMdns", "--ignore-certificate-errors-spki-list=" + pin])
        try:
            page = browser.new_page()
            errors, requests, responses = [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda req: requests.append((req.url, req.method)))
            page.on("response", lambda response: responses.append((response.url, response.status, response.headers)))
            page.goto(origins["ui"] + "/?testMode=webrtc-contract")
            page.wait_for_function("() => Boolean(window.__speedtestTestHooks)")
            assert page.locator("#nodeSelect").input_value() == "fixture-agent"
            paired = page.evaluate("() => window.__speedtestTestHooks.runPairedMeasurementForTest({planned:3})")
            assert paired["complete"] and not paired["invalidReasons"] and paired["uploadReceivedSeqs"] == [0, 1, 2], paired
            page.click("#restartBtn")
            page.wait_for_function("() => ['RTT complete','Error'].includes(document.querySelector('#phase').textContent)", timeout=15000)
            assert page.locator("#phase").inner_text() == "RTT complete", page.locator("#log").inner_text()
            assert page.locator("#rttIncompleteBadge").evaluate("node => node.hidden")
            for selector in ["#rttUpAvg", "#rttDownAvg", "#rttUpP95", "#rttDownP95"]:
                assert float(page.locator(selector).inner_text()) >= 0
            payloads = page.evaluate(BROWSER_PAYLOADS, origins["agent"])
            page.goto(origins["ui"] + "/?testMode=webrtc-contract&node=probe-only")
            page.wait_for_function("() => Boolean(window.__speedtestTestHooks)")
            assert page.locator("#nodeSelect").input_value() == "probe-only"
            probes = page.evaluate(PROBE_ONLY, origins["probe"])
            offers = [url for url, method in requests if "/webrtc/offer" in url and method == "POST"]
            assert offers and all(url.startswith(origins["agent"] + "/") or url.startswith(origins["probe"] + "/") for url in offers), offers
            # Chromium does not expose its internal CORS preflights through
            # Playwright page.request events. Inspect the actual proxy access
            # log instead; the only Python agent OPTIONS above is forbidden.
            access = [json.loads(line) for line in (directory / "agent-access.jsonl").read_text().splitlines()]
            assert any(row["request"]["method"] == "OPTIONS" and row["request"]["uri"] == "/upload"
                       and row["status"] == 204 and row["request"]["headers"].get("Origin") == [origins["ui"]]
                       for row in access), "allowed browser upload preflight was not observed"
            assert any(row["request"]["method"] == "POST" and row["request"]["uri"] == "/upload"
                       and row["status"] == 200 and row["request"]["headers"].get("Sec-Fetch-Site") == ["cross-site"]
                       for row in access), "real cross-site browser upload was not observed"
            for url, _, headers in responses:
                if url.startswith(origins["agent"] + "/") or url.startswith(origins["probe"] + "/"):
                    assert headers.get("access-control-allow-origin") == origins["ui"], (url, headers)
            assert not errors, errors
            return dict(central_ui_only=True, independent_agent_without_assets=True,
                        certificate_chain_hostname_verified=True, untrusted_certificate_rejected=True,
                        scoped_browser_certificate_pin=True, no_route_mocks=True,
                        direct_agent_ip=True, browser_cross_site_verified=True,
                        csp_exact_agent_origins=True, forbidden_preflight_rejected=True,
                        real_browser_preflight=True, exact_cross_origin_responses=True,
                        paired_rtc_complete=True, restart_rtt_rendered=True,
                        agent_payloads=payloads, probe_only=probes)
        finally:
            browser.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for name in ["cpp-ui", "cpp-agent", "rust-ui", "rust-agent", "caddy", "output"]:
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--rust-probe-node-id", required=True,
                   help="Probe-role proxy marker recognized by the chosen Rust reference; may differ in sanitized editions")
    args = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", args.rust_probe_node_id):
        p.error("probe marker must be an identifier, not arbitrary Caddy configuration")
    results = {}
    protected = [Path(__file__), Path(__file__).with_name("http_contract.py"),
                 Path(__file__).with_name("proxy_tls_contract.py"), args.cpp_ui, args.cpp_agent,
                 args.rust_ui, args.rust_agent, args.caddy]
    identities = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    try:
        with tempfile.TemporaryDirectory(prefix="speedtest-central-agent-tls-") as tmp:
            with sync_playwright() as playwright:
                for label, ui, agent in [("rust-rust", args.rust_ui, args.rust_agent),
                                          ("cpp-cpp", args.cpp_ui, args.cpp_agent),
                                          ("rust-cpp", args.rust_ui, args.cpp_agent),
                                          ("cpp-rust", args.cpp_ui, args.rust_agent)]:
                    print("START central UI / separate agent TLS " + label, flush=True)
                    results[label] = run(ui, agent, args.caddy, playwright.chromium,
                                         Path(tmp) / label, args.output, label,
                                         args.rust_probe_node_id if agent == args.rust_agent else "probe-only")
                    print("PASS central UI / separate agent TLS " + label, flush=True)
                assert all(value == results["rust-rust"] for value in results.values()), results
        assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
                   for path, digest in identities.items()), "test sources or executables changed during validation"
    except BaseException as error:
        args.output.write_text(json.dumps(dict(passed=False, results=results,
                                               error_type=type(error).__name__, error=str(error)), indent=2) + "\n")
        raise
    args.output.write_text(json.dumps(dict(passed=True, results=results, processes_exited=True,
                                          unchanged_sources_and_executables=True, sha256=identities), indent=2) + "\n")
    print("PASS four central-UI / independent-agent combinations", flush=True)
