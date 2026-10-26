"""Bounded delay/loss relay for owned encrypted loopback test traffic."""
import heapq,selectors,time
from collections import Counter
from rtc_udp_proxy import UdpProxy


class ImpairedRelay(UdpProxy):
    def __init__(self,mode,delay=.06):
        assert mode in ['delay','periodic','burst','up-blackout'] and 0<=delay<=.5
        self.mode,self.delay=mode,delay
        self.active=None
        self.application=Counter();self.forwarded=Counter();self.ingress=Counter()
        self.heap=[];self.queued_bytes=0;self.max_queued_bytes=0;self.serial=0
        super().__init__()

    def activate(self):
        with self.lock:self.active=time.monotonic()

    def schedule(self,direction,data,destination):
        self.serial+=1
        if len(self.heap)>=4096 or self.queued_bytes+len(data)>4<<20:
            raise RuntimeError('impaired relay bounded queue exhausted; not an intended loss')
        heapq.heappush(self.heap,(time.monotonic()+self.delay,self.serial,direction,data,destination))
        self.queued_bytes+=len(data)
        self.max_queued_bytes=max(self.max_queued_bytes,self.queued_bytes)

    def run(self):
        selector=selectors.DefaultSelector()
        selector.register(self.client,selectors.EVENT_READ,'up')
        selector.register(self.server,selectors.EVENT_READ,'down')
        try:
            while not self.stop.is_set():
                now=time.monotonic()
                while self.heap and self.heap[0][0]<=now:
                    _,_,direction,data,destination=heapq.heappop(self.heap)
                    self.queued_bytes-=len(data)
                    output=self.server if direction=='up' else self.client
                    output.sendto(data,destination)
                    self.forwarded[direction]+=1
                wait=min(.01,max(0,self.heap[0][0]-time.monotonic())) if self.heap else .01
                for key,_ in selector.select(wait):
                    try:data,source=key.fileobj.recvfrom(65536)
                    except BlockingIOError:continue
                    direction=key.data
                    self.ingress[direction]+=1
                    with self.lock:
                        if direction=='up':self.browser=source
                        elapsed=time.monotonic()-self.active if self.active is not None else None
                        drop=False
                        if elapsed is not None and data and data[0]==23:
                            self.application[direction]+=1
                            n=self.application[direction]
                            drop=(self.mode=='periodic' and n%137==0 or
                                  self.mode=='burst' and n in [150,151,152] or
                                  self.mode=='up-blackout' and direction=='up' and 6<=elapsed<20)
                        if drop:
                            self.drops.append(dict(direction=direction,application_datagram=self.application[direction],seconds=elapsed,bytes=len(data)))
                            continue
                        destination=self.native if direction=='up' else self.browser
                        if destination:self.schedule(direction,data,destination)
                        elif len(self.pending)<8:self.pending.append((direction,data))
                        else:raise RuntimeError('initial destination queue exhausted')
                        if self.native and self.browser and self.pending:
                            for path,packet in self.pending:
                                self.schedule(path,packet,self.native if path=='up' else self.browser)
                            self.pending.clear()
        except BaseException as error:self.failure=error
        finally:selector.close()

    def observation(self):
        with self.lock:
            return dict(mode=self.mode,one_way_delay_ms=self.delay*1000,intended_drops=list(self.drops),
                        ingress=dict(self.ingress),forwarded=dict(self.forwarded),
                        max_queued_bytes=self.max_queued_bytes,queue_byte_limit=4<<20,
                        packet_payloads_saved=False,unexpected_failure=str(self.failure) if self.failure else None)
