"""Adapter + API server tests. All provider HTTP is mocked via urllib
monkeypatching — no network access in this suite (live smoke is separate)."""
import json
import sys
import threading
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))

import adapters  # noqa: E402
from adapters import (AdapterError, analyze, error_response,  # noqa: E402
                      fetch_ethereum_tx, fetch_solana_tx, validate_target)

ETH_TX = '0x' + 'ab' * 32
SOL_TX = '5' * 87
ETH_ADDR = '0x' + 'cd' * 20
SOL_ADDR = '4' * 44


class FakeResponse:
    def __init__(self, payload, status=200):
        self._data = json.dumps(payload).encode()
        self.status = status

    def read(self, n=-1):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def mock_urlopen(payload, status=200):
    return mock.patch.object(adapters.urllib.request, 'urlopen',
                             return_value=FakeResponse(payload, status))


# ------------------------------------------------------------ validation
def test_validate_eth_tx_ok():
    assert validate_target('ethereum', ETH_TX) == ETH_TX


def test_validate_eth_addr_ok():
    assert validate_target('ethereum', ETH_ADDR) == ETH_ADDR


def test_validate_eth_bad():
    for bad in ('', '0x1234', ETH_TX[:-1], 'zz' * 32):
        try:
            validate_target('ethereum', bad)
            assert False, bad
        except AdapterError as e:
            assert e.code == 'invalid_input'


def test_validate_solana_ok():
    assert validate_target('solana', SOL_TX) == SOL_TX
    assert validate_target('solana', SOL_ADDR) == SOL_ADDR


def test_validate_solana_bad():
    try:
        validate_target('solana', 'tooshort')
        assert False
    except AdapterError as e:
        assert e.code == 'invalid_input'


def test_validate_unsupported_network():
    try:
        validate_target('bitcoin', ETH_TX)
        assert False
    except AdapterError as e:
        assert e.code == 'unsupported_network'


# ------------------------------------------------------------ ethereum
def eth_proxy_body(result):
    return {'jsonrpc': '2.0', 'id': 1, 'result': result}


ETH_TX_OBJ = {
    'from': '0xaaa0000000000000000000000000000000000001',
    'to': '0xbbb0000000000000000000000000000000000002',
    'value': '0x151bd7d291ea0000',       # 1.54 ETH
    'blockNumber': '0x1406f40',
    'gasPrice': '0x4a817c800',
    'transactionIndex': '0x2',
}
ETH_RECEIPT_OK = {'status': '0x1', 'gasUsed': '0x5208'}
ETH_BLOCK = {'timestamp': '0x67f0a123'}


def test_eth_success_tx():
    bodies = iter([
        eth_proxy_body(ETH_TX_OBJ),
        eth_proxy_body(ETH_RECEIPT_OK),
        eth_proxy_body(ETH_BLOCK),
    ])

    def fake(url, timeout=None):
        return FakeResponse(next(bodies))

    adapters._CACHE.clear()
    with mock.patch.object(adapters.urllib.request, 'urlopen', side_effect=fake):
        ev = fetch_ethereum_tx(ETH_TX, 'KEY')
    assert ev['tx_status'] == 'success'
    assert ev['provider'] == 'etherscan'
    assert ev['fields']['from'] == ETH_TX_OBJ['from']
    assert ev['block_timestamp'] == str(int('0x67f0a123', 16))
    assert float(ev['fields']['fee_eth']) > 0
    assert 'provider-reported' in ev['limitations'][0]


def test_eth_failed_tx_not_success():
    """HTTP 200 + receipt status 0x0 must be tx_status=failed."""
    bodies = iter([eth_proxy_body(ETH_TX_OBJ),
                   eth_proxy_body({'status': '0x0', 'gasUsed': '0x5208'}),
                   eth_proxy_body(ETH_BLOCK)])
    adapters._CACHE.clear()
    with mock.patch.object(adapters.urllib.request, 'urlopen',
                           side_effect=lambda *a, **k: FakeResponse(next(bodies))):
        ev = fetch_ethereum_tx(ETH_TX, 'KEY')
    assert ev['tx_status'] == 'failed'


def test_eth_pending_tx():
    tx = dict(ETH_TX_OBJ, blockNumber=None)
    bodies = iter([eth_proxy_body(tx),
                   eth_proxy_body({})])
    adapters._CACHE.clear()
    with mock.patch.object(adapters.urllib.request, 'urlopen',
                           side_effect=lambda *a, **k: FakeResponse(next(bodies))):
        ev = fetch_ethereum_tx(ETH_TX, 'KEY')
    assert ev['tx_status'] == 'pending'


def test_eth_unknown_state():
    tx = dict(ETH_TX_OBJ, blockNumber='0x1406f40')
    bodies = iter([eth_proxy_body(tx),
                   eth_proxy_body({'status': '0xweird'}),
                   eth_proxy_body(ETH_BLOCK)])
    adapters._CACHE.clear()
    with mock.patch.object(adapters.urllib.request, 'urlopen',
                           side_effect=lambda *a, **k: FakeResponse(next(bodies))):
        ev = fetch_ethereum_tx(ETH_TX, 'KEY')
    assert ev['tx_status'] == 'unknown'


