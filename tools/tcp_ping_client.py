"""TX-2 client (runs on s1): send a probe every interval over one TCP connection, log RTT.

Probes go out on a fixed schedule (start + k * interval) whether or not
earlier echoes have returned, so one probe is sent every interval as the spec
asks. TCP returns the echoes in order, however late, and each is logged when
it arrives. Output CSV: t,rtt_ms with one row per probe, where t = send time
- t0; a probe still unanswered FINAL_WAIT after the last send, or when SIGTERM
arrives, gets an empty rtt_ms.
"""
import argparse
import select
import signal
import socket
import struct
import time

MSG_SIZE = 64          # bytes per probe; first 4 bytes are the sequence number
FINAL_WAIT = 2.0       # seconds to wait for outstanding echoes after the last probe
SLICE = 0.1            # longest single wait, so a SIGTERM is noticed promptly
CONNECT_TIMEOUT = 2.0
CONNECT_RETRY = 5.0    # seconds to keep retrying the initial connect

stopping = False


def stop(signum, frame):
    global stopping
    stopping = True


def connect(server, port):
    deadline = time.monotonic() + CONNECT_RETRY
    while True:
        try:
            sock = socket.create_connection((server, port), timeout=CONNECT_TIMEOUT)
            sock.settimeout(None)   # a send may block while TCP backs off; that is data, not an error
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return sock
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def read_echoes(sock, buf, pending, out):
    """Read what has arrived and log one row per complete echo."""
    chunk = sock.recv(4096)
    if not chunk:
        raise ConnectionError('echo server closed the connection')
    arrived = time.monotonic()
    buf.extend(chunk)
    while len(buf) >= MSG_SIZE:
        seq = struct.unpack('!I', bytes(buf[:4]))[0]
        del buf[:MSG_SIZE]
        t, sent = pending.pop(seq)
        out.write('%.3f,%.3f\n' % (t, (arrived - sent) * 1000))
    out.flush()


def wait_for_echoes(sock, buf, pending, out, until):
    """Log echoes as they arrive until the monotonic time `until`."""
    while True:
        remaining = until - time.monotonic()
        if remaining <= 0:
            return
        if select.select([sock], [], [], remaining)[0]:
            read_echoes(sock, buf, pending, out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--interval', type=float, default=0.1)
    parser.add_argument('--duration', type=float, default=10.0)
    parser.add_argument('--t0', type=float, default=None)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    t0 = args.t0 if args.t0 is not None else time.time()
    signal.signal(signal.SIGTERM, stop)

    sock = connect(args.server, args.port)
    padding = b'\0' * (MSG_SIZE - 4)
    buf = bytearray()
    pending = {}   # seq -> (t, monotonic send time), in send order

    with open(args.out, 'w') as out:
        out.write('t,rtt_ms\n')
        start = time.monotonic()
        seq = 0
        while not stopping and seq * args.interval < args.duration:
            wait_for_echoes(sock, buf, pending, out, start + seq * args.interval)
            if stopping:
                break
            pending[seq] = (time.time() - t0, time.monotonic())
            sock.sendall(struct.pack('!I', seq) + padding)
            seq += 1

        deadline = time.monotonic() + FINAL_WAIT
        while pending and not stopping and time.monotonic() < deadline:
            wait_for_echoes(sock, buf, pending, out, min(deadline, time.monotonic() + SLICE))
        for t, _ in pending.values():
            out.write('%.3f,\n' % t)
    sock.close()


if __name__ == '__main__':
    main()
