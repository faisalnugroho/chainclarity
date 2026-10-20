"""ChainClarity API server (stdlib only).

Endpoints:
  POST /api/analyze      {network, target}   -> evidence document
  GET  /api/health                       -> {status, contract_address}
  GET  /api/config                       -> supported networks + limits

Secrets: ETHERSCAN_API_KEY / SOLSCAN_API_KEY are read from the
environment server-side and are never echoed in any response. Key
material is excluded from error messages and cached documents.
"""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from adapters import analyze, AdapterError, error_response

MAX_BODY = 2048
RATE_LIMIT = 30            # requests per window per IP
RATE_WINDOW_S = 60
_CONTRACT_ADDRESS = os.environ.get('CHAINCLARITY_CONTRACT', '')
_RATE = {}
_RATE_LOCK = threading.Lock()


def _rate_ok(ip):
    now = time.time()
    with _RATE_LOCK:
        hits = [t for t in _RATE.get(ip, []) if now - t < RATE_WINDOW_S]
        if len(hits) >= RATE_LIMIT:
            _RATE[ip] = hits
            return False
        hits.append(now)
        _RATE[ip] = hits
        return True


class Handler(BaseHTTPRequestHandler):
    server_version = 'ChainClarity/1.0'

    # Restrictive CORS: only the deployed GitHub Pages origin may call the
    # API from a browser (override with CORS_ALLOWED_ORIGINS, comma-sep).
    ALLOWED_ORIGINS = [o.strip() for o in os.environ.get('CORS_ALLOWED_ORIGINS',
        'https://faisalnugroho.github.io').split(',') if o.strip()]

    def _cors(self):
        origin = self.headers.get('Origin', '')
        if origin in self.ALLOWED_ORIGINS:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Access-Control-Max-Age', '600')

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _send(self, code, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _client_ip(self):
        return self.client_address[0] if self.client_address else 'unknown'

    def do_GET(self):
        if self.path == '/api/health':
            return self._send(200, {
                'status': 'ok',
                'contract_configured': bool(_CONTRACT_ADDRESS),
            })
        if self.path == '/api/config':
            return self._send(200, {
                'networks': [
                    {'id': 'ethereum', 'label': 'Ethereum Mainnet',
                     'provider': 'Etherscan'},
                    {'id': 'solana', 'label': 'Solana Mainnet',
                     'provider': 'Solscan Pro'},
                ],
                'wallet_address_analysis': False,
                'contract_address': _CONTRACT_ADDRESS or None,
            })
        return self._send(404, error_response('not_found'))

    def do_POST(self):
        if self.path != '/api/analyze':
            return self._send(404, error_response('not_found'))
        if not _rate_ok(self._client_ip()):
            return self._send(429, error_response('rate_limited'))
        try:
            n = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            return self._send(400, error_response('invalid_input'))
        try:
            req = json.loads(self.rfile.read(n).decode('utf-8', 'replace'))
        except Exception:
            return self._send(400, error_response('invalid_input'))
        if not isinstance(req, dict):
            return self._send(400, error_response('invalid_input'))
        network = str(req.get('network', ''))
        target = str(req.get('target', ''))
        try:
            evidence = analyze(network, target)
        except AdapterError as e:
            return self._send(e.http_status, error_response(e.code))
        # strip internal-only fields before exposing
        pub = {k: v for k, v in evidence.items()
               if k not in ('block_timestamp',)}
        pub['timestamp_source'] = 'provider_block_timestamp' \
            if evidence.get('block_timestamp') else 'unavailable'
        return self._send(200, {'ok': True, 'evidence': pub})

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # never log request bodies (may contain user targets)


def main():
    port = int(os.environ.get('PORT', '8080'))
    srv = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print('ChainClarity API listening on :' + str(port), flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
