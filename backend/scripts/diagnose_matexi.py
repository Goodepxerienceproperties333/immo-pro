"""
Diagnostic Matexi — ID 55655c92-5e71-4750-964e-a89dc14efeaa
ACP Acacia TEST — b2a10c6a-1974-4a86-8734-c31d7ed70dc0

1. Grand livre Matexi <= 30.06.2026
2. Combinaisons totalisant 387,35 €
3. Recouvrement AN / FI
4. Doublons 41010002 vs 41011986
"""
import pymongo
from itertools import combinations
from collections import defaultdict

client = pymongo.MongoClient("mongodb://localhost:27017")
db = client["test_database"]

COPRO = "b2a10c6a-1974-4a86-8734-c31d7ed70dc0"
OWNER_ID = "55655c92-5e71-4750-964e-a89dc14efeaa"
CUTOFF = "2026-06-30"
TARGET = 387.35
ACCOUNTS = {"41010002", "41011986", "41010011", "41000011"}

entries = list(db.journal_entries.find(
    {"copropriete_id": COPRO, "date": {"$lte": CUTOFF}},
    {"_id": 0},
))

# ── 1. Grand livre Matexi ──────────────────────────────────────────
print("=" * 100)
print(f"1. GRAND LIVRE MATEXI  (écritures <= {CUTOFF})")
print("=" * 100)

lines_all = []
for e in entries:
    eid = e.get("id", "?")
    date = e.get("date", "?")
    jtype = e.get("journal_type", "?")
    ref = e.get("reference", "")
    desc = (e.get("description") or "")[:60]
    rev = e.get("is_reversal", False)
    revd = e.get("reversed", False)
    obal = e.get("is_opening_balance", False)
    for ln in e.get("lines") or []:
        acc = ln.get("account_number", "")
        tpid = ln.get("third_party_id", "")
        if tpid == OWNER_ID or acc in ACCOUNTS:
            d = float(ln.get("debit", 0) or 0)
            c = float(ln.get("credit", 0) or 0)
            lines_all.append({
                "date": date, "jtype": jtype, "ref": ref, "desc": desc,
                "acc": acc, "debit": d, "credit": c,
                "rev": rev, "revd": revd, "obal": obal, "eid": eid,
            })

lines_all.sort(key=lambda x: (x["date"], x["jtype"], x["ref"]))

total_d = total_c = 0.0
print(f"{'Date':<12} {'J':>4} {'Ref':<30} {'Compte':<12} {'Debit':>12} {'Credit':>12} {'Flags':<10} {'Description'}")
print("-" * 160)
for ln in lines_all:
    flags = []
    if ln["rev"]:   flags.append("REV")
    if ln["revd"]:  flags.append("REVD")
    if ln["obal"]:  flags.append("OBAL")
    total_d += ln["debit"]
    total_c += ln["credit"]
    print(f"{ln['date']:<12} {ln['jtype']:>4} {ln['ref']:<30} {ln['acc']:<12} {ln['debit']:>12.2f} {ln['credit']:>12.2f} {','.join(flags):<10} {ln['desc']}")

print("-" * 160)
print(f"{'TOTAL':<60} {'':<12} {total_d:>12.2f} {total_c:>12.2f}")
print(f"{'SOLDE NET (D-C)':<60} {'':<12} {total_d - total_c:>12.2f}")
print(f"\nLignes: {len(lines_all)}")

# ── 2. Combinaisons totalisant 387,35 € ────────────────────────────
print("\n" + "=" * 100)
print(f"2. COMBINAISONS DE LIGNES TOTALISANT {TARGET} €")
print("=" * 100)

# Only non-reversed, non-zero net amounts
active_lines = [(i, round(ln["debit"] - ln["credit"], 2), ln)
                for i, ln in enumerate(lines_all)
                if not ln["rev"] and not ln["revd"] and abs(ln["debit"] - ln["credit"]) > 0.001]
print(f"  Lignes actives (non-REV/REVD): {len(active_lines)}")

found_combos = []
for r in range(1, min(len(active_lines), 4) + 1):
    if r >= 4 and len(active_lines) > 80:
        print(f"  (skip r={r}, trop de lignes)")
        continue
    for combo in combinations(active_lines, r):
        s = round(sum(x[1] for x in combo), 2)
        if abs(s - TARGET) < 0.005 or abs(s + TARGET) < 0.005:
            found_combos.append((s, combo))

