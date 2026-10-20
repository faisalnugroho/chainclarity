"""ChainClarity server-side data adapters.

Reads transaction data from public explorers and converts it into the
bounded evidence document consumed by the ChainClarity Intelligent
Contract. API keys come from environment variables only:

    ETHERSCAN_API_KEY   (Ethereum mainnet, Etherscan v2, chainid=1)
    SOLSCAN_API_KEY     (Solana mainnet, Solscan Pro API v2)

Security properties:
- keys never leave the server; no key material appears in responses;
- all inputs validated; request timeouts; bounded response sizes;
- caching (TTL) to reduce quota usage; explicit error taxonomy;
- HTTP 200 is NEVER treated as transaction success — success comes
  only from the provider's own transaction-status field;
- provider output is reported as provider-reported fields, never as
  independently verified on-chain truth.
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

ETH_HASH_RE = re.compile(r'^0x[0-9a-fA-F]{64}$')
SOL_SIG_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{87,88}$')
ETH_ADDR_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')
SOL_ADDR_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')

ETHERSCAN_URL = 'https://api.etherscan.io/v2/api'
SOLSCAN_TX_URL = 'https://pro-api.solscan.io/v2.0/transaction/detail'

TIMEOUT_S = 15
MAX_BODY_BYTES = 512 * 1024
CACHE_TTL_S = 120
CACHE_MAX = 200
WEI = 10 ** 18
LAMPORT = 10 ** 9


class AdapterError(Exception):
    def __init__(self, code, http_status=502):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def validate_target(network: str, target: str) -> str:
    t = (target or '').strip()
    if network == 'ethereum':
        if not (ETH_HASH_RE.match(t) or ETH_ADDR_RE.match(t)):
            raise AdapterError('invalid_input', 400)
    elif network == 'solana':
        if not (SOL_SIG_RE.match(t) or SOL_ADDR_RE.match(t)):
            raise AdapterError('invalid_input', 400)
    else:
        raise AdapterError('unsupported_network', 400)
    return t


def _fetch_json(url, headers=None):
    """Bounded GET with timeout; raises AdapterError taxonomy codes."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = resp.read(MAX_BODY_BYTES + 1)
            if len(data) > MAX_BODY_BYTES:
                raise AdapterError('response_too_large')
            status = resp.status
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise AdapterError('rate_limited', 429)
        raise AdapterError('provider_http_error', 502)
    except urllib.error.URLError:
        raise AdapterError('provider_unreachable', 504)
    except TimeoutError:
        raise AdapterError('provider_timeout', 504)
    try:
        return status, json.loads(data.decode('utf-8', 'replace'))
    except Exception:
        raise AdapterError('provider_malformed_response', 502)


