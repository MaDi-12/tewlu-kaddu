"""Client WhatsApp Cloud API (Meta Graph API) — TEWLU Kaddu.

Responsable : Khalifa (backend/).

Couvre le strict nécessaire au pipeline P1 (docs/Dossier-projet.md §4) :
  - vérifier le handshake webhook (GET, une fois à la configuration),
  - télécharger une note vocale entrante (media_id -> bytes),
  - envoyer une réponse, en texte ou en note vocale (TTS Kiriku -> OGG/Opus -> media).

Les identifiants sont lus dans l'environnement à l'appel (pas à l'import), pour
rester configurables en test sans recharger le module.
"""

from __future__ import annotations

import os

import httpx

GRAPH_HOST = "https://graph.facebook.com"
TIMEOUT_SECONDES = 30


def _config() -> tuple[str, str, str]:
    token = os.environ.get("WHATSAPP_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    if not token or not phone_id:
        raise RuntimeError(
            "WHATSAPP_TOKEN et WHATSAPP_PHONE_NUMBER_ID doivent être renseignés dans backend/.env"
        )
    version = os.environ.get("WHATSAPP_API_VERSION", "v21.0")
    return token, phone_id, version


def _base_url(phone_id: str, version: str) -> str:
    return f"{GRAPH_HOST}/{version}/{phone_id}"


def _verifier(reponse: httpx.Response) -> None:
    """raise_for_status, avec le message d'erreur de Meta (ex: #131030 destinataire non
    autorisé sur un numéro de test) au lieu d'un simple « 400 Bad Request »."""
    if reponse.is_success:
        return
    try:
        erreur = reponse.json().get("error", {})
        detail = f"(#{erreur.get('code')}) {erreur.get('message')}"
    except ValueError:
        detail = reponse.text[:300]
    raise httpx.HTTPStatusError(
        f"WhatsApp {reponse.status_code} sur {reponse.request.url.path} : {detail}",
        request=reponse.request, response=reponse,
    )


def verifier_handshake(mode: str, verify_token_recu: str, challenge: str) -> str | None:
    """Renvoie `challenge` si le handshake Meta (GET /webhook) est valide, sinon None."""
    verify_token_attendu = os.environ.get("WHATSAPP_VERIFY_TOKEN")
    if mode == "subscribe" and verify_token_attendu and verify_token_recu == verify_token_attendu:
        return challenge
    return None


async def telecharger_media(media_id: str) -> bytes:
    """media_id WhatsApp -> contenu brut (ex: OGG/Opus d'une note vocale)."""
    token, _, version = _config()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDES) as client:
        meta = await client.get(f"{GRAPH_HOST}/{version}/{media_id}", headers=headers)
        _verifier(meta)
        url = meta.json()["url"]

        contenu = await client.get(url, headers=headers)
        _verifier(contenu)
        return contenu.content


async def envoyer_texte(destinataire_wa_id: str, corps: str) -> None:
    token, phone_id, version = _config()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": destinataire_wa_id,
        "type": "text",
        "text": {"body": corps},
    }
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDES) as client:
        reponse = await client.post(f"{_base_url(phone_id, version)}/messages", headers=headers, json=payload)
        _verifier(reponse)


async def envoyer_audio(destinataire_wa_id: str, audio_ogg: bytes) -> None:
    """Téléverse un OGG/Opus (audio_utils.wav_vers_ogg_opus) puis l'envoie comme note vocale.

    WhatsApp refuse le WAV : convertir la sortie du TTS avant d'appeler cette fonction.
    """
    token, phone_id, version = _config()
    headers = {"Authorization": f"Bearer {token}"}
    base_url = _base_url(phone_id, version)

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDES) as client:
        upload = await client.post(
            f"{base_url}/media",
            headers=headers,
            data={"messaging_product": "whatsapp", "type": "audio/ogg"},
            files={"file": ("reponse.ogg", audio_ogg, "audio/ogg")},
        )
        _verifier(upload)
        media_id = upload.json()["id"]

        payload = {
            "messaging_product": "whatsapp",
            "to": destinataire_wa_id,
            "type": "audio",
            "audio": {"id": media_id},
        }
        reponse = await client.post(f"{base_url}/messages", headers=headers, json=payload)
        _verifier(reponse)
