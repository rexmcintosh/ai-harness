"""Loopback development preview. Use the runbook's WSGI command for deployment."""
import argparse
from .app import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--local-http', action='store_true', help='Allow cookies on a loopback-only HTTP preview')
    args = parser.parse_args()
    app = create_app({'SESSION_COOKIE_SECURE': not args.local_http})
    app.run(host='127.0.0.1', port=args.port, debug=False)


if __name__ == '__main__': main()
