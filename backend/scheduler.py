"""Bilan vocal automatique du soir — TEWLU Kaddu.

Responsable : Khalifa (backend/).

Dossier-projet.md §2, tableau "Deux résumés, deux rôles" : le "Bilan du
jour" est envoyé en note vocale WhatsApp chaque soir, automatiquement, sans
action du commerçant. Fonctionnalité P1 (§4). `backend.db.bilan_jour` et
`backend.templates.phrase_bilan` existent déjà (PR précédente) ; ce module
se contente de les déclencher à heure fixe pour chaque commerçant connu.

Limite connue : l'heure de déclenchement est celle du SERVEUR, pas celle de
chaque commerçant — il n'y a pas de fuseau horaire par vendeur dans le
schéma actuel. Sans impact pour la démo (tous les commerçants visés sont à
Dakar), à corriger avant un déploiement multi-fuseaux.

Un commerçant sans aucune opération ce jour-là ne reçoit rien : un bilan à
0 F chaque soir n'apporterait rien et risquerait de lasser.
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend import db, templates
from backend.pipeline import repondre

logger = logging.getLogger("tewlu.scheduler")

HEURE_BILAN = int(os.environ.get("BILAN_HEURE", "21"))

_scheduler: AsyncIOScheduler | None = None


async def envoyer_bilans_du_jour() -> None:
    """Calcule le bilan de chaque commerçant et l'envoie, sauf si rien ne s'est passé."""
    with db.session() as conn:
        a_envoyer = []
        for vendeur_wa_id in db.lister_vendeurs(conn):
            langue = db.get_or_create_vendeur(conn, vendeur_wa_id)
            bilan = db.bilan_jour(conn, vendeur_wa_id)
            if bilan["nb_operations"] > 0:
                a_envoyer.append((vendeur_wa_id, langue, bilan))

    for vendeur_wa_id, langue, bilan in a_envoyer:
        try:
            phrase = templates.phrase_bilan(langue, bilan)
            await repondre(vendeur_wa_id, phrase, langue)
        except Exception:
            logger.exception("Échec de l'envoi du bilan du soir à %s", vendeur_wa_id)


def demarrer() -> AsyncIOScheduler:
    """Démarre le job cron (appelé depuis le lifespan de main.py). Idempotent."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        envoyer_bilans_du_jour,
        CronTrigger(hour=HEURE_BILAN, minute=0),
        id="bilan_du_soir",
    )
    _scheduler.start()
    logger.info("Scheduler du bilan du soir démarré (déclenchement à %dh, heure serveur).", HEURE_BILAN)
    return _scheduler


def arreter() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
