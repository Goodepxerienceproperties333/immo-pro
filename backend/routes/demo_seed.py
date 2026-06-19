"""Demo seed: generates a fully-populated ACP with realistic data for demo/training."""
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone, timedelta
import uuid
import random


def _vcs_mod97(digits: str) -> str:
    """Compute Belgian VCS structured communication (mod 97)."""
    d = int(digits.zfill(10))
    check = d % 97 or 97
    s = f"{d:010d}{check:02d}"
    return f"+++{s[:3]}/{s[3:7]}/{s[7:]}+++"


def create_demo_router(db):
    router = APIRouter(prefix="/api/admin/demo")

    async def _admin(request):
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        if not is_superadmin_only(user.get("role", "")):
            raise HTTPException(403, "Generation de demo reservee au super administrateur")
        return user

    @router.post("/seed")
    async def seed_demo(request: Request):
        """Create a complete demo ACP. Idempotent: if 'Demo - Residence Les Tilleuls' exists, return it."""
        await _admin(request)
        existing = await db.coproprietes.find_one({"name": "Demo - Residence Les Tilleuls"}, {"_id": 0})
        if existing:
            copro_id = existing["id"]
            counts = {
                "owners": await db.owners.count_documents({}),  # owners are global
                "lots": await db.lots.count_documents({"copropriete_id": copro_id}),
                "suppliers": await db.suppliers.count_documents({}),  # suppliers are global
                "invoices": await db.invoices.count_documents({"copropriete_id": copro_id}),
                "fund_calls": await db.fund_calls.count_documents({"copropriete_id": copro_id}),
                "transactions": await db.bank_transactions.count_documents({"copropriete_id": copro_id}),
                "journal_entries": await db.journal_entries.count_documents({"copropriete_id": copro_id}),
                "categories": await db.document_categories.count_documents({"copropriete_id": copro_id}),
                "distribution_keys": await db.distribution_keys.count_documents({"copropriete_id": copro_id}),
                "pcmn_accounts": await db.pcmn_accounts.count_documents({"copropriete_id": copro_id}),
            }
            return {
                "message": "Donnees de demo deja presentes",
                "copropriete_id": copro_id,
                "reference": existing.get("reference"),
                "name": existing["name"],
                "already_exists": True,
                "counts": counts,
            }
        now = datetime.now(timezone.utc)
        iso = now.isoformat()
        today = now.strftime("%Y-%m-%d")  # noqa: F841

        # 1. Create ACP
        from routes.coproprietes import create_coproprietes_router  # noqa
        # Manual seeding to avoid HTTP layer
        copro_id = str(uuid.uuid4())
        count = await db.coproprietes.count_documents({"reference": {"$regex": f"^ACP-{now.strftime('%Y%m')}"}})
        reference = f"ACP-{now.strftime('%Y%m')}-{str(count + 1).zfill(3)}"
        copro = {
            "id": copro_id,
            "reference": reference,
            "name": "Demo - Residence Les Tilleuls",
            "bce": "0876.543.210",
            "address": "Avenue des Tilleuls 25",
            "postal_code": "1180",
            "city": "Uccle",
            "country": "Belgique",
            "description": "ACP de demonstration generee automatiquement",
            "bank_accounts": [
                {"iban": "BE68539007547034", "bic": "GKCCBEBB", "account_type": "vue", "is_default": True,
                 "label": "Compte courant", "pcmn_number": "55103400"},
                {"iban": "BE71096123456769", "bic": "GKCCBEBB", "account_type": "epargne", "is_default": False,
                 "label": "Reserve grosses reparations", "pcmn_number": "55076900"},
            ],
            "quarterly_closing": True,
            "default_provisions": True,
            "status": "active",
            "created_by": "demo",
            "created_at": iso,
        }
        await db.coproprietes.insert_one(copro)

        # 2. Seed PCMN for this ACP
        from pcmn_data import PCMN_ALL_ACCOUNTS
        active_set = {"614000", "615000", "61300", "61050", "6120", "6121", "6140"}
        pcmn_docs = []
        for acc in PCMN_ALL_ACCOUNTS:
            pcmn_docs.append({**acc, "copropriete_id": copro_id,
                              "active": acc["number"] in active_set, "is_custom": False})
        # Add the 2 bank PCMN
        pcmn_docs.append({"number": "55103400", "name": "Banque compte courant 7034", "class_num": 5,
                          "parent": "550000", "type": "balance", "copropriete_id": copro_id, "active": True})
        pcmn_docs.append({"number": "55076900", "name": "Banque epargne 6769", "class_num": 5,
                          "parent": "550000", "type": "balance", "copropriete_id": copro_id, "active": True})
        await db.pcmn_accounts.insert_many(pcmn_docs)

        # 3. Create owners (8)
        owners_data = [
            ("Martin", "Sophie", "sophie.martin@example.be", "+32 475 12 34 56"),
            ("Dubois", "Jean", "jean.dubois@example.be", "+32 478 23 45 67"),
            ("Lefevre", "Marie", "marie.lefevre@example.be", "+32 471 34 56 78"),
            ("Vandenberghe", "Pierre", "p.vandenberghe@example.be", "+32 472 45 67 89"),
            ("Janssens", "Anne", "anne.janssens@example.be", "+32 473 56 78 90"),
            ("Peeters", "Luc", "luc.peeters@example.be", "+32 474 67 89 01"),
            ("De Smet", "Catherine", "c.desmet@example.be", "+32 476 78 90 12"),
            ("Wouters", "Thomas", "thomas.wouters@example.be", "+32 477 89 01 23"),
        ]
        owners = []
        for i, (ln, fn, em, ph) in enumerate(owners_data):
            base = 1000000000 + i * 1234567
            vcs = _vcs_mod97(str(base)[:10])
            vcs_digits = vcs.replace("+", "").replace("/", "")
            o = {
                "id": str(uuid.uuid4()),
                "first_name": fn, "last_name": ln, "name": f"{ln} {fn}",
                "address": f"Avenue des Tilleuls 25, bte {i+1}",
                "postal_code": "1180", "city": "Uccle", "country": "Belgique",
                "email": em, "email2": "", "phone": ph, "phone2": "",
                "vcs_code": vcs, "vcs_digits": vcs_digits,
                "copropriete_id": "",  # global
                "created_at": iso,
            }
            owners.append(o)
        await db.owners.insert_many(owners)

        # 4. Create lots (8) with owners
        lots = []
        lot_specs = [
            ("A001", "Appartement 2 chambres - Rez", "apartment", 0, 75.0, 850),
            ("A002", "Appartement 3 chambres - 1er etage", "apartment", 1, 110.0, 1200),
            ("A003", "Appartement 2 chambres - 1er etage", "apartment", 1, 80.0, 900),
            ("A004", "Appartement 3 chambres - 2e etage", "apartment", 2, 110.0, 1200),
            ("A005", "Appartement penthouse - 3e etage", "apartment", 3, 150.0, 1700),
            ("P001", "Parking 1", "parking", -1, 12.0, 200),
            ("P002", "Parking 2", "parking", -1, 12.0, 200),
            ("C001", "Cave de stockage", "cave", -1, 8.0, 100),
        ]
        for (num, desc, ltype, floor, area, quotity), owner in zip(lot_specs, owners):
            lots.append({
                "id": str(uuid.uuid4()), "number": num, "description": desc, "lot_type": ltype,
                "floor": floor, "area": area, "quotity": quotity,
                "owner_id": owner["id"], "owner_ids": [owner["id"]],
                "copropriete_id": copro_id, "created_at": iso,
            })
        await db.lots.insert_many(lots)

        # 5. Distribution keys
        total_quotity = sum(l["quotity"] for l in lots)
        dk_general = {
            "id": str(uuid.uuid4()),
            "name": "Charges communes generales",
            "description": "Repartition par quotite",
            "key_type": "quotity",
            "lots": [{"lot_id": l["id"], "lot_number": l["number"], "share": l["quotity"]} for l in lots],
            "copropriete_id": copro_id,
            "created_at": iso,
        }
        # Heating: only apartments
        apt_lots = [l for l in lots if l["lot_type"] == "apartment"]
        dk_heating = {
            "id": str(uuid.uuid4()),
            "name": "Chauffage central",
            "description": "Repartition par quotite, appartements uniquement",
            "key_type": "quotity",
            "lots": [{"lot_id": l["id"], "lot_number": l["number"], "share": l["quotity"]} for l in apt_lots],
            "copropriete_id": copro_id,
            "created_at": iso,
        }
        await db.distribution_keys.insert_many([dk_general, dk_heating])

        # 6. Fiscal year
        fy = {
            "id": str(uuid.uuid4()),
            "name": f"Exercice {now.year}",
            "start_date": f"{now.year}-01-01",
            "end_date": f"{now.year}-12-31",
            "status": "open",
            "copropriete_id": copro_id,
            "created_at": iso,
        }
        await db.fiscal_years.insert_one(fy)

        # 7. Suppliers
        suppliers = [
            {"id": str(uuid.uuid4()), "name": "Otis Belgium SA", "vat_number": "BE0403.158.142",
             "iban": "BE12345678901234", "bic": "GEBABEBB", "address": "Bd de l'Empereur 1, 1000 Bruxelles",
             "phone": "+32 2 555 12 34", "email": "facturation@otis.be", "created_at": iso},
            {"id": str(uuid.uuid4()), "name": "AXA Belgium", "vat_number": "BE0404.483.367",
             "iban": "BE98765432101234", "bic": "BBRUBEBB", "address": "Bd du Souverain 25, 1170 Bruxelles",
             "phone": "+32 2 678 90 12", "email": "syndic@axa.be", "created_at": iso},
            {"id": str(uuid.uuid4()), "name": "Sibelga", "vat_number": "BE0222.869.673",
             "iban": "BE45123456789012", "bic": "GEBABEBB", "address": "Quai des Usines 16, 1000 Bruxelles",
             "phone": "+32 2 549 41 00", "email": "facturation@sibelga.be", "created_at": iso},
            {"id": str(uuid.uuid4()), "name": "Vivaqua", "vat_number": "BE0202.962.701",
             "iban": "BE77987654321098", "bic": "GKCCBEBB", "address": "Rue aux Laines 70, 1000 Bruxelles",
             "phone": "+32 2 518 81 11", "email": "syndic@vivaqua.be", "created_at": iso},
            {"id": str(uuid.uuid4()), "name": "Net&Clean Cleaning Services", "vat_number": "BE0500.123.456",
             "iban": "BE11223344556677", "bic": "BBRUBEBB", "address": "Rue Defacqz 12, 1050 Bruxelles",
             "phone": "+32 2 333 44 55", "email": "info@netclean.be", "created_at": iso},
        ]
        await db.suppliers.insert_many(suppliers)

        # 8. Invoices with distribution
        invoices = []
        invoice_specs = [
            ("INV-2026-001", "Otis Belgium SA", "Entretien ascenseur Q1", 450.00, "611200", dk_general["id"], (now - timedelta(days=120)).strftime("%Y-%m-%d")),
            ("INV-2026-002", "AXA Belgium", "Prime assurance incendie annuelle", 1850.00, "613100", dk_general["id"], (now - timedelta(days=90)).strftime("%Y-%m-%d")),
            ("INV-2026-003", "Sibelga", "Electricite parties communes Janv-Fev", 285.50, "612100", dk_general["id"], (now - timedelta(days=80)).strftime("%Y-%m-%d")),
            ("INV-2026-004", "Vivaqua", "Eau parties communes Q1", 178.30, "612300", dk_general["id"], (now - timedelta(days=70)).strftime("%Y-%m-%d")),
            ("INV-2026-005", "Net&Clean Cleaning Services", "Nettoyage parties communes Mars", 520.00, "617000", dk_general["id"], (now - timedelta(days=60)).strftime("%Y-%m-%d")),
            ("INV-2026-006", "Otis Belgium SA", "Reparation ascenseur - changement cable", 1250.00, "611200", dk_general["id"], (now - timedelta(days=45)).strftime("%Y-%m-%d")),
            ("INV-2026-007", "Sibelga", "Electricite parties communes Mars-Avril", 312.80, "612100", dk_general["id"], (now - timedelta(days=30)).strftime("%Y-%m-%d")),
            ("INV-2026-008", "Net&Clean Cleaning Services", "Nettoyage parties communes Avril", 520.00, "617000", dk_general["id"], (now - timedelta(days=20)).strftime("%Y-%m-%d")),
            ("INV-2026-009", "Sibelga", "Chauffage commun gaz Mars", 875.40, "612200", dk_heating["id"], (now - timedelta(days=15)).strftime("%Y-%m-%d")),
            ("INV-2026-010", "Net&Clean Cleaning Services", "Nettoyage parties communes Mai", 520.00, "617000", dk_general["id"], (now - timedelta(days=5)).strftime("%Y-%m-%d")),
        ]
        for num, supp, desc, amount, acc_num, dk_id, inv_date in invoice_specs:
            dk = dk_general if dk_id == dk_general["id"] else dk_heating
            dist_lines = []
            total_shares = sum(l["share"] for l in dk["lots"])
            for kl in dk["lots"]:
                lot = next((l for l in lots if l["id"] == kl["lot_id"]), None)
                owner_name = ""
                if lot and lot.get("owner_id"):
                    o = next((o for o in owners if o["id"] == lot["owner_id"]), None)
                    owner_name = o["name"] if o else ""
                share_ratio = kl["share"] / total_shares if total_shares else 0
                dist_lines.append({
                    "lot_id": kl["lot_id"], "lot_number": kl["lot_number"],
                    "owner_name": owner_name, "share": kl["share"],
                    "amount": round(amount * share_ratio, 2),
                })
            inv_doc = {
                "id": str(uuid.uuid4()), "number": num, "date": inv_date,
                "due_date": (datetime.strptime(inv_date, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d"),
                "supplier": supp, "description": desc,
                "total_amount": amount, "vat_amount": round(amount * 0.21 / 1.21, 2),
                "account_number": acc_num, "distribution_key_id": dk_id,
                "distribution_lines": dist_lines, "status": "unpaid",
                "copropriete_id": copro_id, "created_at": iso,
            }
            invoices.append(inv_doc)
        await db.invoices.insert_many(invoices)

        # 9. Fund calls (quarterly provisions)
        fund_calls = []
        for q_idx, (q_label, q_date) in enumerate([
            ("Q1", f"{now.year}-01-15"),
            ("Q2", f"{now.year}-04-15"),
        ]):
            distribution = []
            amount_per_quotity = 5.00  # 5 EUR per quotity point
            total = round(total_quotity * amount_per_quotity, 2)
            for l in lots:
                owner = next((o for o in owners if o["id"] == l["owner_id"]), None)
                if not owner:
                    continue
                distribution.append({
                    "lot_id": l["id"], "lot_number": l["number"],
                    "owner_id": owner["id"], "owner_name": owner["name"],
                    "vcs_code": owner["vcs_code"], "share": l["quotity"],
                    "amount": round(l["quotity"] * amount_per_quotity, 2),
                    "paid": q_idx == 0,  # Q1 marked paid
                    "paid_date": q_date if q_idx == 0 else "",
                })
            fc = {
                "id": str(uuid.uuid4()),
                "name": f"Provisions {q_label} {now.year}",
                "date": q_date,
                "due_date": (datetime.strptime(q_date, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d"),
                "fiscal_year_id": fy["id"],
                "description": f"Appel de provisions trimestriel {q_label}",
                "total_amount": total, "call_type": "provisions",
                "distribution_key_id": "",
                "distribution": distribution,
                "status": "completed" if q_idx == 0 else "partial",
                "copropriete_id": copro_id,
                "created_at": iso,
            }
            fund_calls.append(fc)
        await db.fund_calls.insert_many(fund_calls)

        # 10. Bank statement + transactions (Q1 payments + some supplier payments)
        stmt = {
            "id": str(uuid.uuid4()),
            "number": "EXTRAIT-001",
            "date": f"{now.year}-02-01",
            "account_number": "BE68 5390 0754 7034",
            "opening_balance": 5000.00,
            "closing_balance": 12450.00,
            "copropriete_id": copro_id,
            "created_at": iso,
        }
        await db.bank_statements.insert_one(stmt)

        # Owner payments (Q1 owners paid)
        txns = []
        for d in fund_calls[0]["distribution"]:
            owner = next((o for o in owners if o["id"] == d["owner_id"]), None)
            if not owner:
                continue
            txns.append({
                "id": str(uuid.uuid4()), "statement_id": stmt["id"],
                "date": f"{now.year}-01-20", "amount": d["amount"],
                "counterparty_name": owner["name"], "counterparty_account": "",
                "communication": owner["vcs_code"], "transaction_type": "credit",
                "account_number": "55103400",
                "matched": True, "matched_to": owner["id"], "match_type": "owner_payment",
                "copropriete_id": copro_id, "created_at": iso,
            })
        # Supplier payments (first 3 invoices marked paid)
        for inv in invoices[:3]:
            txns.append({
                "id": str(uuid.uuid4()), "statement_id": stmt["id"],
                "date": (datetime.strptime(inv["date"], "%Y-%m-%d") + timedelta(days=20)).strftime("%Y-%m-%d"),
                "amount": -inv["total_amount"],
                "counterparty_name": inv["supplier"], "counterparty_account": "",
                "communication": f"Facture {inv['number']}", "transaction_type": "debit",
                "account_number": "55103400",
                "matched": True, "matched_to": inv["id"], "match_type": "invoice",
                "copropriete_id": copro_id, "created_at": iso,
            })
        await db.bank_transactions.insert_many(txns)

        # Mark invoices as paid
        await db.invoices.update_many(
            {"id": {"$in": [inv["id"] for inv in invoices[:3]]}},
            {"$set": {"status": "paid"}}
        )

        # 11. Journal entries (OD, AC, AP)
        journal_entries = []
        # OD: opening balance
        journal_entries.append({
            "id": str(uuid.uuid4()), "journal_type": "OD",
            "date": f"{now.year}-01-01", "reference": "OD-OUVERTURE",
            "description": "Ouverture exercice - solde initial banque",
            "lines": [
                {"account_number": "55103400", "account_name": "Banque compte courant", "debit": 5000.00, "credit": 0},
                {"account_number": "140100", "account_name": "Benefice reporte", "debit": 0, "credit": 5000.00},
            ],
            "total_debit": 5000.00, "total_credit": 5000.00,
            "copropriete_id": copro_id, "created_at": iso,
        })
        # AP: appels de fonds
        for fc in fund_calls:
            journal_entries.append({
                "id": str(uuid.uuid4()), "journal_type": "AP",
                "date": fc["date"], "reference": f"AP-{fc['name']}",
                "description": f"Appel de fonds: {fc['name']}",
                "lines": [
                    {"account_number": "400000", "account_name": "Proprietaires - Appels de fonds", "debit": fc["total_amount"], "credit": 0},
                    {"account_number": "700000", "account_name": "Provisions pour charges communes", "debit": 0, "credit": fc["total_amount"]},
                ],
                "total_debit": fc["total_amount"], "total_credit": fc["total_amount"],
                "fund_call_id": fc["id"], "copropriete_id": copro_id, "created_at": iso,
            })
        # AC: invoices (each becomes a journal entry)
        for inv in invoices:
            journal_entries.append({
                "id": str(uuid.uuid4()), "journal_type": "AC",
                "date": inv["date"], "reference": inv["number"],
                "description": f"Facture {inv['supplier']}: {inv['description']}",
                "lines": [
                    {"account_number": inv["account_number"], "account_name": "Charge", "debit": inv["total_amount"], "credit": 0},
                    {"account_number": "440000", "account_name": "Fournisseurs", "debit": 0, "credit": inv["total_amount"]},
                ],
                "total_debit": inv["total_amount"], "total_credit": inv["total_amount"],
                "copropriete_id": copro_id, "created_at": iso,
            })
        await db.journal_entries.insert_many(journal_entries)

        # 12. Document categories
        cats = []
        for name, desc in [
            ("Reglement d'ordre interieur", "ROI de l'ACP"),
            ("Acte de base", "Acte notarie de constitution"),
            ("Statuts", "Statuts de l'ACP"),
            ("PV d'AG", "Proces-verbaux des assemblees generales"),
            ("Contrats", "Contrats fournisseurs et prestataires"),
            ("Polices d'assurance", "Polices et avenants"),
            ("Factures fournisseurs", "Factures recues"),
            ("Decomptes", "Decomptes annuels par proprietaire"),
            ("Rapports techniques", "Rapports d'expertise, controles, audits"),
            ("Autres", "Documents divers"),
        ]:
            cats.append({
                "id": str(uuid.uuid4()), "name": name, "description": desc,
                "copropriete_id": copro_id, "created_at": iso,
            })
        await db.document_categories.insert_many(cats)

        return {
            "message": "Donnees de demo creees avec succes",
            "copropriete_id": copro_id,
            "reference": reference,
            "name": copro["name"],
            "counts": {
                "owners": len(owners), "lots": len(lots),
                "suppliers": len(suppliers), "invoices": len(invoices),
                "fund_calls": len(fund_calls), "transactions": len(txns),
                "journal_entries": len(journal_entries), "categories": len(cats),
                "distribution_keys": 2, "pcmn_accounts": len(pcmn_docs),
            }
        }

    return router
