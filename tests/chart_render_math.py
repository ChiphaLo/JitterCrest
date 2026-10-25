"""Deterministic display interpolation checks; never smooth measurement inputs."""
from pathlib import Path
from playwright.sync_api import sync_playwright
source=(Path(__file__).resolve().parent.parent/'public/index.html').read_text(encoding='utf-8')
helpers=source[source.index('    function speedCurve('):source.index('    function speedChartView(')]
checks=r'''
let checked=0;
for(const values of [[0,400,50,500,0,0,300],[10,11,1000,1001,1002],[400,300,200,100,0],[0,0,0,0]]) {
  const times=[0,100,201,1500,1501,6000,8000];
  const points=values.map((value,i)=>({atMs:times[i],value,display:value}));
  const before=JSON.stringify(points);
  const {slopes}=speedCurve(points,v=>v,v=>v);
  for(let i=0;i<points.length-1;i++) {
    const a=points[i],b=points[i+1],h=b.atMs-a.atMs;
    for(let j=0;j<=200;j++) {
      const t=j/200;
      const raw=(2*t*t*t-3*t*t+1)*a.display+(t*t*t-2*t*t+t)*h*slopes[i]
        +(-2*t*t*t+3*t*t)*b.display+(t*t*t-t*t)*h*slopes[i+1];
      if(raw < Math.min(a.display,b.display)-1e-8 || raw > Math.max(a.display,b.display)+1e-8)
        throw new Error('cubic path overshot measured display bounds');
      const plotted=speedCurveValue(points,slopes,a.atMs+h*t);
      if(Math.abs(plotted-raw)>1e-7) throw new Error('endpoint/path mismatch');
      checked++;
    }
  }
  if(JSON.stringify(points)!==before) throw new Error('interpolation changed raw samples');
}
return {checked,noNegativeOrOvershoot:true,rawSamplesUnchanged:true,nonUniformSpacing:true};
'''
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page()
    result=page.evaluate('({helpers,checks}) => new Function(helpers+checks)()',{'helpers':helpers,'checks':checks})
    print(result)
    browser.close()