def _fmt_units(raw, divisor):
    try:
        v = int(raw)
    except Exception:
        return None
    if v % divisor == 0:
        return str(v // divisor)
    s = ('%.*f' % (18, v / divisor)).rstrip('0').rstrip('.')
    return s or '0'


def _hex_int(v):
    try:
        return int(str(v), 16)
    except Exception:
        return None


def _eth_amount(wei):
    try:
        w = int(wei)
        if w == 0:
            return '0'
        return str(w / WEI).rstrip('0').rstrip('.') if w % WEI else str(w // WEI)
    except Exception:
        return None


_CACHE = {}


def _cached(key):
    ent = _CACHE.get(key)
    if ent and time.time() - ent[0] < CACHE_TTL_S:
        return ent[1]
    return None


def _store(key, val):
    if len(_CACHE) >= CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = (time.time(), val)


def fetch_ethereum_tx(tx_hash: str, api_key: str) -> dict:
    """Etherscan v2 proxy eth_getTransactionByHash + eth_getTransactionReceipt."""
    if not ETH_HASH_RE.match(tx_hash or ''):
        raise AdapterError('invalid_input', 400)
    ck = 'eth:' + tx_hash
    hit = _cached(ck)
    if hit is not None:
        return hit
    if not api_key:
        raise AdapterError('missing_api_key', 500)

    def q(action):
        url = ETHERSCAN_URL + '?' + urllib.parse.urlencode({
            'chainid': '1', 'module': 'proxy', 'action': action,
            'txhash': tx_hash, 'apikey': api_key})
        st, body = _fetch_json(url)
        # proxy endpoints wrap as {"jsonrpc":..,"result":{...}}
        if not isinstance(body, dict) or 'result' not in body:
            raise AdapterError('provider_bad_result', 502)
        return body['result']

    tx = q('eth_getTransactionByHash')
    if tx is None:
        raise AdapterError('tx_not_found', 404)
    if not isinstance(tx, dict) or not tx:
        raise AdapterError('provider_bad_result', 502)
    receipt = q('eth_getTransactionReceipt')
    if not isinstance(receipt, dict):
        receipt = {}

    status_hex = str(receipt.get('status', ''))
    if status_hex == '0x1':
        tx_status = 'success'
    elif status_hex == '0x0':
        tx_status = 'failed'
    elif not receipt:
        tx_status = 'pending' if _hex_int(tx.get('blockNumber')) is None \
            else 'unknown'
    else:
        tx_status = 'unknown'

    fields = {
        'from': str(tx.get('from', '')),
        'to': str(tx.get('to', '')) or '(contract creation)',
        'value_eth': _fmt_units(tx.get('value', '0'), WEI) or '0',
        'block': str(_hex_int(tx.get('blockNumber')) or ''),
        'gas_used': str(_hex_int(receipt.get('gasUsed')) or ''),
        'fee_eth': _fmt_units(
            int(_hex_int(receipt.get('gasUsed')) or 0) *
            int(_hex_int(tx.get('gasPrice')) or 0), WEI) or '0',
        'transaction_index': str(_hex_int(tx.get('transactionIndex')) or ''),
    }
    ts = ''
    if tx_status != 'pending':
        try:
            blk_url = ETHERSCAN_URL + '?' + urllib.parse.urlencode({
                'chainid': '1', 'module': 'proxy',
                'action': 'eth_getBlockByNumber',
                'tag': tx.get('blockNumber'), 'boolean': 'false',
                'apikey': api_key})
            st, blk = _fetch_json(blk_url)
            bres = blk.get('result') if isinstance(blk, dict) else None
            if isinstance(bres, dict) and bres.get('timestamp'):
                ts = str(_hex_int(bres['timestamp']))
        except AdapterError:
            pass  # timestamp is best-effort; recorded as limitation
    limitations = ['Data is provider-reported (Etherscan), not '
                   'independently verified on-chain truth.']
    if not ts:
        limitations.append('Block timestamp unavailable.')
    out = {
        'provider': 'etherscan',
        'tx_status': tx_status,
        'fields': fields,
        'retrieved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'source_url': 'https://etherscan.io/tx/' + tx_hash,
        'limitations': limitations,
        'block_timestamp': ts,
        'network': 'ethereum',
        'tx_hash': tx_hash,
    }
    _store(ck, out)
    return out


def fetch_solana_tx(signature: str, api_key: str) -> dict:
    if not SOL_SIG_RE.match(signature or ''):
        raise AdapterError('invalid_input', 400)
    ck = 'sol:' + signature
    hit = _cached(ck)
    if hit is not None:
        return hit
    if not api_key:
        raise AdapterError('missing_api_key', 500)
    url = SOLSCAN_TX_URL + '?' + urllib.parse.urlencode({'tx': signature})
    st, body = _fetch_json(
        url, headers={'accept': 'application/json',
                      'token': api_key})
    if not isinstance(body, dict):
        raise AdapterError('provider_malformed_response', 502)
    data = body.get('data', body)
    if not isinstance(data, dict) or not data:
        raise AdapterError('tx_not_found', 404)
    succ = data.get('success')
    slot = data.get('block_id') or data.get('slot')
    if succ is True:
        tx_status = 'success'
    elif succ is False:
        tx_status = 'failed'
    else:
        tx_status = 'pending' if slot is None else 'unknown'
    fee_lamports = data.get('fee')
    fields = {
        'fee_sol': (_fmt_units(fee_lamports, LAMPORT) or 'unknown'),
        'slot': str(slot) if slot is not None else '',
    }
    ts = str(data.get('block_time') or '')
    limitations = ['Data is provider-reported (Solscan Pro), not '
                   'independently verified on-chain truth.']
    # native SOL balance changes (bounded to 3 largest)
    try:
        bals = data.get('balance') or []
        if not bals:
            limitations.append('Balance-change data unavailable.')
        deltas = []
        for b in bals[:50]:
            try:
                d = int(b.get('post_balance')) - int(b.get('pre_balance'))
            except Exception:
                continue
            if d != 0:
                deltas.append((b.get('address'), d))
        deltas.sort(key=lambda x: -abs(x[1]))
        for addr, d in deltas[:3]:
            fields['sol_change_' + str(addr)[:10]] = \
                _fmt_units(d, LAMPORT) or str(d)
    except Exception:
        limitations.append('Balance-change parsing failed.')
    out = {
        'provider': 'solscan',
        'tx_status': tx_status,
        'fields': fields,
        'retrieved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'source_url': 'https://solscan.io/tx/' + signature,
        'limitations': limitations,
        'block_timestamp': ts,
        'network': 'solana',
        'tx_hash': signature,
    }
    _store(ck, out)
    return out


def analyze(network: str, target: str) -> dict:
    """Validated entrypoint used by the API layer. Accepts a transaction
    hash/signature. Wallet-address history is NOT supported (documented
    limitation: address-history endpoints on the configured API plans
    cannot provide reliable bounded evidence)."""
    t = validate_target(network, target)
    is_tx = ETH_HASH_RE.match(t) if network == 'ethereum' \
        else SOL_SIG_RE.match(t)
    if not is_tx:
        raise AdapterError('address_not_supported', 400)
    if network == 'ethereum':
        return fetch_ethereum_tx(t, os_env('ETHERSCAN_API_KEY'))
    return fetch_solana_tx(t, os_env('SOLSCAN_API_KEY'))


def os_env(name):
    import os
    return os.environ.get(name, '')


def error_response(code):
    messages = {
        'invalid_input': 'Input is not a valid transaction hash for the '
                         'selected network.',
        'unsupported_network': 'Network not supported. Supported: '
                               'Ethereum mainnet, Solana mainnet.',
        'address_not_supported': 'Wallet-address analysis is not supported '
                                 'yet. Paste a transaction hash instead.',
        'tx_not_found': 'Transaction not found on the provider. It may be '
                        'too recent, or the hash may be wrong.',
        'missing_api_key': 'Service is not configured with provider '
                           'credentials. Contact the operator.',
        'rate_limited': 'Provider rate limit reached. Try again shortly.',
        'provider_http_error': 'Provider returned an error. Try again.',
        'provider_unreachable': 'Provider could not be reached.',
        'provider_timeout': 'Provider request timed out.',
        'provider_malformed_response': 'Provider returned an unreadable '
                                       'response.',
        'provider_bad_result': 'Provider returned no usable result.',
        'response_too_large': 'Provider response exceeded the size limit.',
    }
    return {'error': code, 'message': messages.get(code, 'Unknown error.')}
