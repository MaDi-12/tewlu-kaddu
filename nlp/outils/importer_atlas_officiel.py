"""Importe l'atlas OFFICIEL validé par Ndèye Maguette dans nlp/data/atlas/ (remplace tout l'ancien atlas).

Source : Off_Annuaire_Commercants_Pulaar_Serere_Wolof_3.xlsx (7 onglets)
    Lexique_Marche_Trilingue, Wolof_Nombres_1_100, Wolof_Montants_FCFA, Pulaar_Nombres,
    Pulaar_Montants, Serere_Nombres, Serere_Montants

Fichiers écrits (les anciens sont écrasés) :
    lexique.csv                          concept, categorie, langue, forme, variantes, source, statut
    nombres_wo.csv / _ff / _srr          valeur, forme, variantes, source, statut
    montants_wo.csv / _ff / _srr         montant_fcfa, forme, variantes, logique, source, statut
    doublons_atlas.csv                   même mot pour deux concepts d'une même langue (à montrer à Ndèye)
    montants_a_verifier.csv              variante d'un montant identique à la forme d'un autre montant (à montrer à Ndèye)

reponses.csv n'est PAS touché (modèles de phrases de Kaddu).

Usage (depuis la racine du dépôt) :
    python nlp/outils/importer_atlas_officiel.py
    python nlp/outils/importer_atlas_officiel.py chemin/vers/Off_Annuaire_....xlsx
"""
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl

RACINE = Path(__file__).resolve().parent.parent
ATLAS = RACINE / "data" / "atlas"
SOURCE = ATLAS / "source" / "Off_Annuaire_Commercants_Pulaar_Serere_Wolof_3.xlsx"
ORIGINE = "ndeye_maguette_officiel"

# Le fichier officiel n'a pas de colonne « catégorie ». Le code en a besoin : un produit n'est comparé
# qu'aux produits, une unité qu'aux unités, et « Oui / Non / C'est ça » donnent les mots de confirmation.
# Ce classement est technique (aucun mot n'est modifié). Un concept nouveau, absent d'ici, est classé « autre »
# et signalé à l'import.
CATEGORIES = {
    "intention": ["Vendre", "Acheter (stock)", "Prendre (client)", "Payer", "Rembourser", "Prêter", "Emprunter",
                  "Donner", "Envoyer (argent)", "Recevoir", "Annuler", "Négocier / Marchander",
                  "Livrer / Livraison", "Peser (balance)", "Compter (l'argent)", "Vérifier / Contrôler",
                  "Vente en gros", "Vente au détail"],
    "paiement": ["Crédit / dette", "Il / elle me doit", "Argent", "Monnaie (rendue)", "Reste", "Tout / total",
                 "Bénéfice", "Prix / Coût", "Réduction / Rabais", "Cher (coûteux)", "Moins cher / Bon marché",
                 "Facture / Reçu", "Perte (financière)", "Espèces / Cash", "Mobile Money (Wave / Orange)"],
    "produit": ["Riz", "Sucre", "Huile", "Lait", "Pain", "Oignon", "Pomme de terre", "Tomate", "Sel", "Piment",
                "Poisson", "Viande", "Poulet", "Œuf", "Eau", "Café", "Thé", "Savon"],
    "unité": ["Sac", "Kilo", "Litre", "Paquet", "Bouteille", "Morceau", "Sachet / Emballage", "Carton", "Boîte",
              "Demi-kilo", "Quart de kilo"],
    "temps": ["Aujourd'hui", "Hier", "Demain", "Matin", "Soir", "Semaine", "Mois"],
    "question": ["Combien", "Qui", "Quoi", "Quand"],
    "confirmation": ["Oui", "Non", "C'est ça", "Répète"],
    "personne": ["Client", "Voisin(e)", "Mère", "Tante", "Vendeur / Boutiquier"],
    "autre": ["Commande", "Marché (lieu)", "Boutique / Étal", "Balance", "Rupture de stock / Épuisé",
              "Disponible / En stock", "Bonne qualité", "Périmé / Gâté", "Portefeuille / Bourse"],
}
CATEGORIE = {concept: cat for cat, concepts in CATEGORIES.items() for concept in concepts}
LANGUES_LEXIQUE = {"wo": (1, 2), "ff": (3, 4), "srr": (5, 6)}   # colonnes (forme, variantes)
NOMBRES = {"wo": "Wolof_Nombres_1_100", "ff": "Pulaar_Nombres", "srr": "Serere_Nombres"}
MONTANTS = {"wo": "Wolof_Montants_FCFA", "ff": "Pulaar_Montants", "srr": "Serere_Montants"}


