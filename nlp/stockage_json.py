"""Stockage des opérations dans un fichier JSON (un fichier par commerçant).

Même interface que pipeline.BaseTest (SQLite) : enregistrer, derniere, annuler, dette, ventes_du_jour.
Pratique pour démarrer l'intégration WhatsApp sans base de données. Pour plusieurs serveurs ou beaucoup
de commerçants, passer à une vraie base (voir docs/integration-kaddu.md).

Format du fichier :
    {"operations": [{"id": 1, "date": "...", "langue": "wo", "intention": "vente_credit", "client": "Awa",
                     "montant_fcfa": 3000, ..., "statut": "active", "articles": [{...}]}]}
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import date
from pathlib import Path


class BaseJSON:
    def __init__(self, chemin: Path | str):
        self.chemin = Path(chemin)
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self._verrou = threading.Lock()

    # -- lecture / écriture du fichier -----------------------------------------------
    def _lire(self) -> dict:
        if not self.chemin.exists():
            return {"operations": []}
        with open(self.chemin, encoding="utf-8") as f:
            return json.load(f)

    def _ecrire(self, donnees: dict) -> None:
        """Écriture atomique : un fichier temporaire puis remplacement (pas de fichier à moitié écrit)."""
        fd, tmp = tempfile.mkstemp(dir=self.chemin.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(donnees, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.chemin)

    def _actives(self) -> list[dict]:
        return [o for o in self._lire()["operations"] if o.get("statut") == "active"]

    # -- les 5 méthodes appelées par Conversation ---------------------------------------
    def enregistrer(self, operation: dict) -> int:
        with self._verrou:
            donnees = self._lire()
            nouvel_id = max((o["id"] for o in donnees["operations"]), default=0) + 1
            donnees["operations"].append({"id": nouvel_id, **operation})
            self._ecrire(donnees)
        return nouvel_id

    def derniere(self) -> dict | None:
        actives = self._actives()
        if not actives:
            return None
        o = actives[-1]
        return {"id": o["id"], "intention": o["intention"], "client": o.get("client"),
                "montant_fcfa": o.get("montant_fcfa")}

    def annuler(self, operation_id) -> None:
        with self._verrou:
            donnees = self._lire()
            for o in donnees["operations"]:
                if o["id"] == operation_id:
                    o["statut"] = "annulee"
            self._ecrire(donnees)

    def dette(self, client: str | None) -> int:
        def meme_client(o):
            return client is None or (o.get("client") or "").casefold() == client.casefold()
        actives = [o for o in self._actives() if meme_client(o)]
        pris = sum(o.get("montant_fcfa") or 0 for o in actives if o["intention"] == "vente_credit")
        rendu = sum(o.get("montant_fcfa") or 0 for o in actives if o["intention"] == "remboursement")
        return pris - rendu

    def ventes_du_jour(self) -> int:
        aujourd_hui = date.today().isoformat()
        return sum(o.get("montant_fcfa") or 0 for o in self._actives()
                   if o["intention"] in ("vente", "vente_credit") and o["date"].startswith(aujourd_hui))

    # -- pour l'affichage -------------------------------------------------------------
    def operations(self) -> list[dict]:
        return self._lire()["operations"]
