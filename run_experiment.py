"""Run the Reno vs Cubic experiment once per queue size; results go to <outdir>/q<N>/."""
import argparse
import datetime
import json
import os
import platform
import re
import signal
import time
import traceback

from mininet.log import setLogLevel

import config as C
from monitor import start_monitors, stop_monitors
from topology import CONGESTION_CONTROL, OFFLOADS, build_net
from traffic import CLIENTS, start_traffic, stop_traffic

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
# The iperf server asks for a 16 MB window (-w 16m); the kernel silently caps
# it at these limits, so they are raised for the run and restored afterwards.
SOCKET_BUF_SYSCTLS = ('net.core.rmem_max', 'net.core.wmem_max')
MIN_SOCKET_BUF = 16 * 1024 * 1024
# One iperf report line, e.g. "[  1] 0.0000-20.3 sec  59.8 MBytes  24.7 Mbits/sec"
# (iperf 2.0.x prints fewer decimals, e.g. "[  3]  0.0-20.0 sec ...").
IPERF_LINE = re.compile(r'\]\s+(\d+\.\d+)-\s*(\d+\.\d+)\s+sec.*?([\d.]+) ([KMG]?)bits/sec')
TO_MBPS = {'': 1e-6, 'K': 1e-3, 'M': 1.0, 'G': 1e3}


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


STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def _exit_on_signal(signum, frame):
    # Python's default SIGTERM/SIGHUP action exits without running finally
    # blocks; raising SystemExit lets every teardown and restore step run.
    # Later stop signals are ignored so they cannot cut that cleanup short
    # (`timeout` alone sends SIGTERM twice: to the process and to its group).
    for sig in STOP_SIGNALS:
        signal.signal(sig, signal.SIG_IGN)
    raise SystemExit('stopped by signal %d; cleaning up' % signum)


