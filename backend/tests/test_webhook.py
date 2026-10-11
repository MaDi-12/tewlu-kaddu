import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.main import app, pipeline


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "webhook.db"))
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "secret-verify")
    with TestClient(app) as test_client:
        yield test_client


def test_handshake_accepte_avec_le_bon_jeton(client):
    reponse = client.get(
        "/webhook/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "secret-verify", "hub.challenge": "1234"},
    )
    assert reponse.status_code == 200
    assert reponse.text == "1234"


def test_handshake_refuse_avec_un_mauvais_jeton(client):
    reponse = client.get(
        "/webhook/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "faux", "hub.challenge": "1234"},
    )
    assert reponse.status_code == 403


def test_reception_webhook_delegue_au_pipeline_en_arriere_plan(client, monkeypatch):
    appels = []

    async def faux_handler(message, vendeur_wa_id):
        appels.append((message, vendeur_wa_id))

    monkeypatch.setattr(pipeline, "handle_incoming_message", faux_handler)

    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{"from": "221770000000", "type": "text", "text": {"body": "vendu 1500"}}]
                }
            }]
        }]
    }
    reponse = client.post("/webhook/whatsapp", json=payload)

    assert reponse.status_code == 200
    assert reponse.json() == {"status": "ok"}
    assert appels == [({"from": "221770000000", "type": "text", "text": {"body": "vendu 1500"}}, "221770000000")]


def test_reception_webhook_ignore_les_accuses_de_statut(client, monkeypatch):
    appels = []
    monkeypatch.setattr(pipeline, "handle_incoming_message", lambda m, w: appels.append((m, w)))

    payload = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}
    reponse = client.post("/webhook/whatsapp", json=payload)

    assert reponse.status_code == 200
    assert appels == []
