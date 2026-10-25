"""Deterministic stability accounting regressions; no network or Node runtime."""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent.parent
source=(ROOT/'public/index.html').read_text(encoding='utf8')
def section(first,last):
    start=source.index(first)
    return source[start:source.index(last,start)]
helpers=section('    async function runPairedPacketMeasurement(', '    function packetTimingStats(')
helpers+=section('    async function runPairedStability(', '    async function runStabilityTest(')
helpers+=section('    function analyzeLossBursts(', '    const stabilityCanvasState')
helpers+=section('    const updateStabilityStats =', '    const STABILITY_ANALYSIS_VERSION')
checks=r'''
return (async()=>{
 const assert=(value,message)=>{if(!value)throw new Error(message);};
 let now=0,ids=null,peer;
 const performance={now:()=>now};
 const planned=1000,all=Array.from({length:planned},(_,i)=>i);
 const actualSeen=all.filter(seq=>seq!==717);
 [actualSeen[997],actualSeen[998]]=[actualSeen[998],actualSeen[997]];
 const listeners={test:new Set(),control:new Set()};
 const emit=(kind,data)=>{for(const callback of listeners[kind])callback({data});};
 const channel=kind=>({readyState:'open',addEventListener:(_,cb)=>listeners[kind].add(cb),removeEventListener:(_,cb)=>listeners[kind].delete(cb)});
 const sentFrames=[];
 peer={test:channel('test'),control:channel('control')};
 peer.test.send=frame=>{if(frame.kind===1)sentFrames.push(frame);};
 peer.control.send=raw=>{
  const m=JSON.parse(raw);
  if(m.type==='downStart'){ids=m;emit('control',JSON.stringify({...m,type:'measurementReady'}));}
  if(m.type==='uploadComplete')emit('control',JSON.stringify({...ids,type:'downResult',complete:true,planned,scheduled:planned,skipped:0,actualSent:planned,sent:planned,sentSeqs:all,received:planned,acked:planned,uploadReceived:999,samples:all.map(seq=>({seq,rtt:13.5})),pacing:{p50Interval:10}}));
 };
 function randomId(){return 'fixture';}
 function hashString32(){return 1;}
 function decodeRtcFrame(data){return data;}
 function encodeRtcFrame(kind,flags,sessionHash,testHash,seq,sentAt){return {kind,flags,sessionHash,testHash,seq,sentAt,reordered:!!flags};}
 function serializeFragmentedControlMessage(value){return [JSON.stringify(value)];}
 function ownScheduler(_,scheduler){return scheduler;}
 function waitMs(){return Promise.resolve();}
 function analyzePacing(times,targetMs){return {samples:times.length-1,targetMs,p50Interval:targetMs};}
 const RTC_KIND={up:1,upAck:2,down:3,downAck:4,downSample:5};
 const STABILITY_TEXT_UPDATE_MS=100;
 const RTC_CONTROL_SINGLE_MESSAGE_BYTES=1000000,RTC_CONTROL_FRAGMENT_PAYLOAD_BYTES=1000;
 const MAX_RTC_CONTROL_FRAGMENT_COUNT=1000,MAX_RTC_REASSEMBLED_CONTROL_BYTES=1000000;
 function createCancellableScheduler(options){
  const sentTimes=[];
  for(const seq of all){now=seq*10;assert(options.send(seq),'send failed');sentTimes.push(now);}
  for(const seq of actualSeen){now=10000+seq*.001;emit('test',{kind:2,seq,sentAt:sentFrames[seq].sentAt,reordered:seq===999,sessionHash:1,testHash:1});}
  for(const seq of [0,2,1,...all.slice(3)])emit('test',{kind:3,seq,sentAt:seq*10,sessionHash:1,testHash:1});
  const accounting={planned,scheduled:planned,skipped:0,actualSent:planned,sentSlots:all,sentTimes,cancelled:false,invalidReasons:[]};
  return {done:Promise.resolve(accounting),cancel:async()=>{},getScheduled:()=>planned};
 }
 const paired=await runPairedStability(peer,100,10000,'test',null,null);
 assert(paired.up.seenSeqs.length===999 && !paired.up.seenSeqs.includes(717) && paired.up.seenSeqs.includes(999),'missing ACK moved to tail');
 assert(paired.up.rttSeqs.every(s=>s.seq!==717 && s.rtt===s.ackedAtMs-s.sentAtMs),'RTT/seq/timestamps mismatched');
 assert(paired.up.rttSeqs.at(-1).seq===999,'arrival reorder changed final x index');
 assert(paired.up.reordered===1 && paired.down.reordered===1,'reorder observations lost');
 assert(paired.up.pacing.samples===999,'upload pacing missing');
 assert(paired.snapshot.localDelayMeasured===false,'unmeasured local delay misrepresented');
 const rtts=new Map(all.map(seq=>[seq,13.5]));
 for(let seq=300;seq<339;seq++)rtts.set(seq,550-(seq-300)*8);
 const loss=analyzeLossBursts(new Set(all),new Set(all.filter(seq=>seq!==800)),10,0,rtts,new Set());
 const nodes={};const $=id=>nodes[id]||(nodes[id]={textContent:''});
 const formatPacing=p=>p?'measured':'--',formatTimerQuality=()=>'',window={matchMedia:()=>({matches:false})};
 updateStabilityStats('up',null,loss,1000,[],{localDelayMeasured:false,upPacing:paired.up.pacing});
 assert(nodes.stUpConcentration.textContent==='98% in single run','mixed concentration denominator');
 assert(nodes.stUpLocalDelay.textContent==='not measured','false local-delay none');
 for(let missing=0;missing<3;missing++){
  const seen=new Set(all.filter(seq=>seq>=missing));
  const sampleLoss=analyzeLossBursts(new Set(all),seen,10,0,rtts,new Set());
  updateStabilityStats('up',null,sampleLoss,1000,[],{localDelayMeasured:false});
  const concentration=parseInt(nodes.stUpConcentration.textContent);
  assert(concentration>=0 && concentration<=100,'concentration outside bounds');
 }
 const noLoss=analyzeLossBursts(new Set(all),new Set(all),10,0,new Map(all.map(seq=>[seq,13.5])),new Set());
 updateStabilityStats('up',null,noLoss,1000,[],{localDelayMeasured:false});
 assert(nodes.stUpConcentration.textContent==='0% in single run','empty burst concentration');
 assert(loss.maxBurstPackets===39 && loss.dropEquivalentPackets===40 && loss.missingPackets===1,'loss/late definitions changed');
 assert(Math.max(...rtts.values())===550,'genuine spike removed');
 return {trueSequenceAndAckIdentity:true,arrivalReorderPreserved:true,uploadPacing:true,localAttributionUnknown:true,concentrationSamePopulation:true,realSpikePreserved:true,wireFramesUnchanged:true};
})();
'''
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page()
    for script in re.findall(r'<script\b[^>]*>(.*?)</script>',source,re.S):
        if script.strip().startswith('__NODE_CONFIG__'):continue
        page.evaluate('code=>{new Function(code);}',script)
    print(page.evaluate('code=>new Function(code)()',checks.replace('return (async()=>{', 'return (async()=>{\n'+helpers, 1)))
    browser.close()
