"""Sample a sender's TCP state (cwnd, RTT, bytes_acked) from `ss -tin`.

Runs inside s1 or s2's network namespace (launched with host.popen).
Writes two CSVs, both with t = time.time() - t0:
  --out        t,cwnd_segments,rtt_ms
  --acked-out  t,bytes_acked          (cumulative; throughput.csv is built from it)

--filter is an ss filter expression, e.g. "( dport = :5001 )" on s1 (the iperf
flow, not the ping train on 5002) or "( sport = :8000 )" on s2 (HTTP server).
Rows are skipped while no matching connection exists.

"""
import argparse
import math
import re
import signal
import subprocess
import sys
import time

CWND = re.compile(r'\bcwnd:(\d+)')
RTT = re.compile(r'\brtt:([\d.]+)/')
ACKED = re.compile(r'\bbytes_acked:(\d+)')

stop = False


def _on_signal(signum, frame):
    global stop
    stop = True


def read_connection(flt):
    """Return (cwnd, rtt_ms or None, bytes_acked) for the matching connection, or None."""
    try:
        out = subprocess.run(['ss', '-tin', 'state', 'established', flt],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    best = None
    for line in out.splitlines():          # the info line carries cwnd:, rtt:, ...
        cwnd, acked = CWND.search(line), ACKED.search(line)
        if cwnd is None or acked is None:
            continue
        rtt = RTT.search(line)
        row = (int(cwnd.group(1)), float(rtt.group(1)) if rtt else None,
               int(acked.group(1)))
        if best is None or row[2] > best[2]:   # several connections: keep the busiest
            best = row
    return best


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--filter', required=True, help='ss filter, e.g. "( dport = :5001 )"')
    parser.add_argument('--out', required=True, help='cwnd CSV path')
    parser.add_argument('--acked-out', required=True, help='bytes_acked CSV path')
    parser.add_argument('--t0', type=float, default=None, help='run start; default: now')
    parser.add_argument('--interval', type=float, default=0.1)
    args = parser.parse_args()

    t0 = args.t0 if args.t0 is not None else time.time()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    with open(args.out, 'w') as f, open(args.acked_out, 'w') as g:
        f.write('t,cwnd_segments,rtt_ms\n')
        g.write('t,bytes_acked\n')
        f.flush()
        g.flush()
        k = max(0, math.ceil((time.time() - t0) / args.interval))
        while not stop:
            target = t0 + k * args.interval
            delay = target - time.time()
            if delay > 0:
                time.sleep(delay)
                if stop:
                    break
            reading = read_connection(args.filter)
            if reading is not None:
                t = time.time() - t0
                cwnd, rtt, acked = reading
                f.write('%.3f,%d,%s\n' % (t, cwnd, '' if rtt is None else '%.3f' % rtt))
                g.write('%.3f,%d\n' % (t, acked))
                f.flush()
                g.flush()
            k = max(k + 1, math.ceil((time.time() - t0) / args.interval))


if __name__ == '__main__':
    sys.exit(main())