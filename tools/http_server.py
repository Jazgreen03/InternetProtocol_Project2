"""TX-3 server (runs on s2): serve a directory over HTTP/1.1.

HTTP/1.1 keeps the connection open between requests, so every download from
d2 travels over the same TCP session (the spec allows S2-D2 only one).
Each request is logged with the client's port, which shows that one session.
"""
import argparse
import functools
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class KeepAliveHandler(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        sys.stderr.write('%s:%d - %s\n' % (self.client_address[0], self.client_address[1],
                                           format % args))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()

    handler = functools.partial(KeepAliveHandler, directory=args.directory)
    ThreadingHTTPServer(('', args.port), handler).serve_forever()


if __name__ == '__main__':
    main()
