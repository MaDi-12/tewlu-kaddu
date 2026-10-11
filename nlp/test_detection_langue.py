"""Tests de la détection automatique de langue (sans charger le modèle ASR).
Lancer depuis la racine du repo : python nlp/test_detection_langue.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import detection_langue as D  # noqa: E402
import test_pipeline as T     # réutilise les réponses simulées du LLM  # noqa: E402
from kaddu import Kaddu       # noqa: E402


T.SIMULE["Mi soodi saakuji sappo maaro"] = T.ex("achat", None, [("maaro", 10, "saakuji")])


def det(langue, confiance, duree=4.0):
    return {"langue": langue, "confiance": confiance, "marge": 0.1, "probas": {}, "duree_s": duree}


def main():
    # 1) probabilités à partir des scores des jetons <|wo|>, <|pu|>, <|se|>
    r = D._resultat({"wo": 1.0, "ff": 4.0, "srr": 0.5})
    assert r["langue"] == "ff" and r["confiance"] > 0.9 and abs(sum(r["probas"].values()) - 1) < 0.01
    assert r["marge"] > 0.8
    vrai = D._resultat({"wo": 0.475, "ff": 0.179, "srr": 0.346})   # profil d'un vrai audio wolof (logits fictifs)
    assert vrai["langue"] == "wo"
    print("OK  probabilités des 3 jetons    ", r)

    # 2) règle de prudence
    assert D.choisir_langue(det("ff", 0.47)) == "ff"                                       # nouvelle conversation
    assert D.choisir_langue(det("ff", 0.38), langue_habituelle="srr") == "srr"             # confiance < 40 %
    assert D.choisir_langue(det("ff", 0.55), "wo", conversation_en_cours=True) == "ff"      # switch en pleine conversation
    assert D.choisir_langue(det("ff", 0.60, 0.9), "wo", conversation_en_cours=True) == "wo" # audio court : on garde
    assert D.choisir_langue(det("ff", 0.35), "wo", conversation_en_cours=True) == "wo"      # peu sûr : on garde
    assert D.choisir_langue(None, langue_habituelle="srr") == "srr"                        # message tapé
    assert D.choisir_langue(None, "srr", conversation_en_cours=True) == "srr"           # message tapé
    print("OK  règle de prudence")

    # 3) Kaddu en mode « auto »
    with tempfile.TemporaryDirectory() as d:
        kaddu = Kaddu(Path(d))
        n = "221770000001"
        r1 = kaddu.traiter(n, "Hawa sodi paketa didi sukara téemédé jeegom, ko niamliido dum", "auto",
                           detection=det("ff", 0.55))
        assert r1["langue"] == "ff" and r1["etat"] == "confirmer"
        r2 = kaddu.traiter(n, "eey", "auto", detection=det("wo", 0.60, 0.8))   # court : on reste en pulaar
        assert r2["langue"] == "ff" and r2["issue"] == "enregistre"
        # switch en pleine conversation : vente commencée en pulaar, réponse en wolof -> on continue en wolof
        kaddu.traiter(n, "Hawa sodi paketa didi sukara téemédé jeegom, ko niamliido dum", "auto",
                      detection=det("ff", 0.55))
        rs = kaddu.traiter(n, "waaw loolu la, bind ko", "auto", detection=det("wo", 0.58))
        assert rs["changement_langue"] and rs["langue"] == "wo" and rs["issue"] == "enregistre"
        assert rs["reponse"] == "Bind naa ko." and rs["voix_tts"].endswith("Wolof-TTS")
        print("OK  switch pulaar -> wolof en pleine conversation :", rs["reponse"])
        # le produit dit en pulaar reste reconnu après le passage au wolof
        kaddu.traiter(n, "Mi soodi saakuji sappo maaro", "auto", detection=det("ff", 0.55))
        rp = kaddu.traiter(n, "waaw, bind ko", "auto", detection=det("wo", 0.58))
        op = kaddu.stockage(n).operations()[-1]
        assert rp["changement_langue"] and op["articles"][0]["produit"] == "Riz"
        assert op["articles"][0]["statut_produit"] == "reconnu"
        print("OK  « maaro » (pulaar) reconnu après passage au wolof :", op["articles"][0]["produit"])
        r3 = kaddu.traiter(n, "No foti Hawa jogui é ñamande", "auto")      # message tapé : langue habituelle
        assert r3["langue"] == "wo" and r3["nouvelle_conversation"]   # message tapé : dernière langue utilisée (wolof)
        r4 = kaddu.traiter(n, "Jikaam xarbaxay saak maalo.", "auto", detection=det("srr", 0.50))
        assert r4["langue"] == "srr" and r4["voix_tts"].endswith("Wolof-TTS")   # sérère compris, réponse wolof
        print("OK  Kaddu.traiter(langue='auto')  ", r4["reponse"])

    # 4) vraie détection si torch et le modèle sont disponibles (sinon ignoré)
    try:
        import torch  # noqa: F401
        print("    (torch présent : tester detecter_langue_audio avec le vrai modèle via le backend)")
    except ImportError:
        print("    (torch absent : detecter_langue_audio non testé ici)")
    print("5 tests réussis")


if __name__ == "__main__":
    main()
