"""Cleanup des utilisateurs de test (preview ET production).

Supprime tous les comptes utilisateurs dont l'email correspond a un pattern de
test (emails contenant 'test_' ou '@example.com').

Reels (a CONSERVER) restent intouches :
- admin@copro.be (Super Administrateur)
- gerald@gep.be
- welcome@goodexperienceproperties.be
- evrard.gerald@outlook.be

Egalement nettoyage :
- Tokens / sessions associes aux users supprimes
- Aucune purge des donnees metier (ACPs, factures...) : ces utilisateurs
  test n'ont pas de donnees rattachees.

Usage :
    # Mode dry-run (preview)
    python /app/backend/scripts/cleanup_test_users.py --dry-run
    # Mode reel
    python /app/backend/scripts/cleanup_test_users.py
    # En production (apres redeploiement, via terminal Emergent) :
    python /app/backend/scripts/cleanup_test_users.py
"""
import os
import re
import sys
import asyncio

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

# Patterns regex pour identifier les comptes test
TEST_EMAIL_PATTERNS = [
    r"^test_[a-f0-9]+@",            # test_2b38e4fb@copro.be, test_be9c662c@copro.be
    r"^test_iter\d+",                # test_iter9_X@example.com, test_iter16_owner_X@example.com
    r"@example\.com$",               # tous les @example.com
    r"^itr\d+",                      # itr76, itr77 ... (UUIDs de test)
    r"^pytest_",
]

# Emails reels a TOUJOURS conserver (whitelist defensive)
KEEP_WHITELIST = {
    "admin@copro.be",
    "gerald@gep.be",
    "welcome@goodexperienceproperties.be",
    "evrard.gerald@outlook.be",
}


def is_test_email(email: str) -> bool:
    """Retourne True si l'email correspond a un pattern de test."""
    if not email:
        return False
    email = email.lower().strip()
    if email in KEEP_WHITELIST:
        return False
    for pat in TEST_EMAIL_PATTERNS:
        if re.search(pat, email):
            return True
    return False


async def run(dry_run: bool = False):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    users = await db.users.find({}, {"_id": 0, "id": 1, "email": 1, "role": 1, "name": 1, "created_at": 1}).to_list(2000)

    to_delete = [u for u in users if is_test_email(u.get("email", ""))]
    to_keep = [u for u in users if not is_test_email(u.get("email", ""))]

    print(f"\n{'DRY-RUN ' if dry_run else ''}Cleanup test users")
    print(f"Total users : {len(users)}")
    print(f"A SUPPRIMER : {len(to_delete)}")
    for u in to_delete:
        print(f"  - {u.get('email','?'):45s}  role={u.get('role','?'):12s}  name={u.get('name','')[:30]}")
    print(f"\nA CONSERVER : {len(to_keep)}")
    for u in to_keep:
        print(f"  + {u.get('email','?'):45s}  role={u.get('role','?'):12s}  name={u.get('name','')[:30]}")

    if not to_delete:
        print("\nRien a supprimer.")
        return

    if dry_run:
        print("\n[DRY-RUN] Aucun changement effectue. Relancez sans --dry-run.")
        return

    # Suppression effective (les users sont identifies par email - pas d'id explicite)
    del_emails = [u["email"] for u in to_delete]
    result = await db.users.delete_many({"email": {"$in": del_emails}})
    # Cleanup tokens/sessions associes (si la collection existe)
    try:
        await db.user_sessions.delete_many({"email": {"$in": del_emails}})
    except Exception:
        pass
    try:
        await db.password_reset_tokens.delete_many({"email": {"$in": del_emails}})
    except Exception:
        pass
    # Cleanup owners orphelins crees lors des tests
    orphan_owners = await db.owners.delete_many({"email": {"$in": del_emails}})
    print(f"\nSupprime : {result.deleted_count} users")
    print(f"Owners orphelins associes : {orphan_owners.deleted_count} (cleanup par email)")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    asyncio.run(run(dry_run=dry))
