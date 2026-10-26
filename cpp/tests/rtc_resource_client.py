"""Browser test client for actual native RTC resource limits, never a backend."""
RESOURCE_CLIENT = r"""() => {
 const peers=new Map(),wait=ms=>new Promise(r=>setTimeout(r,ms));let serial=0;
 const until=async(fn,ms,label)=>{const end=performance.now()+ms;while(!fn()){if(performance.now()>end)throw Error(label);await wait(5);}};
 const hash=s=>{let h=2166136261;for(let i=0;i<s.length;i++)h=Math.imul(h^s.charCodeAt(i),16777619);return h>>>0;};
 const frame=(kind,sh,th,seq,sentAt)=>{const b=new ArrayBuffer(128),v=new DataView(b);v.setUint8(0,kind);v.setUint32(4,sh,true);v.setUint32(8,th,true);v.setUint32(12,seq,true);v.setFloat64(16,sentAt,true);return b;};
 const post=async(attempt,body)=>{const r=await fetch('/webrtc/offer',{method:'POST',headers:{'Content-Type':'application/json','X-Speedtest-Attempt-Id':attempt},body:JSON.stringify({...body,attemptId:attempt})});return {status:r.status,body:r.status===204?null:await r.json()};};
 const accept=(p,v)=>{p.controls.push(v);};
 window.__rtcResources={
   async open(attempt=null){
     const id=String(++serial);attempt=attempt||'resource-'+crypto.randomUUID();
     const pc=new RTCPeerConnection({iceServers:[]}),control=pc.createDataChannel('control'),test=pc.createDataChannel('test',{ordered:false,maxRetransmits:0});test.binaryType='arraybuffer';
     const p={pc,control,test,attempt,acks:new Set(),controls:[],fragments:new Map(),closedAt:null};peers.set(id,p);
     const closed=()=>{if(p.closedAt===null)p.closedAt=performance.now();};
     control.addEventListener('close',closed);test.addEventListener('close',closed);
     test.addEventListener('message',e=>{if(!(e.data instanceof ArrayBuffer)||e.data.byteLength!==128)throw Error('invalid resource probe frame');const v=new DataView(e.data),kind=v.getUint8(0),sh=v.getUint32(4,true),th=v.getUint32(8,true),seq=v.getUint32(12,true),at=v.getFloat64(16,true);if(kind===2)p.acks.add(seq);if(kind===3 && test.readyState==='open')test.send(frame(4,sh,th,seq,at));});
     control.addEventListener('message',e=>{const v=JSON.parse(e.data);if(v.type!=='controlFragment'){accept(p,v);return;}let parts=p.fragments.get(v.fragmentId);if(!parts){parts=new Array(v.count);p.fragments.set(v.fragmentId,parts);}parts[v.index]=Uint8Array.from(atob(v.data),c=>c.charCodeAt(0));if(parts.filter(Boolean).length===v.count){const bytes=new Uint8Array(parts.reduce((n,x)=>n+x.length,0));let at=0;for(const x of parts){bytes.set(x,at);at+=x.length;}accept(p,JSON.parse(new TextDecoder().decode(bytes)));p.fragments.delete(v.fragmentId);}});
     await pc.setLocalDescription(await pc.createOffer());await until(()=>pc.iceGatheringState==='complete',5000,'resource ICE gathering');
     p.offer={type:'offer',sdp:pc.localDescription.sdp};p.requestedAt=performance.now();
     const r=await post(attempt,p.offer);p.answer=r.status===200?r.body.sdp:null;p.offerMs=performance.now()-p.requestedAt;
     if(r.status===200){await pc.setRemoteDescription(r.body);await until(()=>test.readyState==='open'&&control.readyState==='open',8000,'resource native channels '+pc.connectionState);}
     return {id,attempt,status:r.status,body:r.status===200?null:r.body,offerMs:p.offerMs};
   },
   async probe(id,seq=0){const p=peers.get(id);p.test.send(frame(1,0x12345678,0x87654321,seq,performance.now()));await until(()=>p.acks.has(seq),5000,'resource ACK '+seq);return true;},
   candidatePorts(id){return peers.get(id).answer.split(/\r?\n/).filter(s=>s.startsWith('a=candidate:')).map(s=>Number(s.split(/\s+/)[5]));},
   async cancel(id){return await post(peers.get(id).attempt,{action:'cancel'});},
   async replay(id){const p=peers.get(id);return await post(p.attempt,p.offer);},
   async closed(id,ms=5000){const p=peers.get(id);try{await until(()=>p.test.readyState==='closed'&&p.control.readyState==='closed',ms,'resource remote channel close');}catch(error){throw Error(String(error)+' '+JSON.stringify({pc:p.pc.connectionState,ice:p.pc.iceConnectionState,sctp:p.pc.sctp?.state,dtls:p.pc.sctp?.transport?.state,test:p.test.readyState,control:p.control.readyState,testBuffered:p.test.bufferedAmount,controlBuffered:p.control.bufferedAmount,acknowledged:p.acks.size}));}return {closed:true,elapsedMs:p.closedAt-p.requestedAt};},
   async controls(id,count,bytes=2){const p=peers.get(id);for(let i=0;i<count;i++)p.control.send(bytes===2?'{}':'x'.repeat(bytes));return true;},
   async testFlood(id){const p=peers.get(id),deadline=performance.now()+60000;let sent=0;const watchdog=()=>{if(performance.now()>deadline)throw Error('resource flood watchdog '+JSON.stringify({sent,acknowledged:p.acks.size,buffered:p.test.bufferedAmount,state:p.test.readyState}));};for(;sent<40000&&p.test.readyState==='open';sent++){watchdog();p.test.send(frame(1,0x12345678,0x87654321,sent,performance.now()));if(sent%64===63)await wait(2);while(p.test.bufferedAmount>262144&&p.test.readyState==='open'){watchdog();await wait(2);}}await this.closed(id,10000);return {sent,acknowledged:p.acks.size};},
   async downStart(id,token,durationMs=200){const p=peers.get(id),testId='resource-test-'+token,sessionId='resource-session-'+token;const m={type:'downStart',testId,sessionId,sessionHash:hash(sessionId),testHash:hash(testId),rate:20,durationMs};p.control.send(JSON.stringify(m));return testId;},
   async ready(id,testId){const p=peers.get(id);await until(()=>p.controls.some(v=>v.type==='measurementReady'&&v.testId===testId),5000,'resource measurementReady');return true;},
   async result(id,testId){const p=peers.get(id);await until(()=>p.controls.some(v=>v.type==='downResult'&&v.testId===testId),6000,'resource bounded result');return p.controls.find(v=>v.type==='downResult'&&v.testId===testId);},
   async closeAll(){for(const p of peers.values()){try{if(p.control.readyState==='open')p.control.send(JSON.stringify({type:'close'}));await post(p.attempt,{action:'cancel'});}finally{p.pc.close();}}peers.clear();},
   wait
 };
}"""
