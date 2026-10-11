import pytest

from backend import db, scheduler
from contracts.models import DonneesExtraction, ExtractionResponse


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "scheduler.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db(path)
    return path


def _extraction(montant):
    return ExtractionResponse(
        intention="vente",
        donnees=DonneesExtraction(montant=montant),
        langue="wo",
        methode="regles",
        confiance=0.9,
        est_complet=True,
        champs_manquants=[],
        question_clarification=None,
    )


@pytest.mark.anyio
async def test_envoie_seulement_aux_vendeurs_ayant_une_activite(db_path, monkeypatch):
    with db.session(db_path) as conn:
        db.get_or_create_vendeur(conn, "actif")
        db.enregistrer_operation(conn, "actif", _extraction(1500))
        db.get_or_create_vendeur(conn, "inactif")  # aucune opération aujourd'hui

    appels = []

    async def faux_repondre(wa_id, texte, langue):
        appels.append((wa_id, texte, langue))

    monkeypatch.setattr(scheduler, "repondre", faux_repondre)

    await scheduler.envoyer_bilans_du_jour()

    assert len(appels) == 1
    assert appels[0][0] == "actif"
    assert "1500" in appels[0][1]


@pytest.mark.anyio
async def test_aucun_vendeur_actif_ne_fait_rien(db_path, monkeypatch):
    with db.session(db_path) as conn:
        db.get_or_create_vendeur(conn, "inactif")
    appels = []
    monkeypatch.setattr(scheduler, "repondre", lambda *a, **k: appels.append(a))

    await scheduler.envoyer_bilans_du_jour()

    assert appels == []


@pytest.mark.anyio
async def test_demarrer_est_idempotent():
    try:
        premier = scheduler.demarrer()
        deuxieme = scheduler.demarrer()
        assert premier is deuxieme
        assert premier.get_job("bilan_du_soir") is not None
    finally:
        scheduler.arreter()


@pytest.fixture
def anyio_backend():
    return "asyncio"
