import base64
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict

from dotenv import load_dotenv

# Chargé avant tout import local : kiriku_models lit HF_TOKEN au moment du chargement des modèles.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
# httpx journalise chaque URL appelée en INFO : celles de Telegram contiennent le token du bot.
for _bavard in ("httpx", "httpcore"):
    logging.getLogger(_bavard).setLevel(logging.WARNING)

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from huggingface_hub.errors import (
    GatedRepoError, HfHubHTTPError, LocalEntryNotFoundError, RepositoryNotFoundError,
)

from backend import db, kaddu_bridge, kiriku_models, pipeline, scheduler, telegram_bot, whatsapp_client
from backend.kiriku_models import ASR_MODELS, TTS_MODELS


@asynccontextmanager
async def _lifespan(app: FastAPI):
    db.init_db()
    scheduler.demarrer()
    telegram_bot.demarrer()   # seulement si TELEGRAM_BOT_TOKEN est renseigné
    yield
    await telegram_bot.arreter()
    scheduler.arreter()


app = FastAPI(title="Kiriku Models API", lifespan=_lifespan)


@app.exception_handler(GatedRepoError)
@app.exception_handler(RepositoryNotFoundError)
@app.exception_handler(HfHubHTTPError)
@app.exception_handler(LocalEntryNotFoundError)
def hf_access_error(request, exc):
    # snapshot_download wraps HTTP failures in LocalEntryNotFoundError: report the root cause.
    cause = exc
    while cause.__cause__ is not None:
        cause = cause.__cause__
    status = getattr(getattr(cause, "response", None), "status_code", None)
    if status in (401, 403) or isinstance(exc, (GatedRepoError, RepositoryNotFoundError)):
        detail = ("Accès refusé par Hugging Face : vérifiez HF_TOKEN dans backend/.env, acceptez les "
                  "conditions d'accès sur la page du modèle et, pour un token fine-grained, cochez "
                  "« Read access to contents of all public gated repos you can access ».")
        return JSONResponse(status_code=403, content={"detail": detail})
    return JSONResponse(status_code=502, content={"detail": f"Téléchargement Hugging Face impossible : {exc}"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/models")
def list_models():
    return {
        "tts": [{"id": k, "label": v} for k, v in TTS_MODELS.items()],
        "asr": [{"id": k, **v} for k, v in ASR_MODELS.items()],
    }


@app.post("/tts")
def text_to_speech(text: str = Form(...), model: str = Form(...)):
    text = text.strip()
    if not text:
        raise HTTPException(400, "Texte vide.")
    try:
        wav_bytes = kiriku_models.synthesize(text, model)
    except KeyError:
        raise HTTPException(400, f"Modèle TTS inconnu : {model}")
    return Response(content=wav_bytes, media_type="audio/wav")


@app.post("/asr")
def speech_to_text(
    audio: UploadFile = File(...),
    model: str = Form(...),
    language: str | None = Form(None),
):
    try:
        text = kiriku_models.recognize_wav_bytes(audio.file.read(), model, language)
    except KeyError:
        raise HTTPException(400, f"Modèle ASR inconnu : {model}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Audio illisible (envoyez du WAV/FLAC/OGG/MP3) : {e}")

    return {"text": text}


@app.get("/webhook/whatsapp")
def verify_whatsapp_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
):
    """Handshake de vérification Meta (fait une seule fois, à la configuration du webhook)."""
    challenge_valide = whatsapp_client.verifier_handshake(hub_mode, hub_verify_token, hub_challenge)
    if challenge_valide is None:
        raise HTTPException(403, "Jeton de vérification invalide.")
    return PlainTextResponse(challenge_valide)


@app.post("/webhook/whatsapp")
async def receive_whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Reçoit les notes vocales/messages WhatsApp. Répond 200 immédiatement (Meta retire le
    webhook s'il répond trop lentement) et traite le message en arrière-plan."""
    payload = await request.json()
    for message, vendeur_wa_id in pipeline.iter_incoming_messages(payload):
        if pipeline.deja_recu(message):
            continue
        background_tasks.add_task(pipeline.handle_incoming_message, message, vendeur_wa_id)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Routes de démo : le même pipeline que WhatsApp, sans passer par Meta.
# `numero` identifie le commerçant (son carnet et sa conversation en cours).
# ---------------------------------------------------------------------------

def _langue(numero: str, langue: str | None) -> str:
    """Langue demandée (enregistrée comme préférée du commerçant) ou celle de son profil."""
    with db.session() as conn:
        if langue:
            if langue not in pipeline.LANGUE_CONTRAT_VERS_TOKEN_ASR:
                raise HTTPException(400, f"Langue invalide : {langue} (attendu : wo, ff, srr)")
            db.set_langue_preferee(conn, numero, langue)
            return langue
        return db.get_or_create_vendeur(conn, numero)


@app.post("/traiter")
def traiter_texte(numero: str = Form(...), texte: str = Form(...), langue: str | None = Form(None)):
    """Message texte (ou transcription) -> réponse du commerçant, sans audio."""
    texte = texte.strip()
    if not texte:
        raise HTTPException(400, "Texte vide.")
    return asdict(pipeline.comprendre(numero, texte, _langue(numero, langue)))


@app.post("/traiter-vocal")
def traiter_vocal(
    numero: str = Form(...),
    audio: UploadFile = File(...),
    langue: str | None = Form(None),
    avec_audio: bool = Form(True),
):
    """Note vocale -> ASR -> compréhension -> TTS. Réponse audio en WAV base64 (`audio_wav_base64`)."""
    langue = _langue(numero, langue)
    try:
        transcription = pipeline.transcrire(audio.file.read(), langue).strip()
    except Exception as e:
        raise HTTPException(400, f"Audio illisible ou ASR indisponible : {e}")
    if not transcription:
        raise HTTPException(422, "Rien n'a été compris dans l'audio (transcription vide).")

    reponse = pipeline.comprendre(numero, transcription, langue)
    resultat = {"transcription": transcription, **asdict(reponse), "audio_wav_base64": None}
    if avec_audio:
        wav = kiriku_models.synthesize(reponse.texte_tts, reponse.modele_tts)
        resultat["audio_wav_base64"] = base64.b64encode(wav).decode("ascii")
    return resultat


@app.get("/carnet/{numero}")
def carnet(numero: str, limite: int = 50):
    """Journal, dettes en cours et bilan du jour d'un commerçant (pour le tableau de bord)."""
    with db.session() as conn:
        return {
            "numero": numero,
            "langue": db.get_or_create_vendeur(conn, numero),
            "bilan_du_jour": db.bilan_jour(conn, numero),
            "dettes": db.lister_dettes(conn, numero),
            "operations": db.lister_operations(conn, numero, limite),
        }


@app.get("/")
def read_root():
    return {
        "message": "Kiriku API is running.",
        "moteur_comprehension": kaddu_bridge.moteur_actif(),
        "whatsapp_configure": bool(os.environ.get("WHATSAPP_TOKEN") and os.environ.get("WHATSAPP_PHONE_NUMBER_ID")),
        "telegram_actif": telegram_bot.actif(),
    }


if __name__ == "__main__":
    import uvicorn
    # Exécuté depuis la racine du dépôt : "backend" doit être un package importable
    # (contracts.* et nlp.* sont importés par backend.pipeline de la même façon).
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    uvicorn.run(
        "backend.main:app", host="0.0.0.0", port=8000, reload=True,
        app_dir=repo_root, reload_dirs=[repo_root], reload_excludes=["*/venv*", "*/.venv*"],
    )