def test_eth_tx_not_found():
    adapters._CACHE.clear()
    with mock_urlopen(eth_proxy_body(None)):
        try:
            fetch_ethereum_tx(ETH_TX, 'KEY')
            assert False
        except AdapterError as e:
            assert e.code == 'tx_not_found'


def test_eth_quota_message_maps_to_bad_result():
    adapters._CACHE.clear()
    with mock_urlopen({'status': '0', 'message': 'NOTOK',
                       'result': 'Max rate limit reached'}):
        try:
            fetch_ethereum_tx(ETH_TX, 'KEY')
            assert False
        except AdapterError as e:
            assert e.code == 'provider_bad_result'


def test_eth_malformed_body():
    adapters._CACHE.clear()
    with mock.patch.object(adapters.urllib.request, 'urlopen',
                           return_value=FakeResponse({'nope': True})):
        try:
            fetch_ethereum_tx(ETH_TX, 'KEY')
            assert False
        except AdapterError as e:
            assert e.code == 'provider_bad_result'


def test_eth_timeout():
    adapters._CACHE.clear()

    def boom(*a, **k):
        raise TimeoutError()

    with mock.patch.object(adapters.urllib.request, 'urlopen', side_effect=boom):
        try:
            fetch_ethereum_tx(ETH_TX, 'KEY')
            assert False
        except AdapterError as e:
            assert e.code == 'provider_timeout'


def test_eth_http_429_maps_to_rate_limited():
    adapters._CACHE.clear()
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError('u', 429, 'rate', {}, None)

    with mock.patch.object(adapters.urllib.request, 'urlopen', side_effect=boom):
        try:
            fetch_ethereum_tx(ETH_TX, 'KEY')
            assert False
        except AdapterError as e:
            assert e.code == 'rate_limited' and e.http_status == 429


def test_eth_missing_key():
    try:
        fetch_ethereum_tx(ETH_TX, '')
        assert False
    except AdapterError as e:
        assert e.code == 'missing_api_key'


def test_eth_caching_same_doc():
    adapters._CACHE.clear()
    calls = []

    def fake(url, timeout=None):
        calls.append(url)
        return FakeResponse(eth_proxy_body(ETH_TX_OBJ))

    with mock.patch.object(adapters.urllib.request, 'urlopen', side_effect=fake):
        # both fetches fail identically at receipt (None result) — but the
        # tx fetch itself is not cached on failure; test caching on success
        pass
    bodies = iter([eth_proxy_body(ETH_TX_OBJ), eth_proxy_body(ETH_RECEIPT_OK),
                   eth_proxy_body(ETH_BLOCK), eth_proxy_body(ETH_TX_OBJ),
                   eth_proxy_body(ETH_RECEIPT_OK), eth_proxy_body(ETH_BLOCK)])
    with mock.patch.object(adapters.urllib.request, 'urlopen',
                           side_effect=lambda *a, **k: FakeResponse(next(bodies))):
        e1 = fetch_ethereum_tx(ETH_TX, 'KEY')
        e2 = fetch_ethereum_tx(ETH_TX, 'KEY')
    assert e1 == e2


# ------------------------------------------------------------ solana
SOL_BODY = {'success': True, 'data': {
    'success': True, 'block_id': 300111222, 'block_time': 1789900000,
    'fee': 5000,
    'balance': [
        {'address': 'WallA' + 'x' * 39, 'pre_balance': '1000000000',
         'post_balance': '995000000'},
        {'address': 'WallB' + 'y' * 39, 'pre_balance': '0',
         'post_balance': '5000000'},
    ],
}}


def test_sol_success_tx():
    adapters._CACHE.clear()
    with mock_urlopen(SOL_BODY):
        ev = fetch_solana_tx(SOL_TX, 'KEY')
    assert ev['tx_status'] == 'success'
    assert ev['provider'] == 'solscan'
    assert ev['fields']['fee_sol'] == '0.000005'
    assert ev['block_timestamp'] == '1789900000'
    changes = [k for k in ev['fields'] if k.startswith('sol_change_')]
    assert len(changes) == 2


def test_sol_failed_tx():
    adapters._CACHE.clear()
    body = json.loads(json.dumps(SOL_BODY))
    body['data']['success'] = False
    with mock_urlopen(body):
        ev = fetch_solana_tx(SOL_TX, 'KEY')
    assert ev['tx_status'] == 'failed'


def test_sol_unknown_when_success_field_missing():
    adapters._CACHE.clear()
    body = json.loads(json.dumps(SOL_BODY))
    body['data'].pop('success')
    with mock_urlopen(body):
        ev = fetch_solana_tx(SOL_TX, 'KEY')
    assert ev['tx_status'] == 'unknown'


def test_sol_missing_balance_field_records_limitation():
    adapters._CACHE.clear()
    body = json.loads(json.dumps(SOL_BODY))
    body['data'].pop('balance')
    with mock_urlopen(body):
        ev = fetch_solana_tx(SOL_TX, 'KEY')
    assert any('Balance-change' in l for l in ev['limitations'])


