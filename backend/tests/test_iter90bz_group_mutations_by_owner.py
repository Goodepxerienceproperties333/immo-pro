"""iter90bz - Regroupement des mutations lot dans la situation de compte.

Cas d'usage : un promoteur (Matexi) possede 30 lots. A chaque appel trimestriel,
30 ecritures "Mutation lot XXX - Prorata appel" sont creees (une par lot vendu).
La vue "Situation de compte" affiche 120 lignes pour 4 trimestres = illisible.

La fonction _group_movements_by_owner doit maintenant :
- Detecter le pattern "Mutation lot XXX - <label>"
- Extraire le <label> stable comme cle de regroupement
- Fusionner toutes les mutations partageant le meme (date, label, compte, tiers)
- Remplacer la description par "Mutations (N lots) - <label>"
"""
from routes.reports import _normalize_mutation_desc, _group_movements_by_owner


class TestNormalizeMutationDesc:
    def test_simple_mutation_prorata(self):
        r = _normalize_mutation_desc(
            "Mutation lot 001 - Prorata appel (Trimestriel 1/4 - Exercice 2026): Matexi -> Dewinter (206.44 EUR)"
        )
        # iter90dw : returns (label, from_name, to_name) tuple
        assert r[0] == "Mutation lots - Prorata appel (Trimestriel 1/4 - Exercice 2026)"
        assert r[1] == "Matexi"
        assert r[2] == "Dewinter"

    def test_alphanumeric_lot(self):
        r = _normalize_mutation_desc(
            "Mutation lot C9 - Fonds de roulement: Matexi -> Dewinter"
        )
        assert r[0] == "Mutation lots - Fonds de roulement"
        assert r[1] == "Matexi"
        assert r[2] == "Dewinter"

    def test_alphanumeric_lot_with_prefix(self):
        r = _normalize_mutation_desc(
            "Mutation lot Pe01 - Appel futur (Trimestriel 2/4 - Exercice 2026): Matexi -> Buyer"
        )
        assert r[0] == "Mutation lots - Appel futur (Trimestriel 2/4 - Exercice 2026)"

    def test_with_journal_prefix(self):
        r = _normalize_mutation_desc(
            "[OD] Mutation lot 302 - Appel futur (Trimestriel 4/4)"
        )
        assert r[0] == "Mutation lots - Appel futur (Trimestriel 4/4)"

    def test_with_operation_prefix(self):
        r = _normalize_mutation_desc(
            "Operation : Mutation lot 001 - Prorata appel (Q1): Matexi -> Buyer"
        )
        assert r[0] == "Mutation lots - Prorata appel (Q1)"
        assert r[1] == "Matexi"
        assert r[2] == "Buyer"

    def test_non_matching_returns_none(self):
        assert _normalize_mutation_desc("Appel de provisions - Q1 2026") is None
        assert _normalize_mutation_desc("Paiement recu: Matexi") is None
        assert _normalize_mutation_desc("") is None
        assert _normalize_mutation_desc(None) is None
        assert _normalize_mutation_desc("Frais privatif - Matexi") is None


