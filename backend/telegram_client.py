"""Client Telegram Bot API — canal de repli de WhatsApp — TEWLU Kaddu.

Responsable : Khalifa (backend/).

Mêmes fonctions que backend.whatsapp_client (telecharger_media, envoyer_texte,
envoyer_audio) : le pipeline choisit le canal d'après l'identifiant du
commerçant (pipeline.canal_pour). Un commerçant Telegram est identifié par
"tg-<chat_id>" : son carnet et sa conversation Kaddu ne se mélangent pas avec
ceux d'un numéro WhatsApp.

Les messages arrivent par long polling (backend.telegram_bot) : pas besoin
d'URL publique ni de ngrok, seulement d'un TELEGRAM_BOT_TOKEN (@BotFather).
"""

from __future__ import annotations

import asyncio
import os

import httpx

API = "https://api.telegram.org"
PREFIXE = "tg-"
TIMEOUT_SECONDES = 30
# Une connexion qui échoue avant l'envoi (coupure réseau, TLS) est rejouée : la
# requête n'est jamais arrivée chez Telegram, donc pas de risque de doublon.
TENTATIVES = 3
_ERREURS_DE_CONNEXION = (httpx.ConnectError, httpx.ConnectTimeout)


async def _avec_reessai(requete):
    for tentative in range(TENTATIVES):
        try:
            return await requete()
        except _ERREURS_DE_CONNEXION:
            if tentative == TENTATIVES - 1:
                raise
            await asyncio.sleep(1 + tentative)


def _token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN doit être renseigné dans backend/.env (token donné par @BotFather)")
    return token


def vendeur_id(chat_id: int | str) -> str:
    return f"{PREFIXE}{chat_id}"


def est_telegram(vendeur: str) -> bool:
    return vendeur.startswith(PREFIXE)


def _chat_id(vendeur: str) -> str:
    return vendeur[len(PREFIXE):] if est_telegram(vendeur) else vendeur


async def appeler(client: httpx.AsyncClient, methode: str, **kwargs):
    """Appelle une méthode de la Bot API et renvoie `result` (erreur explicite sinon)."""
    reponse = await _avec_reessai(lambda: client.post(f"{API}/bot{_token()}/{methode}", **kwargs))
    try:
        donnees = reponse.json()
    except ValueError:
        reponse.raise_for_status()
        raise
    if not donnees.get("ok"):
        raise RuntimeError(f"Telegram {methode} : {donnees.get('description', reponse.status_code)}")
    return donnees["result"]


async def telecharger_media(file_id: str) -> bytes:
    """file_id Telegram -> contenu brut (OGG/Opus pour une note vocale)."""
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDES) as client:
        fichier = await appeler(client, "getFile", data={"file_id": file_id})
        contenu = await _avec_reessai(lambda: client.get(f"{API}/file/bot{_token()}/{fichier['file_path']}"))
        contenu.raise_for_status()
        return contenu.content


async def envoyer_texte(vendeur: str, corps: str) -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDES) as client:
        await appeler(client, "sendMessage", data={"chat_id": _chat_id(vendeur), "text": corps})


async def envoyer_audio(vendeur: str, audio_ogg: bytes) -> None:
    """Envoie une note vocale (Telegram exige de l'OGG/Opus, comme WhatsApp)."""
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDES) as client:
        await appeler(
            client, "sendVoice",
            data={"chat_id": _chat_id(vendeur)},
            files={"voice": ("reponse.ogg", audio_ogg, "audio/ogg")},
        )


def message_depuis_update(update: dict) -> tuple[dict, str] | None:
    """Update Telegram -> (message au format du pipeline, identifiant du commerçant).

    Le pipeline a été écrit pour les messages WhatsApp ({"type": "audio", "audio":
    {"id": ...}} ou {"type": "text", "text": {"body": ...}}) : on traduit vers ce
    format plutôt que de dupliquer la logique. None pour tout ce qui n'est pas un
    message privé ou de groupe (modifications, boutons, etc.).
    """
    message = update.get("message")
    if not message or "chat" not in message:
        return None
    vendeur = vendeur_id(message["chat"]["id"])
    identifiant = f"tg-{update['update_id']}"
    media = message.get("voice") or message.get("audio")
    if media:
        return {"id": identifiant, "type": "audio", "audio": {"id": media["file_id"]}}, vendeur
    if "text" in message:
        return {"id": identifiant, "type": "text", "text": {"body": message["text"]}}, vendeur
    return {"id": identifiant, "type": "autre"}, vendeur
