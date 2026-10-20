"""Keyless evidence adapters — fallback when ETHERSCAN_API_KEY /
SOLSCAN_API_KEY are absent.

Providers (all keyless, verified live Oct 2026):
  Ethereum: Blockscout v2 (eth.blockscout.com/api/v2) + independent
            confirmation via public RPC (ethereum-rpc.publicnode.com)
  Solana:   Solana mainnet-beta JSON-RPC (api.mainnet-beta.solana.com),
            confirmed via publicnode

Trust model is UNCHANGED: every document is provider-reported and labeled
as such; dual-source agreement is recorded, never presented as on-chain
truth. When official API keys ARE configured, the keyed adapters remain
preferred (analyze() tries them first).
"""
import json
import time
import urllib.error
import urllib.request

from adapters import (AdapterError, ETH_HASH_RE, SOL_SIG_RE,
                      _fmt_units, _hex_int, _store, _cached, _fetch_json)

BS_URL = 'https://eth.blockscout.com/api/v2'
BS_TXS = BS_URL + '/transactions'
ETH_RPC = 'https://ethereum-rpc.publicnode.com'
SOL_RPC = 'https://api.mainnet-beta.solana.com'
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) ChainClarity/1.0'}


def _bs_get(path):
    return _fetch_json(BS_URL + path, headers=UA)


def _rpc(url, method, params):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1,
                       'method': method, 'params': params}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': UA['User-Agent']})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read(256 * 1024).decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        raise AdapterError('provider_http_error', 502)
    except Exception:
        raise AdapterError('provider_unreachable', 504)
    if 'error' in data:
        raise AdapterError('provider_bad_result', 502)
    return data['result']


def _base_doc(provider, network, tx_id, tx_status, fields, limitations):
    return {
        'provider': provider,
        'tx_status': tx_status,
        'fields': fields,
        'retrieved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'source_url': ('https://etherscan.io/tx/' if network == 'ethereum'
                       else 'https://solscan.io/tx/') + tx_id,
        'limitations': limitations,
        'network': network,
        'tx_hash': tx_id,
    }


# ------------------------------------------------------------- Ethereum
def fetch_ethereum_tx_keyless(tx_hash: str) -> dict:
    if not ETH_HASH_RE.match(tx_hash or ''):
        raise AdapterError('invalid_input', 400)
    ck = 'etk:' + tx_hash
    hit = _cached(ck)
    if hit is not None:
        return hit
    st, tx = _bs_get('/transactions/' + tx_hash)
    if not isinstance(tx, dict) or not tx.get('hash'):
        raise AdapterError('tx_not_found', 404)
    status = str(tx.get('status', ''))
    if status == 'ok':
        tx_status = 'success'
    elif status in ('error', 'reverted'):
        tx_status = 'failed'
    elif tx.get('block_number') is None:
        tx_status = 'pending'
    else:
        tx_status = 'unknown'

    to = (tx.get('to') or {}).get('hash') or '(contract creation)'
    fields = {
        'from': str((tx.get('from') or {}).get('hash', '')),
        'to': str(to),
        'value_eth': _fmt_units(tx.get('value', '0'), 10 ** 18) or '0',
        'block': str(tx.get('block_number') or ''),
        'gas_used': str(tx.get('gas_used') or ''),
        'fee_eth': _fmt_units(tx.get('fee', {}).get('value', '0')
                              if isinstance(tx.get('fee'), dict)
                              else tx.get('fee', '0'), 10 ** 18) or '0',
        'transaction_types': ','.join(tx.get('type', '') and
                                      [str(tx.get('type'))] or []),
    }
    fields.pop('transaction_types', None)
    limitations = ['Data is provider-reported (Blockscout), not '
                   'independently verified on-chain truth.']
    # independent confirmation via public RPC (best-effort, dual source)
    confirmed = None
    try:
        rc = _rpc(ETH_RPC, 'eth_getTransactionReceipt', [tx_hash])
        if isinstance(rc, dict) and rc.get('status') is not None:
            confirmed = '0x1' if rc['status'] == '0x1' else '0x0'
        elif rc is None:
            limitations.append('Independent RPC has no receipt for this '
                               'hash (unknown to that node).')
    except AdapterError:
        limitations.append('Independent RPC confirmation unavailable.')
    if confirmed:
        agree = (confirmed == '0x1') == (tx_status == 'success')
        fields['independent_rpc_status_agrees'] = str(agree)
        if not agree:
            limitations.append('WARNING: second provider disagrees on '
                               'status — treat all fields as unreliable.')
    ts = ''
    if tx.get('timestamp'):
        ts = str(tx['timestamp'])
        fields['block_timestamp_unix'] = ts
    else:
        limitations.append('Block timestamp unavailable.')
    out = _base_doc('blockscout+public-rpc', 'ethereum', tx_hash,
                    tx_status, fields, limitations)
    _store(ck, out)
    return out


# ------------------------------------------------------------- Solana
def fetch_solana_tx_keyless(signature: str) -> dict:
    if not SOL_SIG_RE.match(signature or ''):
        raise AdapterError('invalid_input', 400)
    ck = 'stk:' + signature
    hit = _cached(ck)
    if hit is not None:
        return hit
    tx = _rpc(SOL_RPC, 'getTransaction',
              [signature, {'encoding': 'json', 'maxSupportedTransactionVersion': 0}])
    if tx is None:
        raise AdapterError('tx_not_found', 404)
    meta = tx.get('meta') or {}
    err = meta.get('err')
    if err is None:
        tx_status = 'success'
    elif isinstance(err, dict):
        tx_status = 'failed'
    else:
        tx_status = 'unknown'
    fields = {
        'fee_sol': _fmt_units(meta.get('fee', 0), 10 ** 9) or '0',
        'slot': str(tx.get('slot', '')),
    }
    limitations = ['Data is provider-reported (Solana JSON-RPC), not '
                   'independently verified on-chain truth.',
                   'Native SOL balance changes only; token transfers '
                   'are not decoded in v1.']
    try:
        pre = meta.get('preBalances') or []
        post = meta.get('postBalances') or []
        accts = ((tx.get('transaction') or {}).get('message')
                 or {}).get('accountKeys') or []
        deltas = []
        for i, a in enumerate(accts[:50]):
            if i < len(pre) and i < len(post):
                d = int(post[i]) - int(pre[i])
                if d != 0:
                    deltas.append((str(a)[:10], d))
        deltas.sort(key=lambda x: -abs(x[1]))
        for addr, d in deltas[:3]:
            fields['sol_change_' + addr] = _fmt_units(d, 10 ** 9) or str(d)
        if not deltas:
            limitations.append('No nonzero native balance changes.')
    except Exception:
        limitations.append('Balance-change parsing failed.')
    if tx.get('blockTime'):
        fields['block_timestamp_unix'] = str(tx['blockTime'])
    else:
        limitations.append('Block timestamp unavailable.')
    if err is not None:
        fields['error'] = str(err)
    out = _base_doc('solana-rpc', 'solana', signature, tx_status,
                    fields, limitations)
    _store(ck, out)
    return out
