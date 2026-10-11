"""Pipeline de conversation TEWLU Kaddu (wolof, pulaar, sérère), du message reçu jusqu'à l'enregistrement.

    message -> LLM (extraire) -> atlas (rapprocher, calcul des montants) -> questions si besoin
            -> confirmation -> oui : enregistré | non : correction (2 fois maximum)

Répartition des rôles :
    LLM    : comprendre (intention, client, articles, prix, sens des mots, type de réponse : oui/non/correction)
    Code   : vérifier et calculer (montants par les règles, additions, atlas), décider des questions
    Modèles de phrases : toutes les réponses de Kaddu (nlp/data/atlas/reponses.csv), jamais écrites par le LLM

Langues : wo (wolof), ff (pulaar), srr (sérère). Voir LANGUES (codes ASR, voix TTS, mots oui / non).

Stockage : Conversation n'appelle que 5 méthodes du stockage (voir la classe Stockage ci-dessous).
    BaseTest (SQLite, ici) et stockage_json.BaseJSON (un fichier JSON) les fournissent ; une vraie base
    (PostgreSQL...) n'a qu'à fournir les mêmes. Voir docs/integration-kaddu.md.
Chaque conversation terminée est notée dans un fichier CSV de sessions (pour les métriques).
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "outils"))
import atlas  # noqa: E402
from extraire_llm import Extraction, controler, corriger, extraire  # noqa: E402

DATA = Path(__file__).parent / "data"
BASE_TEST = DATA / "base_test.db"
SESSIONS = DATA / "sessions_test.csv"
MAX_CORRECTIONS = 2   # le commerçant dit « non » puis corrige : 2 fois au maximum
MAX_MESSAGES = 8      # sécurité : au-delà, on arrête la conversation
# Ce que Kaddu sait faire pour l'instant. Les autres intentions (dépense) reçoivent « pas encore disponible ».
# « achat » = entrée de stock (scénario 001 du document Phases Tests de Ndèye) : enregistré, sans effet sur
# les ventes ni les dettes, prix facultatif.
INTENTIONS_ACTIVES = {"vente", "vente_credit", "remboursement", "achat", "question_dette", "question_bilan",
                      "annulation"}
AVEC_CLIENT = ("vente_credit", "remboursement")
MONTANT_FACULTATIF = ("achat",)
QUESTIONS_OUI_NON = ("proposer_mot", "confirmer", "confirmer_annulation")

VOIX_TTS = {"Wolof": "AIHubSN/Kiriku-Wolof-TTS", "Pulaar": "AIHubSN/Kiriku-Pulaar-TTS"}
ASR_MODELE = "AIHubSN/M-Kiriku-ASR"
# Codes : atlas / LLM (wo, ff, srr) ≠ ASR Kiriku (wo, pu, se). Pas de TTS sérère : voix pulaar.
LANGUES = {
    "wo": {"nom": "Wolof", "asr": "wo", "voix": "Wolof", "oui": "waaw", "non": "déet"},
    "ff": {"nom": "Pulaar", "asr": "pu", "voix": "Pulaar", "oui": "eey", "non": "alaa"},
    "srr": {"nom": "Sérère", "asr": "se", "voix": "Wolof", "oui": "iyo", "non": "xa'a"},
}
# Langue des RÉPONSES de Kaddu, quand elle diffère de la langue comprise. Sérère : pas de voix sérère, et la voix
# pulaar donnait un accent étrange (retour de Ndèye) -> Kaddu comprend le sérère mais répond en wolof (texte et voix).
# Les phrases sérère de reponses.csv sont gardées : retirer "srr" d'ici suffit pour les réutiliser.
LANGUE_REPONSE = {"srr": "wo"}


def _mots_confirmation() -> dict[str, tuple[set, set]]:
    """Mots oui / non par langue : concepts « Oui », « C'est ça » et « Non » de l'atlas officiel, plus le français."""
    mots = {lang: ({atlas.normaliser(m) for m in ("oui", "ok", LANGUES[lang]["oui"])},
                   {atlas.normaliser(m) for m in ("non", "no", LANGUES[lang]["non"])}) for lang in LANGUES}
    for r in atlas._lire("lexique.csv"):
        if r["categorie"] != "confirmation" or r["langue"] not in mots:
            continue
        formes = {atlas.normaliser(m) for m in [r["forme"], *atlas._variantes(r["variantes"])]}
        if r["concept"] in ("Oui", "C'est ça"):
            mots[r["langue"]][0].update(formes)
        elif r["concept"] == "Non":
            mots[r["langue"]][1].update(formes)
    mots["srr"][0].update(mots["wo"][0])     # Kaddu répond en wolof au sérère : « waaw / déet » compris aussi
    mots["srr"][1].update(mots["wo"][1])
    return mots


MOTS_OUI_NON = _mots_confirmation()
OUI, NON = MOTS_OUI_NON["wo"]   # compatibilité avec l'ancien code


# ---------------------------------------------------------------------------
# Modèles de réponses
# ---------------------------------------------------------------------------

def _modeles():
    with open(DATA / "atlas" / "reponses.csv", encoding="utf-8") as f:
        return {r["cle"]: r for r in csv.DictReader(f)}


# Langue de secours quand une phrase n'est pas encore traduite (pulaar, sérère) : le wolof, en attendant
# les phrases de Ndèye. Si la phrase manque aussi en wolof, elle est dite en français.
LANGUE_SECOURS = "wo"


def phrase_et_langue(cle: str, langue: str = "wo", secours: str = LANGUE_SECOURS, **variables) -> tuple[str, str]:
    """(texte, langue réellement utilisée) : la langue demandée, sinon la langue de secours, sinon le français."""
    m = _modeles()[cle]
    utilisee = next((l for l in (langue, secours) if l in LANGUES and (m.get(l) or "").strip()), "fr")
    texte = (m.get(utilisee) or m["fr"]).strip().format(**variables)
    return texte[:1].upper() + texte[1:], utilisee   # « dix saak ceeb » -> « Dix saak ceeb » (le TTS lit en minuscules)


def phrase(cle: str, langue: str = "wo", secours: str = LANGUE_SECOURS, **variables) -> str:
    return phrase_et_langue(cle, langue, secours, **variables)[0]


def _nombre(x) -> str:
    from num2words import num2words
    x = float(x)
    return num2words(int(x) if x.is_integer() else x, lang="fr")


def _montant(f, langue: str = "wo", avec_forme_locale: bool = True) -> str:
    """Montant en français, suivi de la forme locale : « cent cinquante francs, maanaam fanweeri dërëm ».
    Le mot « maanaam » vient de reponses.csv (ligne maanaam) : sans traduction, on ne dit que le français."""
    formes = atlas.montant_a_dire(int(f), langue)
    maanaam = (_modeles()["maanaam"].get(langue) or "").strip()
    if avec_forme_locale and formes["local"] and maanaam:
        return f"{formes['francais']}, {maanaam} {formes['local']}"
    return formes["francais"]


def texte_pour_tts(texte: str) -> str:
    """Prépare un texte pour le TTS Kiriku : tout en minuscules (une majuscule au début n'est pas lue)
    et « x » en début de mot écrit « kh » (sinon il n'est pas lu). Le texte affiché ne change pas."""
    return re.sub(r"\bx", "kh", texte.lower())


def _articles(articles: list[dict], langue: str) -> str:
    morceaux = []
    for a in articles:
        mots = [_nombre(a["quantite"]) if a.get("quantite") else "", a.get("unite_dite") or "",
                a.get("produit_affiche") or a.get("produit_dit") or ""]
        if a.get("prix_fcfa") is not None:
            mots.append(_montant(a["prix_fcfa"], langue, avec_forme_locale=False))
        morceaux.append(" ".join(m for m in mots if m))
    et = f" {phrase('et', langue).lower()} "
    return et.join(morceaux)


def oui_ou_non(texte: str, langue: str = "wo") -> tuple[str | None, bool]:
    """(oui | non | None, le message contient-il autre chose ?)"""
    oui, non = MOTS_OUI_NON.get(langue, MOTS_OUI_NON["wo"])
    mots = atlas.normaliser(texte).split()
    if not mots:
        return None, False
    # « waaw » ou « waaw waaw » : réponse simple ; « waaw, loolu la » ou « déet, 3000 la » : le LLM lit la suite
    if " ".join(mots) in oui:                       # « mooy loolu », « ko ɗum » : oui en plusieurs mots
        return "oui", False
    if " ".join(mots) in non:
        return "non", False
    if mots[0] in oui:
        return "oui", any(m not in oui for m in mots)
    if mots[0] in non:
        return "non", any(m not in non for m in mots)
    return None, True


# ---------------------------------------------------------------------------
# Stockage
# ---------------------------------------------------------------------------

class Stockage(Protocol):
    """Les 5 méthodes que Conversation appelle. Toute base (SQLite, JSON, PostgreSQL...) qui les fournit marche."""

    def enregistrer(self, operation: dict) -> int | str: ...      # operation = operation_a_enregistrer(conv)
    def derniere(self) -> dict | None: ...                        # {id, intention, client, montant_fcfa}
    def annuler(self, operation_id) -> None: ...                  # statut -> « annulee » (on n'efface rien)
    def dette(self, client: str | None) -> int: ...               # crédits - remboursements (client None : tous)
    def ventes_du_jour(self) -> int: ...                          # ventes + ventes à crédit du jour


def operation_a_enregistrer(conv: "Conversation") -> dict:
    """L'opération confirmée, prête à être enregistrée (même format pour toutes les bases)."""
    e = conv.extraction
    return {
        "date": datetime.now().isoformat(timespec="seconds"), "langue": conv.langue, "intention": e.intention,
        "client": e.client, "montant_fcfa": e.montant_fcfa, "montant_tel_que_dit": e.montant_tel_que_dit,
        "controle_montant": conv.controle["controle_montant"]["statut"],
        "transcription": " | ".join(conv.messages_commercant), "modele": conv.modele,
        "nb_corrections": conv.nb_corrections, "statut": "active",
        "articles": [{"produit_dit": a["produit_dit"], "produit": a["produit_final"],
                      "statut_produit": a["statut_final"], "quantite": a["quantite"],
                      "unite": a.get("unite") or a.get("unite_dite"), "prix_fcfa": a.get("montant_ligne")}
                     for a in conv.articles_finaux()],
    }


class BaseTest:
    """Base SQLite de test (une base = un carnet). Tables operations et articles."""

    def __init__(self, chemin: Path | str = BASE_TEST):
        self.chemin = Path(chemin)
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.chemin, check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY, date TEXT, langue TEXT, intention TEXT, client TEXT,
                montant_fcfa INTEGER, montant_tel_que_dit TEXT, controle_montant TEXT,
                transcription TEXT, modele TEXT, nb_corrections INTEGER, statut TEXT DEFAULT 'active');
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY, operation_id INTEGER REFERENCES operations(id),
                produit_dit TEXT, produit TEXT, statut_produit TEXT, quantite REAL, unite TEXT);""")
        try:   # colonne ajoutée après coup
            self.db.execute("ALTER TABLE articles ADD COLUMN prix_fcfa INTEGER")
        except sqlite3.OperationalError:
            pass

    def enregistrer(self, op: dict) -> int:
        cur = self.db.execute(
            """INSERT INTO operations (date, langue, intention, client, montant_fcfa, montant_tel_que_dit,
               controle_montant, transcription, modele, nb_corrections) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (op["date"], op["langue"], op["intention"], op["client"], op["montant_fcfa"],
             op["montant_tel_que_dit"], op["controle_montant"], op["transcription"], op["modele"],
             op["nb_corrections"]))
        for a in op["articles"]:
            self.db.execute("""INSERT INTO articles (operation_id, produit_dit, produit, statut_produit,
                               quantite, unite, prix_fcfa) VALUES (?,?,?,?,?,?,?)""",
                            (cur.lastrowid, a["produit_dit"], a["produit"], a["statut_produit"],
                             a["quantite"], a["unite"], a["prix_fcfa"]))
        self.db.commit()
        return cur.lastrowid

    def derniere(self) -> dict | None:
        r = self.db.execute("""SELECT id, intention, client, montant_fcfa FROM operations
                               WHERE statut = 'active' ORDER BY id DESC LIMIT 1""").fetchone()
        return dict(zip(("id", "intention", "client", "montant_fcfa"), r)) if r else None

    def annuler(self, operation_id) -> None:
        self.db.execute("UPDATE operations SET statut = 'annulee' WHERE id = ?", (operation_id,))
        self.db.commit()

    def dette(self, client: str | None) -> int:
        filtre, params = ("AND lower(client) = lower(?)", (client,)) if client else ("", ())
        pris = self.db.execute(f"""SELECT COALESCE(SUM(montant_fcfa), 0) FROM operations
            WHERE statut = 'active' AND intention = 'vente_credit' {filtre}""", params).fetchone()[0]
        rendu = self.db.execute(f"""SELECT COALESCE(SUM(montant_fcfa), 0) FROM operations
            WHERE statut = 'active' AND intention = 'remboursement' {filtre}""", params).fetchone()[0]
        return pris - rendu

    def ventes_du_jour(self) -> int:
        return self.db.execute("""SELECT COALESCE(SUM(montant_fcfa), 0) FROM operations
            WHERE statut = 'active' AND intention IN ('vente', 'vente_credit') AND date LIKE ?""",
                               (date.today().isoformat() + "%",)).fetchone()[0]

    def tables(self):
        ops = self.db.execute("SELECT * FROM operations ORDER BY id DESC").fetchall()
        arts = self.db.execute("SELECT * FROM articles ORDER BY id DESC").fetchall()
        return ops, arts

    def vider(self) -> None:
        """Efface tout le carnet (utilisé par le bouton « Remettre le carnet à zéro » de l'espace de test)."""
        self.db.executescript("DELETE FROM articles; DELETE FROM operations;")
        self.db.commit()


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

