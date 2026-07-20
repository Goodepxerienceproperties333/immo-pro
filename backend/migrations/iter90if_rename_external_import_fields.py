"""iter90if - Migration idempotente : renomme les champs MongoDB lies aux
imports externes historiques pour ne plus exposer les noms de logiciels
tiers dans les donnees stockees.

Renommages :
  - invoices.internal_ref_optipro          -> invoices.internal_ref
  - journal_entries.optipro_reference      -> journal_entries.source_reference
  - import_wizard_sessions.source_system   -> valeurs "Optipro"/"Sogis" -> "external"

Idempotent : marque un flag dans `system_migrations` apres execution
reussie. Les appels suivants sont no-ops.
"""
from motor.motor_asyncio import AsyncIOMotorDatabase


MIGRATION_KEY = "iter90if_rename_external_import_fields"


async def run_migration_iter90if(db: AsyncIOMotorDatabase) -> dict:
    """Renomme les champs et normalise les valeurs source_system. Retourne
    un rapport de type {invoices: N, journal_entries: N, sessions: N,
    skipped: bool}."""
    existing = await db.system_migrations.find_one({"key": MIGRATION_KEY})
    if existing and existing.get("status") == "done":
        return {"skipped": True, "reason": "already applied"}

    report = {"skipped": False, "invoices": 0, "journal_entries": 0, "sessions": 0}

    # 1. invoices.internal_ref_optipro -> internal_ref
    #    Si les 2 champs existent (rare), internal_ref prime, on drop
    #    internal_ref_optipro.
    res1 = await db.invoices.update_many(
        {"internal_ref_optipro": {"$exists": True}, "internal_ref": {"$in": [None, ""]}},
        [{"$set": {"internal_ref": "$internal_ref_optipro"}},
         {"$unset": "internal_ref_optipro"}],
    )
    report["invoices"] += res1.modified_count
    # Cleanup des doublons residuels
    res1b = await db.invoices.update_many(
        {"internal_ref_optipro": {"$exists": True}},
        {"$unset": {"internal_ref_optipro": ""}},
    )
    report["invoices"] += res1b.modified_count

    # 2. journal_entries.optipro_reference -> source_reference
    res2 = await db.journal_entries.update_many(
        {"optipro_reference": {"$exists": True},
         "source_reference": {"$in": [None, ""]}},
        [{"$set": {"source_reference": "$optipro_reference"}},
         {"$unset": "optipro_reference"}],
    )
    report["journal_entries"] += res2.modified_count
    res2b = await db.journal_entries.update_many(
        {"optipro_reference": {"$exists": True}},
        {"$unset": {"optipro_reference": ""}},
    )
    report["journal_entries"] += res2b.modified_count

    # 3. import_wizard_sessions.source_system : 'Optipro' / 'Sogis' -> 'external'
    res3 = await db.import_wizard_sessions.update_many(
        {"source_system": {"$in": ["Optipro", "Sogis", "optipro", "sogis"]}},
        {"$set": {"source_system": "external"}},
    )
    report["sessions"] += res3.modified_count

    # Marque la migration comme executee
    from datetime import datetime, timezone
    await db.system_migrations.update_one(
        {"key": MIGRATION_KEY},
        {"$set": {
            "key": MIGRATION_KEY,
            "status": "done",
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "report": report,
        }},
        upsert=True,
    )
    return report