def propre(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def morceaux(champ) -> list[str]:
    """« sukar, suukir » -> [sukar, suukir]. Séparateurs ; , / ; « _ » = aucune ;
    les précisions entre parenthèses sont retirées (« sow (lait caillé) » -> « sow »)."""
    texte = re.sub(r"\(.*?\)", " ", propre(champ))
    vus, sortie = set(), []
    for m in re.split(r"\s*[;,/]\s*", texte):
        m = propre(m)
        if m and m != "_" and m.lower() not in vus:
            vus.add(m.lower())
            sortie.append(m)
    return sortie


def lignes(ws):
    return [r for r in ws.iter_rows(min_row=2, values_only=True) if r and r[0] not in (None, "")]


def ecrire(nom, champs, contenu):
    with open(ATLAS / nom, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=champs)
        w.writeheader()
        w.writerows(contenu)
    print(f"  {nom:22} {len(contenu):5} lignes")


def importer(source: Path):
    wb = openpyxl.load_workbook(source, data_only=True)
    print(f"Import de {source.name} -> {ATLAS}")

    # --- Lexique : une ligne par (concept, langue) ; « Meew / Sow » : 1re forme principale, les autres en variantes
    lexique, inconnus = [], []
    for r in lignes(wb["Lexique_Marche_Trilingue"]):
        concept = propre(r[0])
        if concept not in CATEGORIE:
            inconnus.append(concept)
        for langue, (c_forme, c_var) in LANGUES_LEXIQUE.items():
            formes = morceaux(r[c_forme])
            if not formes:
                continue
            variantes = [v for v in formes[1:] + morceaux(r[c_var]) if v.lower() != formes[0].lower()]
            lexique.append({"concept": concept, "categorie": CATEGORIE.get(concept, "autre"), "langue": langue,
                            "forme": formes[0], "variantes": " ; ".join(dict.fromkeys(variantes)),
                            "source": ORIGINE, "statut": "validé"})
    ecrire("lexique.csv", ["concept", "categorie", "langue", "forme", "variantes", "source", "statut"], lexique)

    # --- Nombres et montants
    for langue in ("wo", "ff", "srr"):
        nombres = [{"valeur": int(r[0]), "forme": propre(r[2]), "variantes": " ; ".join(morceaux(r[3])),
                    "source": ORIGINE, "statut": "validé"} for r in lignes(wb[NOMBRES[langue]])]
        ecrire(f"nombres_{langue}.csv", ["valeur", "forme", "variantes", "source", "statut"], nombres)
        montants = [{"montant_fcfa": int(r[0]), "forme": propre(r[2]), "variantes": " ; ".join(morceaux(r[4])),
                     "logique": propre(r[3]), "source": ORIGINE, "statut": "validé"}
                    for r in lignes(wb[MONTANTS[langue]])]
        ecrire(f"montants_{langue}.csv", ["montant_fcfa", "forme", "variantes", "logique", "source", "statut"],
               montants)

    # --- Doublons : un même mot rattaché à deux concepts dans la même langue (questions pour Ndèye)
    sys.path.insert(0, str(RACINE))
    from atlas import normaliser
    par_mot = defaultdict(set)
    for l in lexique:
        for mot in [l["forme"], *[v for v in l["variantes"].split(" ; ") if v]]:
            par_mot[(l["langue"], normaliser(mot))].add(l["concept"])
    doublons = [{"langue": lang, "mot": mot, "concepts": " | ".join(sorted(c))}
                for (lang, mot), c in sorted(par_mot.items()) if len(c) > 1]
    ecrire("doublons_atlas.csv", ["langue", "mot", "concepts"], doublons)

    # --- Variantes de montants ambiguës : la même variante pour plusieurs montants (souvent une variante tronquée)
    from atlas import _norm_montant
    a_verifier = []
    for langue in ("wo", "ff", "srr"):
        with open(ATLAS / f"montants_{langue}.csv", encoding="utf-8") as f:
            montants = list(csv.DictReader(f))
        principales = {_norm_montant(m["forme"]): int(m["montant_fcfa"]) for m in montants}
        for m in montants:
            for v in [x for x in m["variantes"].split(" ; ") if x]:
                autre = principales.get(_norm_montant(v))
                if autre is not None and autre != int(m["montant_fcfa"]):
                    a_verifier.append({"langue": langue, "montant_fcfa": m["montant_fcfa"], "forme": m["forme"],
                                       "variante": v, "probleme": f"c'est la forme de {autre} F"})
    ecrire("montants_a_verifier.csv", ["langue", "montant_fcfa", "forme", "variante", "probleme"], a_verifier)

    if inconnus:
        print("  Nouveaux concepts sans catégorie (classés « autre », à ajouter dans CATEGORIES) :", ", ".join(inconnus))
    print("Terminé. Lance ensuite : python nlp/test_atlas.py && python nlp/test_pipeline.py")


if __name__ == "__main__":
    importer(Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE)
