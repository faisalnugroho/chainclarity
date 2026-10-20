#!/usr/bin/env python3
"""Deploy ChainClarity to GenLayer Studionet + live smoke test.

Usage: ~/genlayer-env/bin/python scripts/deploy_studionet.py [--smoke-only ADDR]

Logs every scenario to docs/deployment_log.json (flushed after EACH
scenario). Fresh account per deploy, keyfile saved to
~/.config/chainclarity/deploy_key.json (gitignored path, never in repo).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus, ExecutionResult

ROOT = Path(__file__).resolve().parents[1]
KEYFILE = Path.home() / '.config/chainclarity/deploy_key.json'
LOG = ROOT / 'docs/deployment_log.json'
CONTRACT = ROOT / 'contracts/chainclarity.py'


def log_entry(entry):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = json.loads(LOG.read_text()) if LOG.exists() else {'entries': []}
    log['entries'].append(entry)
    LOG.write_text(json.dumps(log, indent=2))  # flush after EVERY entry


def receipt_failed(receipt):
    leader = (receipt or {}).get('consensus_data', {}).get('leader_receipt')
    if not isinstance(leader, list) or not leader:
        return None
    lr = leader[0]
    exec_name = (receipt or {}).get('tx_execution_result_name') or \
        lr.get('execution_result')
    if exec_name in ('FAILURE', 'ERROR'):
        return str(lr.get('genvm_result', {}).get('stderr', ''))[-1500:] or \
            str(lr.get('result', ''))[-500:]
    return None


def wait_final(client, tx_hash, timeout_s=240):
    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash, status=TransactionStatus.FINALIZED,
        full_transaction=True, retries=max(10, int(timeout_s * 1000 // 3000)))
    vote = receipt.get('result_name')
    err = receipt_failed(receipt)
    return receipt, vote, err


def main():
    smoke_only = None
    if len(sys.argv) > 2 and sys.argv[1] == '--smoke-only':
        smoke_only = sys.argv[2]
    client = create_client(chain=studionet, account=create_account())
    if smoke_only:
        addr = smoke_only
    else:
        if KEYFILE.exists():
            kf = json.loads(KEYFILE.read_text())
            account = create_account(account_private_key=kf['private_key'])
        else:
            account = create_account()
            KEYFILE.parent.mkdir(parents=True, exist_ok=True)
            KEYFILE.write_text(json.dumps({
                'address': account.address,
                'private_key': account.key.hex()}))
        client = create_client(chain=studionet, account=account)
        try:
            client.fund_account(account.address, 10 ** 18)
        except Exception as e:
            print('fund_account:', e)
        code = CONTRACT.read_text()
        print('deploying', len(code), 'bytes…', flush=True)
        tx_hash = client.deploy_contract(code=code, account=account,
                                         args=[],
                                         leader_only=True)
        receipt, vote, err = wait_final(client, tx_hash)
        if err:
            print('DEPLOY FAILED:', err)
            log_entry({'step': 'deploy', 'ok': False, 'tx': tx_hash,
                       'error': err, 'vote': vote})
            sys.exit(1)
        addr = receipt.get('data', {}).get('contract_address')
        print('DEPLOYED', addr, 'tx', tx_hash, flush=True)
        log_entry({'step': 'deploy', 'ok': True, 'tx': tx_hash,
                   'contract_address': addr,
                   'contract_sha256': __import__('hashlib').sha256(
                       CONTRACT.read_bytes()).hexdigest(),
                   'deployer': account.address, 'ts': time.time()})

    def read(fn, args):
        return client.read_contract(address=addr, function_name=fn, args=args)

    print('stats:', read('get_stats', []))

    # fresh in-memory account for smoke writes
    acct = create_account()
    client = create_client(chain=studionet, account=acct)
    try:
        client.fund_account(acct.address, 10 ** 17)
    except Exception as e:
        print('fund smoke account:', e)

    ETH_TX = '0x' + '9f' * 32
    ev = json.dumps({
        'provider': 'etherscan', 'tx_status': 'success',
        'fields': {'from': '0x' + 'aa' * 20, 'to': '0x' + 'bb' * 20,
                   'value_eth': '1.5', 'block': '21000000'},
        'retrieved_at': '2026-10-20T00:00:00Z',
        'source_url': 'https://etherscan.io/tx/' + ETH_TX,
        'limitations': ['smoke test evidence']})

    scenarios = []
    for verdict_note in ('smoke SUPPORTED-expected',):
        scenarios.append({
            'network': 'ethereum', 'tx_hash': ETH_TX,
            'claim': 'The transaction transferred 1.5 ETH from one address to another.',
            'evidence': ev})
    # negative: contradicted claim
    scenarios.append({
        'network': 'ethereum', 'tx_hash': '0x' + '7e' * 32,
        'claim': 'The transaction sent 100 ETH to the burn address.',
        'evidence': ev})
    # fail-safe: garbage evidence
    scenarios.append({
        'network': 'ethereum', 'tx_hash': '0x' + '5c' * 32,
        'claim': 'The transaction was legitimate and verified by auditors.',
        'evidence': 'not-json'})

    results = []
    for i, sc in enumerate(scenarios):
        entry = {'step': 'scenario', 'idx': i, 'note': sc['claim'][:60]}
        try:
            t0 = time.time()
            tx1 = client.write_contract(
                address=addr, function_name='submit_claim',
                args=[sc['network'], sc['tx_hash'], sc['claim'],
                      sc['evidence']], account=acct)
            r1, v1, e1 = wait_final(client, tx1)
            if e1:
                if 'evidence_malformed' in e1 and i == len(scenarios) - 1:
                    # expected fail-safe guard on the garbage-evidence case
                    entry['submit_tx'] = tx1
                    entry['verdict'] = 'GUARD_REJECTED (evidence_malformed)'
                    entry['ok'] = True
                    entry['duration_s'] = round(time.time() - t0, 1)
                    results.append(entry)
                    log_entry(entry)
                    print(json.dumps(entry), flush=True)
                    continue
                raise RuntimeError('submit_claim reverted: ' + e1)
            entry['submit_tx'] = tx1
            raw = r1.get('consensus_data', {}).get('leader_receipt', [{}])[0] \
                .get('result', {}).get('payload')
            m = re.search(r"'readable': '?\"?([0-9]+)", str(raw))
            if not m:
                raise RuntimeError('unreadable claim id payload: ' + str(raw))
            claim_id = int(m.group(1))
            entry['claim_id'] = claim_id
            tx2 = client.write_contract(
                address=addr, function_name='adjudicate',
                args=[claim_id], account=acct)
            r2, v2, e2 = wait_final(client, tx2)
            if e2:
                raise RuntimeError('adjudicate reverted: ' + e2)
            entry['adjudicate_tx'] = tx2
            rec = json.loads(read('get_claim', [claim_id]))
            entry['verdict'] = rec['verdict']
            entry['reasoning'] = rec['reasoning'][:200]
            entry['vote1'], entry['vote2'] = v1, v2
            entry['ok'] = rec['verdict'] in (
                'SUPPORTED', 'CONTRADICTED', 'INSUFFICIENT_EVIDENCE')
            entry['duration_s'] = round(time.time() - t0, 1)
        except Exception as e:
            entry['ok'] = False
            entry['error'] = str(e)[:500]
        results.append(entry)
        log_entry(entry)
        print(json.dumps(entry), flush=True)
        time.sleep(2)

    ok = sum(1 for r in results if r.get('ok'))
    print('SMOKE %d/%d ok' % (ok, len(results)))
    log_entry({'step': 'smoke_summary', 'ok_count': ok,
               'total': len(results), 'contract': addr})


if __name__ == '__main__':
    main()
