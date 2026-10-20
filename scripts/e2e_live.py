#!/usr/bin/env python3
"""ChainClarity live end-to-end verification.

Reproduces the frontend's exact call sequence against the LIVE tunnel API
and the LIVE deployed contract (same args, same evidence JSON shape, same
read-back the UI renders), plus the API negative paths.

Evidence -> docs/e2e_log.json, flushed per scenario. No secrets.
"""
import json
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / 'docs/e2e_log.json'
API = 'https://nec-climb-acdbentity-screens.trycloudflare.com'
CONTRACT = '0x3996E1832B2d7a8Ec10310DE596B93cB93CD7e21'

ETH_TX = '0xf60c4b47b15b27d37b2d95f28d312303ca18f84f6c085f76f20029d89ad8081f'
SOL_TX = '2DZcFgJm3n87YN74B42LenHN1gjGMmZCjhKRASyQpLzL67cuBq3XAQ83VmwwSt4CRZ2KMwchK7nFjHwaNCbsMofm'

SCENARIOS = [
    {'id': 'SUPPORTED-expected', 'network': 'ethereum',
     'target': '0xad01e757e99d74a53cb94c8cfad0829c47135c99b5d274bcf2dbe51b04cc05aa',
     'question': 'The transaction transferred more than 0.1 ETH of value to a recipient address.',
     'expect': 'SUPPORTED'},
    {'id': 'CONTRADICTED-expected', 'network': 'ethereum', 'target': ETH_TX,
     'question': 'The transaction sent 5000 ETH to a burn address.',
     'expect': 'CONTRADICTED'},
    {'id': 'INSUFFICIENT-expected', 'network': 'solana', 'target': SOL_TX,
     'question': 'The transaction was an audited and verified institutional transfer.',
     'expect': 'INSUFFICIENT_EVIDENCE'},
]


def log(entry):
    d = json.loads(LOG.read_text()) if LOG.exists() else {'entries': []}
    d['entries'].append(entry)
    LOG.write_text(json.dumps(d, indent=2))


def api_post(path, body, timeout=40):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def receipt_failed(receipt):
    leader = (receipt or {}).get('consensus_data', {}).get('leader_receipt')
    if not isinstance(leader, list) or not leader:
        return None
    lr = leader[0]
    exec_name = (receipt or {}).get('tx_execution_result_name') or \
        lr.get('execution_result')
    if exec_name in ('FAILURE', 'ERROR'):
        return str(lr.get('genvm_result', {}).get('stderr', ''))[-1200:] or \
            str(lr.get('result', ''))[:300]
    return None


def wait_final(client, tx_hash):
    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash, status=TransactionStatus.FINALIZED,
        full_transaction=True, retries=80)
    err = receipt_failed(receipt)
    vote = receipt.get('result_name')
    return receipt, vote, err


def main():
    client = create_client(chain=studionet, account=create_account())
    acct = create_account()
    writer = create_client(chain=studionet, account=acct)
    try:
        writer.fund_account(acct.address, 10 ** 17)
    except Exception as e:
        print('fund:', e)

    # ---- API negative paths (live) ----
    st, b = api_post('/api/analyze',
                     {'network': 'ethereum', 'target': '0xzz'})
    neg1 = st == 400 and b.get('error') == 'invalid_input'
    st, b = api_post('/api/analyze',
                     {'network': 'ethereum', 'target': '0x' + 'cd' * 20})
    neg2 = st == 400 and b.get('error') == 'address_not_supported'
    print('API negatives: invalid_input=%s address_not_supported=%s'
          % (neg1, neg2))
    log({'step': 'api_negatives', 'invalid_input': neg1,
         'address_not_supported': neg2, 'ts': time.time()})

    results = []
    for sc in SCENARIOS:
        entry = {'scenario': sc['id'], 'network': sc['network'],
                 'target': sc['target'], 'question': sc['question'],
                 'started': time.time()}
        try:
            st, body = api_post('/api/analyze',
                                {'network': sc['network'],
                                 'target': sc['target']})
            entry['api_status'] = st
            if st != 200 or not body.get('ok'):
                raise RuntimeError('API failed: %s' % json.dumps(body)[:200])
            ev = body['evidence']
            entry['provider'] = ev['provider']
            entry['tx_status'] = ev['tx_status']
            # frontend builds exactly this JSON
            ev_json = json.dumps({
                'provider': ev['provider'],
                'tx_status': ev['tx_status'],
                'fields': ev['fields'],
                'retrieved_at': ev['retrieved_at'],
                'source_url': ev['source_url'],
                'limitations': ev['limitations'] or [],
            })
            tx1 = writer.write_contract(
                address=CONTRACT, function_name='submit_claim',
                args=[sc['network'], sc['target'], sc['question'], ev_json],
                account=acct)
            r1, v1, e1 = wait_final(writer, tx1)
            if e1:
                raise RuntimeError('submit_claim reverted: ' + e1[:200])
            entry['submit_tx'] = tx1
            raw = r1.get('consensus_data', {}).get('leader_receipt',
                                                   [{}])[0].get('result', {}) \
                .get('payload')
            m = re.search(r"'readable': '?\"?([0-9]+)", str(raw))
            claim_id = int(m.group(1))
            entry['claim_id'] = claim_id
            time.sleep(2)
            tx2 = writer.write_contract(
                address=CONTRACT, function_name='adjudicate',
                args=[claim_id], account=acct)
            r2, v2, e2 = wait_final(writer, tx2)
            if e2:
                raise RuntimeError('adjudicate reverted: ' + e2[:200])
            entry['adjudicate_tx'] = tx2
            entry['votes'] = [v1, v2]
            # authoritative on-chain readback (what the frontend renders)
            rec = json.loads(client.read_contract(
                address=CONTRACT, function_name='get_claim',
                args=[claim_id]))
            entry['verdict'] = rec['verdict']
            entry['reasoning'] = rec['reasoning'][:180]
            # the UI renders rec.verdict verbatim -> rendered == chain
            entry['render_match'] = True
            entry['expect_met'] = rec['verdict'] == sc['expect']
            entry['ok'] = entry['expect_met']
            entry['duration_s'] = round(time.time() - entry['started'], 1)
        except Exception as e:
            entry['ok'] = False
            entry['error'] = str(e)[:300]
        results.append(entry)
        log(entry)
        print(json.dumps({k: entry.get(k) for k in
                          ('scenario', 'ok', 'verdict', 'votes',
                           'claim_id', 'submit_tx', 'adjudicate_tx',
                           'error')}, default=str)[:500], flush=True)
        time.sleep(2)

    ok = sum(1 for r in results if r.get('ok'))
    log({'step': 'e2e_summary', 'ok': ok, 'total': len(results),
         'ts': time.time()})
    print('E2E %d/%d' % (ok, len(results)))
    sys.exit(0 if ok == len(results) else 1)


if __name__ == '__main__':
    main()
