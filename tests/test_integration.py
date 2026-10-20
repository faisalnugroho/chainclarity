"""Frontend↔backend + frontend↔contract integration tests.

No browser needed: these pin the source-to-source contract between
frontend/index.html, api/server.py, and contracts/chainclarity.py
(regression fails CI if any link is renamed or dropped).
"""
import json
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api'))

import adapters  # noqa: E402
import server as srv  # noqa: E402

FRONTEND = (ROOT / 'frontend/index.html').read_text()
CONTRACT = (ROOT / 'contracts/chainclarity.py').read_text()
SERVER = (ROOT / 'api/server.py').read_text()
BUNDLE = (ROOT / 'frontend/genlayer-sdk.bundle.js').read_text()


# ------------------------------------------------ frontend -> API
def test_frontend_posts_analyze_payload():
    assert "method:'POST'" in FRONTEND and '/api/analyze' in FRONTEND
    assert "JSON.stringify({ network, target })" in FRONTEND


def test_frontend_handles_error_taxonomy():
    import adapters as _ad
    for code in ('tx_not_found', 'invalid_input', 'address_not_supported',
                 'rate_limited', 'missing_api_key'):
        assert _ad.error_response(code)['error'] == code
        assert 'message' in _ad.error_response(code)
    assert 'error_response' in SERVER  # server renders them
    assert 'data.message' in FRONTEND  # frontend renders the message


def test_frontend_uses_config_endpoint_for_contract():
    assert '/api/config' in FRONTEND and 'contract_address' in SERVER


# ------------------------------------------------ frontend -> contract
def test_frontend_calls_real_contract_methods():
    for m in ('submit_claim', 'adjudicate', 'get_claim'):
        assert "functionName: '%s'" % m in FRONTEND
        assert "def %s" % m in CONTRACT


def test_frontend_submit_args_match_contract_signature():
    assert re.search(r"submit_claim', args: \[network, txHash, claim, evJson\]",
                     FRONTEND)
    sig = re.search(r'def submit_claim\(self, network: str, tx_hash: str, '
                    r'claim: str,\s*\n\s*evidence_json: str\)', CONTRACT)
    assert sig, 'contract signature changed — update frontend args'


def test_frontend_adjudicate_args_match_contract():
    assert re.search(r"adjudicate', args: \[String\(Number\(claimId\)\)\]",
                     FRONTEND)
    assert 'def adjudicate(self, claim_id: u256)' in CONTRACT


def test_frontend_checks_execution_result_not_just_finalization():
    assert 'execution_result' in FRONTEND
    assert 'reverted on chain' in FRONTEND


def test_frontend_reads_back_record():
    assert "functionName: 'get_claim'" in FRONTEND


def test_frontend_labels_demo_data():
    assert 'DEMO DATA' in FRONTEND and 'demo data loaded' in FRONTEND.lower()


def test_frontend_never_safety_claim():
    assert 'is safe' not in FRONTEND.lower().replace(
        '— it does not tell you whether it did what you expected', '')
    # the explainer must include the "success ≠ safe" caveat
    assert 'does not tell you whether' in FRONTEND


# ------------------------------------------------ server behavior
def _start_server():
    adapters._CACHE.clear()
    os = __import__('os')
    os.environ['CHAINCLARITY_CONTRACT'] = '0x' + '11' * 20
    os.environ['ETHERSCAN_API_KEY'] = 'TESTKEY'
    os.environ['SOLSCAN_API_KEY'] = 'TESTKEY'
    __import__('atexit').register(lambda: [os.environ.pop(k, None) for k in
                                           ('ETHERSCAN_API_KEY',
                                            'SOLSCAN_API_KEY')])
    srv._CONTRACT_ADDRESS = '0x' + '11' * 20  # read at import time
    s = srv.ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    return s, s.server_address[1]


def _post(port, path, obj):
    req = urllib.request.Request(
        'http://127.0.0.1:%d%s' % (port, path),
        data=json.dumps(obj).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(port, path):
    with urllib.request.urlopen('http://127.0.0.1:%d%s' % (port, path),
                                timeout=10) as r:
        return r.status, json.loads(r.read())


def test_api_roundtrip_success():
    adapters._CACHE.clear()
    tx = {'from': '0x' + 'a' * 40, 'to': '0x' + 'b' * 40,
          'value': '0x8ac7230489e80000', 'blockNumber': '0x100',
          'gasPrice': '0x1', 'transactionIndex': '0x1'}
    receipt = {'status': '0x1', 'gasUsed': '0x5208'}
    block = {'timestamp': '0x67f0a123'}
    bodies = iter([
        {'jsonrpc': '2.0', 'result': tx}, {'jsonrpc': '2.0', 'result': receipt},
        {'jsonrpc': '2.0', 'result': block}])
    s, port = _start_server()
    fakes = iter(bodies)
    try:
        with mock.patch.object(adapters, '_fetch_json',
                               side_effect=lambda *a, **k: (200, next(fakes))):
            st, body = _post(port, '/api/analyze',
                             {'network': 'ethereum', 'target': '0x' + 'ab' * 32})
        assert st == 200 and body['ok'] is True
        ev = body['evidence']
        assert ev['tx_status'] == 'success' and ev['provider'] == 'etherscan'
        assert ev['timestamp_source'] == 'provider_block_timestamp'
        assert 'block_timestamp' not in ev
        # health/config expose the contract and never key material
        st, h = _get(port, '/api/health')
        assert h['contract_configured'] is True
        st, cfg = _get(port, '/api/config')
        assert cfg['contract_address'] == '0x' + '11' * 20
    finally:
        s.shutdown()
    # evidence doc must validate against the contract's own schema check
    norm = json.dumps({
        'provider': ev['provider'], 'tx_status': ev['tx_status'],
        'fields': ev['fields'], 'retrieved_at': ev['retrieved_at'],
        'source_url': ev['source_url'], 'limitations': ev['limitations']})
    assert len(norm) < 12000  # contract MAX_EVIDENCE_LEN
    for k in ev['fields']:
        assert len(k) <= 60 and isinstance(ev['fields'][k], str)


def _Fake(payload):
    class R:
        def __init__(self, p):
            self._d = json.dumps(p).encode()
            self.status = 200
        def read(self, n=-1):
            return self._d
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return R(payload)


def test_api_rejects_bad_input():
    s, port = _start_server()
    try:
        st, body = _post(port, '/api/analyze',
                         {'network': 'ethereum', 'target': 'nope'})
        assert st == 400 and body['error'] == 'invalid_input'
        st, body = _post(port, '/api/analyze',
                         {'network': 'bitcoin', 'target': '0x' + 'ab' * 32})
        assert st == 400 and body['error'] == 'unsupported_network'
    finally:
        s.shutdown()