class Conversation:
    """Une conversation = une opération, du premier message jusqu'à « c'est noté » (ou abandon)."""

    def __init__(self, modele: str, base: Stockage, langue: str = "wo", source: str = "texte",
                 file_termes: atlas.FileTermes | None = None, sessions: Path | str | None = SESSIONS,
                 secours: str = LANGUE_SECOURS):
        if langue not in LANGUES:
            raise ValueError(f"Langue inconnue : {langue} (attendu : {', '.join(LANGUES)})")
        self.modele, self.base, self.langue, self.source = modele, base, langue, source
        self.secours = secours                   # langue des phrases pas encore traduites (wo, ou fr)
        self.langue_reponse = LANGUE_REPONSE.get(langue, langue)   # sérère compris, réponses en wolof
        self.file_termes = file_termes
        self.sessions = Path(sessions) if sessions else None   # None : ne pas noter la session
        self.etat = "nouveau_message"
        self.extraction = None
        self.controle = None
        self.messages_commercant: list[str] = []
        self.historique: list[dict] = []        # {"role": commercant|kaddu, "texte", "details"}
        self.nb_corrections = 0                   # le commerçant a changé quelque chose
        self.nb_complements = 0                   # réponses à une question (montant, client, produit...)
        self.duree_llm = 0.0
        self.mots_acceptes: dict[str, dict] = {}  # mot dit -> {concept, forme} accepté par le commerçant
        self.mots_refuses: set[str] = set()       # suggestions refusées par le commerçant
        self.mots_ecrits: set[str] = set()        # mots nouveaux écrits tels quels
        self.repetition_demandee = False          # on a déjà demandé de répéter un produit inconnu
        self.question_en_cours: dict = {}
        self.operation_a_effacer: dict | None = None
        self.operation_id = None                  # id de l'opération enregistrée (si « enregistre »)
        self.issue = None
        self.debut = datetime.now().isoformat(timespec="seconds")
        self.derniere_activite = self.debut

    # -- sauvegarde (une conversation WhatsApp dure plusieurs messages : on la garde entre deux) ------------
    def vers_dict(self) -> dict:
        """État complet de la conversation, sérialisable en JSON."""
        return {"version": 1, "modele": self.modele, "langue": self.langue, "source": self.source,
                "secours": self.secours,
                "etat": self.etat, "extraction": self.extraction.model_dump() if self.extraction else None,
                "controle": self.controle, "messages_commercant": self.messages_commercant,
                "historique": self.historique, "nb_corrections": self.nb_corrections,
                "nb_complements": self.nb_complements, "duree_llm": self.duree_llm,
                "mots_acceptes": self.mots_acceptes, "mots_refuses": sorted(self.mots_refuses),
                "mots_ecrits": sorted(self.mots_ecrits), "repetition_demandee": self.repetition_demandee,
                "question_en_cours": self.question_en_cours, "operation_a_effacer": self.operation_a_effacer,
                "operation_id": self.operation_id, "issue": self.issue, "debut": self.debut,
                "derniere_activite": self.derniere_activite}

    @classmethod
    def depuis_dict(cls, d: dict, base: Stockage, file_termes: atlas.FileTermes | None = None,
                    sessions: Path | str | None = SESSIONS) -> "Conversation":
        """Recrée une conversation à partir de vers_dict() (la base et la file des termes sont redonnées)."""
        conv = cls(d["modele"], base, d["langue"], d.get("source", "texte"), file_termes, sessions,
                   d.get("secours", LANGUE_SECOURS))
        for cle in ("etat", "controle", "messages_commercant", "historique", "nb_corrections", "nb_complements",
                    "duree_llm", "mots_acceptes", "repetition_demandee", "question_en_cours",
                    "operation_a_effacer", "operation_id", "issue", "debut", "derniere_activite"):
            if cle in d:
                setattr(conv, cle, d[cle])
        conv.extraction = Extraction.model_validate(d["extraction"]) if d.get("extraction") else None
        conv.mots_refuses, conv.mots_ecrits = set(d.get("mots_refuses", [])), set(d.get("mots_ecrits", []))
        return conv

    # -- outils ----------------------------------------------------------------
    def _dire(self, cle: str, **variables) -> str:
        texte, langue_phrase = phrase_et_langue(cle, self.langue_reponse, self.secours, **variables)
        details = {"modele_reponse": cle, "langue_phrase": langue_phrase}
        if self.extraction:
            details["llm"] = self.extraction.model_dump()
            details["atlas"] = self.controle
        self.historique.append({"role": "kaddu", "texte": texte, "details": details})
        return texte

    def _historique_texte(self) -> str:
        """La conversation jusqu'ici, sans le dernier message (envoyé à part au LLM)."""
        return "\n".join(("Commerçant : " if m["role"] == "commercant" else "Kaddu : ") + m["texte"]
                         for m in self.historique[:-1])

    def _llm(self, texte: str, correction: bool):
        debut = time.time()
        if correction:
            e, brut = corriger(self.extraction, texte, self.modele, self._historique_texte(), self.langue)
        else:
            e, brut = extraire(texte, self.modele, self.langue)
        self.duree_llm += time.time() - debut
        return e, brut

    def _appliquer(self, e, texte: str):
        """Garde la nouvelle extraction, passe dans l'atlas, et prend le montant calculé par le code."""
        self.extraction = e
        self.controle = controler(e, texte, langue=self.langue)
        self.extraction.montant_fcfa = self.controle["controle_montant"]["montant_retenu"]

    def articles_finaux(self) -> list[dict]:
        """Articles avec le produit retenu après les réponses du commerçant."""
        finaux = []
        for a in (self.controle or {}).get("articles", []):
            a = dict(a)
            dit = a["produit_dit"]
            if a["statut_produit"] == "reconnu":
                a["produit_final"], a["statut_final"] = a["produit"], "reconnu"
            elif dit in self.mots_acceptes:
                a["produit_final"], a["statut_final"] = self.mots_acceptes[dit]["concept"], "reconnu_apres_question"
            else:
                a["produit_final"], a["statut_final"] = dit, "nouveau"
            a["produit_affiche"] = self.mots_acceptes[dit]["forme"] if dit in self.mots_acceptes else dit
            finaux.append(a)
        return finaux

    def _terminer(self, issue: str, cle: str, **variables) -> str:
        self.issue = issue
        self.etat = "termine"
        texte = self._dire(cle, **variables)
        self._noter_session()
        return texte

    def _noter_session(self):
        if not self.sessions:
            return
        arts = (self.controle or {}).get("articles", [])
        ligne = {"date": datetime.now().isoformat(timespec="seconds"), "source": self.source,
                 "message_initial": self.messages_commercant[0] if self.messages_commercant else "",
                 "modele": self.modele, "intention": self.extraction.intention if self.extraction else "",
                 "nb_articles": len(arts),
                 "produits_reconnus": sum(a["statut_produit"] == "reconnu" for a in arts),
                 "produits_proches": sum(a["statut_produit"] == "proche" for a in arts),
                 "produits_nouveaux": sum(a["statut_produit"] == "nouveau" for a in arts),
                 "controle_montant": (self.controle or {}).get("controle_montant", {}).get("statut", ""),
                 "champs_incertains": ", ".join(self.extraction.champs_incertains) if self.extraction else "",
                 "nb_messages_commercant": len(self.messages_commercant),
                 "nb_corrections": self.nb_corrections, "nb_complements": self.nb_complements,
                 "issue": self.issue, "duree_llm_s": round(self.duree_llm, 1), "langue": self.langue}
        champs = list(ligne)
        if self.sessions.exists():            # fichier existant : on garde ses colonnes (anciens fichiers sans « langue »)
            with open(self.sessions, encoding="utf-8") as f:
                champs = next(csv.reader(f), champs) or champs
        nouveau = not self.sessions.exists()
        self.sessions.parent.mkdir(parents=True, exist_ok=True)
        with open(self.sessions, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=champs, extrasaction="ignore")
            if nouveau:
                w.writeheader()
            w.writerow(ligne)

    # -- point d'entrée ----------------------------------------------------------
    def recevoir(self, texte: str) -> str:
        """Reçoit un message du commerçant (texte de l'ASR) et renvoie la réponse de Kaddu."""
        if self.etat == "termine":
            raise RuntimeError("Conversation terminée : en commencer une nouvelle.")
        self.derniere_activite = datetime.now().isoformat(timespec="seconds")
        self.messages_commercant.append(texte)
        self.historique.append({"role": "commercant", "texte": texte, "details": {}})
        if len(self.messages_commercant) > MAX_MESSAGES:
            return self._terminer("abandon", "abandon")

        if self.etat == "nouveau_message":
            e, brut = self._llm(texte, correction=False)
            if not e:
                return self._terminer("reponse_llm_illisible", "incomprehension")
            self._appliquer(e, texte)
            return self._suite()

        # Question oui/non : un « waaw / eey / iyo » ou « déet / alaa / xa'a » seul est compris sans le LLM
        if self.etat in QUESTIONS_OUI_NON:
            reponse, autre_chose = oui_ou_non(texte, self.langue)
            if reponse and not autre_chose:
                return self._oui_non(reponse)

        # Sinon le LLM lit la réponse avec toute la conversation : confirme, refuse ou corrige ?
        e, brut = self._llm(texte, correction=True)
        if not e:   # réponse illisible : on garde l'opération en cours et on redemande
            self.etat = "attente_correction"
            return self._dire("redemander")
        if self.etat in QUESTIONS_OUI_NON and e.type_reponse in ("confirme", "refuse"):
            return self._oui_non("oui" if e.type_reponse == "confirme" else "non")
        if self.etat == "attente_correction" and e.type_reponse == "refuse":
            return self._terminer("abandon_par_commercant", "pas_enregistre")

        if self.etat in ("attente_complement", "attente_repetition"):
            self.nb_complements += 1
        else:
            self.nb_corrections += 1
        if self.nb_corrections > MAX_CORRECTIONS:
            return self._terminer("abandon", "abandon")
        self._appliquer(e, texte)
        return self._suite()

    def _oui_non(self, reponse: str) -> str:
        if self.etat == "proposer_mot":
            terme = self.question_en_cours["terme"]
            if reponse == "oui":
                self.mots_acceptes[terme] = {"concept": self.question_en_cours["suggestion"],
                                             "forme": self.question_en_cours["forme"]}
            else:
                self.mots_refuses.add(terme)
            return self._suite()
        if self.etat == "confirmer":
            if reponse == "oui":
                for a in self.articles_finaux():
                    if a["statut_final"] == "nouveau" and self.file_termes:
                        self.file_termes.ajouter(a["produit_dit"], self.langue, "produit",
                                                 self.messages_commercant[0], a.get("sens_fr"))
                self.operation_id = self.base.enregistrer(operation_a_enregistrer(self))
                return self._terminer("enregistre", "enregistre")
            self.etat = "attente_correction"
            return self._dire("redemander")
        if self.etat == "confirmer_annulation":
            if reponse == "oui":
                self.base.annuler(self.operation_a_effacer["id"])
                return self._terminer("annulation_faite", "efface")
            return self._terminer("annulation_refusee", "pas_enregistre")
        raise ValueError(self.etat)

    # -- décide de la prochaine question -------------------------------------------
    def _suite(self) -> str:
        e = self.extraction
        L = self.langue_reponse          # langue des réponses (montants, articles, phrases)
        if e.intention == "incomprehension":
            return self._terminer("incomprehension", "incomprehension")
        if e.intention not in INTENTIONS_ACTIVES:
            return self._terminer("pas_disponible", "pas_disponible")
        if e.intention == "question_dette":
            montant = self.base.dette(e.client)
            if not e.client:
                return self._terminer("question", "reponse_dettes_total", montant=_montant(montant, L))
            if montant <= 0:
                return self._terminer("question", "reponse_dette_rien", client=e.client)
            return self._terminer("question", "reponse_dette", client=e.client, montant=_montant(montant, L))
        if e.intention == "question_bilan":   # solde global : ventes du jour + total des crédits
            ventes, dettes = self.base.ventes_du_jour(), self.base.dette(None)
            return self._terminer("question", "reponse_bilan", ventes=_montant(ventes, L),
                                  dettes=_montant(max(dettes, 0), L), montant=_montant(ventes, L))
        if e.intention == "annulation":
            derniere = self.base.derniere()
            if not derniere:
                return self._terminer("annulation_rien", "rien_a_effacer")
            self.operation_a_effacer = derniere
            montant = derniere.get("montant_fcfa")
            resume = " ".join(str(x) for x in (derniere["intention"].replace("_", " "), derniere.get("client") or "",
                                                _montant(montant, L, False) if montant else "") if x)
            self.etat = "confirmer_annulation"
            return self._dire("confirmer_annulation", resume=resume)

        # Opérations : on vérifie chaque produit avant la confirmation finale
        for a in self.controle["articles"]:
            dit = a["produit_dit"]
            if not dit:
                self.etat = "attente_complement"
                return self._dire("demander_produit")
            if a["statut_produit"] == "reconnu" or dit in self.mots_acceptes or dit in self.mots_ecrits:
                continue
            # Ressemble à un produit connu : on demande confirmation
            if a["statut_produit"] == "proche" and dit not in self.mots_refuses:
                self.etat = "proposer_mot"
                self.question_en_cours = {"terme": dit, "suggestion": a["suggestion_produit"],
                                          "forme": a["suggestion_forme_produit"]}
                return self._dire("proposer_mot", terme=dit,
                                  suggestion=f"{a['suggestion_forme_produit']} ({a['suggestion_produit'].lower()})")
            # Ne ressemble à rien de connu :
            #  - le LLM sait ce que c'est (sens donné, sans doute) -> on l'écrit directement ;
            #  - sinon on demande une fois de répéter ; après la répétition, on l'écrit tel quel.
            sur = a.get("sens_fr") and "articles" not in e.champs_incertains
            if sur or self.repetition_demandee:
                self.mots_ecrits.add(dit)
                continue
            self.repetition_demandee = True
            self.etat = "attente_repetition"
            return self._dire("demander_produit")
        if e.intention in AVEC_CLIENT and not e.client:
            self.etat = "attente_complement"
            return self._dire("demander_client")
        if e.montant_fcfa is None and e.intention not in MONTANT_FACULTATIF:
            self.etat = "attente_complement"
            return self._dire("demander_montant")

        self.etat = "confirmer"
        articles = _articles(self.articles_finaux(), L)
        variables = {"client": e.client or "", "articles": articles}
        cle = f"confirmer_{e.intention}"
        if e.montant_fcfa is None:                      # entrée de stock sans prix
            return self._dire(f"{cle}_sans_montant", **variables)
        variables["montant"] = _montant(e.montant_fcfa, L)
        if e.intention == "remboursement" and e.client:  # on annonce ce qu'il restera à payer
            dette_avant = self.base.dette(e.client)
            if dette_avant > 0:
                variables["reste"] = _montant(max(dette_avant - e.montant_fcfa, 0), L)
                cle = "confirmer_remboursement_reste"
        return self._dire(cle, **variables)
