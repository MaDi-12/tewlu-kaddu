"""Carnet de démonstration : 60 jours d'activité réaliste d'une boutique de quartier, pour le tableau de bord.

Écrit dans nlp/data/equipe/carnet_demo.db (séparé du carnet de l'équipe et de la base de test).
Ventes au comptant et à crédit, remboursements partiels, entrées de stock, quelques annulations, en wolof,
pulaar et sérère, avec les noms des produits tels qu'on les dit (atlas officiel).

    python nlp/outils/donnees_demo.py        # (re)crée le carnet de démonstration
"""
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atlas  # noqa: E402
from pipeline import BaseTest  # noqa: E402

CHEMIN = Path(__file__).resolve().parent.parent / "data" / "equipe" / "carnet_demo.db"

CLIENTS = ["Awa", "Fatou", "Moussa", "Hawa", "Aminata", "Ibrahima", "Coumba", "Ousmane", "Mariama", "Abdou",
           "Ndèye", "Cheikh"]
# concept de l'atlas, unité, prix unitaire (FCFA, multiple de 25), quantités possibles
PRODUITS = [("Riz", "kilo", 450, [1, 2, 3, 5]), ("Riz", "saak", 22500, [1]), ("Sucre", "paket", 600, [1, 2, 3]),
            ("Huile", "liitar", 1300, [1, 2]), ("Lait", "sachet", 250, [1, 2, 4]), ("Pain", None, 150, [1, 2, 4, 6]),
            ("Oignon", "kilo", 500, [1, 2]), ("Pomme de terre", "kilo", 600, [1, 2, 3]), ("Savon", None, 300, [1, 2, 3]),
            ("Thé", "paket", 250, [1, 2, 4]), ("Café", None, 100, [2, 5, 10]), ("Tomate", "bowaat", 400, [1, 2])]
LANGUES = ["wo"] * 6 + ["ff"] * 3 + ["srr"] * 2


def forme(concept: str, langue: str) -> str:
    """Le mot tel que le commerçant le dit dans sa langue (forme principale de l'atlas officiel)."""
    for r in atlas._lire("lexique.csv"):
        if r["concept"] == concept and r["langue"] == langue:
            return r["forme"].split("/")[0].strip().lower()
    return concept.lower()


def operation(date, langue, intention, client, montant, articles, statut="active"):
    return {"date": date.isoformat(timespec="seconds"), "langue": langue, "intention": intention, "client": client,
            "montant_fcfa": montant, "montant_tel_que_dit": str(montant), "controle_montant": "demo",
            "transcription": "(démonstration)", "modele": "demo", "nb_corrections": 0, "statut": statut,
            "articles": articles}


def generer(chemin: Path = CHEMIN, jours: int = 60, graine: int = 7) -> int:
    rnd = random.Random(graine)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    base = BaseTest(chemin)
    base.vider()
    dettes = {c: 0 for c in CLIENTS}
    maintenant = datetime.now()
    n, a_annuler = 0, []
    for j in range(jours - 1, -1, -1):
        jour = (maintenant - timedelta(days=j)).replace(hour=7, minute=0, second=0, microsecond=0)
        week_end = jour.weekday() >= 5
        nb = rnd.randint(7, 13) + (4 if week_end else 0)        # plus de monde le week-end
        heures = sorted(rnd.uniform(0, 14) for _ in range(nb))
        if j == 0:                                              # aujourd'hui : seulement jusqu'à maintenant
            heures = [h for h in heures if jour + timedelta(hours=h) <= maintenant] or heures[:3]
        for h in heures:
            quand = jour + timedelta(hours=h)
            langue = rnd.choice(LANGUES)
            tirage = rnd.random()
            if tirage < 0.16 and any(d > 0 for d in dettes.values()):          # remboursement
                client = rnd.choice([c for c, d in dettes.items() if d > 0])
                if rnd.random() < 0.25:                                         # rembourse tout
                    montant = dettes[client]
                else:                                                           # ou une partie
                    montant = min(dettes[client], rnd.choice([500, 1000, 1500, 2000, 2500, 5000]))
                dettes[client] -= montant
                base.enregistrer(operation(quand, langue, "remboursement", client, montant, []))
            elif tirage < 0.20:                                                 # entrée de stock
                concept, unite, prix, _ = rnd.choice(PRODUITS)
                qte = rnd.choice([10, 20, 25, 50]) if unite != "saak" else rnd.choice([2, 5, 10])
                montant = int(qte * prix * 0.8 // 25 * 25)
                art = [{"produit_dit": forme(concept, langue), "produit": concept, "statut_produit": "reconnu",
                        "quantite": qte, "unite": unite, "prix_fcfa": montant}]
                base.enregistrer(operation(quand, langue, "achat", None, montant, art))
            else:                                                               # vente (comptant ou crédit)
                articles, total = [], 0
                rayon = PRODUITS if rnd.random() < 0.03 else [p for p in PRODUITS if p[1] != "saak"]
                for concept, unite, prix, quantites in rnd.sample(rayon, rnd.choice([1, 1, 2, 2, 3])):
                    qte = rnd.choice(quantites)
                    articles.append({"produit_dit": forme(concept, langue), "produit": concept,
                                     "statut_produit": "reconnu", "quantite": qte, "unite": unite,
                                     "prix_fcfa": qte * prix})
                    total += qte * prix
                credit = rnd.random() < 0.3
                client = rnd.choice(CLIENTS) if credit or rnd.random() < 0.3 else None
                statut = "annulee" if rnd.random() < 0.02 else "active"
                if credit and statut == "active":
                    dettes[client] += total
                op_id = base.enregistrer(operation(quand, langue, "vente_credit" if credit else "vente", client,
                                                   total, articles, statut))
                if statut == "annulee":
                    a_annuler.append(op_id)
            n += 1
    for op_id in a_annuler:            # BaseTest.enregistrer ne garde pas le statut : on passe par annuler()
        base.annuler(op_id)
    base.db.close()
    return n


if __name__ == "__main__":
    print(generer(), "opérations écrites dans", CHEMIN)
