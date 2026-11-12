"""Plot a results folder: four plots per queue size, plus a throughput summary.

Usage:
    python3 plot.py results/run1                 # plots go to results/run1/plots/
    python3 plot.py results/run1 --out my_plots  # choose another folder
    python3 plot.py results/run1 --free          # queue plot shows free buffer space

Needs only matplotlib (pip install matplotlib); no root, no Mininet.
Expects <folder>/q<N>/ with cwnd_s1.csv, cwnd_s2.csv, queue_r1.csv,
queue_r2.csv, throughput.csv, ping.csv (and optionally meta.json, summary.json).
A missing or empty file skips that plot with a warning instead of failing.

"""
import argparse
import csv
import json
import os
import re
import sys

import matplotlib
matplotlib.use('Agg')            # no display needed
import matplotlib.pyplot as plt

DEFAULT_DURATION = 180
S1_COLOR, S2_COLOR = '#1f77b4', '#d95f02'     # Reno, Cubic
R1_COLOR, R2_COLOR = '#2a9d8f', '#8e44ad'


def load_csv(path, columns):
    """Return {column: [float or None, ...]}; None marks an empty cell."""
    data = {c: [] for c in columns}
    try:
        with open(path) as f:
            for row in csv.DictReader(f):
                try:
                    values = [float(row[c]) if row[c] not in ('', None) else None
                              for c in columns]
                except (KeyError, ValueError):
                    continue      # a half-written last row
                if values[0] is None:
                    continue
                for c, v in zip(columns, values):
                    data[c].append(v)
    except OSError:
        return None
    return data if data[columns[0]] else None


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def clip(data, columns, duration):
    """Keep rows with t <= duration and drop rows whose y-columns are empty."""
    out = {c: [] for c in columns}
    for i, t in enumerate(data[columns[0]]):
        if t <= duration:
            for c in columns:
                out[c].append(data[c][i])
    return out


def series(data, ycol):
    """(t, y) lists without the rows where y is empty."""
    pairs = [(t, y) for t, y in zip(data['t'], data[ycol]) if y is not None]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def finish(fig, ax, title, ylabel, duration, path):
    ax.set_xlim(0, duration)
    ax.set_xlabel('Time (s)')
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.3)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print('wrote', path)


def warn(qdir, name):
    print('WARNING: %s missing or empty in %s; skipping that plot' % (name, qdir))


def plot_cwnd(qdir, q, duration, outdir):
    s1 = load_csv(os.path.join(qdir, 'cwnd_s1.csv'), ['t', 'cwnd_segments'])
    s2 = load_csv(os.path.join(qdir, 'cwnd_s2.csv'), ['t', 'cwnd_segments'])
    if s1 is None and s2 is None:
        return warn(qdir, 'cwnd_s1.csv / cwnd_s2.csv')
    fig, ax = plt.subplots(figsize=(10, 4.5))
    if s1:
        s1 = clip(s1, ['t', 'cwnd_segments'], duration)
        ax.plot(s1['t'], s1['cwnd_segments'], color=S1_COLOR, label='S1-D1 (Reno)')
    else:
        warn(qdir, 'cwnd_s1.csv')
    if s2:
        s2 = clip(s2, ['t', 'cwnd_segments'], duration)
        # S2 sends a file every 3 s; between downloads ss keeps showing the last window.
        ax.plot(s2['t'], s2['cwnd_segments'], color=S2_COLOR, drawstyle='steps-post',
                label='S2-D2 (Cubic)')
    else:
        warn(qdir, 'cwnd_s2.csv')
    ax.legend(loc='upper right')
    finish(fig, ax, 'Congestion window, queue size %d packets' % q,
           'cwnd (segments)', duration, os.path.join(outdir, 'cwnd_q%d.png' % q))


def plot_queue(qdir, q, duration, outdir, free):
    r1 = load_csv(os.path.join(qdir, 'queue_r1.csv'),
                  ['t', 'backlog_pkts', 'dropped_total'])
    r2 = load_csv(os.path.join(qdir, 'queue_r2.csv'),
                  ['t', 'backlog_pkts', 'dropped_total'])
    if r1 is None and r2 is None:
        return warn(qdir, 'queue_r1.csv / queue_r2.csv')
    cols = ['t', 'backlog_pkts', 'dropped_total']
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True,
                                  gridspec_kw={'height_ratios': [3, 1.4]})
    # R1 has one buffer (limit q); R2's two egress buffers are summed (limit 2q).
    for data, name, color, cap in ((r1, 'R1', R1_COLOR, q), (r2, 'R2', R2_COLOR, 2 * q)):
        if data is None:
            warn(qdir, 'queue_%s.csv' % name.lower())
            continue
        data = clip(data, cols, duration)
        if free:
            ys = [cap - b for b in data['backlog_pkts']]
            label = '%s free space (of %d)' % (name, cap)
        else:
            ys = data['backlog_pkts']
            label = '%s queue length' % name
        ax.plot(data['t'], ys, color=color, label=label, linewidth=1.2)
        ax2.plot(data['t'], data['dropped_total'], color=color, label='%s drops' % name)
    if not free:
        ax.axhline(q, color='gray', linestyle='--', linewidth=1,
                   label='R1 buffer limit (%d)' % q)
    ax.set_ylabel('Free buffer space (packets)' if free else 'Queue length (packets)')
    ax.legend(loc='upper right')
    ax.grid(alpha=0.3)
    ax2.set_ylabel('Cumulative drops')
    ax2.legend(loc='upper left')
    finish(fig, ax2, 'Router queues, queue size %d packets (R2 = sum of its two egress '
                     'buffers)' % q, 'Cumulative drops', duration,
           os.path.join(outdir, 'queue_q%d.png' % q))


