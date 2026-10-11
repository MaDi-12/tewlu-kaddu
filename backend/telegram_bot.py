"""Boucle de réception Telegram (long polling) — TEWLU Kaddu.

Responsable : Khalifa (backend/).

Démarrée par le lifespan de main.py si TELEGRAM_BOT_TOKEN est renseigné.
Le bot va chercher les messages chez Telegram (getUpdates) : rien à exposer
sur Internet. Les messages sont traités un par un, dans l'ordre d'arrivée,
pour qu'un "waaw" ne soit jamais traité avant la vente qu'il confirme.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from backend import pipeline, telegram_client

logger = logging.getLogger("tewlu.telegram")

ATTENTE_POLLING = 30   # secondes que Telegram garde la requête ouverte s'il n'y a rien
PAUSE_APRES_ERREUR = 5

ACCUEIL = (
    "Kaddu : envoie une note vocale pour noter une vente, un crédit ou un remboursement, "
    "ou pour demander une dette ou le bilan du jour.\n"
    "Langue : écris wolof, pulaar ou seereer."
)

_tache: asyncio.Task | None = None


async def traiter_update(update: dict) -> None:
    converti = telegram_client.message_depuis_update(update)
    if converti is None:
        return
    message, vendeur = converti
    texte = message.get("text", {}).get("body", "").strip()
    if texte.startswith("/start") or texte.startswith("/aide"):
        await telegram_client.envoyer_texte(vendeur, ACCUEIL)
        return
    await pipeline.handle_incoming_message(message, vendeur)


async def _boucle() -> None:
    decalage = None
    async with httpx.AsyncClient(timeout=ATTENTE_POLLING + 10) as client:
        # getUpdates est refusé tant qu'un webhook est déclaré pour ce bot.
        await telegram_client.appeler(client, "deleteWebhook")
        moi = await telegram_client.appeler(client, "getMe")
        logger.info("Bot Telegram @%s à l'écoute (long polling).", moi.get("username"))
        while True:
            try:
                updates = await telegram_client.appeler(
                    client, "getUpdates",
                    data={"timeout": ATTENTE_POLLING, "offset": decalage or "",
                          "allowed_updates": '["message"]'},
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("getUpdates a échoué, nouvel essai dans %ss", PAUSE_APRES_ERREUR)
                await asyncio.sleep(PAUSE_APRES_ERREUR)
                continue
            for update in updates:
                decalage = update["update_id"] + 1
                try:
                    await traiter_update(update)
                except Exception:
                    # handle_incoming_message gère déjà ses erreurs ; filet de sécurité
                    # pour que la boucle ne s'arrête jamais sur un message.
                    logger.exception("Update Telegram %s non traité", update.get("update_id"))


async def _boucle_protegee() -> None:
    try:
        await _boucle()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Bot Telegram arrêté (token invalide ?)")


def demarrer() -> asyncio.Task | None:
    """Lance la boucle en tâche de fond si TELEGRAM_BOT_TOKEN est renseigné. Idempotent."""
    global _tache
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        return None
    if _tache is None or _tache.done():
        _tache = asyncio.get_running_loop().create_task(_boucle_protegee())
    return _tache


def actif() -> bool:
    return _tache is not None and not _tache.done()


async def arreter() -> None:
    global _tache
    if _tache is not None:
        _tache.cancel()
        try:
            await _tache
        except asyncio.CancelledError:
            pass
        _tache = None
