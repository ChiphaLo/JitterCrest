"""Bounded loopback UDP relay used only by real encrypted WebRTC loss tests.

No decryption or SCTP implementation. A selected DTLS application datagram is
dropped once, while later packets on exactly the same ICE path keep flowing.
"""
import re
import selectors
import socket
import threading


class UdpProxy:
    def __init__(self):
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for sock in [self.client, self.server]:
            sock.bind(("127.0.0.1", 0))
            sock.setblocking(False)
        self.ports = dict(clientPort=self.client.getsockname()[1], serverPort=self.server.getsockname()[1])
        self.native = self.browser = None
        self.pending = []
        self.lock = threading.Lock()
        self.direction = None
        self.drops = []
        self.stop = threading.Event()
        self.failure = None
        self.thread = threading.Thread(target=self.run, name="rtc-loopback-loss-relay")
        self.thread.start()

    def target(self, sdp):
        candidates = []
        for line in sdp.splitlines():
            if line.startswith("a=candidate:"):
                tokens = line.split()
                if len(tokens) >= 8 and tokens[2].lower() == "udp" and ":" not in tokens[4]:
                    candidates.append((tokens[4], int(tokens[5])))
        if not candidates:
            raise AssertionError("answer contains no native IPv4 UDP candidate")
        with self.lock:
            self.native = next((c for c in candidates if c[0] == "127.0.0.1"), candidates[0])
        return True

    def arm(self, direction):
        with self.lock:
            assert self.direction is None
            self.direction = direction
        return True

    def run(self):
        selector = selectors.DefaultSelector()
        selector.register(self.client, selectors.EVENT_READ, "up")
        selector.register(self.server, selectors.EVENT_READ, "down")
        try:
            while not self.stop.is_set():
                for key, _ in selector.select(.02):
                    try:
                        data, source = key.fileobj.recvfrom(65536)
                    except BlockingIOError:
                        continue
                    direction = key.data
                    with self.lock:
                        if direction == "up":
                            self.browser = source
                        if self.direction == direction and len(data) >= 180 and data[0] == 23:
                            self.drops.append(dict(direction=direction, bytes=len(data)))
                            self.direction = None
                            continue
                        destination = self.native if direction == "up" else self.browser
                        output = self.server if direction == "up" else self.client
                        if destination:
                            output.sendto(data, destination)
                        elif len(self.pending) < 8:
                            self.pending.append((direction, data))
                        if self.native and self.browser and self.pending:
                            for path, packet in self.pending:
                                target = self.native if path == "up" else self.browser
                                (self.server if path == "up" else self.client).sendto(packet, target)
                            self.pending.clear()
        except BaseException as error:
            self.failure = error
        finally:
            selector.close()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        self.client.close()
        self.server.close()
        assert not self.thread.is_alive()
        if self.failure:
            raise self.failure
