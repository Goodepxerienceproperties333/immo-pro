"""iter90ex : suppression du verrou 1:1 entre nature de depense et
compte PCMN. Plusieurs natures peuvent partager le meme compte PCMN
(ex: "RC copro" et "Assurance RC CoC & Comm.aux comptes" tous deux
sur 6141).
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

import httpx  # noqa: E402

BACKEND = "http://localhost:8001"


async def _login():
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BACKEND}/api/auth/login",
                         json={"email": "admin@copro.be", "password": "admin123"})
        r.raise_for_status()
        return r.cookies


async def _setup_pcmn():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    suffix = uuid.uuid4().hex[:6]
    cid = f"iter90ex-cid-{suffix}"
    acc_num = f"6141{suffix[:2]}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "Iter90exACP", "reference": f"T90ex{suffix}",
        "status": "active",
    })
    await db.pcmn_accounts.insert_one({
        "number": acc_num, "name": "Responsabilite civile", "class_num": 6,
        "copropriete_id": cid,
    })
    return db, cid, acc_num, suffix


async def _cleanup(db, cid):
    await db.coproprietes.delete_one({"id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.expense_categories.delete_many({"copropriete_id": cid})


def test_can_create_multiple_categories_same_account():
    async def _run():
        db, cid, acc_num, suffix = await _setup_pcmn()
        cookies = await _login()
        try:
            async with httpx.AsyncClient(cookies=cookies) as c:
                r1 = await c.post(f"{BACKEND}/api/expense-categories", json={
                    "name": f"RC copro {suffix}", "account_number": acc_num,
                    "copropriete_id": cid,
                })
                assert r1.status_code == 200, r1.text
                r2 = await c.post(f"{BACKEND}/api/expense-categories", json={
                    "name": f"Assurance RC CoC {suffix}", "account_number": acc_num,
                    "copropriete_id": cid,
                })
                assert r2.status_code == 200, (
                    f"iter90ex : creation d'une 2eme nature sur meme compte "
                    f"doit reussir, got {r2.status_code}: {r2.text}"
                )
                # Verifier les 2 lignes existent
                r_list = await c.get(f"{BACKEND}/api/expense-categories",
                                     params={"copropriete_id": cid})
                assert r_list.status_code == 200
                names = {x["name"] for x in r_list.json()
                         if x.get("account_number") == acc_num}
                assert f"RC copro {suffix}" in names
                assert f"Assurance RC CoC {suffix}" in names
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


def test_can_reassign_existing_category_to_used_account():
    async def _run():
        db, cid, acc_num, suffix = await _setup_pcmn()
        # 2eme compte
        acc_num2 = f"6142{suffix[:2]}"
        await db.pcmn_accounts.insert_one({
            "number": acc_num2, "name": "Autre charge", "class_num": 6,
            "copropriete_id": cid,
        })
        cookies = await _login()
        try:
            async with httpx.AsyncClient(cookies=cookies) as c:
                r1 = await c.post(f"{BACKEND}/api/expense-categories", json={
                    "name": f"NatureA {suffix}", "account_number": acc_num,
                    "copropriete_id": cid,
                })
                assert r1.status_code == 200
                r2 = await c.post(f"{BACKEND}/api/expense-categories", json={
                    "name": f"NatureB {suffix}", "account_number": acc_num2,
                    "copropriete_id": cid,
                })
                assert r2.status_code == 200
                cat_b_id = r2.json()["id"]
                # Reaffecter NatureB sur le compte de NatureA
                r_up = await c.put(f"{BACKEND}/api/expense-categories/{cat_b_id}", json={
                    "name": f"NatureB {suffix}", "account_number": acc_num,
                    "copropriete_id": cid,
                })
                assert r_up.status_code == 200, (
                    f"iter90ex : reaffectation vers compte deja utilise "
                    f"doit reussir, got {r_up.status_code}: {r_up.text}"
                )
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


if __name__ == "__main__":
    test_can_create_multiple_categories_same_account()
    test_can_reassign_existing_category_to_used_account()
    print("OK")
