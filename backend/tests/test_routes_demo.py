import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import db, pipeline, templates
from backend.main import app

VENDEUR = "221770000000"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "demo.db"))
    with TestClient(app) as test_client:
        yield test_client


def test_traiter_texte_puis_carnet(client):
    reponse = client.post("/traiter", data={"numero": VENDEUR, "texte": "jaay naa 1500"}).json()
    assert reponse["moteur"] == "regles"
    assert reponse["texte"] == "1500 F. Waaw walla déet ?"

    client.post("/traiter", data={"numero": VENDEUR, "texte": "waaw"})
    carnet = client.get(f"/carnet/{VENDEUR}").json()
    assert carnet["bilan_du_jour"]["total_ventes"] == 1500
    assert [op["montant"] for op in carnet["operations"]] == [1500]


def test_traiter_refuse_une_langue_inconnue(client):
    reponse = client.post("/traiter", data={"numero": VENDEUR, "texte": "jaay naa 1500", "langue": "pu"})
    assert reponse.status_code == 400


def test_racine_indique_le_moteur_actif(client):
    assert client.get("/").json()["moteur_comprehension"] == "regles"


def test_webhook_ignore_un_message_deja_recu(client, monkeypatch):
    appels = []

    async def faux_handle(message, vendeur_wa_id):
        appels.append(message["id"])

    monkeypatch.setattr(pipeline, "handle_incoming_message", faux_handle)
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"id": "wamid.UNIQUE-42", "from": VENDEUR, "type": "text", "text": {"body": "waaw"}}]}}]}]}

    client.post("/webhook/whatsapp", json=payload)
    client.post("/webhook/whatsapp", json=payload)   # Meta rejoue le même webhook

    assert appels == ["wamid.UNIQUE-42"]


@pytest.mark.parametrize("texte, attendu", [
    ("Pulaar", "ff"), ("langue seereer", "srr"), ("/langue wo", "wo"), ("Sérère", "srr"),
    ("jaay naa pulaar 1500", None), ("waaw", None),
])
def test_detecter_langue_demandee(texte, attendu):
    assert templates.detecter_langue_demandee(texte) == attendu


@pytest.mark.anyio
async def test_message_de_changement_de_langue_met_a_jour_le_profil(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "langue.db"))
    db.init_db()
    textes = []

    async def faux_envoyer_texte(wa_id, texte):
        textes.append(texte)

    monkeypatch.setattr(pipeline.whatsapp_client, "envoyer_texte", faux_envoyer_texte)
    await pipeline.handle_incoming_message({"type": "text", "text": {"body": "pulaar"}}, VENDEUR)

    with db.session() as conn:
        assert db.get_or_create_vendeur(conn, VENDEUR) == "ff"
    assert textes == ["Pulaar ✓"]


def test_migration_d_une_base_creee_par_la_premiere_version(tmp_path):
    chemin = str(tmp_path / "ancienne.db")
    ancienne = sqlite3.connect(chemin)
    ancienne.execute(
        "CREATE TABLE operations (id INTEGER PRIMARY KEY AUTOINCREMENT, vendeur_wa_id TEXT NOT NULL, "
        "client_id INTEGER, intention TEXT NOT NULL, produit TEXT, quantite REAL, unite TEXT, montant INTEGER, "
        "langue TEXT NOT NULL, methode TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    ancienne.execute(
        "INSERT INTO operations (vendeur_wa_id, intention, montant, langue, methode, created_at) "
        "VALUES (?, 'vente', 1000, 'wo', 'regles', '2026-10-09T10:00:00+00:00')", (VENDEUR,)
    )
    ancienne.commit()
    ancienne.close()

    db.init_db(chemin)

    with db.session(chemin) as conn:
        [operation] = db.lister_operations(conn, VENDEUR)
    assert operation["statut"] == "active"


@pytest.fixture
def anyio_backend():
    return "asyncio"
