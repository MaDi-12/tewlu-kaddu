import asyncio

import httpx
import pytest
import respx

from backend import db, pipeline, telegram_bot
from backend import telegram_client as tc

TOKEN = "123:ABC"
BOT = f"https://api.telegram.org/bot{TOKEN}"


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)


@pytest.fixture
def base(tmp_path, monkeypatch):
    path = str(tmp_path / "telegram.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db(path)


def _update(update_id=7, **message):
    return {"update_id": update_id, "message": {"message_id": 1, "chat": {"id": 555}, **message}}


def test_note_vocale_convertie_au_format_du_pipeline():
    message, vendeur = tc.message_depuis_update(_update(voice={"file_id": "F1", "duration": 3}))
    assert vendeur == "tg-555"
    assert message == {"id": "tg-7", "type": "audio", "audio": {"id": "F1"}}


def test_texte_et_messages_ignores():
    message, _ = tc.message_depuis_update(_update(text="waaw"))
    assert message["type"] == "text" and message["text"]["body"] == "waaw"
    assert tc.message_depuis_update({"update_id": 8, "edited_message": {}}) is None
    assert tc.message_depuis_update(_update(photo=[{}]))[0]["type"] == "autre"


def test_le_canal_depend_du_commercant():
    assert pipeline.canal_pour("tg-555") is tc
    assert pipeline.canal_pour("221770000000") is pipeline.whatsapp_client


@pytest.mark.anyio
async def test_telecharger_puis_envoyer_voix_et_texte():
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{BOT}/getFile").respond(json={"ok": True, "result": {"file_path": "voice/f1.oga"}})
        mock.get(f"https://api.telegram.org/file/bot{TOKEN}/voice/f1.oga").respond(content=b"OggS...")
        route_voix = mock.post(f"{BOT}/sendVoice").respond(json={"ok": True, "result": {}})
        route_texte = mock.post(f"{BOT}/sendMessage").respond(json={"ok": True, "result": {}})

        assert await tc.telecharger_media("F1") == b"OggS..."
        await tc.envoyer_audio("tg-555", b"OggS-reponse")
        await tc.envoyer_texte("tg-555", "Bind naa ko.")

    corps_voix = route_voix.calls.last.request.content
    assert b'name="voice"' in corps_voix and b"OggS-reponse" in corps_voix and b"555" in corps_voix
    assert b"chat_id=555" in route_texte.calls.last.request.content


@pytest.mark.anyio
async def test_erreur_telegram_explicite():
    with respx.mock() as mock:
        mock.post(f"{BOT}/sendMessage").respond(400, json={"ok": False, "description": "chat not found"})
        with pytest.raises(RuntimeError, match="chat not found"):
            await tc.envoyer_texte("tg-1", "salut")


@pytest.mark.anyio
async def test_start_envoie_l_accueil_sans_passer_par_le_pipeline(monkeypatch):
    envoyes = []

    async def faux_texte(vendeur, texte):
        envoyes.append((vendeur, texte))

    async def interdit(*args):
        raise AssertionError("/start ne doit pas aller au pipeline")

    monkeypatch.setattr(tc, "envoyer_texte", faux_texte)
    monkeypatch.setattr(pipeline, "handle_incoming_message", interdit)
    await telegram_bot.traiter_update(_update(text="/start"))
    assert envoyes == [("tg-555", telegram_bot.ACCUEIL)]


@pytest.mark.anyio
async def test_note_vocale_telegram_de_bout_en_bout(base, monkeypatch):
    monkeypatch.setattr(pipeline.kiriku_models, "recognize_wav_bytes", lambda wav, modele, langue: "jaay naa 1500")
    monkeypatch.setattr(pipeline.audio_utils, "ogg_vers_wav", lambda ogg: b"wav")
    monkeypatch.setattr(pipeline.kiriku_models, "synthesize", lambda texte, modele: b"wav-tts")
    monkeypatch.setattr(pipeline.audio_utils, "wav_vers_ogg_opus", lambda wav: b"OggS-reponse")

    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{BOT}/getFile").respond(json={"ok": True, "result": {"file_path": "voice/f1.oga"}})
        mock.get(f"https://api.telegram.org/file/bot{TOKEN}/voice/f1.oga").respond(content=b"OggS-commercant")
        route_voix = mock.post(f"{BOT}/sendVoice").respond(json={"ok": True, "result": {}})
        route_texte = mock.post(f"{BOT}/sendMessage").respond(json={"ok": True, "result": {}})
        # Aucun appel WhatsApp : respx refuserait toute requête vers graph.facebook.com.

        await telegram_bot.traiter_update(_update(voice={"file_id": "F1"}))

    assert route_voix.called
    assert "1500 F. Waaw walla d" in httpx.QueryParams(route_texte.calls.last.request.content.decode()).get("text")
    with db.session() as conn:
        assert db.get_pending(conn, "tg-555") is not None   # attend le "waaw", rien d'écrit


@pytest.mark.anyio
async def test_boucle_de_polling_recoit_repond_et_s_arrete():
    with respx.mock() as mock:
        mock.post(f"{BOT}/deleteWebhook").respond(json={"ok": True, "result": True})
        mock.post(f"{BOT}/getMe").respond(json={"ok": True, "result": {"username": "kaddu_bot"}})
        polling = mock.post(f"{BOT}/getUpdates")
        polling.side_effect = [
            httpx.Response(200, json={"ok": True, "result": [_update(update_id=41, text="/start")]}),
        ] + [httpx.Response(200, json={"ok": True, "result": []})] * 50
        accueil = mock.post(f"{BOT}/sendMessage").respond(json={"ok": True, "result": {}})

        assert telegram_bot.demarrer() is not None
        for _ in range(50):
            await asyncio.sleep(0.01)
            if accueil.called and polling.call_count >= 2:
                break
        await telegram_bot.arreter()

    assert accueil.called
    # L'update 41 est acquitté : la requête suivante demande à partir de 42.
    assert b"offset=42" in polling.calls[1].request.content
    assert telegram_bot.actif() is False


def test_sans_token_le_bot_ne_demarre_pas(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert telegram_bot.demarrer() is None
    assert telegram_bot.actif() is False


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_coupure_reseau_ponctuelle_rejouee(monkeypatch):
    async def sans_attente(secondes):
        pass

    monkeypatch.setattr(tc.asyncio, "sleep", sans_attente)
    with respx.mock() as mock:
        route = mock.post(f"{BOT}/sendMessage")
        route.side_effect = [httpx.ConnectError("coupure"), httpx.Response(200, json={"ok": True, "result": {}})]
        await tc.envoyer_texte("tg-555", "Bind naa ko.")
    assert route.call_count == 2
