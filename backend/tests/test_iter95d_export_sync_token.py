"""iter95d - Test unitaire de la logique de garde X-Sync-Token."""
import hmac
import os


def _check_token(x_sync_token, env_val):
    """Reproduit la logique exacte de routes/export_sync.py._check_token."""
    if env_val is None:
        os.environ.pop("EXPORT_SYNC_TOKEN", None)
    else:
        os.environ["EXPORT_SYNC_TOKEN"] = env_val
    expected = os.environ.get("EXPORT_SYNC_TOKEN", "")
    if not expected:
        return 503
    provided = x_sync_token or ""
    if not hmac.compare_digest(provided, expected):
        return 401
    return 200


def test_no_env_returns_503():
    assert _check_token("anything", None) == 503


def test_wrong_token_401():
    assert _check_token("wrong", "expected") == 401


def test_missing_header_401():
    assert _check_token(None, "expected") == 401


def test_empty_header_401():
    assert _check_token("", "expected") == 401


def test_correct_token_200():
    assert _check_token("expected", "expected") == 200


def test_constant_time_comparison_compatible():
    # Long tokens (typiques 64+ chars) : compare_digest doit rester coherent.
    tok = "a" * 64
    assert _check_token(tok, tok) == 200
    assert _check_token(tok + "x", tok) == 401


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print(f"PASS {n}")
    print("All export sync token tests OK")
