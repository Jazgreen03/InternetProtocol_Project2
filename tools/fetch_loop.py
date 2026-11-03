"""TX-3 client (runs on d2): start a download of the URL every interval, log each one.

Downloads start on a fixed tick (start + k * interval), each in its own thread,
so a slow download never delays the next start. Output CSV: t_start,t_end,bytes
with times relative to t0, sorted by t_start when the loop ends. A failed
download logs the bytes curl received; one cut off by SIGTERM logs empty bytes.
"""
import argparse
import os
import signal
import subprocess
import threading
import time

HEADER = 't_start,t_end,bytes\n'
FINISH_WAIT = 30.0     # seconds to let in-flight downloads finish after the last start
POLL = 0.05            # seconds between checks for the next tick or a stop request

lock = threading.RLock()   # re-entrant: the SIGTERM handler runs in the main thread
procs = []
rows = []
stopping = False


def append_row(path, row):
    with lock:
        rows.append(row)
        with open(path, 'a') as f:
            f.write('%.3f,%.3f,%s\n' % row)


def download(url, t0, path):
    t_start = time.time() - t0
    proc = subprocess.Popen(['curl', '-s', '-o', '/dev/null', '-w', '%{size_download}', url],
                            stdout=subprocess.PIPE)
    with lock:
        procs.append(proc)
    stdout, _ = proc.communicate()
    with lock:
        procs.remove(proc)
    append_row(path, (t_start, time.time() - t0, stdout.decode().strip()))


def stop(signum, frame):
    global stopping
    stopping = True
    with lock:
        for proc in procs:
            proc.kill()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--interval', type=float, default=3.0)
    parser.add_argument('--duration', type=float, default=15.0)
    parser.add_argument('--t0', type=float, default=None)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    t0 = args.t0 if args.t0 is not None else time.time()
    signal.signal(signal.SIGTERM, stop)

    with open(args.out, 'w') as f:
        f.write(HEADER)

    threads = []
    start = time.monotonic()
    k = 0
    while not stopping and k * args.interval < args.duration:
        delay = start + k * args.interval - time.monotonic()
        if delay > 0:
            time.sleep(min(delay, POLL))
            continue
        thread = threading.Thread(target=download, args=(args.url, t0, args.out), daemon=True)
        thread.start()
        threads.append(thread)
        k += 1

    deadline = time.monotonic() + FINISH_WAIT
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))

    with lock:
        tmp = args.out + '.tmp'
        with open(tmp, 'w') as f:
            f.write(HEADER)
            f.writelines('%.3f,%.3f,%s\n' % row for row in sorted(rows))
        os.replace(tmp, args.out)


if __name__ == '__main__':
    main()
