"""Run the Reno vs Cubic experiment once per queue size; results go to <outdir>/q<N>/."""
import argparse
import datetime
import json
import os
import platform
import time

from mininet.log import setLogLevel

import config as C
from monitor import start_monitors, stop_monitors
from topology import CONGESTION_CONTROL, build_net
from traffic import start_traffic, stop_traffic

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
# With iperf -w 16m, a smaller socket buffer cap silently limits the window.
MIN_SOCKET_BUF = 16 * 1024 * 1024


def _read_sysctl(name):
    try:
        with open('/proc/sys/' + name.replace('.', '/')) as f:
            return f.read().strip()
    except OSError:
        return None


def write_meta(qdir, queue_size, duration, t0):
    meta = {
        'queue_size': queue_size,
        'duration': duration,
        't0': t0,
        'date': datetime.datetime.fromtimestamp(t0).isoformat(),
        'link_bw_mbit': C.LINK_BW,
        'access_delay': C.ACCESS_DELAY,
        'access_queue': C.ACCESS_QUEUE,
        'bottleneck_iface': C.BOTTLENECK_IFACE,
        'congestion_control': CONGESTION_CONTROL,
        'offloads': 'tso/gso/gro off on all interfaces',
        'file_size_mb': C.FILE_SIZE_MB,
        'ping_interval': C.PING_INTERVAL,
        'download_interval': C.DOWNLOAD_INTERVAL,
        'kernel': platform.release(),
        'python': platform.python_version(),
        'net.core.rmem_max': _read_sysctl('net.core.rmem_max'),
        'net.core.wmem_max': _read_sysctl('net.core.wmem_max'),
    }
    with open(os.path.join(qdir, 'meta.json'), 'w') as f:
        json.dump(meta, f, indent=2)


def summarize(qdir, queue_size):
    def rows(name):
        try:
            with open(os.path.join(qdir, name)) as f:
                return f.read().splitlines()[1:]
        except OSError:
            return []

    ping = rows('ping.csv')
    timeouts = sum(1 for r in ping if r.endswith(','))
    downloads = [r for r in rows('downloads.csv')
                 if r.split(',')[2] == str(C.FILE_SIZE_BYTES)]
    rate = 'no final total (client stopped before reporting)'
    try:
        with open(os.path.join(qdir, 'iperf_s1.txt')) as f:
            totals = [l for l in f if ' 0.0000-' in l and ' 0.0000-1.0000 ' not in l]
        if totals:
            rate = ' '.join(totals[-1].split()[-2:])
    except OSError:
        pass
    print('q=%d: ping rows %d (%d timeouts), downloads complete %d, iperf %s'
          % (queue_size, len(ping), timeouts, len(downloads), rate))


def run_one(queue_size, duration, outdir):
    qdir = os.path.join(outdir, 'q%d' % queue_size)
    os.makedirs(qdir, exist_ok=True)
    net = build_net(queue_size)
    monitors = traffic = None
    try:
        t0 = time.time()
        write_meta(qdir, queue_size, duration, t0)
        monitors = start_monitors(net, qdir, t0)
        traffic = start_traffic(net, duration, qdir, t0)
        time.sleep(duration)
    finally:
        if traffic is not None:
            stop_traffic(traffic)
        if monitors is not None:
            stop_monitors(monitors)
        net.stop()
        os.system('mn -c > /dev/null 2>&1')
    summarize(qdir, queue_size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true',
                        help='short run of %d s' % C.QUICK_TIME)
    parser.add_argument('--queues', type=int, nargs='+', default=C.QUEUE_SIZES,
                        help='bottleneck queue sizes in packets (default: %s)' % C.QUEUE_SIZES)
    parser.add_argument('--outdir', default=os.path.join(REPO_DIR, 'results'))
    parser.add_argument('--duration', type=float, help='override run length in seconds')
    args = parser.parse_args()

    duration = args.duration or (C.QUICK_TIME if args.quick else C.EXPERIMENT_TIME)
    for name in ('net.core.rmem_max', 'net.core.wmem_max'):
        value = _read_sysctl(name)
        if value is not None and int(value) < MIN_SOCKET_BUF:
            print('WARNING: %s = %s; iperf -w 16m will be capped. Run: sudo sysctl -w %s=%d'
                  % (name, value, name, MIN_SOCKET_BUF))

    setLogLevel('warning')
    for queue_size in args.queues:
        print('=== queue %d packets, %g s' % (queue_size, duration))
        run_one(queue_size, duration, args.outdir)


if __name__ == '__main__':
    main()
