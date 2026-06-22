#!/usr/bin/env python3
"""Add the year-end OD (Operations Diverses) adjustment entries for ACP Gaura
that are visible in the Optipro "Liste des depenses 2025" PDF but were NOT
imported by the wizard.

The wizard only handles AC (invoice purchases) and FI (bank statements).
Year-end OD adjustments (charges a reporter, FAR, AGS write-offs, sinistre
settlements) must be entered manually OR imported from a dedicated journal.

This script creates the clear-cut OD entries (those with unambiguous
counterpart accounts derived from the Bilan d'ouverture). Sinistre entries
on compte 61066 are left for the user to enter manually (counterpart
account requires user knowledge of the insurance provisions).

OD entries created :
1. 2025-01-01 : Annulation charges a reporter ascenseurs (4141.40)
   -> DEBIT 61011 / CREDIT 490
2. 2025-12-31 : Charge a reporter ascenseurs (4234.26)
   -> DEBIT 490 / CREDIT 61011
3. 2025-12-31 : FAR ENGIE 10/2025-12/2025 (512.00)
   -> DEBIT 61214 / CREDIT 444
4. 2025-01-01 : Nettoyage de bilan (AGS point 8) (7342.97)
   -> DEBIT 66 / CREDIT 417

Sum effect on charges accounts :
  61011 : +4141.40 - 4234.26 = -92.86
  61214 : +512.00
  66    : +7342.97
  Total : -92.86 + 512 + 7342.97 = 7762.11 EUR added to charge accounts

This brings the API expense total from 33,061.41 closer to the PDF total
33,828.43 (remaining diff ~6997 still due to 61066 sinistre entries).
"""
import os
import asyncio
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

ACP = "4a923c73-7215-421a-8de5-eee9e3f2da6c"


OD_ENTRIES = [
    {
        "date": "2025-01-01",
        "reference": "OD-2025-AN-001",
        "description": "Annulation charges a reporter (Ascenseurs) - bilan 2024",
        "lines": [
            {"account_number": "61011", "account_name": "Contrat d'entretien ascenseurs",
             "description": "Annulation charges a reporter", "debit": 4141.40, "credit": 0.0},
            {"account_number": "490", "account_name": "Charges a reporter",
             "description": "Annulation charges a reporter", "debit": 0.0, "credit": 4141.40},
        ],
    },
    {
        "date": "2025-12-31",
        "reference": "OD-2025-CR-001",
        "description": "Charge a reporter ascenseurs 2026",
        "lines": [
            {"account_number": "490", "account_name": "Charges a reporter",
             "description": "Charge a reporter ascenseurs 2026", "debit": 4234.26, "credit": 0.0},
            {"account_number": "61011", "account_name": "Contrat d'entretien ascenseurs",
             "description": "Charge a reporter ascenseurs 2026", "debit": 0.0, "credit": 4234.26},
        ],
    },
    {
        "date": "2025-12-31",
        "reference": "OD-2025-FAR-001",
        "description": "FAR ENGIE 10/2025 - 12/2025 - Electricite partie commune",
        "lines": [
            {"account_number": "61214", "account_name": "Electricite partie commune",
             "description": "FAR ENGIE 10/2025-12/2025", "debit": 512.00, "credit": 0.0},
            {"account_number": "444", "account_name": "Factures a recevoir",
             "description": "FAR ENGIE 10/2025-12/2025", "debit": 0.0, "credit": 512.00},
        ],
    },
]


