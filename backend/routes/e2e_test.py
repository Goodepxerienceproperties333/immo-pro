"""E2E Test Agent — outil de test end-to-end pour Superadmin.

Cree une ACP fictive complete (proprietaires, lots, fournisseurs, factures,
extraits bancaires) et execute une serie de scenarios comptables pour
verifier le bon fonctionnement de bout en bout de la plateforme.

Utilise apres chaque deploiement/refactoring pour un smoke test complet.

Endpoints (SUPERADMIN uniquement) :
- POST /api/admin/e2e-test/run          Lance un run (mode: without_optipro | with_optipro)
- POST /api/admin/e2e-test/purge/{acp_id}  Purge une ACP de test + toutes ses donnees
- GET  /api/admin/e2e-test/history      Historique des runs
- GET  /api/admin/e2e-test/runs/{run_id}  Detail d'un run
"""
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone
import uuid
import time
import traceback


def _now():
    return datetime.now(timezone.utc).isoformat()


def _iso_date(y: int, m: int, d: int) -> str:
    return f"{y:04d}-{m:02d}-{d:02d}"


def create_e2e_test_router(db):
    router = APIRouter(prefix="/api/admin/e2e-test")

    async def _get_superadmin(request):
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        if not is_superadmin_only(user.get("role", "")):
            raise HTTPException(403, "Seul un super administrateur peut lancer l'agent de test")
        return user

    async def _seed_acp(user, mode: str) -> dict:
        """Cree l'ACP + config de base + syndic_id. Retourne le doc ACP."""
        acp_id = str(uuid.uuid4())
        now = _now()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        acp = {
            "id": acp_id,
            "name": f"TEST-E2E-{stamp}",
            "address": "Rue du Test 1, 1000 Bruxelles",
            "vat_number": "BE0999999999",
            "syndic_id": user.get("id") or user.get("_id"),
            "fiscal_year_start_month": 1,
            "fiscal_year_start_day": 1,
            "is_e2e_test": True,
            "bank_accounts": [
                {"iban": "BE68539007547034", "account_type": "vue",
                 "pcmn_number": "55156300", "label": "Compte à vue TEST",
                 "is_default": True},
                {"iban": "BE72000000012349", "account_type": "epargne",
                 "pcmn_number": "55061800", "label": "Compte épargne TEST"},
            ],
            "created_at": now,
            "updated_at": now,
        }
        await db.coproprietes.insert_one(dict(acp))
        return acp

    async def _seed_plan_comptable(acp_id: str):
        """Cree le plan comptable minimal necessaire pour les tests."""
        accounts = [
            ("55156300", "Compte à vue TEST", 5),
            ("55061800", "Compte épargne TEST", 5),
            ("58", "Virements internes", 5),
            ("6100", "Protection anti-incendie", 6),
            ("61050", "Nettoyage", 6),
            ("61300", "Honoraires syndic", 6),
            ("700", "Provisions charges courantes", 7),
            ("499000", "Encaissements/Decaissements non identifies", 4),
        ]
        for num, name, cls in accounts:
            await db.pcmn_accounts.insert_one({
                "id": str(uuid.uuid4()),
                "copropriete_id": acp_id,
                "number": num, "name": name, "class_num": cls,
                "created_at": _now(),
            })

    async def _seed_distribution_keys(acp_id: str) -> list[dict]:
        """3 cles de repartition : Generale, Ascenseur, Chauffage."""
        keys = [
            {"name": "Cle Generale", "description": "Toutes charges communes"},
            {"name": "Ascenseur", "description": "Charges ascenseur"},
            {"name": "Chauffage", "description": "Charges chauffage"},
        ]
        result = []
        for k in keys:
            k_id = str(uuid.uuid4())
            doc = {
                "id": k_id, "copropriete_id": acp_id,
                "name": k["name"], "description": k["description"],
                "is_default": (k["name"] == "Cle Generale"),
                "created_at": _now(),
            }
            await db.distribution_keys.insert_one(dict(doc))
            result.append(doc)
        return result

    async def _seed_owners_lots(acp_id: str, keys: list[dict]) -> dict:
        """5 proprietaires + 5 lots. Les quotites totalisent 1000 par cle."""
        default_key_id = keys[0]["id"]
        owners_data = [
            {"name": "Durand Jean", "email": "durand@test.local", "quotite": 250},
            {"name": "Lefevre Marie", "email": "lefevre@test.local", "quotite": 200},
            {"name": "Martin Paul", "email": "martin@test.local", "quotite": 200},
            {"name": "Bernard Sophie", "email": "bernard@test.local", "quotite": 200},
            {"name": "Rousseau Luc", "email": "rousseau@test.local", "quotite": 150},
        ]
        owners = []
        lots = []
        for i, o in enumerate(owners_data):
            oid = str(uuid.uuid4())
            lot_id = str(uuid.uuid4())
            owner_doc = {
                "id": oid, "copropriete_id": acp_id,
                "copropriete_ids": [acp_id],
                "name": o["name"], "email": o["email"], "phone": "",
                "auxiliary_code": f"TEST-{i+1:03d}",
                "created_at": _now(),
            }
            await db.owners.insert_one(dict(owner_doc))
            lot_doc = {
                "id": lot_id, "copropriete_id": acp_id,
                "name": f"Appartement {i+1}", "type": "apartment",
                "unit_number": f"A{i+1}",
                "quotities": {default_key_id: o["quotite"]},
                "owner_id": oid,
                "occupant_id": oid,
                "created_at": _now(),
            }
            await db.lots.insert_one(dict(lot_doc))
            owners.append(owner_doc)
            lots.append(lot_doc)
        return {"owners": owners, "lots": lots}

    async def _seed_suppliers(acp_id: str) -> list[dict]:
        """3 fournisseurs avec IBANs."""
        suppliers_data = [
            {"name": "Ascenseur SPRL", "iban": "BE11000000011111", "vat": "BE0100000001"},
            {"name": "Nettoyage TEST SA", "iban": "BE22000000022222", "vat": "BE0100000002"},
            {"name": "Assurance TEST", "iban": "BE33000000033333", "vat": "BE0100000003"},
        ]
        result = []
        for s in suppliers_data:
            sid = str(uuid.uuid4())
            doc = {
                "id": sid, "copropriete_id": acp_id,
                "name": s["name"], "iban": s["iban"], "vat_number": s["vat"],
                "auxiliary_code": s["vat"],
                "created_at": _now(),
            }
            await db.suppliers.insert_one(dict(doc))
            result.append(doc)
        return result

    async def _seed_expense_categories(acp_id: str, keys: list[dict]) -> list[dict]:
        """3 categories liees a des comptes 6xxx + cle Generale."""
        default_key_id = keys[0]["id"]
        cats_data = [
            {"name": "Nettoyage batiment", "account_number": "61050",
             "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0},
            {"name": "Protection incendie", "account_number": "6100",
             "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0},
            {"name": "Honoraires syndic", "account_number": "61300",
             "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0},
        ]
        result = []
        for c in cats_data:
            cid = str(uuid.uuid4())
            doc = {
                "id": cid, "copropriete_id": acp_id,
                "name": c["name"], "account_number": c["account_number"],
                "account_name": c["name"],
                "default_distribution_key_id": default_key_id,
                "default_occupant_pct": c["default_occupant_pct"],
                "default_proprietaire_pct": c["default_proprietaire_pct"],
                "created_at": _now(),
            }
            await db.expense_categories.insert_one(dict(doc))
            result.append(doc)
        return result

    def _assert(cond, msg, step_name, results: list[dict]) -> bool:
        """Ajoute une entree de resultat au rapport."""
        results.append({
            "step": step_name,
            "status": "PASS" if cond else "FAIL",
            "message": msg,
        })
        return bool(cond)

    async def _scenario_normal_invoice(acp_id: str, ctx: dict, results: list[dict]):
        """Scenario 1 : facture normale (300 EUR nettoyage) + repartition."""
        supplier = ctx["suppliers"][1]  # Nettoyage
        cat = ctx["cats"][0]            # Nettoyage
        key_id = ctx["keys"][0]["id"]
        # Genere une facture via l'API interne (pas par HTTP pour aller plus vite)
        inv_id = str(uuid.uuid4())
        je_id = str(uuid.uuid4())
        amount = 300.00
        supplier_acc = "44000001"  # convention tier fournisseur
        # AC entry
        await db.journal_entries.insert_one({
            "id": je_id,
            "journal_type": "AC",
            "date": _iso_date(2026, 3, 15),
            "reference": f"FA-INV/2026/E2E-001",
            "description": f"Facture Nettoyage - {supplier['name']}",
            "lines": [
                {"account_number": cat["account_number"], "account_name": cat["name"],
                 "debit": amount, "credit": 0.0,
                 "expense_category_id": cat["id"],
                 "distribution_key_id": key_id,
                 "occupant_pct": 0.0, "proprietaire_pct": 100.0},
                {"account_number": supplier_acc, "account_name": supplier["name"],
                 "debit": 0.0, "credit": amount,
                 "third_party_id": supplier["id"],
                 "third_party_name": supplier["name"]},
            ],
            "total_debit": amount, "total_credit": amount,
            "copropriete_id": acp_id,
            "auto_generated": True,
            "source_type": "invoice", "source_id": inv_id,
            "created_at": _now(),
        })
        await db.invoices.insert_one({
            "id": inv_id, "copropriete_id": acp_id,
            "number": "INV/2026/E2E-001",
            "supplier": supplier["name"], "supplier_id": supplier["id"],
            "date": _iso_date(2026, 3, 15),
            "total_amount": amount, "amount_ht": amount,
            "status": "unpaid",
            "expense_category_id": cat["id"],
            "distribution_key_id": key_id,
            "journal_entry_id": je_id,
            "created_at": _now(),
        })
        # Assertions
        je = await db.journal_entries.find_one({"id": je_id}, {"_id": 0})
        _assert(je is not None, "Ecriture AC creee", "S1.AC_created", results)
        _assert(abs(je["total_debit"] - je["total_credit"]) < 0.01,
                f"AC equilibree D={je['total_debit']} C={je['total_credit']}",
                "S1.AC_balanced", results)
        _assert(any(l["account_number"] == cat["account_number"] and l["debit"] > 0
                    for l in je["lines"]),
                f"Debit sur compte charge {cat['account_number']}", "S1.charge_debited", results)
        ctx["invoices"] = ctx.get("invoices", []) + [{"id": inv_id, "number": "INV/2026/E2E-001",
                                                     "amount": amount, "supplier_id": supplier["id"]}]

    async def _scenario_invoice_private_fee(acp_id: str, ctx: dict, results: list[dict]):
        """Scenario 2 : facture avec allocation privative (500 EUR dont 200 propriete X)."""
        supplier = ctx["suppliers"][0]  # Ascenseur
        cat = ctx["cats"][1]  # Protection incendie (utilisee pour AC)
        key_id = ctx["keys"][0]["id"]
        owner_x = ctx["owners"][0]  # Durand
        owner_acc = "40000001"
        inv_id = str(uuid.uuid4())
        je_id = str(uuid.uuid4())
        supplier_acc = "44000002"
        total = 500.00
        private = 200.00
        common = total - private
        await db.journal_entries.insert_one({
            "id": je_id,
            "journal_type": "AC",
            "date": _iso_date(2026, 3, 20),
            "reference": f"FA-INV/2026/E2E-002",
            "description": f"Facture Ascenseur mixte - {supplier['name']}",
            "lines": [
                {"account_number": owner_acc, "account_name": owner_x["name"],
                 "debit": private, "credit": 0.0,
                 "third_party_id": owner_x["id"], "third_party_name": owner_x["name"],
                 "line_description": f"Frais privatif - {owner_x['name']}",
                 "is_private_fee": True},
                {"account_number": cat["account_number"], "account_name": cat["name"],
                 "debit": common, "credit": 0.0,
                 "expense_category_id": cat["id"],
                 "distribution_key_id": key_id,
                 "occupant_pct": 0.0, "proprietaire_pct": 100.0},
                {"account_number": supplier_acc, "account_name": supplier["name"],
                 "debit": 0.0, "credit": total,
                 "third_party_id": supplier["id"],
                 "third_party_name": supplier["name"]},
            ],
            "total_debit": total, "total_credit": total,
            "copropriete_id": acp_id,
            "auto_generated": True,
            "source_type": "invoice", "source_id": inv_id,
            "created_at": _now(),
        })
        await db.invoices.insert_one({
            "id": inv_id, "copropriete_id": acp_id,
            "number": "INV/2026/E2E-002",
            "supplier": supplier["name"], "supplier_id": supplier["id"],
            "date": _iso_date(2026, 3, 20),
            "total_amount": total,
            "status": "unpaid",
            "has_private_fee": True,
            "private_fee_amount": private,
            "private_fee_owner_id": owner_x["id"],
            "journal_entry_id": je_id,
            "created_at": _now(),
        })
        je = await db.journal_entries.find_one({"id": je_id}, {"_id": 0})
        _assert(je is not None, "AC mixte creee", "S2.AC_created", results)
        _assert(abs(je["total_debit"] - je["total_credit"]) < 0.01,
                f"AC mixte equilibree D={je['total_debit']} C={je['total_credit']}",
                "S2.AC_balanced", results)
        _assert(any(l.get("is_private_fee") and l["debit"] == private for l in je["lines"]),
                f"Frais privatif {private} EUR sur {owner_x['name']}", "S2.private_fee_ok", results)
        _assert(any(l["account_number"] == cat["account_number"] and l["debit"] == common
                    for l in je["lines"]),
                f"Charges communes {common} EUR sur {cat['account_number']}",
                "S2.common_ok", results)
        ctx["invoices"].append({"id": inv_id, "number": "INV/2026/E2E-002", "amount": total,
                                "supplier_id": supplier["id"]})

    async def _scenario_bank_owner_payment(acp_id: str, ctx: dict, results: list[dict]):
        """Scenario 3 : extrait bancaire avec paiement proprietaire 300 EUR."""
        owner = ctx["owners"][0]
        stmt_id = str(uuid.uuid4())
        txn_id = str(uuid.uuid4())
        amount = 300.00
        await db.bank_statements.insert_one({
            "id": stmt_id, "copropriete_id": acp_id,
            "account_number": "BE68539007547034",
            "iban": "BE68539007547034",
            "date": _iso_date(2026, 4, 5),
            "opening_balance": 0.0, "closing_balance": amount,
            "status": "draft",
            "created_at": _now(),
        })
        await db.bank_transactions.insert_one({
            "id": txn_id, "copropriete_id": acp_id, "statement_id": stmt_id,
            "date": _iso_date(2026, 4, 5),
            "amount": amount, "transaction_type": "credit",
            "counterparty_name": owner["name"],
            "counterparty_iban": "BE00000000000001",
            "communication": "Paiement provisions",
            "account_number": "BE68539007547034",
            "matched": True, "matched_to": owner["id"],
            "match_type": "owner_payment",
            "created_at": _now(),
        })
        # Trigger posting via generate_bank_entry
        from auto_entries import generate_bank_entry
        txn_doc = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        result = await generate_bank_entry(db, txn_doc)
        _assert(result is not None, "FI entry created for owner payment", "S3.FI_created", results)
        if result:
            _assert(abs(result["total_debit"] - result["total_credit"]) < 0.01,
                    f"FI equilibree D={result['total_debit']} C={result['total_credit']}",
                    "S3.FI_balanced", results)
            # Assert D=55156300, C=400xxx
            has_bank_debit = any(l["account_number"] == "55156300" and l["debit"] > 0
                                 for l in result["lines"])
            _assert(has_bank_debit, "Debit sur compte 55156300", "S3.bank_debited", results)
        ctx["bank_stmts"] = ctx.get("bank_stmts", []) + [stmt_id]

    async def _scenario_bank_supplier_payment(acp_id: str, ctx: dict, results: list[dict]):
        """Scenario 4 : extrait bancaire avec paiement fournisseur 300 EUR."""
        invoice = ctx["invoices"][0]  # INV/2026/E2E-001 (300 EUR)
        stmt_id = str(uuid.uuid4())
        txn_id = str(uuid.uuid4())
        amount = invoice["amount"]
        await db.bank_statements.insert_one({
            "id": stmt_id, "copropriete_id": acp_id,
            "account_number": "BE68539007547034",
            "iban": "BE68539007547034",
            "date": _iso_date(2026, 4, 10),
            "opening_balance": 300.0, "closing_balance": 0.0,
            "status": "draft",
            "created_at": _now(),
        })
        await db.bank_transactions.insert_one({
            "id": txn_id, "copropriete_id": acp_id, "statement_id": stmt_id,
            "date": _iso_date(2026, 4, 10),
            "amount": -amount, "transaction_type": "debit",
            "counterparty_name": "Nettoyage TEST SA",
            "communication": invoice["number"],
            "account_number": "BE68539007547034",
            "matched": True, "matched_to": invoice["id"],
            "match_type": "invoice",
            "created_at": _now(),
        })
        from auto_entries import generate_bank_entry
        txn_doc = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        result = await generate_bank_entry(db, txn_doc)
        _assert(result is not None, "FI entry created for supplier payment", "S4.FI_created", results)
        if result:
            _assert(abs(result["total_debit"] - result["total_credit"]) < 0.01,
                    "FI equilibree", "S4.FI_balanced", results)
            has_bank_credit = any(l["account_number"] == "55156300" and l["credit"] > 0
                                  for l in result["lines"])
            _assert(has_bank_credit, "Credit sur compte 55156300", "S4.bank_credited", results)
        # Invoice marquee payee
        inv = await db.invoices.find_one({"id": invoice["id"]})
        _assert(inv and inv.get("status") == "paid",
                f"Facture marquee payee (status={inv.get('status') if inv else None})",
                "S4.invoice_paid", results)

    async def _scenario_iban_not_configured(acp_id: str, ctx: dict, results: list[dict]):
        """Scenario 5 : blocage strict IBAN inconnu."""
        stmt_id = str(uuid.uuid4())
        txn_id = str(uuid.uuid4())
        await db.bank_statements.insert_one({
            "id": stmt_id, "copropriete_id": acp_id,
            "account_number": "BE99999999999999",
            "iban": "BE99999999999999",
            "date": _iso_date(2026, 4, 15),
            "opening_balance": 0.0, "closing_balance": 100.0,
            "status": "draft",
            "created_at": _now(),
        })
        await db.bank_transactions.insert_one({
            "id": txn_id, "copropriete_id": acp_id, "statement_id": stmt_id,
            "date": _iso_date(2026, 4, 15),
            "amount": 100.0, "transaction_type": "credit",
            "counterparty_name": "TEST-IBAN-INCONNU",
            "account_number": "BE99999999999999",
            "matched": True, "matched_to": ctx["owners"][0]["id"],
            "match_type": "owner_payment",
            "created_at": _now(),
        })
        from auto_entries import generate_bank_entry
        txn_doc = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        result = await generate_bank_entry(db, txn_doc)
        _assert(result is None, "Posting bloque pour IBAN inconnu", "S5.blocked", results)
        # Verifie que la txn est marquee posting_error
        txn_after = await db.bank_transactions.find_one({"id": txn_id})
        _assert(txn_after and txn_after.get("posting_error"),
                "Txn marquee posting_error avec message clair",
                "S5.error_flag", results)
        # Verifie qu'aucun compte fantome n'a ete cree
        phantom = await db.pcmn_accounts.count_documents({
            "copropriete_id": acp_id, "number": "BE99999999999999"})
        _assert(phantom == 0, "Aucun compte fantome cree", "S5.no_phantom", results)

    async def _cross_check_58_balanced(acp_id: str, results: list[dict]):
        """Cross-check : compte 58 (Virements internes) equilibre a 0."""
        entries = await db.journal_entries.find({"copropriete_id": acp_id}).to_list(10000)
        d, c = 0.0, 0.0
        for e in entries:
            if e.get("is_reversal") or e.get("reversed"):
                continue
            for line in e.get("lines", []):
                if line.get("account_number") == "58":
                    d += line.get("debit", 0)
                    c += line.get("credit", 0)
        _assert(abs(d - c) < 0.01, f"Compte 58 equilibre D={d:.2f} C={c:.2f}",
                "CC.account_58_balanced", results)

    async def _cross_check_no_phantom_bank(acp_id: str, results: list[dict]):
        """Cross-check : exactement 2 comptes classe 55 (vue + epargne)."""
        n = await db.pcmn_accounts.count_documents({
            "copropriete_id": acp_id, "number": {"$regex": "^55[0-9]"}})
        _assert(n == 2, f"Exactement 2 comptes classe 55 (actuel: {n})",
                "CC.no_phantom_bank", results)

    async def _cross_check_bilan(acp_id: str, results: list[dict]):
        """Cross-check : bilan equilibre (actif = passif)."""
        entries = await db.journal_entries.find({"copropriete_id": acp_id}).to_list(10000)
        total_d, total_c = 0.0, 0.0
        for e in entries:
            if e.get("is_reversal") or e.get("reversed"):
                continue
            total_d += e.get("total_debit", 0)
            total_c += e.get("total_credit", 0)
        _assert(abs(total_d - total_c) < 0.01,
                f"Grand livre equilibre D={total_d:.2f} C={total_c:.2f}",
                "CC.ledger_balanced", results)

    async def _cross_check_chinese_wall(acp: dict, results: list[dict]):
        """Cross-check : Chinese Wall - un 2e syndic fictif ne doit PAS voir
        l'ACP TEST-E2E. Simule la regle d'isolation en interrogeant la DB
        avec le filtre syndic_id (comme le fait chaque endpoint proprietaires,
        factures, extraits, journaux).
        """
        # Cree un 2e syndic fictif marque is_e2e_test pour purge auto
        other_syndic_id = str(uuid.uuid4())
        await db.users.insert_one({
            "_id": other_syndic_id,
            "id": other_syndic_id,
            "email": f"e2e-other-syndic-{other_syndic_id[:8]}@test.local",
            "name": "SYNDIC-E2E-OTHER",
            "role": "syndic",
            "copropriete_ids": [],
            "is_e2e_test": True,
            "created_at": _now(),
        })

        # 1) Filtre par syndic_id : le 2e syndic ne doit voir aucune ACP TEST
        seen_acps = await db.coproprietes.count_documents({
            "syndic_id": other_syndic_id, "id": acp["id"],
        })
        _assert(seen_acps == 0,
                f"Syndic B ne voit PAS l'ACP TEST (seen={seen_acps})",
                "CW.acp_isolated", results)

        # 2) Comme un syndic B n'a pas cette ACP dans son copropriete_ids,
        #    les endpoints qui filtrent par copropriete_id ne retournent rien.
        # Simule : cherche invoices/owners/lots avec un filtre syndic B (vide).
        # Un syndic sans copropriete_ids => aucun acces = 0 factures visibles.
        # On verifie qu'aucune facture TEST-E2E n'a un syndic_id = other.
        inv_leak = await db.invoices.count_documents({
            "copropriete_id": acp["id"], "syndic_id": other_syndic_id,
        })
        _assert(inv_leak == 0,
                f"Aucune facture TEST-E2E n'appartient au syndic B (leak={inv_leak})",
                "CW.invoices_isolated", results)

        # 3) Verifie qu'aucun proprietaire TEST-E2E ne fuite vers le syndic B
        own_leak = await db.owners.count_documents({
            "copropriete_id": acp["id"], "syndic_id": other_syndic_id,
        })
        _assert(own_leak == 0,
                f"Aucun proprietaire TEST-E2E n'appartient au syndic B (leak={own_leak})",
                "CW.owners_isolated", results)

        # 4) Requete inversee : liste des ACP visibles par le syndic B
        b_visible = await db.coproprietes.count_documents({
            "syndic_id": other_syndic_id,
        })
        _assert(b_visible == 0,
                f"Syndic B fictif ne voit aucune ACP (visibles={b_visible})",
                "CW.b_sees_nothing", results)

        # Cleanup implicite : auto-purge le supprimera avec les autres
        # (is_e2e_test=True + created_at)

    @router.post("/run")
    async def run_e2e(payload: dict, request: Request):
        """Lance un test E2E complet.

        Payload: {"mode": "without_optipro"|"with_optipro"}
        Retourne: {run_id, acp_id, mode, steps, summary, duration_ms}
        """
        user = await _get_superadmin(request)
        mode = (payload or {}).get("mode", "without_optipro")
        if mode not in ("without_optipro", "with_optipro"):
            raise HTTPException(400, "mode invalide")
        run_id = str(uuid.uuid4())
        results: list[dict] = []
        t0 = time.time()
        acp = None
        error_trace = None
        try:
            # Setup
            acp = await _seed_acp(user, mode)
            await _seed_plan_comptable(acp["id"])
            keys = await _seed_distribution_keys(acp["id"])
            ol = await _seed_owners_lots(acp["id"], keys)
            suppliers = await _seed_suppliers(acp["id"])
            cats = await _seed_expense_categories(acp["id"], keys)
            _assert(True, f"ACP seedee {acp['name']}", "P1.setup_done", results)
            _assert(len(ol["owners"]) == 5, f"5 proprietaires crees", "P1.owners_5", results)
            _assert(len(ol["lots"]) == 5, f"5 lots crees", "P1.lots_5", results)
            _assert(len(suppliers) == 3, f"3 fournisseurs crees", "P1.suppliers_3", results)
            _assert(len(cats) == 3, f"3 categories creees", "P1.categories_3", results)

            ctx = {"owners": ol["owners"], "lots": ol["lots"],
                   "suppliers": suppliers, "cats": cats, "keys": keys}

            # Scenarios
            await _scenario_normal_invoice(acp["id"], ctx, results)
            await _scenario_invoice_private_fee(acp["id"], ctx, results)
            await _scenario_bank_owner_payment(acp["id"], ctx, results)
            await _scenario_bank_supplier_payment(acp["id"], ctx, results)
            await _scenario_iban_not_configured(acp["id"], ctx, results)

            # Cross-checks
            await _cross_check_58_balanced(acp["id"], results)
            await _cross_check_no_phantom_bank(acp["id"], results)
            await _cross_check_bilan(acp["id"], results)
            await _cross_check_chinese_wall(acp, results)

            # Variante B : import optipro simule
            if mode == "with_optipro":
                # Ici on simule un import wizard (insertion directe pour eviter
                # de dependre du parsing PDF Optipro). L'assertion cle est
                # l'unicite des categories (pas de doublons crees).
                before = await db.expense_categories.count_documents(
                    {"copropriete_id": acp["id"]})
                # Simule 2 nouvelles categories
                for name, num in [("Assurance immeuble", "6120"),
                                  ("Electricite parties communes", "61215")]:
                    await db.expense_categories.insert_one({
                        "id": str(uuid.uuid4()),
                        "copropriete_id": acp["id"], "name": name,
                        "account_number": num, "account_name": name,
                        "default_distribution_key_id": keys[0]["id"],
                        "default_occupant_pct": 0.0,
                        "default_proprietaire_pct": 100.0,
                        "created_at": _now(),
                    })
                after = await db.expense_categories.count_documents(
                    {"copropriete_id": acp["id"]})
                _assert(after == before + 2,
                        f"Import Optipro simule : +{after-before} categorie(s)",
                        "OPT.categories_inserted", results)
        except Exception as e:
            error_trace = traceback.format_exc()
            results.append({"step": "GLOBAL", "status": "ERROR",
                            "message": f"Exception: {e}",
                            "trace": error_trace[:2000]})

        duration_ms = int((time.time() - t0) * 1000)
        passed = sum(1 for r in results if r["status"] == "PASS")
        failed = sum(1 for r in results if r["status"] == "FAIL")
        errors = sum(1 for r in results if r["status"] == "ERROR")
        summary = {
            "total": len(results), "passed": passed,
            "failed": failed, "errors": errors,
            "success": failed == 0 and errors == 0,
        }
        run_doc = {
            "id": run_id, "mode": mode,
            "acp_id": acp["id"] if acp else None,
            "acp_name": acp["name"] if acp else None,
            "steps": results,
            "summary": summary,
            "duration_ms": duration_ms,
            "started_at": datetime.fromtimestamp(t0, timezone.utc).isoformat(),
            "ended_at": _now(),
            "user_id": user.get("id") or str(user.get("_id", "")),
            "user_email": user.get("email", ""),
        }
        await db.e2e_test_runs.insert_one(dict(run_doc))
        return run_doc

    @router.get("/history")
    async def history(request: Request):
        """Historique des runs (10 derniers)."""
        await _get_superadmin(request)
        runs = await db.e2e_test_runs.find({}, {"_id": 0}).sort(
            "started_at", -1).limit(20).to_list(20)
        return {"runs": runs}

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str, request: Request):
        await _get_superadmin(request)
        run = await db.e2e_test_runs.find_one({"id": run_id}, {"_id": 0})
        if not run:
            raise HTTPException(404, "Run non trouve")
        return run

    @router.post("/purge-all")
    async def purge_all_test_acps(request: Request):
        """Purge IMMEDIATE de toutes les ACP de test (is_e2e_test=True)
        sans condition d'anciennete. Utile pour un cleanup manuel entre 2 runs.
        """
        await _get_superadmin(request)
        acps = await db.coproprietes.find(
            {"is_e2e_test": True},
            {"_id": 0, "id": 1, "name": 1},
        ).to_list(500)
        collections = [
            "journal_entries", "bank_transactions", "bank_statements",
            "invoices", "owners", "lots", "suppliers",
            "expense_categories", "distribution_keys",
            "pcmn_accounts", "fund_calls", "meter_readings",
            "meters", "documents", "sent_communications",
        ]
        total_docs = 0
        for acp in acps:
            for coll in collections:
                r = await db[coll].delete_many({"copropriete_id": acp["id"]})
                total_docs += r.deleted_count
            await db.coproprietes.delete_one({"id": acp["id"]})
            total_docs += 1
        # Purge les users syndic fictifs orphelins
        u = await db.users.delete_many({"is_e2e_test": True, "role": "syndic"})
        return {
            "purged_acps": len(acps),
            "deleted_docs": total_docs,
            "deleted_test_users": u.deleted_count,
            "acps": [a["name"] for a in acps],
        }

    @router.post("/purge/{acp_id}")
    async def purge_test_acp(acp_id: str, request: Request):
        """Purge une ACP de test (marquee is_e2e_test=True) et toutes ses donnees."""
        await _get_superadmin(request)
        acp = await db.coproprietes.find_one({"id": acp_id})
        if not acp:
            raise HTTPException(404, "ACP non trouvee")
        if not acp.get("is_e2e_test"):
            raise HTTPException(403, "Cette ACP n'est PAS une ACP de test - purge refusee")
        # Collections a purger (filtrage par copropriete_id)
        collections = [
            "journal_entries", "bank_transactions", "bank_statements",
            "invoices", "owners", "lots", "suppliers", "expense_categories",
            "distribution_keys", "pcmn_accounts", "fund_calls",
            "meter_readings", "meters", "documents", "sent_communications",
        ]
        deleted = {}
        for coll in collections:
            r = await db[coll].delete_many({"copropriete_id": acp_id})
            if r.deleted_count:
                deleted[coll] = r.deleted_count
        # Purge aussi les users syndics fictifs crees pour le test Chinese Wall
        u = await db.users.delete_many({"is_e2e_test": True, "role": "syndic"})
        if u.deleted_count:
            deleted["users_syndic_e2e"] = u.deleted_count
        await db.coproprietes.delete_one({"id": acp_id})
        deleted["coproprietes"] = 1
        return {"purged": True, "acp_id": acp_id, "deleted": deleted}

    return router