def write_meta(qdir, queue_size, duration, t0, traffic_start):
    meta = {
        'queue_size': queue_size,
        'duration': duration,
        't0': t0,
        'traffic_start': round(traffic_start, 3),   # seconds after t0 the clients started
        'date': datetime.datetime.fromtimestamp(t0).isoformat(),
        'link_bw_mbit': C.LINK_BW,
        'host_delay': C.ACCESS_DELAY,
        'host_queue': C.ACCESS_QUEUE,
        'bottleneck_iface': C.BOTTLENECK_IFACE,
        'congestion_control': CONGESTION_CONTROL,
        'offloads_off': list(OFFLOADS),
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


def _iperf_mbps(path):
    """Return Mbit/s from iperf's whole-run line (from 0 to the last end time), or None."""
    try:
        with open(path) as f:
            lines = [m.groups() for m in map(IPERF_LINE.search, f) if m]
    except OSError:
        return None
    if not lines:
        return None
    last_end = max(float(end) for _, end, _, _ in lines)
    totals = [float(rate) * TO_MBPS[prefix] for start, end, rate, prefix in lines
              if float(start) == 0 and float(end) == last_end]
    return totals[-1] if totals else None


def _csv_rows(path):
    try:
        with open(path) as f:
            return f.read().splitlines()[1:]
    except OSError:
        return []


def summarize(qdir, queue_size, duration, client_errors):
    """Print the run's key numbers and save them to summary.json."""
    ping = _csv_rows(os.path.join(qdir, 'ping.csv'))
    downloads = [r.split(',') for r in _csv_rows(os.path.join(qdir, 'downloads.csv'))]
    sizes = [int(r[2]) for r in downloads if len(r) == 3 and r[2].isdigit()]

    s1d1, source = _iperf_mbps(os.path.join(qdir, 'iperf_s1.txt')), 'iperf client'
    if s1d1 is None:
        s1d1, source = _iperf_mbps(os.path.join(qdir, 'iperf_d1.txt')), 'iperf server'
    summary = {
        'ping_rows': len(ping),
        'ping_unanswered': sum(1 for r in ping if r.endswith(',')),
        'downloads_complete': sum(1 for s in sizes if s == C.FILE_SIZE_BYTES),
        's1d1_avg_mbps': s1d1,
        's1d1_source': source if s1d1 is not None else None,
        's2d2_avg_mbps': round(sum(sizes) * 8 / duration / 1e6, 3),
        'client_errors': client_errors,
    }
    with open(os.path.join(qdir, 'summary.json'), 'w') as f:
        json.dump(summary, f, indent=2)

    s1d1_text = '%.1f Mbit/s (%s)' % (s1d1, source) if s1d1 is not None else 'not reported'
    print('q=%d: ping rows %d (%d unanswered), downloads complete %d, '
          'S1-D1 average %s, S2-D2 average %.2f Mbit/s'
          % (queue_size, summary['ping_rows'], summary['ping_unanswered'],
             summary['downloads_complete'], s1d1_text, summary['s2d2_avg_mbps']))


def kill_leftovers(net):
    """Kill any process still running in a host's network namespace.

    stop_traffic and stop_monitors should leave none; this is a safety net so a
    run always terminates cleanly. Returns 'host: command' for each kill.
    """
    hosts = {}
    for host in net.hosts:
        try:
            hosts[os.readlink('/proc/%d/ns/net' % host.pid)] = host
        except OSError:
            pass
    shells = {host.pid for host in net.hosts}
    killed = []
    for entry in os.listdir('/proc'):
        if not entry.isdigit() or int(entry) in shells:
            continue
        try:
            host = hosts.get(os.readlink('/proc/%s/ns/net' % entry))
            if host is None:
                continue
            with open('/proc/%s/cmdline' % entry, 'rb') as f:
                command = f.read().replace(b'\0', b' ').decode(errors='replace').strip()
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
        monitors = start_monitors(net, qdir, t0)
        traffic = start_traffic(net, duration, qdir, t0)
        write_meta(qdir, queue_size, duration, t0, time.time() - t0)
        time.sleep(duration)
    finally:
        # The inner finally tears the network down even if a stop step raises.
        try:
            if traffic is not None:
                stop_traffic(traffic)
            if monitors is not None:
                stop_monitors(monitors)
            for line in kill_leftovers(net):
                print('WARNING: killed leftover process ' + line)
        finally:
            # No `mn -c` here: net.stop() removes this run's interfaces, and
            # mn -c also deletes /tmp/*.log and /tmp/*.out, which would wipe a
            # log the user redirected there. Run `sudo mn -c` by hand after a crash.
            net.stop()

    # A positive exit code is the client's own error; negative ones are the
    # signals stop_traffic sent to a client that overran.
    client_errors = ['%s exited with code %d' % (name, traffic[name].returncode)
                     for name in CLIENTS if traffic[name].returncode > 0]
    for error in client_errors:
        print('WARNING: %s; see %s' % (error, os.path.join(qdir, 'traffic.log')))
    summarize(qdir, queue_size, duration, client_errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true',
                        help='short run of %d s' % C.QUICK_TIME)
    parser.add_argument('--queues', type=int, nargs='+', default=C.QUEUE_SIZES,
                        help='router buffer sizes in packets (default: %s)' % C.QUEUE_SIZES)
    parser.add_argument('--outdir', default=os.path.join(REPO_DIR, 'results'))
    parser.add_argument('--duration', type=float, help='override run length in seconds')
    args = parser.parse_args()
    if args.duration is not None:
        duration = args.duration
    else:
        duration = C.QUICK_TIME if args.quick else C.EXPERIMENT_TIME
    if duration <= 0:
        parser.error('duration must be positive')

    for sig in STOP_SIGNALS:
        if signal.getsignal(sig) is not signal.SIG_IGN:   # e.g. leave nohup in effect
            signal.signal(sig, _exit_on_signal)
    setLogLevel('warning')
    saved = {}
    try:
        for name in SOCKET_BUF_SYSCTLS:
            value = _read_sysctl(name)
            if value is not None and int(value) < MIN_SOCKET_BUF:
                _write_sysctl(name, MIN_SOCKET_BUF)
                saved[name] = value
                print('%s raised from %s to %d for this run' % (name, value, MIN_SOCKET_BUF))
        failed = []
        for queue_size in args.queues:
            print('=== queue %d packets, %g s' % (queue_size, duration))
            try:
                run_one(queue_size, duration, args.outdir)
            except Exception:
                # One failed run should not cost the remaining queue sizes.
                traceback.print_exc()
                failed.append(queue_size)
        if failed:
            raise SystemExit('runs failed for queue sizes %s' % failed)
    finally:
        for name, value in saved.items():
            _write_sysctl(name, value)


if __name__ == '__main__':
    main()
