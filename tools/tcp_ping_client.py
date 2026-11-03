"""TX-2 client (runs on s1): send a probe every interval over one TCP connection, log RTT.

Probes go out on a fixed schedule (start + k * interval) whether or not
earlier echoes have returned, so one probe is sent every interval as the spec
asks. Echoes are matched to probes by sequence number. Output CSV: t,rtt_ms
with one row per probe, where t = send time - t0; a probe with no echo within
TIMEOUT gets an empty rtt_ms.
"""
import argparse
import collections
import select
import socket
import struct
import time

MSG_SIZE = 64          # bytes per probe; first 4 bytes are the sequence number
TIMEOUT = 2.0          # seconds to wait for an echo
CONNECT_RETRY = 5.0    # seconds to keep retrying the initial connect


def connect(server, port):
    deadline = time.monotonic() + CONNECT_RETRY
    while True:
        try:
            sock = socket.create_connection((server, port), timeout=TIMEOUT)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return sock
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


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

    sock = connect(args.server, args.port)
    padding = b'\0' * (MSG_SIZE - 4)
    buf = bytearray()
    pending = collections.OrderedDict()   # seq -> (t, monotonic send time), oldest first

    with open(args.out, 'w') as out:
        out.write('t,rtt_ms\n')
        start = time.monotonic()
        seq = 0
        while True:
            sending = seq * args.interval < args.duration
            if not sending and not pending:
                break
            now = time.monotonic()

            if sending and now >= start + seq * args.interval:
                pending[seq] = (time.time() - t0, now)
                sock.sendall(struct.pack('!I', seq) + padding)
                seq += 1
                continue

            # Sleep until the next send, the oldest probe's timeout, or an echo.
            wake = start + seq * args.interval if sending else float('inf')
            if pending:
                wake = min(wake, next(iter(pending.values()))[1] + TIMEOUT)
            readable, _, _ = select.select([sock], [], [], max(0.0, wake - now))
            if readable:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError('echo server closed the connection')
                arrived = time.monotonic()
                buf.extend(chunk)
                while len(buf) >= MSG_SIZE:
                    got = struct.unpack('!I', bytes(buf[:4]))[0]
                    del buf[:MSG_SIZE]
                    if got in pending:
                        t, sent = pending.pop(got)
                        out.write('%.3f,%.3f\n' % (t, (arrived - sent) * 1000))

            now = time.monotonic()
            while pending and next(iter(pending.values()))[1] + TIMEOUT <= now:
                t, _ = pending.popitem(last=False)[1]
                out.write('%.3f,\n' % t)
            out.flush()
    sock.close()


if __name__ == '__main__':
    main()
