"""Point d'entrée unique de la couche Kaddu pour l'intégration (WhatsApp, API...).

    from nlp.kaddu import Kaddu
    kaddu = Kaddu("nlp/data/whatsapp")                     # stockage JSON par défaut
    r = kaddu.traiter("221770000000", "Awa jënd na ñaari paket sukar, ñetti junni, daf koo leb", "wo")
    r["reponse"]     -> texte à afficher / envoyer
    r["texte_tts"]   -> même texte préparé pour le TTS (minuscules, x -> kh)
    r["voix_tts"]    -> modèle TTS à donner à POST /tts

Une conversation par numéro, gardée sur disque entre deux messages (dossier/conversations/<numero>.json).
Une fois terminée (« c'est noté », abandon...), le message suivant ouvre une nouvelle conversation.
Une conversation sans message depuis `expiration_minutes` est abandonnée et la suivante repart de zéro.

Essai dans le terminal (depuis la racine du dépôt) :
    python nlp/kaddu.py --langue ff
"""
from __future__ import annotations

import json
import re
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "outils"))   # extraire_llm (correctif de Khalifa, PR #3)
import atlas  # noqa: E402
from extraire_llm import MODELES  # noqa: E402
from pipeline import (ASR_MODELE, LANGUE_SECOURS, LANGUES, VOIX_TTS, BaseTest, Conversation, Stockage,
                      texte_pour_tts)  # noqa: E402
from stockage_json import BaseJSON  # noqa: E402

# Codes de l'ASR Kiriku -> codes de Kaddu (on accepte les deux)
CODES_ASR = {v["asr"]: k for k, v in LANGUES.items()}


def langue_kaddu(code: str) -> str:
    """'wo' / 'ff' / 'srr' ou code ASR 'pu' / 'se' -> code Kaddu."""
    code = (code or "wo").lower()
    if code in LANGUES:
        return code
    if code in CODES_ASR:
        return CODES_ASR[code]
    raise ValueError(f"Langue inconnue : {code}")


class Kaddu:
    def __init__(self, dossier: Path | str = Path(__file__).parent / "data" / "whatsapp",
                 modele: str = MODELES[0], stockage: str = "json", expiration_minutes: int = 30,
                 fabrique_stockage: Callable[[str], Stockage] | None = None, langue_secours: str = LANGUE_SECOURS):
        """
        dossier            : où ranger conversations, carnets, sessions et mots à valider
        stockage           : "json" (un fichier par commerçant) ou "sqlite" (une base par commerçant)
        langue_secours     : langue des phrases pas encore traduites en pulaar / sérère ("wo" ou "fr")
        fabrique_stockage  : pour brancher une vraie base : fonction numero -> objet Stockage
                             (5 méthodes, voir pipeline.Stockage). Remplace `stockage` si donnée.
        """
        self.dossier = Path(dossier)
        self.modele, self.type_stockage = modele, stockage
        self.expiration = timedelta(minutes=expiration_minutes)
        self.fabrique_stockage = fabrique_stockage
        self.langue_secours = langue_secours
        (self.dossier / "conversations").mkdir(parents=True, exist_ok=True)
        self.file_termes = atlas.FileTermes(self.dossier / "termes_a_valider.db")
        self.sessions = self.dossier / "sessions.csv"
        self._verrous: dict[str, threading.Lock] = {}
        self._verrou_global = threading.Lock()

    # -- stockage -----------------------------------------------------------------------
    @staticmethod
    def _id(numero: str) -> str:
        propre = re.sub(r"[^0-9A-Za-z+_-]", "", str(numero))
        if not propre:
            raise ValueError("Numéro vide")
        return propre

    def stockage(self, numero: str) -> Stockage:
        """Le carnet du commerçant (ses opérations)."""
        if self.fabrique_stockage:
            return self.fabrique_stockage(numero)
        nom = self._id(numero)
        if self.type_stockage == "sqlite":
            return BaseTest(self.dossier / "commercants" / f"{nom}.db")
        return BaseJSON(self.dossier / "commercants" / f"{nom}.json")

    def _chemin_conversation(self, numero: str) -> Path:
        return self.dossier / "conversations" / f"{self._id(numero)}.json"

    def _verrou(self, numero: str) -> threading.Lock:
        with self._verrou_global:
            return self._verrous.setdefault(self._id(numero), threading.Lock())

    def conversation(self, numero: str) -> Conversation | None:
        """La conversation en cours de ce numéro (None s'il n'y en a pas)."""
        chemin = self._chemin_conversation(numero)
        if not chemin.exists():
            return None
        d = json.loads(chemin.read_text(encoding="utf-8"))
        return Conversation.depuis_dict(d, self.stockage(numero), self.file_termes, self.sessions)

    def oublier(self, numero: str) -> None:
        """Abandonne la conversation en cours de ce numéro (le carnet n'est pas touché)."""
        self._chemin_conversation(numero).unlink(missing_ok=True)

    # -- le point d'entrée --------------------------------------------------------------------
    def traiter(self, numero: str, texte: str, langue: str = "wo", source: str = "whatsapp") -> dict:
        """Un message du commerçant (texte de l'ASR) -> la réponse de Kaddu et l'état de la conversation."""
        langue = langue_kaddu(langue)
        with self._verrou(numero):
            conv = self.conversation(numero)
            nouvelle = (conv is None or conv.etat == "termine" or conv.langue != langue
                        or datetime.now() - datetime.fromisoformat(conv.derniere_activite) > self.expiration)
            if nouvelle:
                conv = Conversation(self.modele, self.stockage(numero), langue, source, self.file_termes,
                                    self.sessions, self.langue_secours)
            reponse = conv.recevoir(texte)
            chemin = self._chemin_conversation(numero)
            chemin.write_text(json.dumps(conv.vers_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        derniere = conv.historique[-1]["details"] if conv.historique else {}
        return {
            "numero": numero, "langue": langue, "reponse": reponse, "texte_tts": texte_pour_tts(reponse),
            "voix_tts": VOIX_TTS[LANGUES[langue]["voix"]], "langue_asr": LANGUES[langue]["asr"],
            "modele_asr": ASR_MODELE, "etat": conv.etat, "termine": conv.etat == "termine", "issue": conv.issue,
            "operation_id": conv.operation_id if conv.issue == "enregistre" else None,
            "nouvelle_conversation": nouvelle, "modele_reponse": derniere.get("modele_reponse"),
            "langue_phrase": derniere.get("langue_phrase"),
        }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Discuter avec Kaddu dans le terminal (texte).")
    p.add_argument("--numero", default="test_terminal")
    p.add_argument("--langue", default="wo", help="wo, ff ou srr (ou pu / se)")
    p.add_argument("--dossier", default=str(Path(__file__).parent / "data" / "whatsapp_essai"))
    args = p.parse_args()
    kaddu = Kaddu(args.dossier)
    print(f"Kaddu ({LANGUES[langue_kaddu(args.langue)]['nom']}) — écris un message, Ctrl+C pour quitter.")
    while True:
        try:
            message = input("\nCommerçant > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if message:
            r = kaddu.traiter(args.numero, message, args.langue, source="terminal")
            print(f"Kaddu      > {r['reponse']}")
            if r["termine"]:
                print(f"           (conversation terminée : {r['issue']})")
