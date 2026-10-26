import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from network_benchmark import PayloadBudget, percentile, run_workload


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):
        body=b'' if self.path=='/latency' else b'x'*10240
        self.send_response(204 if self.path=='/latency' else 200)
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        size=len(self.rfile.read(int(self.headers['Content-Length'])))
        body=json.dumps(dict(bytes=size)).encode()
        self.send_response(200)
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Tests(unittest.TestCase):
    def test_percentiles(self):
        self.assertEqual(percentile([4,1,3,2],.5),2)
        self.assertEqual(percentile([4,1,3,2],.99),4)
        with self.assertRaises(ValueError):percentile([],1)

    def test_budget(self):
        budget=PayloadBudget(10)
        budget.reserve(10)
        with self.assertRaises(RuntimeError):budget.reserve(1)
        self.assertEqual(budget.used,10)

    def test_preconnect_error_is_not_hidden_by_barrier(self):
        class Target:
            headers={}
            def connection(self):raise OSError('synthetic preconnect failure')
        with self.assertRaisesRegex(RuntimeError,'synthetic preconnect failure'):
            run_workload(Target(),'GET',10240,2,.1,0,PayloadBudget(10240))

    def test_oversize_is_not_successful_payload(self):
        class Response:
            status=200
            def read(self,maximum):return b'x'*maximum
        class Connection:
            def connect(self):pass
            def request(self,*args,**kwargs):pass
            def getresponse(self):return Response()
            def close(self):pass
        class Target:
            headers={}
            def connection(self):return Connection()
        row=run_workload(Target(),'GET',10240,2,.1,0,PayloadBudget(1<<20))
        self.assertFalse(row['passed'])
        self.assertEqual(row['requests'],0)
        self.assertEqual(row['payload_mbps'],0)
        self.assertTrue(any('oversized response' in v for v in row['errors']))

    def test_bad_parameters(self):
        for method,size,concurrency in [('GET',0,1),('LATENCY',10240,1),('POST',10240,17)]:
            with self.assertRaises(ValueError):
                run_workload(None,method,size,concurrency,.1,0,PayloadBudget(10240))

    def test_real_keepalive_io(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever)
        thread.start()
        class Target:
            headers={}
            def connection(self):return http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        phases=[]
        try:
            for method,size in [('GET',10240),('POST',10240),('LATENCY',0)]:
                row=run_workload(Target(),method,size,2,.12,.03,PayloadBudget(1<<28),observe=lambda p:phases.append(p))
                self.assertTrue(row['passed'],row)
                self.assertGreaterEqual(row['observed_seconds'],.12)
                self.assertGreater(row['requests'],0)
                self.assertEqual(row['errors'],[])
            self.assertEqual(phases,['before','after']*3)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__=='__main__':unittest.main()
