# TEMPORARY STUB written by Teammate A so run_experiment.py runs.
"""Monitors for the Reno vs Cubic experiment (replaces Teammate A's stub).

start_monitors launches one sampler per node (s1, s2, r1, r2) inside that
node's network namespace; stop_monitors stops them and builds throughput.csv.

Files written to outdir (t = time.time() - t0 in every file):
  cwnd_s1.csv, cwnd_s2.csv    t,cwnd_segments,rtt_ms
  queue_r1.csv, queue_r2.csv  t,backlog_pkts,dropped_total
  throughput.csv              t,s1d1_mbps,s2d2_mbps
  acked_s1.csv, acked_s2.csv  t,bytes_acked (raw cumulative bytes; input to throughput.csv)
"""
import csv
import os
import subprocess
import sys

import config as C

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(REPO_DIR, 'tools')
SAMPLE_INTERVAL = 0.1      # seconds between samples
STOP_WAIT = 2.0            # seconds between terminate() and kill()


class Handles(list):
    """A list of Popen handles that also remembers where the files are."""
    outdir = None


def _tool(name):
    # -u: unbuffered, so nothing is lost when a sampler is terminated.
    return [sys.executable, '-u', os.path.join(TOOLS_DIR, name)]


def _launch(handles, host, command, log):
    handles.append(host.popen(command, stdout=subprocess.DEVNULL, stderr=log))


def start_monitors(net, outdir, t0):
    """Start all samplers and return their handles; never blocks."""
    os.makedirs(outdir, exist_ok=True)

    def path(name):
        return os.path.join(outdir, name)

    common = ['--t0', repr(t0), '--interval', str(SAMPLE_INTERVAL)]
    handles = Handles()
    handles.outdir = outdir
    log = open(path('monitors.log'), 'w')
    try:
        for name, flt in (('s1', '( dport = :%d )' % C.IPERF_PORT),   # iperf, not the ping train
                          ('s2', '( sport = :%d )' % C.HTTP_PORT)):    # HTTP server
            _launch(handles, net[name], _tool('cwnd_sampler.py') + common + [
                '--filter', flt,
                '--out', path('cwnd_%s.csv' % name),
                '--acked-out', path('acked_%s.csv' % name)], log)
        _launch(handles, net['r1'], _tool('queue_sampler.py') + common + [
            '--ifaces', C.BOTTLENECK_IFACE,
            '--out', path('queue_r1.csv')], log)
        _launch(handles, net['r2'], _tool('queue_sampler.py') + common + [
            '--ifaces'] + list(C.R2_EGRESS_IFACES) + [
            '--out', path('queue_r2.csv')], log)
    except BaseException:   # includes the SystemExit raised on SIGTERM
        _stop(handles)
        raise
    finally:
        log.close()
    return handles


def _stop(procs):
    for proc in procs:
        try:
            proc.terminate()
        except OSError:
            pass
    for proc in procs:
        try:
            proc.wait(STOP_WAIT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _read_acked(path):
    """Return {sample index k (t rounded to the 0.1 s grid): cumulative bytes}."""
    series = {}
    try:
        with open(path) as f:
            for row in csv.DictReader(f):
                try:
                    k = int(round(float(row['t']) / SAMPLE_INTERVAL))
                    series[k] = int(row['bytes_acked'])
                except (KeyError, TypeError, ValueError):
                    continue          # a half-written last row
    except OSError:
        pass
    return series


def _rates(series):
    """Turn cumulative bytes into {k: Mbit/s}; a gap in samples is averaged over."""
    rates = {}
    prev_k, prev_bytes = None, 0
    for k in sorted(series):
        if prev_k is None:
            prev_k = k - 1            # counters start at 0 when the connection opens
        elapsed = (k - prev_k) * SAMPLE_INTERVAL
        rates[k] = max(0, series[k] - prev_bytes) * 8 / elapsed / 1e6
        prev_k, prev_bytes = k, series[k]
    return rates


def build_throughput(outdir):
    """Write throughput.csv (t,s1d1_mbps,s2d2_mbps) from the two acked files."""
    s1 = _rates(_read_acked(os.path.join(outdir, 'acked_s1.csv')))
    s2 = _rates(_read_acked(os.path.join(outdir, 'acked_s2.csv')))
    with open(os.path.join(outdir, 'throughput.csv'), 'w') as f:
        f.write('t,s1d1_mbps,s2d2_mbps\n')
        for k in sorted(set(s1) | set(s2)):
            f.write('%.1f,%s,%s\n' % (k * SAMPLE_INTERVAL,
                                      '%.3f' % s1[k] if k in s1 else '',
                                      '%.3f' % s2[k] if k in s2 else ''))


def stop_monitors(handles):
    """Stop every sampler started above, then build throughput.csv. Never raises."""
    _stop(list(handles))
    outdir = getattr(handles, 'outdir', None)
    if outdir:
        try:
            build_throughput(outdir)
        except Exception as error:     # a bad CSV must not break teardown
            print('WARNING: could not build throughput.csv: %s' % error)
