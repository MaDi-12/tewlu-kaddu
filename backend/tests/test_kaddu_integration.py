"""Couche Kaddu (nlp/kaddu.py) branchée sur la base du backend, LLM simulé.

Le LLM est remplacé comme dans nlp/test_pipeline.py (pipeline.extraire /
pipeline.corriger) : le reste (atlas, contrôle des montants, phrases de
reponses.csv, conversation) est le vrai code de Marème.
"""

import pytest

from backend import db, kaddu_bridge, pipeline
from backend.carnet import CarnetSQLite

VENDEUR = "221770000000"


def _ex(intention, client=None, articles=(), montant=None):
    from extraire_llm import Extraction

    arts = [{"produit_tel_que_dit": mot, "quantite": quantite, "unite": unite, "sens_fr": None,
             "prix_fcfa": None, "prix_tel_que_dit": None, "prix_par_unite": False}
            for mot, quantite, unite in articles]
    return Extraction.model_validate({
        "type_reponse": "corrige", "intention": intention, "client": client, "articles": arts,
        "montant_fcfa": montant, "montant_tel_que_dit": None, "champs_incertains": [], "traduction_fr": ""})


@pytest.fixture
def kaddu_simule(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "kaddu.db"))
    db.init_db()
    monkeypatch.setenv("NLU_MOTEUR", "kaddu")
    monkeypatch.setenv("KADDU_DOSSIER", str(tmp_path / "conversations"))
    monkeypatch.setattr(kaddu_bridge, "_kaddu", None)

    kaddu_bridge._preparer_imports()
    import pipeline as nlp_pipeline

    phrases = {
        "Awa jënd na ñaari paket sukar, 3000, dafa ko leb": _ex("vente_credit", "Awa", [("sukar", 2, "paket")], 3000),
        "Awa fey na 1000": _ex("remboursement", "Awa", montant=1000),
        "Ñaata la ma Awa bor ?": _ex("question_dette", "Awa"),
        "Far li mujj": _ex("annulation"),
    }
    monkeypatch.setattr(nlp_pipeline, "extraire", lambda texte, modele, langue="wo": (phrases[texte], ""))
    monkeypatch.setattr(
        nlp_pipeline, "corriger",
        lambda prec, texte, modele, historique="", langue="wo":
            (phrases.get(texte) or prec.model_copy(update={"type_reponse": "confirme"}), ""),
    )
    yield
    kaddu_bridge._kaddu = None


def _dire(texte):
    return pipeline.comprendre(VENDEUR, texte, "wo")


def test_rien_n_est_ecrit_avant_le_waaw_puis_la_dette_est_dans_la_base_du_backend(kaddu_simule):
    reponse = _dire("Awa jënd na ñaari paket sukar, 3000, dafa ko leb")
    assert reponse.moteur == "kaddu"
    assert reponse.details["etat"] == "confirmer"
    assert reponse.modele_tts == "AIHubSN/Kiriku-Wolof-TTS"
    with db.session() as conn:
        assert db.lister_operations(conn, VENDEUR) == []

    reponse = _dire("waaw")
    assert reponse.details["issue"] == "enregistre"
    with db.session() as conn:
        [operation] = db.lister_operations(conn, VENDEUR)
        assert db.solde_client(conn, VENDEUR, "Awa") == 3000
        assert db.bilan_jour(conn, VENDEUR)["total_credits"] == 3000
    assert operation["methode"] == "llm" and operation["produit"] == "Sucre"
    assert operation["id"] == reponse.details["operation_id"]


def test_remboursement_puis_annulation_restaure_la_dette(kaddu_simule):
    _dire("Awa jënd na ñaari paket sukar, 3000, dafa ko leb")
    _dire("waaw")
    _dire("Awa fey na 1000")
    _dire("waaw")
    assert CarnetSQLite(VENDEUR).dette("Awa") == 2000

    assert _dire("Far li mujj").details["etat"] == "confirmer_annulation"
    assert _dire("waaw").details["issue"] == "annulation_faite"

    with db.session() as conn:
        assert db.solde_client(conn, VENDEUR, "Awa") == 3000
        statuts = [op["statut"] for op in db.lister_operations(conn, VENDEUR)]
    assert statuts == ["annulee", "active"]   # on n'efface jamais


def test_question_dette_lit_le_carnet_du_backend(kaddu_simule):
    _dire("Awa jënd na ñaari paket sukar, 3000, dafa ko leb")
    _dire("waaw")
    reponse = _dire("Ñaata la ma Awa bor ?")
    assert reponse.details["issue"] == "question"
    assert "trois mille" in reponse.texte


def test_carnet_dette_tolere_un_nom_deforme_par_l_asr(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "carnet.db"))
    db.init_db()
    carnet = CarnetSQLite(VENDEUR)
    carnet.enregistrer({"intention": "vente_credit", "client": "Aminata", "montant_fcfa": 5000,
                        "langue": "wo", "transcription": "...", "articles": []})
    assert carnet.dette("aminatta") == 5000
    assert carnet.dette("Moussa") == 0
    assert carnet.dette(None) == 5000
    assert carnet.ventes_du_jour() == 5000
    assert carnet.derniere()["client"] == "Aminata"


def test_moteur_auto_sans_cle_retombe_sur_les_regles(monkeypatch):
    monkeypatch.setenv("NLU_MOTEUR", "auto")
    monkeypatch.setattr(kaddu_bridge, "cle_llm_presente", lambda: False)
    assert kaddu_bridge.moteur_actif() == "regles"
    monkeypatch.setattr(kaddu_bridge, "cle_llm_presente", lambda: True)
    assert kaddu_bridge.moteur_actif() == "kaddu"
