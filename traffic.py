"""Start and stop the three transmissions (TX-1 iperf, TX-2 ping train, TX-3 HTTP)."""
import os
import subprocess
import sys
import time

import config as C

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(REPO_DIR, 'tools')
SERVER_START_DELAY = 1.0   # seconds between starting servers and clients
CLIENT_GRACE = 5.0         # seconds clients get to finish on their own
STOP_WAIT = 2.0            # seconds between terminate() and kill()

CLIENTS = ['iperf_client', 'ping_client', 'fetch_loop']
SERVERS = ['iperf_server', 'echo_server', 'http_server']


def _tool(name):
    return [sys.executable, os.path.join(TOOLS_DIR, name)]


def _make_file(serve_dir):
    path = os.path.join(serve_dir, 'file_s2d2')
    if not os.path.exists(path) or os.path.getsize(path) != C.FILE_SIZE_BYTES:
        os.makedirs(serve_dir, exist_ok=True)
        subprocess.run(['dd', 'if=/dev/urandom', 'of=' + path, 'bs=1M',
                        'count=%d' % C.FILE_SIZE_MB],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_traffic(net, duration, outdir, t0):
    """Start servers, then clients. Returns a dict of named Popen handles."""
    s1, s2, d1, d2 = net['s1'], net['s2'], net['d1'], net['d2']
    os.makedirs(outdir, exist_ok=True)
    serve_dir = os.path.join(outdir, 'www')
    _make_file(serve_dir)

    log = open(os.path.join(outdir, 'traffic.log'), 'w')
    to_log = {'stdout': log, 'stderr': log}
    handles = {}
    try:
        handles['iperf_server'] = d1.popen(
            ['iperf', '-s', '-w', '16m', '-p', str(C.IPERF_PORT)], **to_log)
        handles['echo_server'] = d1.popen(
            _tool('tcp_echo_server.py') + ['--port', str(C.PING_PORT)], **to_log)
        # S2 runs the HTTP server because congestion control acts on the data
        # sender; with the server on d2, Cubic would never be exercised.
        handles['http_server'] = s2.popen(
            [sys.executable, '-m', 'http.server', str(C.HTTP_PORT), '--directory', serve_dir],
            **to_log)
        time.sleep(SERVER_START_DELAY)

        with open(os.path.join(outdir, 'iperf_s1.txt'), 'w') as iperf_out:
            handles['iperf_client'] = s1.popen(
                ['iperf', '-c', C.IP['d1'], '-p', str(C.IPERF_PORT), '-t', str(duration),
                 '-i', '1', '-w', '16m', '-Z', 'reno'],
                stdout=iperf_out, stderr=log)
        common = ['--duration', str(duration), '--t0', repr(t0)]
        handles['ping_client'] = s1.popen(
            _tool('tcp_ping_client.py') + common +
            ['--server', C.IP['d1'], '--port', str(C.PING_PORT),
             '--interval', str(C.PING_INTERVAL), '--out', os.path.join(outdir, 'ping.csv')],
            **to_log)
        handles['fetch_loop'] = d2.popen(
            _tool('fetch_loop.py') + common +
            ['--url', 'http://%s:%d/file_s2d2' % (C.IP['s2'], C.HTTP_PORT),
             '--interval', str(C.DOWNLOAD_INTERVAL), '--out', os.path.join(outdir, 'downloads.csv')],
            **to_log)
    except Exception:
        stop_traffic(handles)
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


def stop_traffic(handles):
    """Stop all traffic processes. Never raises if a process already exited."""
    clients = [handles[n] for n in CLIENTS if n in handles]
    deadline = time.monotonic() + CLIENT_GRACE
    for proc in clients:
        try:
            proc.wait(max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    _stop([p for p in clients if p.poll() is None])
    _stop([handles[n] for n in SERVERS if n in handles and handles[n].poll() is None])
