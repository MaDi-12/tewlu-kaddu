"""Tests de l'atlas. Lancer depuis la racine du repo : python nlp/test_atlas.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import atlas as a  # noqa: E402


def test_lexique():
    assert a.chercher("sukkar", "wo").concept == "Sucre"      # faute d'orthographe tolérée
    assert a.chercher("ceeb", "wo").concept == "Riz"
    assert a.chercher("sucre", "ff").concept == "Sucre"       # nom français accepté
    assert a.chercher("xyz", "wo") is None                     # inconnu -> None, pas d'erreur


def test_montants_wolof():
    assert a.texte_vers_montants("ñetti junni", "wo") == [15000]            # 3 x 5000
    assert a.texte_vers_montants("ñaari téeméer", "wo") == [1000]           # 2 x 500
    assert a.texte_vers_montants("junni ak juróom téeméer", "wo") == [7500]
    assert a.texte_vers_montants("ñeent téeméer ak juróom fukk", "wo") == [2250]  # 50 dërëm = 250


def test_montants_chiffres_et_francais():
    assert a.texte_vers_montants("2500", "wo") == [2500]
    assert a.texte_vers_montants("22 500", "wo") == [22500]
    assert a.texte_vers_montants("onze mille", "wo") == [11000]
    assert a.texte_vers_montants("deux mille cinq cents", "ff") == [2500]


def test_montants_pulaar_serere():
    assert a.texte_vers_montants("teemédé ɗiɗi", "ff") == [1000]
    assert a.texte_vers_montants("thiouni dik", "srr") == [10000]
    assert a.texte_vers_montants("thiapantati", "ff") == [150]
    assert a.texte_vers_montants("teeméde dik fo xarbéndik", "srr") == [1100]
    assert a.texte_vers_montants("ujunéré e teemedéré", "ff") == [5500]


def test_atlas_officiel():
    """Formes de l'atlas officiel de Ndèye (Off_Annuaire_..._3.xlsx)."""
    assert a._nombres("wo")[4] == "ñent"
    assert a.forme_locale(25, "wo") == "juróom dërëm"
    assert a.forme_locale(100, "wo") == "ñaar-fukki dërëm"
    assert a.forme_locale(100, "srr") == "xarbéndik"
    assert a.forme_locale(5000, "srr") == "thiouni"
    assert a.chercher("maalo", "srr").concept == "Riz"
    assert a.chercher("basalle", "ff").concept == "Oignon"


def test_regles_reproduisent_atlas():
    """Les règles (au-delà de l'atlas) redonnent les lignes de l'atlas. Deux écarts connus, à trancher avec Ndèye."""
    connus = {("ff", 150000), ("srr", 100000)}
    for langue in a.LANGUES:
        for f, forme in a._montants_atlas(langue).items():
            if (langue, f) not in connus:
                assert a.normaliser(a.forme_par_regles(f, langue)) == a.normaliser(forme), (langue, f)


def test_verification():
    assert a.verifier_montant(15000, "ñetti junni", "wo")["statut"] == "coherent"
    assert a.verifier_montant(3000, "ñetti junni", "wo")["statut"] == "ecart"
    assert a.verifier_montant(1000, None, "ff")["statut"] == "non_verifiable"


def test_lecture():
    d = a.montant_a_dire(15000, "wo")
    assert d["tts"] == "quinze mille francs" and d["local"] == "ñetti junni"


def test_classer():
    assert a.classer("soble", "wo", "produit")["statut"] == "reconnu"
    assert a.classer("pommes de terre", "wo", "produit")["concept"] == "Pomme de terre"   # nom français
    proche = a.classer("sobel", "wo", "produit")
    assert proche["statut"] == "proche" and proche["suggestion"] == "Oignon"
    assert a.classer("bantare", "ff", "produit")["statut"] == "nouveau"
    assert a.classer("palaas", "wo", "produit", sens="plat")["statut"] == "nouveau"      # pas « pataas »
    assert a.classer("plat", "wo", "produit")["statut"] == "nouveau"                     # pas « lait »
    assert a.classer("sobel", "wo", "produit", sens="oignon")["suggestion"] == "Oignon"


def test_plusieurs_montants():
    assert a.texte_vers_montants("4000 13 000f", "wo") == [4000, 13000]                  # pas 400013000
    assert a.verifier_montant(17000, "4000 13 000f", "wo")["statut"] == "coherent"       # total


def test_rapprocher_plusieurs_articles_sans_rejet():
    extraction = {"articles": [{"produit_tel_que_dit": "pomme de terre", "quantite": 2, "unite": "kilo"},
                               {"produit_tel_que_dit": "bantare", "quantite": 3, "unite": "kilo"}],
                  "montant_fcfa": 15000, "montant_tel_que_dit": "ñetti junni"}
    with tempfile.TemporaryDirectory() as dossier:
        file = a.FileTermes(Path(dossier) / "t.db")
        r = a.rapprocher(extraction, "wo", "phrase", file)
        assert [x["statut_produit"] for x in r["articles"]] == ["reconnu", "nouveau"]
        assert r["articles"][1]["produit"] == "bantare"              # gardé tel qu'entendu
        assert r["controle_montant"]["statut"] == "lecture_ok"
        assert file.lister()[0][:3] == ("bantare", "wo", "produit")


if __name__ == "__main__":
    tests = [f for nom, f in dict(globals()).items() if nom.startswith("test_")]
    for t in tests:
        t()
        print("OK ", t.__name__)
    print(f"{len(tests)} tests réussis")
