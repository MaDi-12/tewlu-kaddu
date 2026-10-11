"""Branchement de la couche Kaddu (nlp/kaddu.py, Marème) sur le backend — TEWLU Kaddu.

Responsable : Khalifa (backend/).

Deux moteurs de compréhension, choisis par NLU_MOTEUR (backend/.env) :
  - "kaddu"  : couche de Marème (LLM via OpenRouter -> atlas -> conversation avec
               confirmation, phrases de reponses.csv, wolof/pulaar/sérère) ;
  - "regles" : ancien moteur à règles (nlp/extractor.py) + machine à états de
               backend/pipeline.py. Ne demande ni clé ni réseau ;
  - "auto"   : (défaut) "kaddu" si une clé OPENROUTER_API_KEY est trouvée, sinon
               "regles". La démo ne tombe jamais en panne faute de clé.

Kaddu garde l'état de ses conversations dans KADDU_DOSSIER (JSON, hors Git) et
écrit les opérations confirmées dans la base du backend (backend.carnet).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from backend.carnet import CarnetSQLite

logger = logging.getLogger("tewlu.kaddu")

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_NLP = RACINE / "nlp"
DOSSIER_KADDU_DEFAUT = DOSSIER_NLP / "data" / "whatsapp"   # ignoré par Git (.gitignore)

MOTEURS = ("auto", "kaddu", "regles")


@dataclass
class Reponse:
    """Ce que le pipeline envoie au commerçant, quel que soit le moteur."""
    texte: str                  # affiché / envoyé en message texte
    texte_tts: str              # prononcé par le TTS Kiriku
    modele_tts: str             # modèle TTS Kiriku à utiliser
    moteur: str                 # "kaddu" | "regles"
    details: dict = field(default_factory=dict)   # sortie complète de Kaddu.traiter (état, issue...)


_kaddu = None
_verrou = threading.Lock()


def _preparer_imports() -> None:
    # nlp/kaddu.py importe ses voisins en absolu (atlas, pipeline, extraire_llm) :
    # nlp/ et nlp/outils/ doivent être dans sys.path AVANT son import (sinon
    # « No module named 'extraire_llm' » quand rien n'a importé pipeline avant).
    for dossier in (DOSSIER_NLP / "outils", DOSSIER_NLP):
        if str(dossier) not in sys.path:
            sys.path.insert(0, str(dossier))


def cle_llm_presente() -> bool:
    """Clé OpenRouter trouvée (variable d'environnement, backend/.env ou .env racine)."""
    try:
        _preparer_imports()
        import extraire_llm

        extraire_llm.cle_openrouter()
        return True
    except Exception:
        return False


def moteur_actif() -> str:
    choix = (os.environ.get("NLU_MOTEUR") or "auto").strip().lower()
    if choix not in MOTEURS:
        logger.warning("NLU_MOTEUR=%r inconnu (attendu : %s) : mode auto.", choix, ", ".join(MOTEURS))
        choix = "auto"
    if choix == "auto":
        return "kaddu" if cle_llm_presente() else "regles"
    return choix


def kaddu():
    """L'instance Kaddu unique du serveur (créée au premier message)."""
    global _kaddu
    with _verrou:
        if _kaddu is None:
            _preparer_imports()
            from kaddu import Kaddu
            from extraire_llm import MODELES

            dossier = os.environ.get("KADDU_DOSSIER") or DOSSIER_KADDU_DEFAUT
            modele = os.environ.get("KADDU_MODELE") or MODELES[0]
            _kaddu = Kaddu(dossier, modele=modele, fabrique_stockage=CarnetSQLite)
            logger.info("Couche Kaddu chargée (modèle %s, conversations dans %s).", modele, dossier)
        return _kaddu


def traiter(vendeur_wa_id: str, texte: str, langue: str) -> Reponse:
    """Un message transcrit -> réponse de Kaddu (bloquant : appel LLM)."""
    r = kaddu().traiter(vendeur_wa_id, texte, langue)
    return Reponse(texte=r["reponse"], texte_tts=r["texte_tts"], modele_tts=r["voix_tts"],
                   moteur="kaddu", details=r)

