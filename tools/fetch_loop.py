"""TX-3 client (runs on d2): download the URL every interval over ONE TCP connection.

All downloads share one HTTP/1.1 keep-alive connection, so S2-D2 is a single
TCP session for the whole run. Downloads start on a fixed tick
(start + k * interval). One connection carries one download at a time, so a
download that overruns its tick delays the next one, which then starts at once.
Output CSV: t_start,t_end,bytes with times relative to t0. A download cut off
by an error or by SIGTERM logs the bytes received so far.
"""
import argparse
import http.client
import signal
import sys
import time
from urllib.parse import urlsplit

CHUNK = 4096           # bytes per read; also the precision of a cut-off download's count
TIMEOUT = 30.0         # seconds without progress before a download is abandoned
CONNECT_RETRY = 5.0    # seconds to keep retrying the initial connect
POLL = 0.05            # seconds between checks for the next tick or a stop request

stopping = False


def stop(signum, frame):
    global stopping
    stopping = True


def connect(host, port):
    deadline = time.monotonic() + CONNECT_RETRY
    while True:
        conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        try:
            conn.connect()
            return conn
        except OSError:
            conn.close()
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def download(conn, path):
    """GET path over conn; return the body bytes received, even if cut short."""
    received = 0
    try:
        conn.request('GET', path)
        response = conn.getresponse()
        while not stopping:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            received += len(chunk)
    except (OSError, http.client.HTTPException) as e:
        # http.client reconnects on the next request, opening a new session.
        sys.stderr.write('download failed after %d bytes: %r\n' % (received, e))
        conn.close()
    return received


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--interval', type=float, default=3.0)
    parser.add_argument('--duration', type=float, default=15.0)
    parser.add_argument('--t0', type=float, default=None)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    t0 = args.t0 if args.t0 is not None else time.time()
    url = urlsplit(args.url)
    signal.signal(signal.SIGTERM, stop)

    conn = connect(url.hostname, url.port or 80)
    with open(args.out, 'w') as out:
        out.write('t_start,t_end,bytes\n')
        out.flush()
        start = time.monotonic()
        k = 0
        while not stopping and k * args.interval < args.duration:
            delay = start + k * args.interval - time.monotonic()
            if delay > 0:
                time.sleep(min(delay, POLL))
                continue
            t_start = time.time() - t0
            received = download(conn, url.path)
            out.write('%.3f,%.3f,%d\n' % (t_start, time.time() - t0, received))
            out.flush()
            # Ticks that passed entirely during an overrunning download are
            # dropped, so late downloads are not bunched together.
            k = max(k + 1, int((time.monotonic() - start) / args.interval))
    conn.close()


if __name__ == '__main__':
    main()
