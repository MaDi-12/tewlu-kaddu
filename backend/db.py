"""Persistance SQLite — TEWLU Kaddu.

Responsable : Khalifa (backend/).

Trois préoccupations :
  - le répertoire client et la dette (`clients`, scopés par commerçant `vendeur_wa_id`),
  - le journal des opérations (`operations`), source du bilan du soir. Une opération
    annulée n'est jamais effacée : elle passe au statut "annulee" (règle de la couche
    Kaddu, cf. docs/integration-kaddu.md §5),
  - l'état d'attente de confirmation vocale (`pending_extractions`) : rien n'est
    écrit dans les deux tables ci-dessus avant le "waaw" du commerçant
    (docs/Dossier-projet.md §2 : "aucune donnée n'est enregistrée sans le waaw").
"""

from __future__ import annotations

import datetime
import difflib
import os
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass

from contracts.models import ExtractionResponse

DB_PATH = os.environ.get("DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "tewlu.db")

# Intentions qui affectent la dette d'un client, et dans quel sens.
_INTENTIONS_CREDIT = {"vente_credit"}       # solde_dette += montant
_INTENTIONS_REMBOURSEMENT = {"remboursement"}  # solde_dette -= montant (plancher 0)

# Correspondance utilisée uniquement pour retrouver un client par similarité
# quand l'ASR déforme un nom (cf. Dossier-projet.md §6 : "Un nom mal orthographié
# peut être rapproché du répertoire clients par similarité").
_SIMILARITE_MIN = 0.72


def _normaliser_nom(nom: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode("ascii")
    return sans_accents.strip().lower()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def get_connection(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(path: str | None = None):
    conn = get_connection(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: str | None = None) -> None:
    conn = get_connection(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vendeurs (
                wa_id TEXT PRIMARY KEY,
                langue_preferee TEXT NOT NULL DEFAULT 'wo',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendeur_wa_id TEXT NOT NULL,
                nom TEXT NOT NULL,
                nom_normalise TEXT NOT NULL,
                solde_dette INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE (vendeur_wa_id, nom_normalise)
            );

            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendeur_wa_id TEXT NOT NULL,
                client_id INTEGER REFERENCES clients(id),
                intention TEXT NOT NULL,
                produit TEXT,
                quantite REAL,
                unite TEXT,
                montant INTEGER,
                langue TEXT NOT NULL,
                methode TEXT NOT NULL,
                created_at TEXT NOT NULL,
                statut TEXT NOT NULL DEFAULT 'active',
                transcription TEXT,
                articles_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_operations_vendeur_date
                ON operations (vendeur_wa_id, created_at);

            CREATE TABLE IF NOT EXISTS pending_extractions (
                vendeur_wa_id TEXT PRIMARY KEY,
                extraction_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        _migrer(conn)
        conn.commit()
    finally:
        conn.close()


# Colonnes ajoutées après la première version du schéma : une base tewlu.db déjà
# créée par la PR #1 doit continuer à fonctionner sans être supprimée.
_COLONNES_AJOUTEES = {
    "operations": {
        "statut": "TEXT NOT NULL DEFAULT 'active'",
        "transcription": "TEXT",
        "articles_json": "TEXT",
    },
}


def _migrer(conn: sqlite3.Connection) -> None:
    for table, colonnes in _COLONNES_AJOUTEES.items():
        existantes = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for nom, definition in colonnes.items():
            if nom not in existantes:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {nom} {definition}")


# ---------------------------------------------------------------------------
# Vendeurs (commerçants)
# ---------------------------------------------------------------------------

def get_or_create_vendeur(conn: sqlite3.Connection, vendeur_wa_id: str) -> str:
    """Retourne la langue préférée du commerçant (par défaut 'wo')."""
    row = conn.execute("SELECT langue_preferee FROM vendeurs WHERE wa_id = ?", (vendeur_wa_id,)).fetchone()
    if row:
        return row["langue_preferee"]
    conn.execute(
        "INSERT INTO vendeurs (wa_id, langue_preferee, created_at) VALUES (?, 'wo', ?)",
        (vendeur_wa_id, _now()),
    )
    return "wo"


def lister_vendeurs(conn: sqlite3.Connection) -> list[str]:
    """Tous les commerçants connus (pour le bilan automatique du soir)."""
    rows = conn.execute("SELECT wa_id FROM vendeurs").fetchall()
    return [row["wa_id"] for row in rows]


def set_langue_preferee(conn: sqlite3.Connection, vendeur_wa_id: str, langue: str) -> None:
    get_or_create_vendeur(conn, vendeur_wa_id)
    conn.execute("UPDATE vendeurs SET langue_preferee = ? WHERE wa_id = ?", (langue, vendeur_wa_id))


# ---------------------------------------------------------------------------
# Clients (répertoire de dettes d'un commerçant)
# ---------------------------------------------------------------------------

def find_client(conn: sqlite3.Connection, vendeur_wa_id: str, nom: str) -> sqlite3.Row | None:
    """Recherche exacte (normalisée), puis par similarité si l'ASR a déformé le nom."""
    nom_normalise = _normaliser_nom(nom)
    row = conn.execute(
        "SELECT * FROM clients WHERE vendeur_wa_id = ? AND nom_normalise = ?",
        (vendeur_wa_id, nom_normalise),
    ).fetchone()
    if row:
        return row

    candidats = conn.execute(
        "SELECT * FROM clients WHERE vendeur_wa_id = ?", (vendeur_wa_id,)
    ).fetchall()
    if not candidats:
        return None
    noms = [c["nom_normalise"] for c in candidats]
    proches = difflib.get_close_matches(nom_normalise, noms, n=1, cutoff=_SIMILARITE_MIN)
    if not proches:
        return None
    return next(c for c in candidats if c["nom_normalise"] == proches[0])


def get_or_create_client(conn: sqlite3.Connection, vendeur_wa_id: str, nom: str) -> sqlite3.Row:
    existant = find_client(conn, vendeur_wa_id, nom)
    if existant:
        return existant
    conn.execute(
        "INSERT INTO clients (vendeur_wa_id, nom, nom_normalise, solde_dette, created_at) "
        "VALUES (?, ?, ?, 0, ?)",
        (vendeur_wa_id, nom, _normaliser_nom(nom), _now()),
    )
    return find_client(conn, vendeur_wa_id, nom)


def solde_client(conn: sqlite3.Connection, vendeur_wa_id: str, nom: str) -> int | None:
    row = find_client(conn, vendeur_wa_id, nom)
    return row["solde_dette"] if row else None


# ---------------------------------------------------------------------------
# Opérations
# ---------------------------------------------------------------------------

@dataclass
class ResultatOperation:
    operation_id: int
    client_nom: str | None
    nouveau_solde: int | None


def enregistrer_operation(
    conn: sqlite3.Connection, vendeur_wa_id: str, extraction: ExtractionResponse
) -> ResultatOperation:
    """Persiste une opération confirmée ("waaw") et met à jour la dette du client.

    Précondition : extraction.est_complet est True (vérifié par l'appelant, le
    pipeline, avant de proposer la confirmation vocale).
    """
    donnees = extraction.donnees
    return inserer_operation(
        conn,
        vendeur_wa_id,
        intention=extraction.intention,
        client=donnees.client,
        montant=donnees.montant,
        langue=extraction.langue,
        methode=extraction.methode,
        produit=donnees.produit,
        quantite=donnees.quantite,
        unite=donnees.unite,
    )


def inserer_operation(
    conn: sqlite3.Connection,
    vendeur_wa_id: str,
    *,
    intention: str,
    client: str | None,
    montant: int | None,
    langue: str,
    methode: str,
    produit: str | None = None,
    quantite: float | None = None,
    unite: str | None = None,
    transcription: str | None = None,
    articles_json: str | None = None,
) -> ResultatOperation:
    """Écrit une opération confirmée et met à jour la dette du client concerné.

    Point d'écriture unique, partagé par le moteur à règles (enregistrer_operation)
    et par la couche Kaddu (backend.carnet.CarnetSQLite.enregistrer).
    """
    client_row = None
    if client:
        client_row = get_or_create_client(conn, vendeur_wa_id, client)
        if intention in _INTENTIONS_CREDIT:
            nouveau_solde = client_row["solde_dette"] + (montant or 0)
        elif intention in _INTENTIONS_REMBOURSEMENT:
            nouveau_solde = max(0, client_row["solde_dette"] - (montant or 0))
        else:
            nouveau_solde = client_row["solde_dette"]
        conn.execute("UPDATE clients SET solde_dette = ? WHERE id = ?", (nouveau_solde, client_row["id"]))
    else:
        nouveau_solde = None

    cur = conn.execute(
        "INSERT INTO operations "
        "(vendeur_wa_id, client_id, intention, produit, quantite, unite, montant, langue, methode, created_at, "
        "transcription, articles_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            vendeur_wa_id,
            client_row["id"] if client_row else None,
            intention,
            produit,
            quantite,
            unite,
            montant,
            langue,
            methode,
            _now(),
            transcription,
            articles_json,
        ),
    )
    return ResultatOperation(
        operation_id=cur.lastrowid,
        client_nom=client_row["nom"] if client_row else None,
        nouveau_solde=nouveau_solde,
    )


def derniere_operation(conn: sqlite3.Connection, vendeur_wa_id: str) -> sqlite3.Row | None:
    """Dernière opération active du commerçant (avec le nom du client), ou None."""
    return conn.execute(
        "SELECT o.*, c.nom AS client_nom FROM operations o LEFT JOIN clients c ON c.id = o.client_id "
        "WHERE o.vendeur_wa_id = ? AND o.statut = 'active' ORDER BY o.id DESC LIMIT 1",
        (vendeur_wa_id,),
    ).fetchone()


def annuler_derniere_operation(conn: sqlite3.Connection, vendeur_wa_id: str) -> sqlite3.Row | None:
    """Annule la dernière opération active du commerçant et son effet sur la dette."""
    op = derniere_operation(conn, vendeur_wa_id)
    if not op:
        return None
    annuler_operation(conn, vendeur_wa_id, op["id"])
    return op


def annuler_operation(conn: sqlite3.Connection, vendeur_wa_id: str, operation_id: int) -> bool:
    """Passe une opération active au statut "annulee" et retire son effet sur la dette.

    On n'efface jamais : l'historique reste consultable. Renvoie False si l'opération
    n'existe pas, appartient à un autre commerçant ou est déjà annulée.
    """
    op = conn.execute(
        "SELECT * FROM operations WHERE id = ? AND vendeur_wa_id = ? AND statut = 'active'",
        (operation_id, vendeur_wa_id),
    ).fetchone()
    if not op:
        return False

    if op["client_id"] is not None:
        if op["intention"] in _INTENTIONS_CREDIT:
            conn.execute(
                "UPDATE clients SET solde_dette = solde_dette - ? WHERE id = ?",
                (op["montant"] or 0, op["client_id"]),
            )
        elif op["intention"] in _INTENTIONS_REMBOURSEMENT:
            conn.execute(
                "UPDATE clients SET solde_dette = solde_dette + ? WHERE id = ?",
                (op["montant"] or 0, op["client_id"]),
            )

    conn.execute("UPDATE operations SET statut = 'annulee' WHERE id = ?", (op["id"],))
    return True


def dette_totale(conn: sqlite3.Connection, vendeur_wa_id: str) -> int:
    """Somme des dettes de tous les clients du commerçant."""
    row = conn.execute(
        "SELECT COALESCE(SUM(solde_dette), 0) AS total FROM clients WHERE vendeur_wa_id = ?", (vendeur_wa_id,)
    ).fetchone()
    return row["total"]


def ventes_du_jour(conn: sqlite3.Connection, vendeur_wa_id: str, jour_iso: str | None = None) -> int:
    """Ventes comptant + ventes à crédit actives du jour (UTC, = heure de Dakar)."""
    bilan = bilan_jour(conn, vendeur_wa_id, jour_iso)
    return bilan["total_ventes"] + bilan["total_credits"]


def lister_operations(conn: sqlite3.Connection, vendeur_wa_id: str, limite: int = 50) -> list[dict]:
    """Journal récent du commerçant (route de démo GET /carnet)."""
    rows = conn.execute(
        "SELECT o.id, o.created_at, o.intention, c.nom AS client, o.montant, o.produit, o.quantite, o.unite, "
        "o.langue, o.methode, o.statut, o.transcription, o.articles_json "
        "FROM operations o LEFT JOIN clients c ON c.id = o.client_id "
        "WHERE o.vendeur_wa_id = ? ORDER BY o.id DESC LIMIT ?",
        (vendeur_wa_id, limite),
    ).fetchall()
    return [dict(row) for row in rows]


def lister_dettes(conn: sqlite3.Connection, vendeur_wa_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT nom, solde_dette FROM clients WHERE vendeur_wa_id = ? AND solde_dette > 0 ORDER BY nom",
        (vendeur_wa_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def bilan_jour(conn: sqlite3.Connection, vendeur_wa_id: str, jour_iso: str | None = None) -> dict:
    """Agrège les opérations du jour (UTC) pour le bilan vocal du soir."""
    jour_iso = jour_iso or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    rows = conn.execute(
        "SELECT intention, montant FROM operations "
        "WHERE vendeur_wa_id = ? AND statut = 'active' AND substr(created_at, 1, 10) = ?",
        (vendeur_wa_id, jour_iso),
    ).fetchall()

    bilan = {
        "total_ventes": 0,
        "total_credits": 0,
        "total_remboursements": 0,
        "total_achats": 0,
        "total_depenses": 0,
        "nb_operations": len(rows),
    }
    cles_par_intention = {
        "vente": "total_ventes",
        "vente_credit": "total_credits",
        "remboursement": "total_remboursements",
        "achat": "total_achats",
        "depense": "total_depenses",
    }
    for row in rows:
        cle = cles_par_intention.get(row["intention"])
        if cle:
            bilan[cle] += row["montant"] or 0
    return bilan


# ---------------------------------------------------------------------------
# Confirmation en attente ("waaw" / "déet")
# ---------------------------------------------------------------------------

def save_pending(conn: sqlite3.Connection, vendeur_wa_id: str, extraction: ExtractionResponse) -> None:
    conn.execute(
        "INSERT INTO pending_extractions (vendeur_wa_id, extraction_json, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(vendeur_wa_id) DO UPDATE SET extraction_json = excluded.extraction_json, "
        "created_at = excluded.created_at",
        (vendeur_wa_id, extraction.model_dump_json(), _now()),
    )


def get_pending(conn: sqlite3.Connection, vendeur_wa_id: str) -> ExtractionResponse | None:
    row = conn.execute(
        "SELECT extraction_json FROM pending_extractions WHERE vendeur_wa_id = ?", (vendeur_wa_id,)
    ).fetchone()
    if not row:
        return None
    return ExtractionResponse.model_validate_json(row["extraction_json"])


def clear_pending(conn: sqlite3.Connection, vendeur_wa_id: str) -> None:
    conn.execute("DELETE FROM pending_extractions WHERE vendeur_wa_id = ?", (vendeur_wa_id,))
