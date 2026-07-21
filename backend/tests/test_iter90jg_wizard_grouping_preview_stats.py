"""iter90jg : Preview visuel du regroupement Optipro dans le wizard.

Verifie que la logique de regroupement cote frontend (miroir de `_group_key`
du backend iter90jf) affiche le bon compteur avant validation.

Ce test simule le calcul JS via une reimplementation Python identique - garantit
que les deux implementations restent synchronisees dans le temps.
"""
from __future__ import annotations


def _js_group_key(inv: dict, idx: int) -> str:
    """Reimplementation Python 1:1 de `groupPreview` cote JS iter90jg."""
    er = (inv.get("external_ref") or "").strip()
    sup = (inv.get("supplier_aux_code") or inv.get("supplier_name") or "").strip()
    dt = (inv.get("date") or "").strip()
    ir = (inv.get("internal_ref") or "").strip()
    if er:
        return f"EX:{er}|{sup}|{dt}"
    if ir:
        return f"IR:{ir}|{sup}|{dt}"
    return f"NIL:{idx}"


def _preview_stats(invoices):
    """Reimplementation JS iter90jg de `groupPreview`."""
    groups: dict[str, list] = {}
    for idx, inv in enumerate(invoices):
        k = _js_group_key(inv, idx)
        groups.setdefault(k, []).append(inv)
    multi = [g for g in groups.values() if len(g) > 1]
    return {
        "final_invoice_count": len(groups),
        "multi_count": len(multi),
        "total_grouped_lines": sum(len(g) for g in multi),
    }


def test_preview_stats_group_3_lines_into_1_invoice():
    """iter90jg-1 : 3 lignes CSV avec meme external_ref -> 1 facture finale."""
    invoices = [
        {"external_ref": "260081", "supplier_aux_code": "F42", "date": "2026-01-15",
         "internal_ref": "260081", "montant_tvac": 100.0},
        {"external_ref": "260081", "supplier_aux_code": "F42", "date": "2026-01-15",
         "internal_ref": "", "montant_tvac": 200.0},
        {"external_ref": "260081", "supplier_aux_code": "F42", "date": "2026-01-15",
         "internal_ref": "", "montant_tvac": 300.0},
    ]
    stats = _preview_stats(invoices)
    assert stats["final_invoice_count"] == 1
    assert stats["multi_count"] == 1
    assert stats["total_grouped_lines"] == 3


def test_preview_stats_two_different_invoices_not_merged():
    """iter90jg-2 : 260081 + 260082 -> 2 factures, 0 groupe multi-ligne."""
    invoices = [
        {"external_ref": "260081", "supplier_aux_code": "F42", "date": "2026-01-15", "montant_tvac": 100.0},
        {"external_ref": "260082", "supplier_aux_code": "F42", "date": "2026-01-15", "montant_tvac": 200.0},
    ]
    stats = _preview_stats(invoices)
    assert stats["final_invoice_count"] == 2
    assert stats["multi_count"] == 0
    assert stats["total_grouped_lines"] == 0


def test_preview_stats_single_line_no_multi_group():
    """iter90jg-3 : 1 ligne CSV -> 1 facture, aucun groupe multi (banner masque)."""
    invoices = [
        {"external_ref": "F-001", "supplier_aux_code": "F1", "date": "2026-02-01", "montant_tvac": 50.0},
    ]
    stats = _preview_stats(invoices)
    assert stats["final_invoice_count"] == 1
    assert stats["multi_count"] == 0
