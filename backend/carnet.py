"""Carnet du commerçant pour la couche Kaddu (nlp/kaddu.py, Marème) — TEWLU Kaddu.

Responsable : Khalifa (backend/).

`nlp.pipeline.Conversation` n'appelle que 5 méthodes sur le carnet (protocole
`nlp.pipeline.Stockage`, docs/integration-kaddu.md §5, option B). Cette classe
les fournit au-dessus de la base SQLite du backend (backend/db.py) : ce que
Kaddu enregistre est donc visible du bilan du soir (scheduler.py), de la dette
client (fuzzy match des noms déformés par l'ASR) et de la route GET /carnet.

Chaque appel ouvre sa propre session : Kaddu est appelé depuis un thread de
travail (asyncio.to_thread), une connexion SQLite ne doit pas changer de thread.
"""

from __future__ import annotations

import json

from backend import db

METHODE_KADDU = "llm"   # contracts.models.MethodeExtraction


class CarnetSQLite:
    def __init__(self, vendeur_wa_id: str):
        self.vendeur_wa_id = vendeur_wa_id

    def enregistrer(self, operation: dict) -> int:
        """`operation` = nlp.pipeline.operation_a_enregistrer(conv) (format documenté §5)."""
        articles = operation.get("articles") or []
        # Les colonnes produit/quantite/unite ne décrivent qu'un article : elles ne sont
        # remplies que s'il n'y en a qu'un. La liste complète va dans articles_json.
        seul = articles[0] if len(articles) == 1 else {}
        with db.session() as conn:
            db.get_or_create_vendeur(conn, self.vendeur_wa_id)
            resultat = db.inserer_operation(
                conn,
                self.vendeur_wa_id,
                intention=operation["intention"],
                client=operation.get("client"),
                montant=operation.get("montant_fcfa"),
                langue=operation.get("langue") or "wo",
                methode=METHODE_KADDU,
                produit=seul.get("produit") or seul.get("produit_dit"),
                quantite=seul.get("quantite"),
                unite=seul.get("unite"),
                transcription=operation.get("transcription"),
                articles_json=json.dumps(articles, ensure_ascii=False) if articles else None,
            )
        return resultat.operation_id

    def derniere(self) -> dict | None:
        with db.session() as conn:
            op = db.derniere_operation(conn, self.vendeur_wa_id)
        if op is None:
            return None
        return {"id": op["id"], "intention": op["intention"], "client": op["client_nom"],
                "montant_fcfa": op["montant"]}

    def annuler(self, operation_id) -> None:
        with db.session() as conn:
            db.annuler_operation(conn, self.vendeur_wa_id, int(operation_id))

    def dette(self, client: str | None) -> int:
        with db.session() as conn:
            if client is None:
                return db.dette_totale(conn, self.vendeur_wa_id)
            return db.solde_client(conn, self.vendeur_wa_id, client) or 0

    def ventes_du_jour(self) -> int:
        with db.session() as conn:
            return db.ventes_du_jour(conn, self.vendeur_wa_id)
