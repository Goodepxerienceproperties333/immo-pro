"""iter95r - Test unitaire du detecteur PCMN-vs-IBAN utilise a l'import."""
import sys
sys.path.insert(0, '/app/backend')
from routes.banking import _looks_like_pcmn_code, _build_pcmn_warning


def test_positive_cases():
    # Codes PCMN classiques
    assert _looks_like_pcmn_code('551000') is True
    assert _looks_like_pcmn_code('55103400') is True
    assert _looks_like_pcmn_code('5500591') is True
    assert _looks_like_pcmn_code('550') is True  # 3 chiffres minimum
    # Avec separateurs
    assert _looks_like_pcmn_code('551 000') is True
    assert _looks_like_pcmn_code('551-000') is True


def test_negative_cases():
    # Vrais IBAN
    assert _looks_like_pcmn_code('BE68539007547034') is False
    assert _looks_like_pcmn_code('BE68 5390 0754 7034') is False
    assert _looks_like_pcmn_code('FR7630006000011234567890189') is False
    # Ne commence pas par 55
    assert _looks_like_pcmn_code('44000') is False
    assert _looks_like_pcmn_code('12345') is False
    assert _looks_like_pcmn_code('4400001') is False
    # Trop court / trop long
    assert _looks_like_pcmn_code('55') is False        # < 3
    assert _looks_like_pcmn_code('551000000') is False  # > 8
    # Vide / None
    assert _looks_like_pcmn_code('') is False
    assert _looks_like_pcmn_code(None) is False


def test_message_content():
    m = _build_pcmn_warning('551000')
    assert '551000' in m
    assert 'PCMN' in m
    assert 'IBAN' in m
    assert 'Parametres' in m
    assert 'Comptes bancaires' in m


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
            print(f"PASS {name}")
    print("All iter95r detector tests OK")
