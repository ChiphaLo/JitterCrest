"""Corroborate browser CORS rejection with exact owned proxy observations.

A fetch TypeError alone is NOT evidence of a probe-only policy denial: TLS,
network failure and CORS can all produce it. A unique per-request nonce links
the browser attempt to a real proxy OPTIONS404, with no actual payload request.
No raw header values, authorization tokens or response bodies are returned.
"""
from urllib.parse import parse_qs, urlsplit
import re


BROWSER_DENIALS = r"""async ({base,nonce}) => {
 const observations=[];
 const paths=[['GET','/download?size=10240'],['GET','/download-stream'],
  ['POST','/upload'],['POST','/upload-stream'],['POST','/upload-progress']];
 for(let i=0;i<paths.length;i++) {
  const [method,path]=paths[i],url=new URL(path,base);
  url.searchParams.set('acceptanceProbe',nonce+'-'+i);
  try {
   const response=await fetch(url,{method,cache:'no-store'});
   await response.arrayBuffer();
   if(response.status!==404)throw Error('probe-only payload was not denied');
   observations.push({method,path:url.pathname+url.search,outcome:'response-404'});
  } catch(error) {
   if(!(error instanceof TypeError))throw error;
   observations.push({method,path:url.pathname+url.search,outcome:'fetch-rejected'});
  }
 }
 return observations;
}"""


def header(headers, name):
    value = next((v for k,v in headers.items() if k.lower()==name.lower()), None)
    return value if isinstance(value,list) else [value] if isinstance(value,str) else []


def validate_denials(observations, proxy_records, central_origin):
    if not observations or not central_origin.startswith('https://'):
        raise AssertionError('nonempty browser observations and central HTTPS origin required')
    paths=set()
    result=[]
    for observation in observations:
        method,path,outcome=(observation[k] for k in ['method','path','outcome'])
        parsed=urlsplit(path)
        nonce=parse_qs(parsed.query,keep_blank_values=True).get('acceptanceProbe',[])
        allowed={('GET','/download'),('GET','/download-stream'),('POST','/upload'),
                 ('POST','/upload-stream'),('POST','/upload-progress')}
        if ((method,parsed.path) not in allowed or not path.startswith('/') or parsed.netloc or parsed.fragment or
            len(nonce)!=1 or not re.fullmatch('[A-Za-z0-9_.-]{1,128}',nonce[0]) or path in paths):
            raise AssertionError('each probe requires an owned unique endpoint query')
        paths.add(path)
        expected='OPTIONS' if outcome=='fetch-rejected' else method if outcome=='response-404' else None
        if not expected:
            raise AssertionError('unsupported browser observation')
        matching=[r for r in proxy_records if r.get('request',{}).get('uri')==path]
        proof=[r for r in matching if r['request'].get('method')==expected]
        if not proof:
            raise AssertionError('browser rejection lacks matching owned HTTP evidence')
        for row in proof:
            fields=row['request'].get('headers',{})
            if (row.get('status')!=404 or header(fields,'origin')!=[central_origin] or
                header(fields,'sec-fetch-site')!=['cross-site']):
                raise AssertionError('wrong status, origin or cross-site provenance')
        if outcome=='fetch-rejected' and any(r['request'].get('method')==method for r in matching):
            raise AssertionError('payload request observed despite claimed preflight denial')
        result.append(dict(method=method,outcome='preflight-denied' if expected=='OPTIONS' else 'response-denied',corroborating_records=len(proof)))
    return result
