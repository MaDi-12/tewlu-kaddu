import httpx
import json

import pytest
import respx

from backend import whatsapp_client as wc


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("WHATSAPP_TOKEN", "token-de-test")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "secret-verify")
    monkeypatch.setenv("WHATSAPP_API_VERSION", "v21.0")


def test_handshake_valide_renvoie_le_challenge():
    assert wc.verifier_handshake("subscribe", "secret-verify", "1234") == "1234"


def test_handshake_jeton_invalide_renvoie_none():
    assert wc.verifier_handshake("subscribe", "mauvais-jeton", "1234") is None


def test_handshake_sans_jeton_configure_renvoie_none(monkeypatch):
    monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN", raising=False)
    assert wc.verifier_handshake("subscribe", "peu-importe", "1234") is None


@pytest.mark.anyio
async def test_telecharger_media_suit_les_deux_etapes():
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://graph.facebook.com/v21.0/media-id-123").respond(
            json={"url": "https://lookaside.fbsbx.com/fichier.ogg"}
        )
        mock.get("https://lookaside.fbsbx.com/fichier.ogg").respond(content=b"contenu-audio-ogg")

        contenu = await wc.telecharger_media("media-id-123")

    assert contenu == b"contenu-audio-ogg"


@pytest.mark.anyio
async def test_envoyer_texte_poste_le_bon_payload():
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://graph.facebook.com/v21.0/1234567890/messages").respond(json={"ok": True})

        await wc.envoyer_texte("221770000000", "Awa amel nala 2000 F ci bor.")

    requete = route.calls.last.request
    assert requete.headers["Authorization"] == "Bearer token-de-test"
    payload = json.loads(requete.content)
    assert payload["to"] == "221770000000"
    assert payload["type"] == "text"
    assert payload["text"]["body"] == "Awa amel nala 2000 F ci bor."


@pytest.mark.anyio
async def test_envoyer_audio_televerse_puis_envoie_par_media_id():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://graph.facebook.com/v21.0/1234567890/media").respond(json={"id": "media-456"})
        route_messages = mock.post("https://graph.facebook.com/v21.0/1234567890/messages").respond(
            json={"ok": True}
        )

        route_media = mock.routes[0]
        await wc.envoyer_audio("221770000000", b"faux-ogg")

    payload = json.loads(route_messages.calls.last.request.content)
    assert payload["audio"]["id"] == "media-456"
    # WhatsApp refuse le WAV : le média doit être déclaré en OGG.
    televerse = route_media.calls.last.request.content
    assert b"audio/ogg" in televerse and b"audio/wav" not in televerse


@pytest.mark.anyio
async def test_sans_identifiants_leve_une_erreur_claire(monkeypatch):
    monkeypatch.delenv("WHATSAPP_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="WHATSAPP_TOKEN"):
        await wc.envoyer_texte("221770000000", "peu importe")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_erreur_meta_lisible_dans_l_exception():
    with respx.mock() as mock:
        mock.post("https://graph.facebook.com/v21.0/1234567890/messages").respond(
            400, json={"error": {"code": 131030, "message": "Recipient phone number not in allowed list"}}
        )
        with pytest.raises(httpx.HTTPStatusError, match="131030.*not in allowed list"):
            await wc.envoyer_texte("16315551181", "test")
