"""Run the Reno vs Cubic experiment once per queue size; results go to <outdir>/q<N>/."""
import argparse
import datetime
import json
import os
import platform
import signal
import time

from mininet.log import setLogLevel

import config as C
from monitor import start_monitors, stop_monitors
from topology import CONGESTION_CONTROL, build_net
from traffic import start_traffic, stop_traffic

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
# The iperf server asks for a 16 MB window (-w 16m); the kernel silently caps
# it at these limits, so they are raised for the run and restored afterwards.
SOCKET_BUF_SYSCTLS = ('net.core.rmem_max', 'net.core.wmem_max')
MIN_SOCKET_BUF = 16 * 1024 * 1024


def _sysctl_path(name):
    return '/proc/sys/' + name.replace('.', '/')


def _read_sysctl(name):
    try:
        with open(_sysctl_path(name)) as f:
            return f.read().strip()
    except OSError:
        return None


def _write_sysctl(name, value):
    with open(_sysctl_path(name), 'w') as f:
        f.write(str(value))


def write_meta(qdir, queue_size, duration, t0):
    meta = {
        'queue_size': queue_size,
        'duration': duration,
        't0': t0,
        'date': datetime.datetime.fromtimestamp(t0).isoformat(),
        'link_bw_mbit': C.LINK_BW,
        'host_delay': C.ACCESS_DELAY,
        'host_queue': C.ACCESS_QUEUE,
        'router_queue': queue_size,
        'bottleneck_iface': C.BOTTLENECK_IFACE,
        'congestion_control': CONGESTION_CONTROL,
        'offloads': 'tso/gso/gro off on all interfaces',
        'file_size_bytes': C.FILE_SIZE_BYTES,
        'ping_interval': C.PING_INTERVAL,
        'download_interval': C.DOWNLOAD_INTERVAL,
        'kernel': platform.release(),
        'python': platform.python_version(),
    }
    for name in SOCKET_BUF_SYSCTLS:
        meta[name] = _read_sysctl(name)
    with open(os.path.join(qdir, 'meta.json'), 'w') as f:
        json.dump(meta, f, indent=2)


def _iperf_total(path):
    """Return the rate from iperf's whole-run summary line, or None."""
    try:
        with open(path) as f:
            totals = [l for l in f if ' 0.0000-' in l and ' 0.0000-1.0000 ' not in l]
    except OSError:
        return None
    return ' '.join(totals[-1].split()[-2:]) if totals else None


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
    rate = _iperf_total(os.path.join(qdir, 'iperf_s1.txt'))
    if rate is None:
        server_rate = _iperf_total(os.path.join(qdir, 'iperf_d1.txt'))
        rate = '%s (server side)' % server_rate if server_rate else 'no total reported'
    print('q=%d: ping rows %d (%d timeouts), downloads complete %d, iperf %s'
          % (queue_size, len(ping), timeouts, len(downloads), rate))


def kill_leftovers(net):
    """Kill any process still running in a host's network namespace.

    stop_traffic and stop_monitors should leave none; this is a safety net so a
    run always terminates cleanly. Returns 'host: command' for each kill.
    """
    killed = []
    for host in net.hosts:
        try:
            netns = os.readlink('/proc/%d/ns/net' % host.pid)
        except OSError:
            continue
        for entry in os.listdir('/proc'):
            if not entry.isdigit() or int(entry) == host.pid:
                continue
            try:
                if os.readlink('/proc/%s/ns/net' % entry) != netns:
                    continue
                with open('/proc/%s/cmdline' % entry, 'rb') as f:
                    command = f.read().replace(b'\0', b' ').decode().strip()
                os.kill(int(entry), signal.SIGKILL)
                killed.append('%s: %s' % (host.name, command))
            except OSError:
                pass
    return killed


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
        for line in kill_leftovers(net):
            print('WARNING: killed leftover process ' + line)
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

    saved = {}
    for name in SOCKET_BUF_SYSCTLS:
        value = _read_sysctl(name)
        if value is not None and int(value) < MIN_SOCKET_BUF:
            saved[name] = value
            _write_sysctl(name, MIN_SOCKET_BUF)
            print('%s raised from %s to %d for this run' % (name, value, MIN_SOCKET_BUF))

    setLogLevel('warning')
    try:
        for queue_size in args.queues:
            print('=== queue %d packets, %g s' % (queue_size, duration))
            run_one(queue_size, duration, args.outdir)
    finally:
        for name, value in saved.items():
            _write_sysctl(name, value)


if __name__ == '__main__':
    main()