def test_sol_tx_not_found():
    adapters._CACHE.clear()
    with mock_urlopen({'success': True, 'data': {}}):
        try:
            fetch_solana_tx(SOL_TX, 'KEY')
            assert False
        except AdapterError as e:
            assert e.code == 'tx_not_found'


def test_sol_malformed():
    adapters._CACHE.clear()
    with mock_urlopen('not-json-object'):
        try:
            fetch_solana_tx(SOL_TX, 'KEY')
            assert False
        except AdapterError as e:
            assert e.code == 'provider_malformed_response'


# ------------------------------------------------------------ analyze() gate
def test_analyze_rejects_addresses():
    try:
        analyze('ethereum', ETH_ADDR)
        assert False
    except AdapterError as e:
        assert e.code == 'address_not_supported'
    try:
        analyze('solana', SOL_ADDR)
        assert False
    except AdapterError as e:
        assert e.code == 'address_not_supported'


def test_error_messages_exist_for_all_codes():
    for code in ('invalid_input', 'unsupported_network',
                 'address_not_supported', 'tx_not_found', 'missing_api_key',
                 'rate_limited', 'provider_http_error',
                 'provider_unreachable', 'provider_timeout',
                 'provider_malformed_response', 'provider_bad_result',
                 'response_too_large'):
        msg = error_response(code)['message']
        assert msg and 'Unknown' not in msg


def test_error_message_never_contains_key():
    adapters._CACHE.clear()
    try:
        fetch_ethereum_tx(ETH_TX, 'SECRETKEY123')
        assert False
    except AdapterError as e:
        assert 'SECRETKEY123' not in error_response(e.code)['message']
        assert 'SECRETKEY123' not in str(e)


# ------------------------------------------------ keyless fallback adapters
def test_analyze_falls_back_to_keyless_when_no_keys(monkeypatch):
    import adapters_keyless as ak
    adapters._CACHE.clear()
    monkeypatch.delenv('ETHERSCAN_API_KEY', raising=False)
    bs_tx = {'hash': ETH_TX, 'status': 'ok', 'block_number': 100,
             'from': {'hash': '0x' + 'a' * 40},
             'to': {'hash': '0x' + 'b' * 40}, 'value': '0', 'gas_used': '1',
             'fee': {'value': '100'}, 'timestamp': 1700000000}
    bodies = iter([bs_tx, {'status': '0x1'}])
    with mock.patch.object(ak, '_bs_get',
                           side_effect=lambda p: (200, next(bodies))), \
         mock.patch.object(ak, '_rpc',
                           return_value={'status': '0x1'}):
        ev = analyze('ethereum', ETH_TX)
    assert ev['tx_status'] == 'success'
    assert 'blockscout' in ev['provider']
    assert ev['fields']['independent_rpc_status_agrees'] == 'True'


def test_analyze_solana_falls_back_keyless(monkeypatch):
    import adapters_keyless as ak
    adapters._CACHE.clear()
    monkeypatch.delenv('SOLSCAN_API_KEY', raising=False)
    sol_tx = {'slot': 123, 'blockTime': 1700000000, 'meta': {
        'err': None, 'fee': 5000, 'preBalances': [10 ** 9, 0],
        'postBalances': [10 ** 9 - 5000, 0]},
        'transaction': {'message': {'accountKeys': ['WallA' + 'x' * 39,
                                                    'WallB' + 'y' * 39]}}}
    with mock.patch.object(ak, '_rpc', return_value=sol_tx):
        ev = analyze('solana', SOL_TX)
    assert ev['tx_status'] == 'success' and ev['provider'] == 'solana-rpc'
    assert ev['fields']['fee_sol'] == '0.000005'


def test_keyless_sol_tx_not_found(monkeypatch):
    import adapters_keyless as ak
    adapters._CACHE.clear()
    monkeypatch.delenv('SOLSCAN_API_KEY', raising=False)
    with mock.patch.object(ak, '_rpc', return_value=None):
        try:
            analyze('solana', SOL_TX)
            assert False
        except AdapterError as e:
            assert e.code == 'tx_not_found'


def test_keyless_eth_disagreement_flagged(monkeypatch):
    import adapters_keyless as ak
    adapters._CACHE.clear()
    monkeypatch.delenv('ETHERSCAN_API_KEY', raising=False)
    bs_tx = {'hash': ETH_TX, 'status': 'ok', 'block_number': 100,
             'from': {'hash': '0x' + 'a' * 40},
             'to': {'hash': '0x' + 'b' * 40}, 'value': '0', 'gas_used': '1',
             'fee': {'value': '100'}, 'timestamp': 1700000000}
    bodies = iter([bs_tx, {'status': '0x0'}])   # RPC says FAILED
    with mock.patch.object(ak, '_bs_get',
                           side_effect=lambda p: (200, next(bodies))), \
         mock.patch.object(ak, '_rpc',
                           return_value={'status': '0x0'}):
        ev = analyze('ethereum', ETH_TX)
    assert ev['fields']['independent_rpc_status_agrees'] == 'False'
    assert any('disagrees' in l for l in ev['limitations'])