class TestGroupMovementsMutationAggregation:
    """Reproduit le cas Matexi : plusieurs mutations lot pour la meme date."""

    def _mut(self, lot: str, amount: float, label: str = "Prorata appel (Trimestriel 1/4 - Exercice 2026)"):
        return {
            "date": "2025-10-01",
            "description": f"Mutation lot {lot} - {label}: Matexi -> Buyer ({amount:.2f} EUR)",
            "reference": f"MUT-{lot}-Q1",
            "account_number": "41010001",
            "debit": 0.0,
            "credit": amount,
            "journal_type": "OD",
            "third_party_id": "owner-matexi",
        }

    def test_aggregates_30_lot_mutations_into_one(self):
        movements = [self._mut(f"lot{i:03d}", i * 10.0) for i in range(1, 31)]
        result = _group_movements_by_owner(movements)
        assert len(result) == 1, f"30 mutations doivent etre fusionnees en 1, obtenu {len(result)}"
        row = result[0]
        assert row["credit"] == round(sum(i * 10.0 for i in range(1, 31)), 2)
        assert row["debit"] == 0.0
        assert "Mutations (30 lots)" in row["description"]
        assert "Prorata appel" in row["description"]
        assert row["reference"] == "MUT-AGG (30)"

    def test_single_mutation_keeps_original_description(self):
        """Si une seule mutation, on garde le numero de lot pour tracabilite."""
        movements = [self._mut("001", 206.44)]
        result = _group_movements_by_owner(movements)
        assert len(result) == 1
        assert result[0]["description"].startswith("Mutation lot 001 - ")

    def test_appel_de_provisions_line_not_grouped(self):
        """La ligne globale "Appel de provisions" (VE) ne doit pas etre fusionnee
        avec les mutations OD."""
        movements = [
            {
                "date": "2025-10-01",
                "description": "[VE] Appel de provisions - Trimestriel 1/4 - Exercice 2026",
                "reference": "FC-Q1",
                "account_number": "41010001",
                "debit": 4580.80, "credit": 0.0,
                "journal_type": "VE",
                "third_party_id": "owner-matexi",
            },
            self._mut("001", 206.44),
            self._mut("002", 268.51),
            self._mut("101", 178.92),
        ]
        result = _group_movements_by_owner(movements)
        # 1 VE + 1 mutation agregee (3 lots)
        assert len(result) == 2
        ve = [r for r in result if r["journal_type"] == "VE"]
        muts = [r for r in result if r["journal_type"] == "OD"]
        assert len(ve) == 1
        assert ve[0]["debit"] == 4580.80
        assert len(muts) == 1
        assert muts[0]["credit"] == round(206.44 + 268.51 + 178.92, 2)
        assert "Mutations (3 lots)" in muts[0]["description"]

    def test_different_quarters_stay_separate(self):
        """Prorata Q1 vs Prorata Q2 => 2 groupes distincts (label different)."""
        movements = [
            self._mut("001", 100.0, "Prorata appel (Q1)"),
            self._mut("002", 200.0, "Prorata appel (Q1)"),
            self._mut("001", 150.0, "Prorata appel (Q2)"),
            self._mut("002", 250.0, "Prorata appel (Q2)"),
        ]
        # Ajuster les dates pour reproduire trimestres differents
        movements[2]["date"] = "2026-01-01"
        movements[3]["date"] = "2026-01-01"
        result = _group_movements_by_owner(movements)
        assert len(result) == 2
        by_q = {r["description"]: r for r in result}
        q1 = next(v for k, v in by_q.items() if "Q1" in k)
        q2 = next(v for k, v in by_q.items() if "Q2" in k)
        assert q1["credit"] == 300.0
        assert q2["credit"] == 400.0
        assert "Mutations (2 lots)" in q1["description"]
        assert "Mutations (2 lots)" in q2["description"]

    def test_different_owners_stay_separate(self):
        """Mutations de proprietaires DIFFERENTS ne doivent JAMAIS fusionner."""
        m1 = self._mut("001", 100.0)
        m1["third_party_id"] = "owner-A"
        m2 = self._mut("002", 200.0)
        m2["third_party_id"] = "owner-A"
        m3 = self._mut("003", 150.0)
        m3["third_party_id"] = "owner-B"
        result = _group_movements_by_owner([m1, m2, m3])
        assert len(result) == 2
        by_owner = {r["third_party_id"]: r for r in result}
        assert by_owner["owner-A"]["credit"] == 300.0
        assert by_owner["owner-B"]["credit"] == 150.0
        assert "Mutations (2 lots)" in by_owner["owner-A"]["description"]
        # 1 seul lot cote B => description originale conservee
        assert "Mutation lot 003" in by_owner["owner-B"]["description"]

    def test_running_balance_impact_matexi_scenario(self):
        """Scenario Matexi reel : 4580.80 debit puis 30 mutations credit."""
        movements = [
            {
                "date": "2025-10-01",
                "description": "[VE] Appel de provisions - Trimestriel 1/4 - Exercice 2026",
                "reference": "FC-Q1",
                "account_number": "41010001",
                "debit": 4580.80, "credit": 0.0,
                "journal_type": "VE",
                "third_party_id": "matexi",
            }
        ]
        for i in range(30):
            movements.append(self._mut(f"L{i:03d}", 75.53))
        for m in movements:
            m["third_party_id"] = "matexi"
        result = _group_movements_by_owner(movements)
        assert len(result) == 2
        total_debit = sum(r["debit"] for r in result)
        total_credit = sum(r["credit"] for r in result)
        assert total_debit == 4580.80
        assert total_credit == round(75.53 * 30, 2)  # 2265.90

    def test_fonds_de_roulement_mutations_aggregated(self):
        """Les Fonds de roulement Matexi -> Buyer1, -> Buyer2 doivent aussi
        s'agreger correctement (meme label 'Fonds de roulement')."""
        movements = [
            {
                "date": "2025-11-07",
                "description": "Operation : Mutation lot 102 - Fonds de roulement: Matexi -> DEWINTER (622.44 EUR)",
                "reference": "MUT-102-FR",
                "account_number": "41000001",
                "debit": 622.44, "credit": 0.0,
                "journal_type": "OD",
                "third_party_id": "matexi",
            },
            {
                "date": "2025-11-07",
                "description": "Operation : Mutation lot C9 - Fonds de roulement: Matexi -> DEWINTER (8.32 EUR)",
                "reference": "MUT-C9-FR",
                "account_number": "41000001",
                "debit": 8.32, "credit": 0.0,
                "journal_type": "OD",
                "third_party_id": "matexi",
            },
            {
                "date": "2025-11-07",
                "description": "Operation : Mutation lot Pe04 - Fonds de roulement: Matexi -> DEWINTER (17.68 EUR)",
                "reference": "MUT-Pe04-FR",
                "account_number": "41000001",
                "debit": 17.68, "credit": 0.0,
                "journal_type": "OD",
                "third_party_id": "matexi",
            },
        ]
        result = _group_movements_by_owner(movements)
        assert len(result) == 1
        assert result[0]["debit"] == round(622.44 + 8.32 + 17.68, 2)
        assert "Mutations (3 lots) - Fonds de roulement" in result[0]["description"]
