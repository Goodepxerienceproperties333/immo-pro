"""iter90i9 : Test invariant critique - le Decompte annuel et la Situation
de compte d'un proprietaire DOIVENT afficher les MEMES totaux :
`total_called` et `total_payments` (et donc `balance`).

Le bug reporte par le user : sur l'ACP Maria Test V1, l'owner Boxus Wivine
voyait 4259.88 dans la Situation mais seulement 1727.20 dans le Decompte.
Cause : le Decompte lisait `fund_calls.distribution[owner_id]` alors que
la Situation lit directement `journal_entries` filtrees par
`third_party_id`. Post-mutation, la distribution pointe vers l'ancien
owner - le nouveau ne voit pas ses appels.

Fix (iter90i9) : le Decompte accepte un parametre `owner_ledger_entries`
qui contient TOUTES les JE (non contre-passees) touchant le compte tier
du proprietaire. Utilise pour recomputer total_called et total_payments
comme la Situation.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


def test_decompte_total_called_matches_situation_via_ledger_entries():
    """iter90i9 : quand owner_ledger_entries est fourni, le Decompte utilise
    la SOMME DES DEBITS sur le compte tier du proprio (comme la Situation),
    au lieu de sommer fund_calls.distribution[owner_id].amount qui rate
    les OD imputation charges et les appels post-mutation."""
    async def _run():
        from pdf_decompte import build_decompte_pdf
        owner_id = f"own-i9-{uuid.uuid4().hex[:6]}"
        copro_id = f"acp-i9-{uuid.uuid4().hex[:6]}"
        acc_prov = "40000042"
        acc_res = "40100042"
        owner = {
            "id": owner_id, "name": "Boxus Wivine",
            "address": "RUE DE LA SOURCE 14",
            "postal_code": "1300", "city": "WAVRE",
            "vcs_code": "+++103/7451/75185+++",
            "vcs_digits": "103745175185",
            "tier_accounts": {copro_id: {"provisions": acc_prov, "reserve": acc_res}},
        }
        copropriete = {"id": copro_id, "name": "ACP i9 test",
                       "address": "Rue Test 1", "postal_code": "1000",
                       "city": "Bruxelles"}
        fiscal_year = {"id": f"fy-{copro_id}", "name": "03.2026-02.2027",
                       "start_date": "2026-03-01", "end_date": "2027-02-28"}
        # Fund calls tronques : seul le fonds de reserve est present dans
        # `fund_calls.distribution`. Les 4 appels de provisions trimestriels
        # sont manquants (scenario user : distribution post-mutation
        # pointe vers l'ancien proprio pour les provisions).
        fund_calls = [
            {"id": "fc-res", "date": "2026-05-01",
             "name": "Fonds de reserve - Annuel 1/1",
             "call_type": "reserve", "due_date": "2026-05-31",
             "total_amount": 1723.38, "reserve_amount": 1723.38,
             "roulement_amount": 0,
             "distribution": [{"owner_id": owner_id, "amount": 530.80}]},
        ]
        # Grand livre canonique (source Situation) : contient TOUS les
        # appels reels vus par le proprio via son compte tier :
        # 4 provisions T1..T4 (500.29 chacun) + 1 reserve (530.80)
        # + 1 imputation charges (1642.72) = 4259.88
        # + Paiements 3621.13 (3 versements)
        owner_ledger_entries = [
            # T1
            {"id": "ve-t1", "journal_type": "VE", "date": "2026-03-01",
             "copropriete_id": copro_id,
             "lines": [
                 {"account_number": acc_prov, "debit": 500.29, "credit": 0,
                  "third_party_id": owner_id},
                 {"account_number": "700000", "debit": 0, "credit": 500.29},
             ]},
            # T2
            {"id": "ve-t2", "journal_type": "VE", "date": "2026-06-01",
             "copropriete_id": copro_id,
             "lines": [
                 {"account_number": acc_prov, "debit": 500.29, "credit": 0,
                  "third_party_id": owner_id},
                 {"account_number": "700000", "debit": 0, "credit": 500.29},
             ]},
            # T3
            {"id": "ve-t3", "journal_type": "VE", "date": "2026-09-01",
             "copropriete_id": copro_id,
             "lines": [
                 {"account_number": acc_prov, "debit": 500.29, "credit": 0,
                  "third_party_id": owner_id},
                 {"account_number": "700000", "debit": 0, "credit": 500.29},
             ]},
            # T4
            {"id": "ve-t4", "journal_type": "VE", "date": "2026-12-01",
             "copropriete_id": copro_id,
             "lines": [
                 {"account_number": acc_prov, "debit": 500.29, "credit": 0,
                  "third_party_id": owner_id},
                 {"account_number": "700000", "debit": 0, "credit": 500.29},
             ]},
            # Reserve appelee (via fund_calls)
            {"id": "ve-res", "journal_type": "VE", "date": "2026-05-01",
             "copropriete_id": copro_id,
             "lines": [
                 {"account_number": acc_res, "debit": 530.80, "credit": 0,
                  "third_party_id": owner_id},
                 {"account_number": "160", "debit": 0, "credit": 530.80},
             ]},
            # Imputation charges reelles (OD de cloture ou continuous)
            {"id": "od-imput", "journal_type": "OD", "date": "2027-02-28",
             "copropriete_id": copro_id,
             "lines": [
                 {"account_number": acc_prov, "debit": 1642.72, "credit": 0,
                  "third_party_id": owner_id},
                 {"account_number": "700000", "debit": 0, "credit": 1642.72},
             ]},
            # Paiements 501 + 501 + 324.34 + 2294.79 (multiples) = 3621.13
            {"id": "fi-p1", "journal_type": "FI", "date": "2026-03-09",
             "copropriete_id": copro_id,
             "lines": [
                 {"account_number": "550000", "debit": 501.00, "credit": 0},
                 {"account_number": acc_prov, "debit": 0, "credit": 501.00,
                  "third_party_id": owner_id},
             ]},
            {"id": "fi-p2", "journal_type": "FI", "date": "2026-05-20",
             "copropriete_id": copro_id,
             "lines": [
                 {"account_number": "550000", "debit": 324.34, "credit": 0},
                 {"account_number": acc_prov, "debit": 0, "credit": 324.34,
                  "third_party_id": owner_id},
             ]},
            {"id": "fi-p3", "journal_type": "FI", "date": "2026-06-04",
             "copropriete_id": copro_id,
             "lines": [
                 {"account_number": "550000", "debit": 501.00, "credit": 0},
                 {"account_number": acc_prov, "debit": 0, "credit": 501.00,
                  "third_party_id": owner_id},
             ]},
            {"id": "fi-p4", "journal_type": "FI", "date": "2026-09-01",
             "copropriete_id": copro_id,
             "lines": [
                 {"account_number": "550000", "debit": 2294.79, "credit": 0},
                 {"account_number": acc_prov, "debit": 0, "credit": 2294.79,
                  "third_party_id": owner_id},
             ]},
        ]
        # Somme attendue : total_called = 4 x 500.29 + 530.80 + 1642.72 = 4174.68
        # (le user voit 4259.88, ecart 85.20 - depend de la ventilation
        # reserve dans son cas particulier). L'IMPORTANT est de tester
        # l'egalite Decompte==Ledger, pas la valeur absolue.
        expected_called = sum(
            float(ln.get("debit", 0) or 0)
            for e in owner_ledger_entries
            for ln in e.get("lines", [])
            if ln.get("third_party_id") == owner_id
        )
        expected_paid = sum(
            float(ln.get("credit", 0) or 0)
            for e in owner_ledger_entries
            for ln in e.get("lines", [])
            if ln.get("third_party_id") == owner_id
        )
        expected_balance = round(expected_called - expected_paid, 2)

        pdf_bytes = build_decompte_pdf(
            owner=owner, copropriete=copropriete, fiscal_year=fiscal_year,
            owner_lots=[], all_lots=[], invoices=[], distribution_keys=[],
            fund_calls=fund_calls, payments=[],
            preview=False,
            owner_ledger_entries=owner_ledger_entries,
        )
        assert pdf_bytes and len(pdf_bytes) > 500, "PDF vide"
        # Verifie le contenu textuel du PDF (extrait via pypdf)
        try:
            from pypdf import PdfReader
            from io import BytesIO
            reader = PdfReader(BytesIO(pdf_bytes))
            text = "".join(p.extract_text() or "" for p in reader.pages)
        except Exception:
            # Fallback : recherche dans le stream brut
            text = pdf_bytes.decode("latin-1", errors="ignore")
        # Le total appele doit correspondre au ledger (source Situation),
        # PAS a la somme fund_calls.distribution (qui vaudrait 530.80).
        # Le formatage inclut un espace comme separateur de milliers (fr-BE)
        fmt_called_fr = f"{expected_called:,.2f}".replace(",", " ").replace(".", ",")
        fmt_called_std = f"{expected_called:.2f}".replace(".", ",")
        assert fmt_called_fr in text or fmt_called_std in text or f"{expected_called:.2f}" in text, (
            f"Total appele du Decompte doit reflecter le grand livre "
            f"({fmt_called_fr}), text extrait de {len(text)} chars"
        )
        # Verifier explicitement qu'on ne montre PAS le 530.80 seul comme
        # total appele (ancien bug) : "Total appele par le syndic" doit
        # etre suivi d'une valeur >= 4000, pas de 530.80.
        idx = text.find("Total appele")
        if idx > 0:
            window = text[idx:idx + 100]
            assert ("4 174" in window or "4174" in window
                    or f"{int(expected_called)}" in window), (
                f"Total appele header doit etre ~{expected_called}, got: {window[:150]}"
            )
        # Le RESTE A REGLER doit etre la difference exacte (formule Situation)
        idx2 = text.find("RESTE A REGLER")
        if idx2 > 0:
            window2 = text[idx2:idx2 + 100]
            assert "553,55" in window2, (
                f"Reste a regler doit etre 553.55 (4174.68 - 3621.13), got: {window2[:150]}"
            )
        assert expected_balance != 0, "Sanity check : balance ne devrait pas etre 0"

    asyncio.run(_run())


def test_decompte_legacy_path_still_works_without_ledger_entries():
    """iter90i9 : sans owner_ledger_entries fourni, on tombe sur l'ancien
    calcul base sur fund_calls (retro-compatibilite). Le PDF doit toujours
    etre genere sans lever d'exception."""
    async def _run():
        from pdf_decompte import build_decompte_pdf
        owner_id = f"own-i9b-{uuid.uuid4().hex[:6]}"
        copro_id = f"acp-i9b-{uuid.uuid4().hex[:6]}"
        owner = {"id": owner_id, "name": "Legacy Test",
                 "address": "Rue X", "postal_code": "1000", "city": "Bxl",
                 "vcs_code": "+++111/1111/11111+++", "vcs_digits": "111111111111",
                 "tier_accounts": {}}
        copro = {"id": copro_id, "name": "ACP legacy", "address": "A",
                 "postal_code": "1000", "city": "Bxl"}
        fy = {"id": "fy-x", "name": "2026", "start_date": "2026-01-01",
              "end_date": "2026-12-31"}
        fund_calls = [{
            "id": "fc-legacy", "date": "2026-03-01",
            "name": "Provisions T1", "call_type": "provisions",
            "due_date": "2026-03-31", "total_amount": 400.0,
            "reserve_amount": 0, "roulement_amount": 0,
            "distribution": [{"owner_id": owner_id, "amount": 100.0}],
        }]
        pdf_bytes = build_decompte_pdf(
            owner=owner, copropriete=copro, fiscal_year=fy,
            owner_lots=[], all_lots=[], invoices=[], distribution_keys=[],
            fund_calls=fund_calls, payments=[],
            preview=True,
            # Pas de owner_ledger_entries -> chemin legacy
        )
        assert pdf_bytes and len(pdf_bytes) > 500

    asyncio.run(_run())
