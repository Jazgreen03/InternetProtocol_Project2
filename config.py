"""Shared constants for the Reno vs Cubic dumbbell experiment."""

QUEUE_SIZES = [20, 50, 100]    # packets
EXPERIMENT_TIME = 180          # seconds
QUICK_TIME = 20                # seconds

LINK_BW = 1000                 # Mbit/s
ACCESS_DELAY = '5ms'
ACCESS_QUEUE = 2000            # packets

FILE_SIZE_MB = 10
PING_INTERVAL = 0.1            # seconds
DOWNLOAD_INTERVAL = 3          # seconds
IPERF_PORT, PING_PORT, HTTP_PORT = 5001, 5002, 8000

IP = {'s1': '10.0.1.2', 's2': '10.0.2.2', 'd1': '10.0.4.2', 'd2': '10.0.5.2',
      'r1_s1': '10.0.1.1', 'r1_s2': '10.0.2.1', 'r1_r2': '10.0.3.1',
      'r2_r1': '10.0.3.2', 'r2_d1': '10.0.4.1', 'r2_d2': '10.0.5.1'}
BOTTLENECK_IFACE = 'r1-eth2'
R2_EGRESS_IFACES = ['r2-eth1', 'r2-eth2']
SENDER_IFACES = {'s1': 's1-eth0', 's2': 's2-eth0'}
