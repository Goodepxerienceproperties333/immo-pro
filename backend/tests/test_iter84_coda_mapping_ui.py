"""Regression test - iter84 - CODA preview + import confirme (UI mapping).

Demande user : "Integration complete interface CODA (P2)".

Verifie :
  - POST /api/banking/coda/preview parse SANS persister
  - Detection doublon via SHA256 (deuxieme upload meme fichier -> duplicate_warning)
  - Suggestions de match (VCS detecte, nom exact, fournisseur)
  - POST /api/banking/coda/import-confirmed cree statement + transactions
  - Application des overrides manuels + auto-lettrage pour les autres
  - Idempotence : import-confirmed avec meme hash echoue (409)
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


# Mini CODA file avec 2 mouvements pour tests rapides
def _build_coda(stmt_num="001", date_dmy="010126", vcs_digits="888777666555"):
    """Genere un fichier CODA minimal avec 2 mouvements (1 credit VCS + 1 debit nominatif)."""
    # Format CODA fixed-width. Champs critiques : pos 0 = record type
    # 0 record : header (128 chars max)
    h = "0" + (" " * 4) + "010126" + "BBL" + "1" + " " + " " + " " * 7 + "REF000000" + ("ACME ACP".ljust(26)) + ("BPOTBEBB".ljust(11)) + ("ACME ACP".ljust(26)) + " " * 30
    h = h.ljust(128)
    # 1 record : old balance
    # Format precis : pos 0="1", pos 1=" " separator, pos 2-4=stmt_num,
    # pos 5-41=account (37 chars), pos 42=sign, pos 43-57=balance (15 chars),
    # pos 58-63=date (6 chars)
    iban = "BE12345678901234567"
    b1 = ("1"
          + " "
          + stmt_num.zfill(3)
          + iban.ljust(37)
          + "0"
          + "0000000010000".zfill(15)
          + date_dmy
          + (" " * 25)
          + "EUR"
          + " " * 6)
    b1 = b1.ljust(128)
    # 8 record : new balance (meme alignment)
    new_bal_str = ("8"
                   + " "
                   + stmt_num.zfill(3)
                   + iban.ljust(37)
                   + "0"
                   + "0000000040000".zfill(15)
                   + date_dmy
                   + " " * 50)
    new_bal_str = new_bal_str.ljust(128)
    # 21 record : movement 1 (credit 500 EUR VCS)
    seq = "0001"
    detail = "0000"
    ref = " " * 21
    amount = "0" + ("0000000050000".zfill(15))  # +500.00
    # Format VCS : +++NNN/NNNN/NNNNN+++ (12 chiffres total)
    vcs_comm = f"+++{vcs_digits[0:3]}/{vcs_digits[3:7]}/{vcs_digits[7:12]}+++"
    m21 = "21" + seq + detail + ref + amount + date_dmy + " " * 8 + vcs_comm.ljust(54) + date_dmy + " " * 41
    m21 = m21.ljust(128)
    # 23 record : counterparty info
    cp_iban = "BE98765432109876"
    cp_name = "JEAN DUPONT"
    m23 = "23" + seq + detail + cp_iban.ljust(37) + cp_name.ljust(35) + " " * 43
    m23 = m23.ljust(128)
    # 21 record : movement 2 (debit 200 EUR, supplier name)
    seq2 = "0002"
    amount2 = "1" + ("0000000020000".zfill(15))  # -200.00 (sign=1=debit)
    comm2 = "FACTURE F-2026-001"
    m21b = "21" + seq2 + detail + ref + amount2 + date_dmy + " " * 8 + comm2.ljust(54) + date_dmy + " " * 41
    m21b = m21b.ljust(128)
    # 23 record : counterparty supplier
    cp_iban2 = "BE11111111112222"
    cp_name2 = "FOURNISSEUR TEST SA"
    m23b = "23" + seq2 + detail + cp_iban2.ljust(37) + cp_name2.ljust(35) + " " * 43
    m23b = m23b.ljust(128)
    # 8 record was already declared above (new_bal_str); remove duplicate
    # 9 trailer
    trailer = "9" + " " * 15 + "1".rjust(6) + ("0000000020000".zfill(15)) + ("0000000050000".zfill(15)) + " " * 71
    trailer = trailer.ljust(128)
    return "\r\n".join([h, b1, m21, m23, m21b, m23b, new_bal_str, trailer])


async def _setup():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    cid = f"itr84c-{uuid.uuid4()}"
    o1_id = f"o-{uuid.uuid4()}"
    s1_id = f"s-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "CODA TEST", "status": "active",
                                       "bank_accounts": [{"iban": "BE12345678901234567"}]})
    # Owner avec VCS code matching la communication (unique pour ce test)
    test_vcs_digits = f"888{uuid.uuid4().hex[:9]}"[:12].replace("a", "1").replace("b", "2").replace("c", "3").replace("d", "4").replace("e", "5").replace("f", "6")
    # Build digits-only version
    test_vcs_digits = "".join(c if c.isdigit() else str(ord(c) % 10) for c in test_vcs_digits)
    test_vcs_code = f"+++{test_vcs_digits[0:3]}/{test_vcs_digits[3:7]}/{test_vcs_digits[7:12]}+++"
    await db.owners.insert_one({
        "id": o1_id, "name": "Jean Dupont", "first_name": "Jean", "last_name": "Dupont",
        "vcs_code": test_vcs_code, "vcs_digits": test_vcs_digits,
        "copropriete_id": cid,
    })
    # Fournisseur avec IBAN matching m23b counterparty
    await db.suppliers.insert_one({
        "id": s1_id, "name": "FOURNISSEUR TEST SA",
        "iban": "BE11111111112222", "copropriete_id": cid,
    })
    return {"db": db, "cid": cid, "o1": o1_id, "s1": s1_id, "vcs_digits": test_vcs_digits}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.owners.delete_many({"id": ctx["o1"]})
    await db.suppliers.delete_many({"id": ctx["s1"]})
    await db.bank_statements.delete_many({"copropriete_id": ctx["cid"]})
    await db.bank_transactions.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})


def _get_endpoint(db, route_path: str):
    from routes.banking import create_banking_router
    router = create_banking_router(db)
    for r in router.routes:
        if r.path == route_path:
            return r.endpoint
    return None


class _FakeUpload:
    """Mock UploadFile for testing CODA preview without actual HTTP."""
    def __init__(self, content: bytes, filename: str):
        self._content = content
        self.filename = filename

    async def read(self):
        return self._content


async def _test_preview_returns_suggestions():
    ctx = await _setup()
    db = ctx["db"]
    try:
        preview_fn = _get_endpoint(db, "/api/banking/coda/preview")
        assert preview_fn is not None
        coda_content = _build_coda(vcs_digits=ctx["vcs_digits"]).encode("latin-1")
        result = await preview_fn(
            file=_FakeUpload(coda_content, "test.cod"),
            copropriete_id=ctx["cid"],
        )
        # Verifie structure
        assert "file_hash" in result and len(result["file_hash"]) == 64
        assert result["filename"] == "test.cod"
        assert "movements" in result
        assert len(result["movements"]) == 2

        # Mouvement 1 : credit avec VCS -> doit suggerer owner_payment
        m1 = result["movements"][0]
        assert m1["amount"] > 0  # credit
        sugg1 = m1["suggestion"]
        assert sugg1["match_type"] == "owner_payment", f"VCS doit matcher owner, recu {sugg1}"
        assert sugg1["match_id"] == ctx["o1"]
        assert sugg1["match_reason"] == "vcs"
        assert sugg1["confidence"] == "high"

        # Mouvement 2 : debit avec IBAN fournisseur -> doit suggerer supplier_payment
        m2 = result["movements"][1]
        assert m2["amount"] < 0  # debit
        sugg2 = m2["suggestion"]
        assert sugg2["match_type"] == "supplier_payment", f"IBAN doit matcher supplier, recu {sugg2}"
        assert sugg2["match_id"] == ctx["s1"]
        assert sugg2["match_reason"] == "supplier_iban"

        # Pas de doublon car premiere fois
        assert result["duplicate_warning"] is None

        # Pas de warning compte (IBAN connu)
        assert result["account_holder_warning"] is None

        # Verifie qu'AUCUNE persistance n'a eu lieu
        stmt_count = await db.bank_statements.count_documents({"copropriete_id": ctx["cid"]})
        txn_count = await db.bank_transactions.count_documents({"copropriete_id": ctx["cid"]})
        assert stmt_count == 0, "Preview NE DOIT PAS persister de statement"
        assert txn_count == 0, "Preview NE DOIT PAS persister de transactions"
        print("OK - preview retourne suggestions sans persister")
    finally:
        await _cleanup(ctx)


async def _test_duplicate_detection():
    """Apres un import, un preview du meme fichier doit signaler duplicate_warning."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        preview_fn = _get_endpoint(db, "/api/banking/coda/preview")
        confirm_fn = _get_endpoint(db, "/api/banking/coda/import-confirmed")
        coda_content = _build_coda(vcs_digits=ctx["vcs_digits"]).encode("latin-1")

        # Premiere fois : preview puis confirm
        p1 = await preview_fn(file=_FakeUpload(coda_content, "test.cod"), copropriete_id=ctx["cid"])
        Input = confirm_fn.__annotations__.get("data")
        # Construit payload de confirmation depuis preview
        movements_payload = [
            {
                "value_date": m["value_date"], "entry_date": m["entry_date"],
                "amount": m["amount"], "type": m["type"],
                "counterparty_name": m["counterparty_name"],
                "counterparty_account": m["counterparty_account"],
                "communication": m["communication"],
                "transaction_code": m["transaction_code"], "reference": m["reference"],
                "manual_match_type": m["suggestion"]["match_type"],
                "manual_match_id": m["suggestion"]["match_id"],
                "include": True,
            } for m in p1["movements"]
        ]
        payload = Input(
            file_hash=p1["file_hash"],
            filename=p1["filename"],
            copropriete_id=ctx["cid"],
            statement_number=p1["old_balance"].get("statement_number", ""),
            statement_date=p1["new_balance"].get("date", ""),
            account_number=p1["old_balance"].get("account_number", ""),
            opening_balance=p1["old_balance"].get("balance", 0),
            closing_balance=p1["new_balance"].get("balance", 0),
            movements=movements_payload,
        )
        result = await confirm_fn(data=payload)
        assert result["transactions_count"] == 2
        # Les 2 doivent etre matched_manual (on a applique la suggestion)
        assert result["matched_manual"] == 2

        # Deuxieme preview du meme fichier -> duplicate_warning
        p2 = await preview_fn(file=_FakeUpload(coda_content, "test.cod"), copropriete_id=ctx["cid"])
        assert p2["duplicate_warning"] is not None
        assert p2["duplicate_warning"]["statement_id"] == result["statement_id"]

        # Tentative de re-import avec meme hash -> 409
        from fastapi import HTTPException
        try:
            await confirm_fn(data=payload)
            assert False, "Doit lever HTTPException 409"
        except HTTPException as e:
            assert e.status_code == 409
        print("OK - duplicate detection via hash + idempotence import-confirmed")
    finally:
        await _cleanup(ctx)


