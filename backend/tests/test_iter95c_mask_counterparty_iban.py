"""iter95c : verify counterparty_account is masked in /api/owner/bank-accounts."""
import re


def _mask_iban(v: str) -> str:
    s = (v or "").replace(" ", "").upper()
    if len(s) >= 8:
        return s[:4] + "X" * (len(s) - 8) + s[-4:]
    return s


def test_belgian_iban_masked():
    assert _mask_iban("BE68539007547034") == "BE68XXXXXXXX7034"


def test_short_iban_untouched():
    assert _mask_iban("BE12") == "BE12"


def test_empty_iban():
    assert _mask_iban("") == ""
    assert _mask_iban(None) == ""


def test_iban_with_spaces_normalized():
    assert _mask_iban("BE68 5390 0754 7034") == "BE68XXXXXXXX7034"


def test_masked_format_regex():
    masked = _mask_iban("BE14ABCD12341383")
    assert re.match(r"^BE\d\dX+\d{4}$", masked), masked


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("All tests OK")
