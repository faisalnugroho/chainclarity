"""Frontend configuration resolution + server CORS tests (source pins)."""
import json
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api'))

import server as srv  # noqa: E402

FRONTEND = (ROOT / 'frontend/index.html').read_text()


def test_config_resolution_chain_present():
    assert 'CHAINCLARITY_CONFIG' in FRONTEND
    assert 'BUNDLED_DEFAULT_API_BASE' in FRONTEND
    assert 'localStorage.getItem(LS_API)' in FRONTEND
    # https page must refuse http API (no downgrade)
    assert "location.protocol === 'https:'" in FRONTEND
    # graceful absence handling
    assert 'Live analysis API is not reachable' in FRONTEND


def test_timeouts_present():
    assert 'AbortController' in FRONTEND and "8000" in FRONTEND \
        and "25000" in FRONTEND


def test_api_endpoint_in_index_is_https_only():
    # any literal URL in the page config must be https
    import re
    for m in re.finditer(r"apiBase:\s*'([^']+)'", FRONTEND):
        assert m.group(1).startswith('https://')


class H(srv.Handler):
    def __init__(self, origin='https://faisalnugroho.github.io'):
        self.headers = {'Origin': origin}
        self._sent = []

    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        self._sent.append((k, v))

    def end_headers(self):
        pass


def test_cors_allows_pages_origin():
    h = H()
    srv.Handler._cors(h)
    allow = dict(h._sent).get('Access-Control-Allow-Origin')
    assert allow == 'https://faisalnugroho.github.io'


def test_cors_rejects_foreign_origin():
    h = H(origin='https://evil.example')
    srv.Handler._cors(h)
    assert 'Access-Control-Allow-Origin' not in dict(h._sent)
