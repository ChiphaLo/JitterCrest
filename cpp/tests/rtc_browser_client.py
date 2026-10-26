"""Browser-only wire client for both backends; never a JS/Node server."""
WIRE_CLIENT = r"""() => {
 const peers=new Map();let serial=0;
 const wait=ms=>new Promise(resolve=>setTimeout(resolve,Math.max(0,ms)));
 const until=async(predicate,ms,label)=>{const end=performance.now()+ms;while(!predicate()){if(performance.now()>=end)throw Error(label);await wait(5);}};
 const hash=s=>{let h=2166136261;for(let i=0;i<s.length;i++)h=Math.imul(h^s.charCodeAt(i),16777619);return h>>>0;};
 const frame=(kind,sh,th,seq,time)=>{const b=new ArrayBuffer(128),v=new DataView(b);v.setUint8(0,kind);v.setUint32(4,sh,true);v.setUint32(8,th,true);v.setUint32(12,seq,true);v.setFloat64(16,time,true);return b;};
 const rewrite=(sdp,port)=>sdp.split(/\r?\n/).filter(s=>s && !s.startsWith('a=candidate:') && s!=='a=end-of-candidates').join('\r\n')+'\r\na=candidate:relay 1 UDP 2130706431 127.0.0.1 '+port+' typ host\r\na=end-of-candidates\r\n';
 const post=async(attempt,body)=>{const r=await fetch('/webrtc/offer',{method:'POST',headers:{'Content-Type':'application/json','X-Speedtest-Attempt-Id':attempt},body:JSON.stringify({...body,attemptId:attempt})});return {status:r.status,body:r.status===204?null:await r.json()};};
 const sendCompletion=(p,m,value,fragmented)=>{
   if(!fragmented){p.control.send(JSON.stringify(value));return;}
   const bytes=new TextEncoder().encode(JSON.stringify(value)),parts=[];
   for(let at=0;at<bytes.length;at+=80){let text='';for(const b of bytes.slice(at,at+80))text+=String.fromCharCode(b);parts.push(btoa(text));}
   const order=Array.from({length:parts.length},(_,i)=>parts.length-1-i);order.unshift(order[0]);
   for(const index of order)p.control.send(JSON.stringify({type:'controlFragment',messageType:'uploadComplete',encoding:'base64',fragmentId:'upload-'+m.testId,index,count:parts.length,data:parts[index],testId:m.testId,sessionId:m.sessionId,sessionHash:m.sessionHash,testHash:m.testHash}));
 };
 window.__rtcWire={
   async connect(relay=null){
     const id=String(++serial),attempt='wire-'+id+'-'+crypto.randomUUID();
     const pc=new RTCPeerConnection({iceServers:[]}),control=pc.createDataChannel('control'),test=pc.createDataChannel('test',{ordered:false,maxRetransmits:0});test.binaryType='arraybuffer';
     const p={pc,control,test,attempt,states:new Map(),acks:new Map(),fragments:new Map(),fragmentCount:0,errors:[]};peers.set(id,p);
     test.addEventListener('message',event=>{
       if(!(event.data instanceof ArrayBuffer) || event.data.byteLength!==128){p.errors.push('invalid frame size');return;}
       const v=new DataView(event.data),kind=v.getUint8(0),sh=v.getUint32(4,true),th=v.getUint32(8,true),seq=v.getUint32(12,true),sentAt=v.getFloat64(16,true),flags=v.getUint8(1);
       const m=p.states.get(sh+':'+th);
       if(kind===2){p.acks.set(seq,{seq,sentAt,flags});if(m){m.upAcks.set(seq,{seq,sentAt,flags});}}
       if(kind===3 && m){m.down.add(seq);p.test.send(frame(4,sh,th,seq,sentAt));}
       if(kind===5 && m)m.samples.set(seq,sentAt);
     });
     const accept=value=>{const m=p.states.get(value.sessionHash+':'+value.testHash);if(!m)return;if(value.type==='measurementReady')m.ready=value;if(value.type==='downResult')m.result=value;};
     control.addEventListener('message',event=>{
       let value;try{value=JSON.parse(event.data);}catch{p.errors.push('invalid control JSON');return;}
       if(value.type!=='controlFragment'){accept(value);return;}
       p.fragmentCount++;let a=p.fragments.get(value.fragmentId);if(!a){a={parts:new Array(value.count),received:0};p.fragments.set(value.fragmentId,a);}
       if(a.parts[value.index])return;a.parts[value.index]=Uint8Array.from(atob(value.data),c=>c.charCodeAt(0));a.received++;
       if(a.received===a.parts.length){const bytes=new Uint8Array(a.parts.reduce((n,x)=>n+x.length,0));let at=0;for(const part of a.parts){bytes.set(part,at);at+=part.length;}accept(JSON.parse(new TextDecoder().decode(bytes)));p.fragments.delete(value.fragmentId);}
     });
     const offer=await pc.createOffer();await pc.setLocalDescription(offer);
     await until(()=>pc.iceGatheringState==='complete',5000,'client ICE gathering');
     p.offer={type:'offer',sdp:relay?rewrite(pc.localDescription.sdp,relay.serverPort):pc.localDescription.sdp};
     const response=await post(attempt,p.offer);if(response.status!==200)throw Error('offer '+JSON.stringify(response));
     let answer=response.body;if(relay){await window.__proxyTarget(answer.sdp);answer={type:answer.type,sdp:rewrite(answer.sdp,relay.clientPort)};}
     await pc.setRemoteDescription(answer);
     await until(()=>control.readyState==='open' && test.readyState==='open',8000,'native channels '+pc.connectionState);
     return id;
   },
   async measure(id,options={}){
     const p=peers.get(id),rate=options.rate||20,durationMs=options.durationMs||2000,planned=Math.floor(rate*durationMs/1000);
     const testId='test-'+crypto.randomUUID(),sessionId='session-'+crypto.randomUUID(),sessionHash=hash(sessionId),testHash=hash(testId);
     const m={testId,sessionId,sessionHash,testHash,upAcks:new Map(),down:new Set(),samples:new Map(),ready:null,result:null};p.states.set(sessionHash+':'+testHash,m);
     const ids={testId,sessionId,sessionHash,testHash};
     p.control.send(JSON.stringify({type:'downStart',...ids,rate,durationMs}));await until(()=>m.ready,4000,'measurementReady');
     const start=performance.now(),sentSeqs=[],order=options.order||Array.from({length:planned},(_,i)=>i);
     if(!options.earlyInvalid){for(let i=0;i<order.length;i++){await wait(start+i*1000/rate-performance.now());const seq=order[i];p.test.send(frame(1,sessionHash,testHash,seq,performance.now()));if(!sentSeqs.includes(seq))sentSeqs.push(seq);}await wait(start+durationMs-performance.now());}
     const completion={type:'uploadComplete',...ids,planned,scheduled:planned,skipped:0,actualSent:planned,sentSeqs:Array.from({length:planned},(_,i)=>i),...(options.completion||{})};
     if(options.omitField)delete completion[options.omitField];
     if(!options.missing){sendCompletion(p,m,completion,options.fragmented===true);if(options.duplicate)sendCompletion(p,m,completion,false);}
     await until(()=>m.result,durationMs+6000,'downResult');
     const value={result:m.result,ready:m.ready,upAckSeqs:[...m.upAcks.keys()].sort((a,b)=>a-b),downSeqs:[...m.down].sort((a,b)=>a-b),
       reordered:[...m.upAcks.values()].filter(a=>a.flags&1).map(a=>a.seq).sort((a,b)=>a-b),sampleSeqs:[...m.samples.keys()].sort((a,b)=>a-b),
       resultFragments:p.fragmentCount,errors:p.errors};p.states.delete(sessionHash+':'+testHash);return value;
   },
   async probes(id,sequences,spacing=50){const p=peers.get(id);for(const seq of sequences){p.test.send(frame(1,0x12345678,0x87654321,seq,performance.now()));await wait(spacing);}return true;},
   acks(id){return [...peers.get(id).acks.keys()].sort((a,b)=>a-b);},
   async duplicateOffer(id){const p=peers.get(id);return (await post(p.attempt,p.offer)).status;},
   async cancel(id){const p=peers.get(id);return (await post(p.attempt,{action:'cancel'})).status;},
   async cancelledOffer(id){const p=peers.get(id);return (await post(p.attempt,p.offer)).status;},
   async cancelUnknown(){const id='unknown-'+crypto.randomUUID();return [(await post(id,{action:'cancel'})).status,(await post(id,{action:'cancel'})).status,(await post(id,{type:'offer',sdp:'v=0\r\n'})).status];},
   async badOffer(){const id='bad-'+crypto.randomUUID(),body={type:'offer',sdp:'v=0\r\n'};return [(await post(id,body)).status,(await post(id,body)).status];},
   ignoreNoise(id){const p=peers.get(id);p.control.send('{');p.control.send('[]');p.control.send(JSON.stringify({type:'unknown'}));},
   async malformed(id){const p=peers.get(id);p.test.send(new Uint8Array(127));await until(()=>p.test.readyState==='closed',5000,'malformed frame close');return (await post(p.attempt,p.offer)).status;},
   async close(id){const p=peers.get(id);if(!p)return;try{if(p.control.readyState==='open')p.control.send(JSON.stringify({type:'close'}));}catch{}await post(p.attempt,{action:'cancel'});p.pc.close();peers.delete(id);},
   async closeAll(){for(const id of [...peers.keys()])await this.close(id);},
   wait
 };
}"""
