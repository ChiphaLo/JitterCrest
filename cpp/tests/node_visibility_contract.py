"""Actual UI configuration visibility, with an explicit private-deployment alias.

Published defaults are generic. A private Rust reference can use a different
probe ID / enable-variable spelling; supply that mapping explicitly rather than
changing its source or publishing its node configuration. Both services receive
the same synthetic node list; only the documented environment-name adapter differs.
"""
import argparse
import json
from pathlib import Path
import re
from http_contract import Server,request


def cases(binary, probe_id, disable_variable, cpp):
    ordinary='probe-only' if probe_id!='probe-only' else 'unrelated-node'
    nodes=[dict(id='fixture',label='Fixture',base=''),
           dict(id=probe_id,label='Probe',base='',rtcTargetMs=45),
           dict(id=ordinary,label='Unrelated node',base=''),None,7]
    results=[]
    for value in [None,'','0','1','false','00',' 0','0 ']:
        env={'SPEEDTEST_NODES':json.dumps(nodes)}
        if value is not None:env[disable_variable]=value
        if cpp:env['SPEEDTEST_PROBE_ONLY_NODE_ID']=probe_id
        server=Server(binary,env)
        try:
            response=request(server.port,'GET','/')
            if response['status']!=200:raise AssertionError('UI build required')
            match=re.search(rb'<script[^>]*id="nodeConfig"[^>]*>(.*?)</script>',response['body'],re.S)
            if not match:raise AssertionError('node configuration script missing')
            actual=json.loads(match.group(1))
            expected=[node for node in nodes if not isinstance(node,dict) or node.get('id')!=probe_id] if value=='0' else nodes
            results.append(dict(flag=value,passed=actual==expected,actual=actual,expected=expected))
        finally:server.close()
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpp',type=Path,required=True)
    p.add_argument('--rust',type=Path)
    p.add_argument('--probe-id',default='probe-only')
    p.add_argument('--rust-disable-variable',default='ENABLE_PROBE_ONLY_NODE')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]{1,128}',args.probe_id) or not re.fullmatch('[A-Z][A-Z0-9_]{0,127}',args.rust_disable_variable):
        p.error('bounded identifier and environment variable required')
    if args.output.exists():p.error('choose a fresh report; never overwrite earlier failures')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    report=dict(passed=False,results={},explicit_alias_adapter=True,rust_source_unchanged=True)
    with args.output.open('x') as out:
        try:
            for label,binary,variable in [('rust',args.rust,args.rust_disable_variable),('cpp',args.cpp,'ENABLE_PROBE_ONLY_NODE')]:
                if binary:
                    rows=cases(binary,args.probe_id,variable,label=='cpp')
                    report['results'][label]=rows
                    print(label+': '+str(sum(r['passed'] for r in rows))+'/'+str(len(rows))+' actual UI visibility cases',flush=True)
            report['passed']=all(r['passed'] for rows in report['results'].values() for r in rows)
        finally:
            json.dump(report,out,indent=2);out.write('\n')
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
