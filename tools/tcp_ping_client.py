"""TX-2 client (runs on s1): send a probe every interval over one TCP connection, log RTT.

Output CSV: t,rtt_ms where t = send time - t0. A probe with no echo within
TIMEOUT is logged with an empty rtt_ms.
"""
import argparse
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


def wait_for_echo(sock, buf, seq, deadline):
    """Read until the echo for seq arrives or deadline passes.

    Late echoes of earlier (timed-out) probes are discarded so the stream
    stays in step with the sequence numbers.
    """
    while True:
        while len(buf) >= MSG_SIZE:
            got = struct.unpack('!I', bytes(buf[:4]))[0]
            del buf[:MSG_SIZE]
            if got == seq:
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            return False
        if not chunk:
            raise ConnectionError('echo server closed the connection')
        buf.extend(chunk)


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
    buf = bytearray()
    padding = b'\0' * (MSG_SIZE - 4)

    with open(args.out, 'w') as out:
        out.write('t,rtt_ms\n')
        start = time.monotonic()
        seq = 0
        while True:
            # Fixed schedule: probe k goes out at start + k * interval. A probe
            # that is due goes out at once; ticks wholly spent waiting for a
            # slow echo are skipped, so probes never bunch up.
            next_send = start + seq * args.interval
            if next_send - start >= args.duration:
                break
            now = time.monotonic()
            if now < next_send:
                time.sleep(next_send - now)

            t = time.time() - t0
            sent = time.monotonic()
            sock.sendall(struct.pack('!I', seq) + padding)
            if wait_for_echo(sock, buf, seq, sent + TIMEOUT):
                out.write('%.3f,%.3f\n' % (t, (time.monotonic() - sent) * 1000))
            else:
                out.write('%.3f,\n' % t)
            out.flush()

            seq = max(seq + 1, int((time.monotonic() - start) / args.interval))
    sock.close()


if __name__ == '__main__':
    main()
