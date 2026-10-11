"""Teste le pipeline de conversation SANS appeler le LLM (réponses du LLM simulées).
Lancer depuis la racine du repo : python nlp/test_pipeline.py"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import atlas  # noqa: E402
import pipeline as P  # noqa: E402
from extraire_llm import Extraction  # noqa: E402
from stockage_json import BaseJSON  # noqa: E402

SIMULE = {}
HISTORIQUES = []
LANGUES_VUES = []


def ex(intention, client=None, articles=(), montant=None, dit=None, type_reponse="corrige"):
    """articles : (mot dit, quantité, unité[, sens_fr[, prix, prix dit, prix par unité]])"""
    arts = []
    for a in articles:
        a = list(a) + [None] * (7 - len(a))
        arts.append({"produit_tel_que_dit": a[0], "quantite": a[1], "unite": a[2], "sens_fr": a[3],
                     "prix_fcfa": a[4], "prix_tel_que_dit": a[5], "prix_par_unite": bool(a[6])})
    return Extraction.model_validate({
        "type_reponse": type_reponse, "intention": intention, "client": client, "articles": arts,
        "montant_fcfa": montant, "montant_tel_que_dit": dit, "champs_incertains": [], "traduction_fr": ""})


def faux_extraire(texte, modele, langue="wo"):
    LANGUES_VUES.append(langue)
    return SIMULE[texte], ""


def faux_corriger(precedente, texte, modele, historique="", langue="wo"):
    HISTORIQUES.append(historique)
    if texte in SIMULE:
        return SIMULE[texte], ""
    return precedente.model_copy(update={"type_reponse": "confirme"}), ""   # « waaw loolu la »...


P.extraire = faux_extraire
P.corriger = faux_corriger

# --- wolof
SIMULE["Mariam jël na ñetti kilo pomme de terre ak ñaari kilo sobel, ñetti junni"] = ex(
    "vente_credit", "Mariam", [("pomme de terre", 3, "kilo"), ("sobel", 2, "kilo")], 15000, "ñetti junni")
SIMULE["Awa jënd na ñaari paket bantare ñetti junni"] = ex(
    "vente_credit", "Awa", [("bantare", 2, "paket")], 15000, "ñetti junni")
SIMULE["bantare"] = ex("vente_credit", "Awa", [("bantare", 2, "paket")], 15000, "ñetti junni")
SIMULE["déet, ñaari junni la"] = ex("vente_credit", "Awa", [("bantare", 2, "paket")], 10000, "ñaari junni")
SIMULE["jaay naa benn saaku ceeb"] = ex("vente", None, [("ceeb", 1, "saaku")])
SIMULE["22500"] = ex("vente", None, [("ceeb", 1, "saaku")], 22500, "22500")
SIMULE["Ñaata la Awa ma bor ?"] = ex("question_dette", "Awa")
SIMULE["madame jumade démb améloon nañu benn palaas ak tay mu nekk 4000 njaay naa fi tamit 13 000f"] = ex(
    "vente", "madame jumade", [("palaas", 1, None, "plat")], 17000, "4000 13 000f")
SIMULE["jaay naa benn mburu fanweer"] = ex("vente", None, [("mburu", 1, None)], 3000, "fanweer")  # erreur du LLM
SIMULE["Fatou jël na ñaari mburu 100 bu nekk, meew 100 ak diw 300"] = ex(
    "vente_credit", "Fatou", [("mburu", 2, None, "pain", 100, "100", True),
                              ("meew", None, None, "lait", 100, "100", False),
                              ("diw", None, None, "huile", 300, "300", False)])
SIMULE["Jend naa fukki saak ceeb."] = ex("achat", None, [("ceeb", 10, "saak")])
SIMULE["dépense transport 500"] = ex("depense", None, [("transport", None, None, "transport")], 500, "500")
SIMULE["Awa fey na ñaari teeméer."] = ex("remboursement", "Awa", [], 1000, "ñaari teeméer")
SIMULE["lima diay tay thi ñaata la tole?"] = ex("question_bilan")
# --- pulaar et sérère (phrases du document Phases Tests de Ndèye)
SIMULE["Hawa sodi paketa didi sukara téemédé jeegom, ko niamliido dum"] = ex(
    "vente_credit", "Hawa", [("sukara", 2, "paketa")], 3000, "téemédé jeegom")
SIMULE["Hawa yobiima téemeedé ɗiɗi"] = ex("remboursement", "Hawa", [], 1000, "téemeedé ɗiɗi")
SIMULE["No foti Hawa jogui é ñamande"] = ex("question_dette", "Hawa")
SIMULE["Jikaam xarbaxay saak maalo."] = ex("achat", None, [("maalo", 10, "saak")])


def dialogue(base, file, messages, langue="wo"):
    conv = P.Conversation("test", base, langue=langue, file_termes=file)
    reponses = [conv.recevoir(m) for m in messages]
    return conv, reponses


def main():
    with tempfile.TemporaryDirectory() as d:
        P.SESSIONS = Path(d) / "sessions.csv"
        base, file = P.BaseTest(Path(d) / "base.db"), atlas.FileTermes(Path(d) / "termes.db")
        enregistre = P.phrase("enregistre")
        n = 0

        def ok(nom, detail=""):
            nonlocal n
            n += 1
            print(f"OK  {nom:32}", detail)

        # ------------------------------------------------------------------ wolof (comme avant)
        conv, r = dialogue(base, file, ["Mariam jël na ñetti kilo pomme de terre ak ñaari kilo sobel, ñetti junni",
                                        "waaw", "waaw"])
        assert "soble" in r[0] and r[2] == enregistre
        ok("mot proche -> question", r[0])

        conv, r = dialogue(base, file, ["Awa jënd na ñaari paket bantare ñetti junni", "bantare",
                                        "déet, ñaari junni la", "waaw"])
        assert r[0] == P.phrase("demander_produit")                 # inconnu : on demande de répéter
        assert "bantare" in r[1] and "dix mille" in r[2] and r[3] == enregistre
        assert conv.nb_corrections == 1 and file.lister()[0][0] == "bantare"
        assert r[1].endswith("Waaw walla déet ?")                   # fin de confirmation du document Phases Tests
        ok("inconnu -> répéter -> écrit", r[1])

        conv, r = dialogue(base, file, ["madame jumade démb améloon nañu benn palaas ak tay mu nekk 4000 "
                                        "njaay naa fi tamit 13 000f", "waaw"])
        assert "palaas" in r[0] and "dix-sept mille" in r[0] and r[1] == enregistre   # écrit sans question
        ok("le LLM connaît le sens", r[0])

        conv, r = dialogue(base, file, ["jaay naa benn saaku ceeb", "22500", "waaw loolu la"])
        assert r[0] == P.phrase("demander_montant") and r[2] == enregistre       # « waaw loolu la » = oui
        ok("« waaw loolu la » = oui", r[1])

        conv, r = dialogue(base, file, ["jaay naa benn mburu fanweer"])
        assert "cent cinquante francs, maanaam fanweeri dërëm" in r[0]
        ok("fanweer = 150 F + wolof", r[0])

        conv, r = dialogue(base, file, ["Fatou jël na ñaari mburu 100 bu nekk, meew 100 ak diw 300", "waaw"])
        assert "six cents francs" in r[0] and r[1] == enregistre      # 2 x 100 + 100 + 300, calculé par le code
        ok("prix par produit, somme", r[0])

        conv, r = dialogue(base, file, ["Ñaata la Awa ma bor ?"])
        assert "dix mille" in r[0]
        ok("question dette", r[0])

        conv, r = dialogue(base, file, ["Awa fey na ñaari teeméer.", "waaw"])      # scénario 003
        assert "mu des neuf mille francs" in r[0] and r[1] == enregistre
        assert base.dette("Awa") == 9000
        ok("remboursement + reste dû", r[0])

        conv, r = dialogue(base, file, ["Jend naa fukki saak ceeb.", "waaw"])         # scénario 001
        assert r[0] == "Dix saak ceeb. Waaw walla déet ?" and r[1] == enregistre
        ok("entrée de stock sans prix", r[0])

        conv, r = dialogue(base, file, ["lima diay tay thi ñaata la tole?"])          # scénario 005
        assert "Ameel nañu la" in r[0] and "vingt-quatre mille" in r[0]               # 15 000 + 9 000 de crédits
        ok("solde global (ventes + crédits)", r[0])

        conv, r = dialogue(base, file, ["dépense transport 500"])
        assert conv.issue == "pas_disponible"
        ok("dépense pas encore prise", r[0][:50] + " ...")

        assert any("Commerçant : Awa jënd na ñaari paket bantare ñetti junni" in h for h in HISTORIQUES)
        ok("contexte transmis au LLM")

        assert P.texte_pour_tts("Danga xam ne Xadi") == "danga kham ne khadi"
        assert P.texte_pour_tts("dix mille") == "dix mille"           # le x au milieu d'un mot ne change pas
        assert P.texte_pour_tts("iyo wala Xa'a ?") == "iyo wala kha'a ?"
        ok("texte pour le TTS", P.texte_pour_tts("Xamaguma « palaas »"))

        # ------------------------------------------------------------------ pulaar
        base_ff = P.BaseTest(Path(d) / "base_ff.db")
        conv, r = dialogue(base_ff, file, ["Hawa sodi paketa didi sukara téemédé jeegom, ko niamliido dum", "eey"],
                           langue="ff")
        assert r[0] == "Hawa a namludi trois mille francs, maanaa teemédé jeegom, ey walla ala ?" and conv.issue == "enregistre"
        assert LANGUES_VUES[-1] == "ff" and conv.historique[-1]["details"]["langue_phrase"] == "ff"   # « Mi winndii ɗum. »
        ok("pulaar : crédit + « eey »", r[0])

        conv, r = dialogue(base_ff, file, ["Hawa yobiima téemeedé ɗiɗi", "ko ɗum"], langue="ff")
        assert r[0] == ("Hawa a yobdi mille francs, maanaa teemédé ɗiɗi, hedo deux mille francs, maanaa teemédé nay. "
                        "Ey walla ala ?") and conv.issue == "enregistre"
        ok("pulaar : remboursement + reste", r[0])

        conv, r = dialogue(base_ff, file, ["No foti Hawa jogui é ñamande"], langue="ff")
        assert r[0] == "Hawa jogui deux mille francs, maanaa teemédé nay."
        ok("pulaar : question dette", r[0])

        # ------------------------------------------------------------------ sérère
        base_srr = BaseJSON(Path(d) / "carnet_srr.json")            # stockage JSON
        conv, r = dialogue(base_srr, file, ["Jikaam xarbaxay saak maalo.", "xa'a", "Jikaam xarbaxay saak maalo.",
                                            "iyo"], langue="srr")
        assert r[0] == "Dix saak maalo. Waaw walla déet ?" and r[1] == P.phrase("redemander", "wo")   # réponse en wolof
        assert conv.issue == "enregistre" and base_srr.operations()[0]["articles"][0]["produit"] == "Riz"
        ok("sérère : stock, xa'a puis iyo", r[0])

        # ------------------------------------------------------------------ oui / non par langue
        assert P.oui_ou_non("eey", "ff") == ("oui", False) and P.oui_ou_non("alaa", "ff") == ("non", False)
        assert P.oui_ou_non("iyo", "srr") == ("oui", False) and P.oui_ou_non("Xa'a", "srr") == ("non", False)
        assert P.oui_ou_non("mooy loolu", "wo") == ("oui", False)
        assert P.oui_ou_non("waaw loolu la", "wo") == ("oui", True)            # la suite est lue par le LLM
        ok("oui / non par langue")

        # ------------------------------------------------------------------ langue de secours
        assert P.phrase("enregistre", "srr") == "Bind naa ko."                         # pas encore de sérère : wolof
        assert P.phrase("demander_montant", "ff") == "No foti fof ?"                    # pulaar validé par Ndèye
        assert P.phrase("enregistre", "srr", secours="fr") == "C'est noté."
        assert P.phrase("reponse_dette", "ff", client="Hawa", montant="x") == "Hawa jogui x."   # pulaar présent
        assert P.phrase_et_langue("pas_disponible", "srr")[1] == "fr"                 # vide aussi en wolof
        ok("langue de secours (wolof)", P.phrase("enregistre", "srr"))

        # ------------------------------------------------------------------ sauvegarde entre deux messages
        conv = P.Conversation("test", base_srr, langue="srr", file_termes=file)
        conv.recevoir("Jikaam xarbaxay saak maalo.")
        sauvegarde = json.loads(json.dumps(conv.vers_dict(), ensure_ascii=False))
        reprise = P.Conversation.depuis_dict(sauvegarde, base_srr, file)
        assert reprise.etat == "confirmer" and reprise.recevoir("waaw") == P.phrase("enregistre", "wo")  # waaw compris en sérère
        assert len(base_srr.operations()) == 2
        ok("conversation sauvegardée (JSON)")

        # ------------------------------------------------------------------ point d'entrée pour WhatsApp
        from kaddu import Kaddu
        kaddu = Kaddu(Path(d) / "whatsapp")
        r1 = kaddu.traiter("+221 77 000 00 00", "Hawa sodi paketa didi sukara téemédé jeegom, ko niamliido dum", "pu")
        assert r1["langue"] == "ff" and r1["etat"] == "confirmer" and r1["voix_tts"] == P.VOIX_TTS["Pulaar"]
        r2 = kaddu.traiter("+221 77 000 00 00", "eey", "ff")
        assert r2["termine"] and r2["issue"] == "enregistre" and r2["operation_id"] == 1
        assert kaddu.stockage("+221 77 000 00 00").dette("hawa") == 3000
        r3 = kaddu.traiter("+221 77 000 00 00", "No foti Hawa jogui é ñamande", "ff")
        assert r3["nouvelle_conversation"] and r3["reponse"] == "Hawa jogui trois mille francs, maanaa teemédé jeegom."
        ok("Kaddu.traiter (WhatsApp)", r3["reponse"])

        ops, arts = base.tables()
        assert len(ops) == 7
        print(f"{n} tests réussis")


if __name__ == "__main__":
    main()