if found_combos:
    for s, combo in found_combos[:10]:
        print(f"\n  Somme = {s:.2f} €")
        for idx, net, ln in combo:
            print(f"    [{idx:3d}] {ln['date']} {ln['jtype']:>4} {ln['ref']:<30} {ln['acc']:<12} net={net:>10.2f}  {ln['desc'][:40]}")
else:
    print("  Aucune combinaison trouvee (jusqu'a 5 lignes).")

# ── 3. Recouvrement AN / FI ────────────────────────────────────────
print("\n" + "=" * 100)
print("3. RECOUVREMENT A-NOUVEAU (AN) vs FINANCIER (FI)")
print("=" * 100)

an_lines = [ln for ln in lines_all if ln["jtype"] == "AN"]
fi_lines = [ln for ln in lines_all if ln["jtype"] == "FI"]

print(f"\n  Écritures AN: {len(an_lines)}")
an_d = sum(ln["debit"] for ln in an_lines)
an_c = sum(ln["credit"] for ln in an_lines)
print(f"    Total AN  D={an_d:.2f}  C={an_c:.2f}  Net={an_d - an_c:.2f}")

print(f"\n  Écritures FI: {len(fi_lines)}")
fi_d = sum(ln["debit"] for ln in fi_lines)
fi_c = sum(ln["credit"] for ln in fi_lines)
print(f"    Total FI  D={fi_d:.2f}  C={fi_c:.2f}  Net={fi_d - fi_c:.2f}")

# Chercher des montants AN qui apparaissent aussi en FI
an_amounts = defaultdict(list)
for ln in an_lines:
    key = round(ln["debit"] if ln["debit"] else -ln["credit"], 2)
    an_amounts[key].append(ln)

fi_amounts = defaultdict(list)
for ln in fi_lines:
    key = round(ln["debit"] if ln["debit"] else -ln["credit"], 2)
    fi_amounts[key].append(ln)

overlap = set(an_amounts.keys()) & set(fi_amounts.keys())
if overlap:
    print(f"\n  RECOUVREMENTS DETECTES ({len(overlap)} montants communs):")
    for amt in sorted(overlap):
        print(f"\n    Montant: {amt:.2f} €")
        for ln in an_amounts[amt]:
            print(f"      AN: {ln['date']} {ln['ref']:<30} {ln['acc']}")
        for ln in fi_amounts[amt]:
            print(f"      FI: {ln['date']} {ln['ref']:<30} {ln['acc']}")
else:
    print("\n  Aucun recouvrement exact de montants entre AN et FI.")

# ── 4. Doublons 41010002 vs 41011986 ───────────────────────────────
print("\n" + "=" * 100)
print("4. DOUBLONS ENTRE 41010002 ET 41011986")
print("=" * 100)

acc_a = [ln for ln in lines_all if ln["acc"] == "41010002"]
acc_b = [ln for ln in lines_all if ln["acc"] == "41011986"]

print(f"\n  Lignes 41010002: {len(acc_a)}")
print(f"  Lignes 41011986: {len(acc_b)}")

a_by_amount = defaultdict(list)
for ln in acc_a:
    key = (round(ln["debit"], 2), round(ln["credit"], 2))
    a_by_amount[key].append(ln)

b_by_amount = defaultdict(list)
for ln in acc_b:
    key = (round(ln["debit"], 2), round(ln["credit"], 2))
    b_by_amount[key].append(ln)

dup_keys = set(a_by_amount.keys()) & set(b_by_amount.keys())
if dup_keys:
    print(f"\n  DOUBLONS DETECTES ({len(dup_keys)} paires de montants):")
    for key in sorted(dup_keys, key=lambda x: max(x), reverse=True):
        d, c = key
        print(f"\n    D={d:.2f} / C={c:.2f}:")
        for ln in a_by_amount[key]:
            print(f"      41010002: {ln['date']} {ln['jtype']:>4} {ln['ref']:<30} {ln['desc'][:40]}")
        for ln in b_by_amount[key]:
            print(f"      41011986: {ln['date']} {ln['jtype']:>4} {ln['ref']:<30} {ln['desc'][:40]}")
else:
    print("\n  Aucun doublon de montants entre les deux comptes.")

print("\n" + "=" * 100)
print("FIN DU DIAGNOSTIC")
print("=" * 100)
