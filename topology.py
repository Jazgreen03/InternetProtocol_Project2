"""Dumbbell topology: S1, S2 -- R1 == R2 -- D1, D2."""
import argparse
import subprocess
import time

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import Node

import config as C

# (node, intf, ip, node, intf, ip)
ACCESS_LINKS = [
    ('s1', C.SENDER_IFACES['s1'], C.IP['s1'], 'r1', 'r1-eth0', C.IP['r1_s1']),
    ('s2', C.SENDER_IFACES['s2'], C.IP['s2'], 'r1', 'r1-eth1', C.IP['r1_s2']),
    ('r2', C.R2_EGRESS_IFACES[0], C.IP['r2_d1'], 'd1', 'd1-eth0', C.IP['d1']),
    ('r2', C.R2_EGRESS_IFACES[1], C.IP['r2_d2'], 'd2', 'd2-eth0', C.IP['d2']),
]
BOTTLENECK_LINK = ('r1', C.BOTTLENECK_IFACE, C.IP['r1_r2'], 'r2', 'r2-eth0', C.IP['r2_r1'])

ROUTES = {
    's1': ['default via ' + C.IP['r1_s1']],
    's2': ['default via ' + C.IP['r1_s2']],
    'd1': ['default via ' + C.IP['r2_d1']],
    'd2': ['default via ' + C.IP['r2_d2']],
    'r1': ['10.0.4.0/24 via ' + C.IP['r2_r1'], '10.0.5.0/24 via ' + C.IP['r2_r1']],
    'r2': ['10.0.1.0/24 via ' + C.IP['r1_r2'], '10.0.2.0/24 via ' + C.IP['r1_r2']],
}

CONGESTION_CONTROL = {'s1': 'reno', 's2': 'cubic'}


class LinuxRouter(Node):
    """A Mininet node that forwards IPv4 packets."""

    def config(self, **params):
        super().config(**params)
        self.cmd('sysctl -w net.ipv4.ip_forward=1')

    def terminate(self):
        self.cmd('sysctl -w net.ipv4.ip_forward=0')
        super().terminate()


def _add_link(net, a, a_intf, a_ip, b, b_intf, b_ip, **tc):
    net.addLink(a, b, intfName1=a_intf, intfName2=b_intf,
                params1={'ip': a_ip + '/24'}, params2={'ip': b_ip + '/24'},
                bw=C.LINK_BW, **tc)


def _disable_offloads(net):
    # With TSO/GSO on, the qdiscs see ~64 KB super-packets and netem's limit
    # counts each as one packet, so queue_size would not mean real packets.
    for node in net.hosts:
        for intf in node.intfNames():
            if intf != 'lo':
                node.cmd('ethtool -K %s tso off gso off gro off' % intf)


def build_net(queue_size):
    """Return a started net with addresses, routes, queue sizes and CC set."""
    net = Mininet(topo=None, link=TCLink, controller=None, build=False)
    try:
        for name in ('s1', 's2', 'd1', 'd2'):
            net.addHost(name, ip=None)
        for name in ('r1', 'r2'):
            net.addHost(name, cls=LinuxRouter, ip=None)

        for link in ACCESS_LINKS:
            _add_link(net, *link, delay=C.ACCESS_DELAY,
                      max_queue_size=C.ACCESS_QUEUE)
        # No delay on the bottleneck: Mininet uses max_queue_size as the netem
        # limit, which also counts packets that are only being delayed (~830
        # for 10 ms at 1 Gbps), so a small delayed queue would drop nearly all.
        _add_link(net, *BOTTLENECK_LINK, max_queue_size=queue_size)

        net.build()
        net.start()
        _disable_offloads(net)

        for name, routes in ROUTES.items():
            for route in routes:
                err = net[name].cmd('ip route add ' + route).strip()
                if err:
                    raise RuntimeError('%s: ip route add %s: %s' % (name, route, err))
        for name, algo in CONGESTION_CONTROL.items():
            net[name].cmd('sysctl -w net.ipv4.tcp_congestion_control=' + algo)
            actual = net[name].cmd('sysctl -n net.ipv4.tcp_congestion_control').strip()
            if actual != algo:
                raise RuntimeError('%s: congestion control is %r, wanted %r' % (name, actual, algo))
    except Exception:
        net.stop()
        raise
    return net


def run_checks(net):
    s1, s2, d1, r1 = net['s1'], net['s2'], net['d1'], net['r1']

    print('\n--- ping s1 -> d1')
    print(s1.cmd('ping -c 5 ' + C.IP['d1']))
    print('--- ping s2 -> d2')
    print(s2.cmd('ping -c 5 ' + C.IP['d2']))

    print('--- bottleneck qdisc')
    print(r1.cmd('tc -s qdisc show dev ' + C.BOTTLENECK_IFACE))
    print('--- bottleneck offloads')
    print(r1.cmd('ethtool -k %s | grep -E "^(tcp-segmentation|generic-segmentation|generic-receive)-offload"'
                 % C.BOTTLENECK_IFACE))

    for name in CONGESTION_CONTROL:
        print('--- %s: %s' % (name, net[name].cmd(
            'sysctl -n net.ipv4.tcp_congestion_control').strip()))

    print('\n--- iperf s1 -> d1, 5 s')
    server = d1.popen(['iperf', '-s', '-p', str(C.IPERF_PORT)],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    print(s1.cmd('iperf -c %s -p %d -t 5' % (C.IP['d1'], C.IPERF_PORT)))
    server.kill()
    server.wait()

    print('--- bottleneck qdisc after iperf')
    print(r1.cmd('tc -s qdisc show dev ' + C.BOTTLENECK_IFACE))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build the dumbbell and run sanity checks.')
    parser.add_argument('--queue', type=int, default=100, help='bottleneck queue size (packets)')
    parser.add_argument('--cli', action='store_true', help='open the Mininet CLI after the checks')
    args = parser.parse_args()

    setLogLevel('info')
    net = build_net(args.queue)
    try:
        run_checks(net)
        if args.cli:
            CLI(net)
    finally:
        net.stop()
