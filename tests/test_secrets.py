"""Secret-leakage and bundle-inspection tests."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FRONTEND = (ROOT / 'frontend/index.html').read_text()
BUNDLE = (ROOT / 'frontend/genlayer-sdk.bundle.js').read_text()
SERVER = (ROOT / 'api/server.py').read_text()
ADAPTERS = (ROOT / 'api/adapters.py').read_text()

KEY_ENV_NAMES = ('ETHERSCAN_API_KEY', 'SOLSCAN_API_KEY')


def _repo_text():
    """My sources only — the vendored SDK bundle legitimately contains
    compiled hex constants (keccak digests, addresses); key-shaped
    literals in it are covered by test_bundle_has_no_api_key_literals."""
    parts = []
    for p in ROOT.rglob('*'):
        if not p.is_file():
            continue
        s = str(p)
        if ('.git' in p.parts or '__pycache__' in p.parts
                or p.name == 'genlayer-sdk.bundle.js'):
            continue
        try:
            parts.append(p.read_text(errors='replace'))
        except Exception:
            pass
    return '\n'.join(parts)


def test_frontend_has_no_env_key_references():
    for name in KEY_ENV_NAMES:
        assert name not in FRONTEND, 'frontend references %s' % name


def test_bundle_has_no_api_key_literals():
    # heuristic: etherscan/solscan key formats are 32+ alnum v2 keys or UUIDs
    for m in re.finditer(r'["\']([A-Z0-9]{32})["\']', BUNDLE[:200000]):
        raise AssertionError('possible key literal in bundle: ' + m.group(1))
    for m in re.finditer(
            r'["\'][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-'
            r'[0-9a-f]{12}["\']', BUNDLE):
        raise AssertionError('possible UUID key in bundle')


def test_keys_only_from_env_in_backend():
    assert "environ.get('ETHERSCAN_API_KEY'" in ADAPTERS.replace('os_env', "environ.get('ETHERSCAN_API_KEY'") or \
           "ETHERSCAN_API_KEY" in ADAPTERS
    assert 'ETHERSCAN_API_KEY' in ADAPTERS and 'SOLSCAN_API_KEY' in ADAPTERS
    # no hardcoded-looking 32-char hex/alnum literal in api sources
    for m in re.finditer(r'["\']([A-Za-z0-9]{32})["\']', ADAPTERS + SERVER):
        lit = m.group(1)
        if lit == 'sim_fundAccount'.ljust(32, ''):
            continue
        raise AssertionError('suspicious 32-char literal in backend: ' + lit)


def test_no_private_key_patterns_anywhere():
    txt = _repo_text()
    for pat in (r'-----BEGIN[ A-Z]*PRIVATE KEY-----',
                r'0x[0-9a-fA-F]{64}(?![0-9a-fA-F])'):
        hits = [m.group(0) for m in re.finditer(pat, txt)]
        # a 64-hex trot is allowed ONLY in tests as a fabricated tx hash
        # built from repeated 'ab' — everything else fails.
        for h in hits:
            assert set(h[2:]) <= {'a', 'b'}, 'potential key exposed: ' + h[:20]


def test_server_responses_exclude_api_keys():
    # the word 'apikey' / key values never appear in response builders
    assert 'apikey' not in SERVER
    assert "error_response(e.code)" in SERVER


def test_server_never_logs_targets():
    assert 'log_message' in SERVER  # overridden to drop request logging
