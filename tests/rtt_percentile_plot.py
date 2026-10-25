"""RTT percentile markers: separate directions, numeric labels and row-local guides."""
import argparse
import json
from pathlib import Path
import re
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent.parent
source=(ROOT/'public/index.html').read_text(encoding='utf-8')
public=(ROOT/'outputs/speedtest-rust/public/index.html')
def helpers(text):
    plot=text[text.index('    function uiColor('):text.index('    const speedCharts')]
    percentile=text[text.index('    function percentile('):text.index('    function rttValues(')]
    return percentile+plot
if public.exists():
    assert helpers(source)==helpers(public.read_text(encoding='utf-8')), 'private/public chart logic diverged'
css='\n'.join(re.findall(r'<style[^>]*>(.*?)</style>',source,re.S))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=ROOT/'target/rtt-percentile-check')
OUT=parser.parse_args().output
OUT.mkdir(parents=True,exist_ok=True)
cases=[
    ('distinct',[10,20,30,40,50],[100,200,300,400,500]),
    ('different_counts',[{'rtt':4},{'rtt':7},{'rtt':9}],[15,20,23,29,32,40,80]),
    ('coincident',[20]*40,[20]*40),
    ('clustered',[20+i*.0001 for i in range(40)],[20+i*.0002 for i in range(40)]),
    ('no_upload',[],[15,20,30]),
    ('no_download',[12,14,18],[]),
    ('empty',[],[]),
    ('invalid',[None,{'rtt':11},{'rtt':13},{'rtt':17}],[None,25,30,35]),
]
results=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1100,'height':500},device_scale_factor=1)
    page.set_content('<style>'+css+'</style><div class="panel" style="width:1000px;margin:0;padding:24px"><h2>WebRTC RTT / Jitter</h2><div id="latencyPlot" class="latency-plot"></div></div>')
    page.evaluate('code=>{window.drawRttTest=new Function("up","down","const $=id=>document.getElementById(id);"+code+";drawLatencyPlot(up,down);");}',helpers(source))
    for name,up,down in cases:
        page.evaluate('({up,down})=>drawRttTest(up,down)',{'up':up,'down':down})
        markers=page.evaluate('''() => [...document.querySelectorAll('.latency-percentile')].map(g=>{
          const line=g.querySelector('.latency-percentile-line'),text=g.querySelector('.latency-percentile-label'),b=text.getBBox();
          return {direction:g.dataset.direction,p:g.dataset.percentile,value:Number(g.dataset.value),
            label:text.textContent,x:Number(line.getAttribute('x1')),y1:Number(line.getAttribute('y1')),y2:Number(line.getAttribute('y2')),
            box:{x:b.x,y:b.y,w:b.width,h:b.height},dash:line.getAttribute('stroke-dasharray')};
        })''')
        for direction,values in [('up',up),('down',down)]:
            vals=sorted(v['rtt'] if isinstance(v,dict) else v for v in values if v is not None)
            selected=[m for m in markers if m['direction']==direction]
            assert len(selected)==(4 if vals else 0),(name,direction,selected)
            for m,q in zip(selected,[.1,.5,.95,.99]):
                expected=vals[int((len(vals)-1)*q)]
                assert m['value']==expected,(name,m,expected)
                assert m['label']==f"{m['p']} {expected:.1f} ms"
                assert m['dash']=='6 6'
                assert (m['y1'],m['y2'])==((44,86) if direction=='up' else (136,178))
                assert 69<=m['box']['x'] and m['box']['x']+m['box']['w']<=977
            for i,a in enumerate(selected):
                for b in selected[i+1:]:
                    x_overlap=min(a['box']['x']+a['box']['w'],b['box']['x']+b['box']['w'])-max(a['box']['x'],b['box']['x'])
                    y_overlap=min(a['box']['y']+a['box']['h'],b['box']['y']+b['box']['h'])-max(a['box']['y'],b['box']['y'])
                    assert x_overlap<=0 or y_overlap<=0,(name,'overlapping labels',a,b)
        assert 'NaN' not in page.locator('#latencyPlot').inner_html()
        results.append({'case':name,'markers':markers})
        if name=='coincident':
            page.locator('.panel').screenshot(path=str(OUT/'coincident.png'))
    up=[11+i*.12+max(0,i-34)*1.8 for i in range(40)]
    down=[17+i*.18+max(0,i-33)*2.2 for i in range(40)]
    page.evaluate('({up,down})=>drawRttTest(up,down)',{'up':up,'down':down})
    page.locator('.panel').screenshot(path=str(OUT/'desktop.png'))
    page.set_viewport_size({'width':390,'height':500})
    page.locator('.panel').evaluate("node=>node.style.width='100%'")
    page.evaluate('({up,down})=>drawRttTest(up,down)',{'up':up,'down':down})
    assert page.eval_on_selector('.panel','node=>node.scrollWidth<=node.clientWidth+1')
    page.locator('.panel').screenshot(path=str(OUT/'mobile.png'))
    page.emulate_media(color_scheme='dark')
    page.evaluate('({up,down})=>drawRttTest(up,down)',{'up':up,'down':down})
    page.locator('.panel').screenshot(path=str(OUT/'dark.png'))
    browser.close()
(OUT/'report.json').write_text(json.dumps({'passed':True,'cases':results},indent=2),encoding='utf-8')
print(json.dumps({'passed':True,'cases':len(results),'report':str(OUT/'report.json')}))
