#!/usr/bin/env python3
"""Wake-only ATX endpoint, bound to loopback behind Tailscale Serve."""
import argparse
import json
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

POWER = Path('/sys/class/gpio/gpio503/value')
LED = Path('/sys/class/gpio/gpio504/value')


class WakeController:
    def __init__(self, led=LED, power=POWER):
        self.led, self.power = led, power
        self.lock = threading.Lock()
        self.last_press = float('-inf')

    def status(self):
        # Active-low LED: steady on means power on; blinking usually means sleep.
        try:
            samples = set()
            for _ in range(40):
                value = self.led.read_text().strip()
                if value not in {'0', '1'}:
                    raise ValueError('Invalid power LED reading')
                samples.add(value)
                time.sleep(0.1)
            if samples == {'0'}:
                return 200, {'state': 'awake', 'detail': 'Motherboard power LED is steadily on.'}
            if samples == {'1'}:
                return 200, {'state': 'off', 'detail': 'Motherboard power LED is off.'}
            return 200, {'state': 'asleep', 'detail': 'Motherboard power LED is blinking.'}
        except (OSError, ValueError):
            return 503, {'state': 'unknown', 'detail': 'Motherboard power LED could not be read safely.'}

    def wake(self):
        if not self.lock.acquire(blocking=False):
            return 409, {'error': 'A wake request is already in progress.'}
        try:
            if time.monotonic() - self.last_press < 30:
                return 429, {'error': 'Wait 30 seconds before trying again.'}
            status, result = self.status()
            if status != 200:
                return status, {'error': result['detail']}
            if result['state'] == 'awake':
                return 200, {'state': 'already-on', 'message': 'Power LED is steadily on; no button press sent.'}
            if self.power.read_text().strip() != '0':
                return 409, {'error': 'ATX button is already pressed; no command sent.'}
            self.last_press = time.monotonic()
            try:
                self.power.write_text('1')
                time.sleep(0.2)
            finally:
                self.power.write_text('0')
            return 200, {'state': 'wake-sent', 'message': 'Short ATX power-button press sent.'}
        except (OSError, ValueError):
            return 503, {'error': 'ATX state could not be read or controlled safely.'}
        finally:
            self.lock.release()


def handler(config, controller):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def reply(self, status, result=None):
            body = json.dumps(result).encode() if result is not None else b''
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Vary', 'Origin')
            if self.headers.get('Origin') in config['origins']:
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Erebor-Wake')
                self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            # Serve removes spoofed identity headers and supplies the peer's login.
            return self.headers.get('Tailscale-User-Login') == config['login']

        def do_OPTIONS(self):
            if self.path != '/wake' or self.headers.get('Origin') not in config['origins']:
                return self.reply(403, {'error': 'Origin or path not allowed.'})
            self.reply(204)

        def do_GET(self):
            if not self.authorized():
                return self.reply(403, {'error': 'Tailscale user not allowed.'})
            if self.path == '/status':
                return self.reply(*controller.status())
            if self.path != '/health':
                return self.reply(404, {'error': 'Not found.'})
            self.reply(200, {'state': 'ready'})

        def do_POST(self):
            if not self.authorized() or self.headers.get('Origin') not in config['origins']:
                return self.reply(403, {'error': 'Tailscale user or origin not allowed.'})
            if self.path != '/wake':
                return self.reply(404, {'error': 'Not found.'})
            if self.headers.get('X-Erebor-Wake') != '1' or self.headers.get('Content-Type') != 'application/json':
                return self.reply(400, {'error': 'Expected a dashboard wake request.'})
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length') != '2':
                return self.reply(400, {'error': 'Expected an empty JSON object.'})
            if self.rfile.read(2) != b'{}':
                return self.reply(400, {'error': 'Expected an empty JSON object.'})
            self.reply(*controller.wake())

    return Handler


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    server = ThreadingHTTPServer(('127.0.0.1', 18091), handler(config, WakeController()))
    # Finish active requests before exiting, including releasing the ATX button.
    server.daemon_threads = False
    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
