"""Sample a router's queue (tc netem backlog and drops) at a fixed interval.

Runs inside a router's network namespace (launched with host.popen). Writes
t,backlog_pkts,dropped_total with t = time.time() - t0. When several
interfaces are given (R2 has two egress interfaces) their backlog and drops
are summed into one row.

"""
import argparse
import math
import re
import signal
import subprocess
import sys
import time

# One netem block: "qdisc netem ... Sent ... (dropped N, ...) backlog Xb Yp ..."
# The htb block above it has the same drop count but no useful backlog.
NETEM = re.compile(r'qdisc netem.*?\(dropped (\d+).*?backlog \d+b (\d+)p', re.DOTALL)

stop = False


def _on_signal(signum, frame):
    global stop
    stop = True


def read_queue(iface):
    """Return (backlog_pkts, dropped_total) for one interface, or None."""
    try:
        out = subprocess.run(['tc', '-s', 'qdisc', 'show', 'dev', iface],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = NETEM.search(out)
    if match is None:
        return None
    return int(match.group(2)), int(match.group(1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ifaces', nargs='+', required=True,
                        help='interface(s) to sample; values are summed')
    parser.add_argument('--out', required=True, help='output CSV path')
    parser.add_argument('--t0', type=float, default=None,
                        help='run start (time.time()); default: now')
    parser.add_argument('--interval', type=float, default=0.1)
    args = parser.parse_args()

    t0 = args.t0 if args.t0 is not None else time.time()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    with open(args.out, 'w') as f:
        f.write('t,backlog_pkts,dropped_total\n')
        f.flush()
        # Sample on the grid t0 + k*interval so rows line up with the other
        # CSVs and a slow tc call cannot make the schedule drift.
        k = max(0, math.ceil((time.time() - t0) / args.interval))
        while not stop:
            target = t0 + k * args.interval
            delay = target - time.time()
            if delay > 0:
                time.sleep(delay)
                if stop:
                    break
            readings = [read_queue(i) for i in args.ifaces]
            if all(r is not None for r in readings):
                backlog = sum(r[0] for r in readings)
                dropped = sum(r[1] for r in readings)
                f.write('%.3f,%d,%d\n' % (time.time() - t0, backlog, dropped))
                f.flush()
            # Skip any slots we fell behind on instead of firing a burst.
            k = max(k + 1, math.ceil((time.time() - t0) / args.interval))


if __name__ == '__main__':
    sys.exit(main())