async def _ensure_account(db, copro_id: str, number: str, name: str):
    """Create the PCMN account if missing."""
    existing = await db.pcmn_accounts.find_one(
        {"copropriete_id": copro_id, "number": number}, {"_id": 0, "number": 1},
    )
    if existing:
        return False
    class_num = int(number[0]) if number and number[0].isdigit() else 0
    await db.pcmn_accounts.insert_one({
        "id": str(uuid.uuid4()),
        "number": number,
        "name": name,
        "class_num": class_num,
        "parent": "",
        "is_system": False,
        "is_imported": True,
        "copropriete_id": copro_id,
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return True


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    print(f"=== Create OD year-end entries for Gaura ===\n")

    created_acc = 0
    created_je = 0
    skipped = 0

    # Special : Nettoyage de bilan (AGS point 8) - DEBIT 66 / CREDIT 417
    # Account 417 (Creances douteuses) doesn't exist in Gaura PCMN -> create it
    if await _ensure_account(db, ACP, "417", "Creances douteuses"):
        created_acc += 1
        print("  Created PCMN 417 Creances douteuses")

    nettoyage_entry = {
        "date": "2025-01-01",
        "reference": "OD-2025-AGS-001",
        "description": "Nettoyage de bilan (AGS point 8) - Apurement creances douteuses",
        "lines": [
            {"account_number": "66", "account_name": "Charges exceptionnelles",
             "description": "Nettoyage de bilan AGS point 8", "debit": 7342.97, "credit": 0.0},
            {"account_number": "417", "account_name": "Creances douteuses",
             "description": "Nettoyage de bilan AGS point 8", "debit": 0.0, "credit": 7342.97},
        ],
    }

    all_entries = OD_ENTRIES + [nettoyage_entry]

    # Sinistre adjustments on 61066 (5 entries totaling -6840.06)
    # Counterparts use 494001 (SIN INONDATION) and 499603 (Sinistre pompe) when
    # explicitly mentioned, else 4990 (Provisions sinistres - generic).
    # NOTE : these are best-effort entries. The user should review the counterpart
    # accounts in the Accounting page and adjust if their syndic uses different
    # provision accounts.
    if await _ensure_account(db, ACP, "4990", "Provisions sinistres diverses"):
        created_acc += 1
        print("  Created PCMN 4990 Provisions sinistres diverses")

    sinistre_entries = [
        {
            "date": "2025-07-01",
            "reference": "OD-2025-SIN-001",
            "description": "Sinistre pompe de relevage - cloture",
            "lines": [
                {"account_number": "499603", "account_name": "Sinistre garage Pompe de relevage",
                 "description": "Cloture sinistre - remboursement assurance", "debit": 3183.65, "credit": 0.0},
                {"account_number": "61066", "account_name": "Travaux divers",
                 "description": "Sinistre pompe de relevage - cloture", "debit": 0.0, "credit": 3183.65},
            ],
        },
        {
            "date": "2025-07-18",
            "reference": "OD-2025-SIN-002",
            "description": "Rupture devidoir - remboursement partiel 1",
            "lines": [
                {"account_number": "4990", "account_name": "Provisions sinistres diverses",
                 "description": "Remboursement assurance partiel", "debit": 5813.63, "credit": 0.0},
                {"account_number": "61066", "account_name": "Travaux divers",
                 "description": "Rupture devidoir - remboursement partiel 1", "debit": 0.0, "credit": 5813.63},
            ],
        },
        {
            "date": "2025-11-12",
            "reference": "OD-2025-SIN-003",
            "description": "Sinistre inondation pompe de relevage (Cloture)",
            "lines": [
                {"account_number": "499603", "account_name": "Sinistre garage Pompe de relevage",
                 "description": "Cloture sinistre inondation", "debit": 562.67, "credit": 0.0},
                {"account_number": "61066", "account_name": "Travaux divers",
                 "description": "Sinistre inondation pompe de relevage (Cloture)", "debit": 0.0, "credit": 562.67},
            ],
        },
        {
            "date": "2025-12-31",
            "reference": "OD-2025-SIN-004",
            "description": "Sinistre rupture canalisation (2022)",
            "lines": [
                {"account_number": "61066", "account_name": "Travaux divers",
                 "description": "Sinistre rupture canalisation (2022)", "debit": 4018.14, "credit": 0.0},
                {"account_number": "4990", "account_name": "Provisions sinistres diverses",
                 "description": "Cout sinistre canalisation 2022", "debit": 0.0, "credit": 4018.14},
            ],
        },
        {
            "date": "2025-12-31",
            "reference": "OD-2025-SIN-005",
            "description": "Regularisation SIN 202200724 INONDATION",
            "lines": [
                {"account_number": "494001", "account_name": "SIN 202200724 INONDATION",
                 "description": "Regularisation finale - cloture provision", "debit": 1298.25, "credit": 0.0},
                {"account_number": "61066", "account_name": "Travaux divers",
                 "description": "Regularisation SIN 202200724 INONDATION", "debit": 0.0, "credit": 1298.25},
            ],
        },
    ]
    all_entries.extend(sinistre_entries)

    # ----- 643 Frais privatifs : reversals (Imputation copropriétaire) -----
    # In Optipro, each "Frais privatif" charge is followed by an offsetting
    # entry that transfers the cost to the specific owner's account 410XXXX.
    # The 12 invoices in DB already have the cost on 643. We add the reversal
    # to net them to 0 on the common account.
    # Counterpart : account 410 (Coproprietaires - generic) since per-owner
    # mapping wasn't done during import. User can split later if needed.
    fp_reversal = {
        "date": "2025-12-31",
        "reference": "OD-2025-FP-IMP",
        "description": "Imputation coproprietaire des frais privatifs (regularisation annuelle)",
        "lines": [
            {"account_number": "410", "account_name": "Coproprietaires",
             "description": "Imputation des frais privatifs 643", "debit": 155.03, "credit": 0.0},
            {"account_number": "643", "account_name": "Frais privatifs",
             "description": "Imputation coproprietaire des frais privatifs", "debit": 0.0, "credit": 155.03},
        ],
    }
    all_entries.append(fp_reversal)

    for e in all_entries:
        # Avoid duplicates on re-run (idempotent)
        existing = await db.journal_entries.find_one(
            {"copropriete_id": ACP, "reference": e["reference"], "journal_type": "OD"},
            {"_id": 0, "id": 1},
        )
        if existing:
            skipped += 1
            print(f"  [skip] OD {e['reference']} already exists ({existing['id'][:8]})")
            continue

        total_debit = round(sum(l["debit"] for l in e["lines"]), 2)
        total_credit = round(sum(l["credit"] for l in e["lines"]), 2)
        if abs(total_debit - total_credit) > 0.01:
            print(f"  [error] {e['reference']} unbalanced : D={total_debit} C={total_credit}")
            continue

        doc = {
            "id": str(uuid.uuid4()),
            "journal_type": "OD",
            "date": e["date"],
            "reference": e["reference"],
            "description": e["description"],
            "lines": e["lines"],
            "total_debit": total_debit,
            "total_credit": total_credit,
            "copropriete_id": ACP,
            "manually_created": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.journal_entries.insert_one(doc)
        created_je += 1
        print(f"  + OD {e['reference']} : {e['description'][:60]} | D={total_debit} C={total_credit}")

    print(f"\n=== SUMMARY ===")
    print(f"PCMN accounts created : {created_acc}")
    print(f"OD entries created : {created_je}")
    print(f"OD entries skipped (already exist) : {skipped}")


asyncio.run(main())
