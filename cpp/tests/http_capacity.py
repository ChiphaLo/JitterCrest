"""Observe accepted simultaneous connections, not just a load-generator target."""
import argparse
import http.client
import json
import os
from pathlib import Path
import sys
import time

from http_contract import Server
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
from benchmark import pin_threads, snapshot


def established(pid, port):
    owned = set()
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(fd)
        except FileNotFoundError:
            continue
        if target.startswith("socket:["):
            owned.add(target[8:-1])
    count = 0
    for line in Path(f"/proc/{pid}/net/tcp").read_text().splitlines()[1:]:
        fields = line.split()
        if fields[3] == "01" and int(fields[1].split(":")[1],16) == port and fields[9] in owned:
            count += 1
    return count


def observe(binary, cpus):
    results = []
    for connections in [1,16,64,128]:
        server = Server(binary, {"RATE_MAX":"1000", "MAX_HTTP_CONNECTIONS":"4096",
                                "SPEEDTEST_IO_THREADS":"2", "TOKIO_WORKER_THREADS":"2"})
        clients = []
        try:
            pin_threads(server.process.pid,cpus)
            time.sleep(.1)
            idle = snapshot(server.process.pid)
            for _ in range(connections):
                client = http.client.HTTPConnection("127.0.0.1",server.port,timeout=4)
                clients.append(client)
                client.request("GET","/health")
                reply = client.getresponse()
                assert reply.status == 200
                reply.read()
            observed = established(server.process.pid,server.port)
            assert observed == connections, (connections,observed)
            active = snapshot(server.process.pid)
            for client in clients:
                client.close()
            deadline = time.monotonic()+2
            while established(server.process.pid,server.port) and time.monotonic() < deadline:
                time.sleep(.02)
            assert established(server.process.pid,server.port) == 0
            results.append(dict(requested=connections,accepted_established=observed,
                idle_rss_bytes=idle["rss_bytes"],active_rss_bytes=active["rss_bytes"],
                peak_rss_bytes=active["peak_rss_bytes"],threads=active["threads"],
                released_connections=True))
        finally:
            for client in clients:
                client.close()
            server.close()
    return results


if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--rust",type=Path,required=True)
    p.add_argument("--cpp",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--server-cpus",default="0,2")
    args=p.parse_args()
    results={}
    try:
        for label,binary in [("rust",args.rust),("cpp",args.cpp)]:
            results[label]=observe(binary,{int(n) for n in args.server_cpus.split(",")})
            print(f"PASS observed 1/16/64/128 accepted connections: {label}",flush=True)
    except BaseException as error:
        args.output.write_text(json.dumps(dict(passed=False,results=results,error=str(error)),indent=2)+"\n")
        raise
    args.output.write_text(json.dumps(dict(passed=True,results=results,processes_exited=True),indent=2)+"\n")