def plot_throughput(qdir, q, duration, outdir, summary):
    th = load_csv(os.path.join(qdir, 'throughput.csv'), ['t', 's1d1_mbps', 's2d2_mbps'])
    if th is None:
        return warn(qdir, 'throughput.csv')
    th = clip(th, ['t', 's1d1_mbps', 's2d2_mbps'], duration)
    s1_avg, s2_avg = summary.get('s1d1_avg_mbps'), summary.get('s2d2_avg_mbps')
    l1 = 'S1-D1 (Reno)' + (', avg %.1f Mbps' % s1_avg if s1_avg is not None else '')
    l2 = 'S2-D2 (Cubic)' + (', avg %.2f Mbps' % s2_avg if s2_avg is not None else '')
    fig, ax = plt.subplots(figsize=(10, 4.5))
    t1, y1 = series(th, 's1d1_mbps')
    t2, y2 = series(th, 's2d2_mbps')
    ax.plot(t1, y1, color=S1_COLOR, label=l1, linewidth=1)
    ax.plot(t2, y2, color=S2_COLOR, label=l2, linewidth=1)
    ax.legend(loc='upper right')
    finish(fig, ax, 'Throughput per 0.1 s interval, queue size %d packets' % q,
           'Throughput (Mbps)', duration, os.path.join(outdir, 'throughput_q%d.png' % q))


def plot_ping(qdir, q, duration, outdir):
    ping = load_csv(os.path.join(qdir, 'ping.csv'), ['t', 'rtt_ms'])
    if ping is None:
        return warn(qdir, 'ping.csv')
    t, rtt = series(clip(ping, ['t', 'rtt_ms'], duration), 'rtt_ms')   # empty = unanswered
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(t, rtt, color=S1_COLOR, linewidth=0.8, label='S1-D1 ping RTT')
    ax.axhline(20, color='gray', linestyle='--', linewidth=1, label='20 ms minimum RTT')
    ax.legend(loc='upper right')
    finish(fig, ax, 'Ping RTT, queue size %d packets' % q, 'RTT (ms)', duration,
           os.path.join(outdir, 'ping_q%d.png' % q))


def plot_summary(results, outdir):
    """Bar chart of the two sessions' average throughput for each queue size."""
    rows = [(q, s.get('s1d1_avg_mbps'), s.get('s2d2_avg_mbps')) for q, s in results]
    rows = [r for r in rows if r[1] is not None and r[2] is not None]
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(7, 4.5))
    xs = range(len(rows))
    w = 0.38
    b1 = ax.bar([x - w / 2 for x in xs], [r[1] for r in rows], w, color=S1_COLOR,
                label='S1-D1 (Reno)')
    b2 = ax.bar([x + w / 2 for x in xs], [r[2] for r in rows], w, color=S2_COLOR,
                label='S2-D2 (Cubic)')
    ax.bar_label(b1, fmt='%.1f', fontsize=8)
    ax.bar_label(b2, fmt='%.2f', fontsize=8)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([str(r[0]) for r in rows])
    ax.set_xlabel('Router queue size (packets)')
    ax.set_ylabel('Average throughput (Mbps)')
    ax.legend()
    ax.grid(axis='y', alpha=0.3)
    fig.suptitle('Average throughput per session')
    fig.tight_layout()
    path = os.path.join(outdir, 'throughput_summary.png')
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print('wrote', path)


def find_queue_dirs(folder):
    dirs = []
    for name in os.listdir(folder):
        m = re.fullmatch(r'q(\d+)', name)
        if m and os.path.isdir(os.path.join(folder, name)):
            dirs.append((int(m.group(1)), os.path.join(folder, name)))
    return sorted(dirs)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('folder', help='results folder containing q20/, q50/, q100/')
    parser.add_argument('--out', help='where to write the PNGs (default: <folder>/plots)')
    parser.add_argument('--free', action='store_true',
                        help='queue plot shows free buffer space instead of queue length')
    args = parser.parse_args()

    if not os.path.isdir(args.folder):
        sys.exit('not a folder: %s' % args.folder)
    queue_dirs = find_queue_dirs(args.folder)
    if not queue_dirs:
        sys.exit('no q<N> subfolders found in %s' % args.folder)
    outdir = args.out or os.path.join(args.folder, 'plots')
    os.makedirs(outdir, exist_ok=True)

    results = []
    for q, qdir in queue_dirs:
        meta = load_json(os.path.join(qdir, 'meta.json'))
        summary = load_json(os.path.join(qdir, 'summary.json'))
        duration = meta.get('duration', DEFAULT_DURATION)
        print('--- queue size %d (%g s)' % (q, duration))
        plot_cwnd(qdir, q, duration, outdir)
        plot_queue(qdir, q, duration, outdir, args.free)
        plot_throughput(qdir, q, duration, outdir, summary)
        plot_ping(qdir, q, duration, outdir)
        results.append((q, summary))
    plot_summary(results, outdir)


if __name__ == '__main__':
    main()