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

# (host, intf, ip, router, intf, ip)
ACCESS_LINKS = [
    ('s1', C.SENDER_IFACES['s1'], C.IP['s1'], 'r1', 'r1-eth0', C.IP['r1_s1']),
    ('s2', C.SENDER_IFACES['s2'], C.IP['s2'], 'r1', 'r1-eth1', C.IP['r1_s2']),
    ('d1', 'd1-eth0', C.IP['d1'], 'r2', C.R2_EGRESS_IFACES[0], C.IP['r2_d1']),
    ('d2', 'd2-eth0', C.IP['d2'], 'r2', C.R2_EGRESS_IFACES[1], C.IP['r2_d2']),
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
OFFLOADS = ('tcp-segmentation-offload', 'generic-segmentation-offload',
            'generic-receive-offload')


class LinuxRouter(Node):
    """A Mininet node that forwards IPv4 packets."""

    def config(self, **params):
        super().config(**params)
        self.cmd('sysctl -w net.ipv4.ip_forward=1')


def _add_link(net, a, a_intf, a_ip, b, b_intf, b_ip, a_tc, b_tc):
    net.addLink(a, b, intfName1=a_intf, intfName2=b_intf,
                params1=dict(a_tc, ip=a_ip + '/24'), params2=dict(b_tc, ip=b_ip + '/24'),
                bw=C.LINK_BW)


def _disable_offloads(net):
    # With TSO/GSO on, the qdiscs see ~64 KB super-packets and netem's limit
    # counts each as one packet, so queue_size would not mean real packets.
    for node in net.hosts:
        for intf in node.intfNames():
            if intf == 'lo':
                continue
            node.cmd('ethtool -K %s tso off gso off gro off' % intf)
            features = node.cmd('ethtool -k ' + intf)
            for offload in OFFLOADS:
                if offload + ': off' not in features:
                    raise RuntimeError('%s: could not turn off %s (is ethtool installed?)'
                                       % (intf, offload))


def build_net(queue_size):
    """Return a started net with addresses, routes, queue sizes and CC set."""
    net = Mininet(topo=None, link=TCLink, controller=None, build=False)
    try:
        for name in ('s1', 's2', 'd1', 'd2'):
            net.addHost(name, ip=None)
        for name in ('r1', 'r2'):
            net.addHost(name, cls=LinuxRouter, ip=None)

        # Delay sits only on host interfaces: data is delayed 10 ms leaving the
        # sender and ACKs 10 ms leaving the receiver, so the RTT is 20 ms. Router
        # interfaces get no delay, because netem's limit (max_queue_size) also
        # counts packets that are only being delayed; this way every router
        # buffer holds exactly queue_size real packets and its backlog is the
        # real router queue.
        host_tc = {'delay': C.ACCESS_DELAY, 'max_queue_size': C.ACCESS_QUEUE}
        router_tc = {'max_queue_size': queue_size}
        for link in ACCESS_LINKS:
            _add_link(net, *link, host_tc, router_tc)
        _add_link(net, *BOTTLENECK_LINK, router_tc, router_tc)

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
    except BaseException:   # includes the SystemExit raised on SIGTERM
        net.stop()
        raise
    return net


def run_checks(net):
    s1, s2, d1, r1 = net['s1'], net['s2'], net['d1'], net['r1']

    print('\n--- ping s1 -> d1')
    print(s1.cmd('ping -c 5 ' + C.IP['d1']))
    print('--- ping s2 -> d2')
    print(s2.cmd('ping -c 5 ' + C.IP['d2']))

    print('--- netem per interface (routers: limit = queue size, no delay)')
    for name in ('s1', 's2', 'd1', 'd2', 'r1', 'r2'):
        for intf in net[name].intfNames():
            if intf != 'lo':
                print('%-8s %s' % (intf, net[name].cmd(
                    'tc qdisc show dev %s | grep -o "netem.*"' % intf).strip()))
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
    parser.add_argument('--queue', type=int, default=100, help='router buffer size (packets)')
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