async def _test_import_with_overrides_and_exclusion():
    """Verifie que l'utilisateur peut override les matchs et exclure des mouvements."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        preview_fn = _get_endpoint(db, "/api/banking/coda/preview")
        confirm_fn = _get_endpoint(db, "/api/banking/coda/import-confirmed")
        coda_content = _build_coda("002", vcs_digits=ctx["vcs_digits"]).encode("latin-1")
        p = await preview_fn(file=_FakeUpload(coda_content, "test2.cod"), copropriete_id=ctx["cid"])

        Input = confirm_fn.__annotations__.get("data")
        # Utilisateur exclut le mouvement 1, garde le 2 mais SANS match
        movements_payload = [
            {**{k: m[k] for k in ["value_date","entry_date","amount","type","counterparty_name",
                                  "counterparty_account","communication","transaction_code","reference"]},
             "manual_match_type": "" if idx == 0 else "",  # toutes sans match
             "manual_match_id": "",
             "include": (idx == 1),  # n'inclut QUE le mouvement 2
             }
            for idx, m in enumerate(p["movements"])
        ]
        payload = Input(
            file_hash=p["file_hash"], filename=p["filename"], copropriete_id=ctx["cid"],
            statement_number=p["old_balance"].get("statement_number", ""),
            statement_date=p["new_balance"].get("date", ""),
            account_number=p["old_balance"].get("account_number", ""),
            opening_balance=p["old_balance"].get("balance", 0),
            closing_balance=p["new_balance"].get("balance", 0),
            movements=movements_payload,
        )
        result = await confirm_fn(data=payload)
        assert result["transactions_count"] == 1, f"Attendu 1 txn (1 exclue), recu {result['transactions_count']}"
        assert result["ignored_count"] == 1
        # Sans match manuel, l'auto-lettrage tente de matcher (le mouvement 2 a IBAN fournisseur)
        # -> doit etre matched_auto = 1
        assert result["matched_auto"] == 1, f"Auto-lettrage devait matcher 1, recu {result['matched_auto']}"

        # Verifie en DB
        txns = await db.bank_transactions.find({"copropriete_id": ctx["cid"]}).to_list(10)
        assert len(txns) == 1
        assert txns[0]["matched"]
        assert txns[0]["match_type"] == "supplier_payment"
        print("OK - exclusion + auto-lettrage pour mouvements non overrides")
    finally:
        await _cleanup(ctx)


def test_iter84_coda_preview():
    asyncio.run(_test_preview_returns_suggestions())


def test_iter84_coda_duplicate_detection():
    asyncio.run(_test_duplicate_detection())


def test_iter84_coda_import_with_overrides():
    asyncio.run(_test_import_with_overrides_and_exclusion())
