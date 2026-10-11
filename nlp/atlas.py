"""Atlas TEWLU Kaddu : lexique, nombres et montants en wolof (wo), pulaar (ff) et sérère (srr).

Source unique : l'atlas OFFICIEL validé par Ndèye Maguette
(nlp/data/atlas/source/Off_Annuaire_Commercants_Pulaar_Serere_Wolof_3.xlsx), converti en CSV par
nlp/outils/importer_atlas_officiel.py : lexique.csv, nombres_{wo,ff,srr}.csv, montants_{wo,ff,srr}.csv.

Fonctions principales :
  chercher(terme, langue)                 -> mot de l'atlas le plus proche, ou None
  texte_vers_montants(texte, langue)      -> montants (FCFA) trouvés dans un texte
  verifier_montant(montant, mots, langue) -> le montant du LLM correspond-il aux mots dits ?
  montant_a_dire(montant, langue)         -> formes française et locale, pour le TTS
  FileTermes                              -> file des termes absents de l'atlas, à valider
  rapprocher(extraction, langue)          -> étape du pipeline après le LLM (reconnu / proche / nouveau)
"""
from __future__ import annotations

import csv
import difflib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

DOSSIER = Path(__file__).parent / "data" / "atlas"
LANGUES = ("wo", "ff", "srr")
MONTANT_MAX = 500_000  # au-delà, les formes locales ne sont pas générées (le chiffre et le français restent gérés)


# ---------------------------------------------------------------------------
# Normalisation : rend comparables les différentes écritures d'un même mot
# ---------------------------------------------------------------------------

def normaliser(texte: str) -> str:
    s = (texte or "").lower().replace("’", "'").replace("-", " ").replace("œ", "oe")
    s = s.replace("ɗ", "d").replace("ɓ", "b").replace("ƴ", "y").replace("ŋ", "ng").replace("ñ", "n")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = s.replace("th", "c").replace("kh", "x")
    s = re.sub(r"[^a-z0-9' ]", " ", s).replace("'", "")
    s = re.sub(r"([a-z])\1+", r"\1", s)           # lettres doublées : teemeer = temer
    return " ".join(s.split())


def _norm_montant(texte: str) -> str:
    """Normalisation plus forte pour les montants : liaison -i et mots d'unité ignorés."""
    s = normaliser(texte)
    s = re.sub(r"\b(derem|deremi|francs?|fcfa|cfa)\b", " ", s)
    s = re.sub(r"(\w{3,})i\b", r"\1", s)            # ñaari junni = ñaar junni
    return " ".join(s.split())


def _lire(nom: str) -> list[dict]:
    chemin = DOSSIER / nom
    if not chemin.exists():
        return []
    with open(chemin, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _variantes(champ: str) -> list[str]:
    return [v.strip() for v in re.split(r"[;,]", champ or "") if v.strip() and v.strip() != "_"]


# ---------------------------------------------------------------------------
# Lexique
# ---------------------------------------------------------------------------

@dataclass
class Correspondance:
    concept: str      # concept en français ("Sucre")
    forme: str        # forme principale dans la langue ("sukar")
    categorie: str
    source: str       # claude | gemini | ndeye_maguette
    statut: str       # à_valider | Saisi | Validé
    score: float      # 1.0 = identique après normalisation
    terme_entendu: str


@lru_cache(maxsize=None)
def _index_lexique(langue: str) -> dict[str, dict]:
    index = {}
    for r in _lire("lexique.csv"):
        if r["langue"] != langue:
            continue
        entree = {k: r[k] for k in ("concept", "forme", "categorie", "source", "statut")}
        for mot in [r["forme"], *_variantes(r["variantes"])]:
            index.setdefault(normaliser(mot), entree)
        # le nom français du concept, souvent dit tel quel ("pomme de terre")
        francais = re.sub(r"\(.*?\)", "", r["concept"]).split("/")[0]
        index.setdefault(normaliser(francais), entree)
    return index


def chercher(terme: str, langue: str, categorie: str | None = None, seuil: float = 0.85) -> Correspondance | None:
    """Cherche un mot dans l'atlas : identique (après normalisation), sinon le plus proche au-dessus du seuil."""
    if not terme:
        return None
    index = _index_lexique(langue)
    if categorie:
        index = {k: v for k, v in index.items() if v["categorie"] == categorie}
    cle = normaliser(terme)
    if cle in index:
        return Correspondance(**index[cle], score=1.0, terme_entendu=terme)
    proches = difflib.get_close_matches(cle, list(index), n=1, cutoff=seuil)
    if proches:
        score = difflib.SequenceMatcher(None, cle, proches[0]).ratio()
        return Correspondance(**index[proches[0]], score=round(score, 2), terme_entendu=terme)
    return None


# ---------------------------------------------------------------------------
# Nombres et montants
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _nombres(langue: str) -> dict[int, str]:
    """Nombres 1 à 100 de l'atlas officiel (forme principale)."""
    return {int(r["valeur"]): r["forme"] for r in _lire(f"nombres_{langue}.csv")}


@lru_cache(maxsize=None)
def _montants_atlas(langue: str) -> dict[int, str]:
    """Montants de l'atlas officiel (25 à 2 500 F de 25 en 25, puis 10 000 à 150 000 F)."""
    return {int(r["montant_fcfa"]): r["forme"] for r in _lire(f"montants_{langue}.csv")}


def _wo_lie(expr: str) -> str:
    """Liaison wolof en -i devant téeméer / junni : ñaar -> ñaari, juróom-benn -> juróom-benni."""
    return expr if expr == "benn" or expr.endswith("i") else expr + "i"


# Règles des montants au-delà de l'atlas, déduites de ses lignes (500 F et 5 000 F, joint entre les parties).
# (forme pour 1, forme pour n) : n est la forme de l'atlas pour le nombre de billets.
REGLES_MONTANTS = {
    "wo": {"5000": ("junni", lambda n: f"{_wo_lie(n)} junni"),
           "500": ("téeméer", lambda n: f"{_wo_lie(n)} téeméer"), "joint": " ak "},
    "ff": {"5000": ("ujunéré", lambda n: f"ujunaajé {n}"),
           "500": ("teemedéré", lambda n: f"teemédé {n}"), "joint": " e "},
    "srr": {"5000": ("thiouni", lambda n: f"thiouni {n}"),
            "500": ("teeméde", lambda n: f"teeméde {n}"), "joint": " fo "},
}


def forme_par_regles(f: int, langue: str) -> str | None:
    """Forme monétaire construite avec les règles (sans regarder si le montant est dans l'atlas)."""
    if f <= 0 or f % 25 or f > MONTANT_MAX or langue not in REGLES_MONTANTS:
        return None
    N, M, R = _nombres(langue), _montants_atlas(langue), REGLES_MONTANTS[langue]
    k5000, reste = divmod(f, 5000)
    k500, reste = divmod(reste, 500)
    parts = []
    for k, cle in ((k5000, "5000"), (k500, "500")):
        if k:
            un, plusieurs = R[cle]
            if k > 1 and k not in N:
                return None
            parts.append(un if k == 1 else plusieurs(N[k]))
    if reste:
        if reste not in M:          # 25 à 475 F : toujours dans l'atlas
            return None
        parts.append(M[reste])
    return R["joint"].join(parts)


def forme_locale(f: int, langue: str) -> str | None:
    """Forme monétaire locale d'un montant : celle de l'atlas si elle existe, sinon construite par les règles."""
    return _montants_atlas(langue).get(f) or forme_par_regles(f, langue)


@lru_cache(maxsize=None)
def _index_montants(langue: str) -> dict[str, int]:
    """Toutes les formes locales connues -> montant. Généré une fois (environ 20 000 entrées par langue)."""
    index = {}
    lignes = sorted(_lire(f"montants_{langue}.csv"), key=lambda r: int(r["montant_fcfa"]))
    for r in lignes:                                           # 1) formes principales de l'atlas
        index[_norm_montant(r["forme"])] = int(r["montant_fcfa"])
    for r in lignes:                                           # 2) variantes, sans écraser une forme principale
        for mot in _variantes(r["variantes"]):                 #    (l'atlas a des variantes tronquées : 2150 -> « fanweer dërëm »)
            index.setdefault(_norm_montant(mot), int(r["montant_fcfa"]))
    for f in range(25, MONTANT_MAX + 1, 25):                   # formes générées par les règles
        forme = forme_par_regles(f, langue)
        if forme:
            index.setdefault(_norm_montant(forme), f)
    index.pop("", None)
    return index


def _montants_francais(texte: str) -> list[int]:
    try:
        from text_to_num import alpha2digit
    except ImportError:
        return []
    try:
        converti = alpha2digit(texte, "fr", threshold=0)
    except TypeError:                                          # text2num 2.x
        converti = alpha2digit(texte, "fr")
    return _montants_chiffres(converti) if converti != texte else []


def _montants_chiffres(texte: str) -> list[int]:
    """Nombres écrits en chiffres. Un espace ou un point ne sépare les milliers que devant
    exactement 3 chiffres : « 13 000 » = 13000, mais « 4000 13 000 » = 4000 et 13000."""
    valeurs = []
    for m in re.finditer(r"\d{1,3}(?:[ .\u00a0\u202f]\d{3})+(?!\d)|\d+", texte):
        valeurs.append(int(re.sub(r"\D", "", m.group(0))))
    return valeurs


def texte_vers_montants(texte: str, langue: str) -> list[int]:
    """Montants trouvés dans un texte : chiffres ("13 000f"), français ("onze mille") ou forme locale."""
    trouves = _montants_chiffres(texte) + _montants_francais(texte)
    norm = f" {_norm_montant(texte)} "
    index = _index_montants(langue)
    # plus longue forme locale présente dans le texte (mot entier)
    candidats = [k for k in index if f" {k} " in norm]
    if candidats:
        trouves.append(index[max(candidats, key=len)])
    return list(dict.fromkeys(trouves))


def verifier_montant(montant: int | None, mots_du_montant: str | None, langue: str) -> dict:
    """Compare le montant donné par le LLM aux mots exacts qu'il dit avoir entendus."""
    if montant is None or not mots_du_montant:
        return {"statut": "non_verifiable", "montants_regles": []}
    regles = texte_vers_montants(mots_du_montant, langue)
    if not regles:
        return {"statut": "non_verifiable", "montants_regles": []}
    if montant in regles:
        return {"statut": "coherent", "montants_regles": regles}
    if len(regles) > 1 and montant == sum(regles):   # plusieurs montants dits, le LLM a fait le total
        return {"statut": "coherent", "montants_regles": regles, "note": "total de plusieurs montants"}
    return {"statut": "ecart", "montants_regles": regles}



def montant_a_dire(f: int, langue: str) -> dict:
    """Formes à faire lire au TTS. Par défaut, le français en toutes lettres (ce que disent les commerçants)."""
    from num2words import num2words
    francais = num2words(f, lang="fr") + " francs"
    return {"francais": francais, "local": forme_locale(f, langue), "tts": francais}


# ---------------------------------------------------------------------------
# File des termes absents de l'atlas
# ---------------------------------------------------------------------------

class FileTermes:
    """Termes entendus mais absents de l'atlas : on les garde pour validation, sans jamais bloquer l'opération."""

    def __init__(self, chemin: Path | str = Path(__file__).parent / "data" / "termes_a_valider.db"):
        self.db = sqlite3.connect(chemin, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS termes_a_valider (
            id INTEGER PRIMARY KEY, terme TEXT, langue TEXT, categorie TEXT, exemple_phrase TEXT,
            occurrences INTEGER DEFAULT 1, premiere_vue TEXT, derniere_vue TEXT,
            statut TEXT DEFAULT 'en_attente', forme_retenue TEXT,
            UNIQUE(terme, langue, categorie))""")
        try:   # colonne ajoutée après coup : le sens proposé par le LLM
            self.db.execute("ALTER TABLE termes_a_valider ADD COLUMN sens_propose TEXT")
        except sqlite3.OperationalError:
            pass

    def ajouter(self, terme: str, langue: str, categorie: str, phrase: str = "", sens: str | None = "") -> None:
        maintenant = datetime.now().isoformat(timespec="seconds")
        self.db.execute("""INSERT INTO termes_a_valider (terme, langue, categorie, exemple_phrase, premiere_vue,
                derniere_vue, sens_propose) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(terme, langue, categorie) DO UPDATE SET
                occurrences = occurrences + 1, derniere_vue = excluded.derniere_vue""",
                        (normaliser(terme), langue, categorie, phrase, maintenant, maintenant, sens or ""))
        self.db.commit()

    def lister(self, statut: str = "en_attente") -> list[tuple]:
        return self.db.execute("""SELECT terme, langue, categorie, occurrences, exemple_phrase, sens_propose
            FROM termes_a_valider WHERE statut = ? ORDER BY occurrences DESC""", (statut,)).fetchall()


# ---------------------------------------------------------------------------
# Étape « atlas » du pipeline, appelée juste après le LLM
# ---------------------------------------------------------------------------

SEUIL_RECONNU = 0.9         # au-dessus : même mot, écrit autrement
SEUIL_PROCHE = 0.7          # entre les deux : mot proche, on demande confirmation au commerçant
SEUIL_PROCHE_COURT = 0.85   # mots de moins de 5 lettres : « plat » ne doit pas devenir « lait »


def classer(terme: str, langue: str, categorie: str, sens: str | None = None) -> dict:
    """reconnu (dans l'atlas), proche (on propose un mot de l'atlas) ou nouveau (gardé tel quel).

    sens : ce que le LLM pense que le mot veut dire, en français (« palaas » -> « plat »).
    - si le mot dit est dans l'atlas : reconnu ;
    - sinon, si le sens donné par le LLM est dans l'atlas : proche, on propose ce sens ;
    - sinon, si le mot ressemble à un mot de l'atlas ET que le LLM ne dit pas autre chose : proche ;
    - sinon : nouveau (le sens du LLM est gardé pour aider Ndèye à valider).
    """
    seuil = SEUIL_PROCHE if len(normaliser(terme).replace(" ", "")) >= 5 else SEUIL_PROCHE_COURT
    # dans la bonne catégorie (un produit ne doit pas devenir « livraison ») ; ailleurs, seulement si identique
    trouve = chercher(terme, langue, categorie, seuil) or chercher(terme, langue, None, SEUIL_RECONNU)
    resultat = {"statut": "nouveau", "concept": None, "suggestion": None, "suggestion_forme": None,
                "score": 0.0, "sens_llm": sens, "origine_suggestion": None}
    if trouve and trouve.score >= SEUIL_RECONNU:
        resultat.update(statut="reconnu", concept=trouve.concept, score=trouve.score)
        return resultat
    par_sens = (chercher(sens, langue, categorie, SEUIL_RECONNU) or chercher(sens, langue, None, SEUIL_RECONNU)) if sens else None
    if par_sens:
        resultat.update(statut="proche", suggestion=par_sens.concept, suggestion_forme=par_sens.forme.lower(),
                        score=par_sens.score, origine_suggestion="sens_llm")
    elif trouve and not sens:
        resultat.update(statut="proche", suggestion=trouve.concept, suggestion_forme=trouve.forme.lower(),
                        score=trouve.score, origine_suggestion="orthographe")
    return resultat


def _controler_un_montant(montant, mots, langue) -> tuple[int | None, dict]:
    """Contrôle un montant du LLM avec les règles. Si les règles lisent un seul montant différent
    dans les mêmes mots (« fanweer » : LLM 3000, règles 150), c'est le montant des règles qui est gardé."""
    c = verifier_montant(montant, mots, langue)
    if c["statut"] == "coherent":
        c["statut"] = "lecture_ok"
    elif c["statut"] == "ecart" and len(c["montants_regles"]) == 1:
        c.update(statut="corrige_par_regles", montant_llm=montant)
        montant = c["montants_regles"][0]
    return montant, c


def rapprocher(extraction: dict, langue: str, transcription: str = "", file: FileTermes | None = None) -> dict:
    """Rapproche chaque article extrait par le LLM de l'atlas, et calcule les montants. Ne rejette rien.

    Pour chaque article : produit_dit (tel qu'entendu), produit (nom de l'atlas si reconnu, sinon le mot dit),
    statut_produit (reconnu | proche | nouveau), suggestion (si proche), l'unité, et le prix contrôlé.
    Montants : c'est le programme qui additionne, jamais le LLM.
      - un total est dit -> on garde le total (contrôlé par les règles) ;
      - sinon, si chaque article a un prix -> total = somme des prix (prix x quantité si prix par unité).
    """
    articles = []
    for a in extraction.get("articles") or []:
        art = {"produit_dit": a.get("produit_tel_que_dit"), "sens_fr": a.get("sens_fr"),
               "quantite": a.get("quantite"), "unite_dite": a.get("unite")}
        for champ, dit, categorie in (("produit", art["produit_dit"], "produit"),
                                      ("unite", art["unite_dite"], "unité")):
            if not dit:
                art.update({champ: None, f"statut_{champ}": None, f"suggestion_{champ}": None,
                            f"suggestion_forme_{champ}": None})
                continue
            c = classer(dit, langue, categorie, art["sens_fr"] if champ == "produit" else None)
            art.update({champ: c["concept"] or dit, f"statut_{champ}": c["statut"],
                        f"suggestion_{champ}": c["suggestion"], f"suggestion_forme_{champ}": c["suggestion_forme"]})
            if c["statut"] == "nouveau" and file:
                file.ajouter(dit, langue, categorie, transcription, art["sens_fr"] if champ == "produit" else "")
        prix, controle_prix = _controler_un_montant(a.get("prix_fcfa"), a.get("prix_tel_que_dit"), langue)
        art.update(prix_fcfa=prix, prix_dit=a.get("prix_tel_que_dit"), prix_par_unite=bool(a.get("prix_par_unite")),
                   controle_prix=controle_prix["statut"] if prix is not None else None)
        if prix is not None:
            art["montant_ligne"] = prix * art["quantite"] if art["prix_par_unite"] and art["quantite"] else prix
        else:
            art["montant_ligne"] = None
        articles.append(art)

    total, controle = _controler_un_montant(extraction.get("montant_fcfa"), extraction.get("montant_tel_que_dit"), langue)
    lignes = [a["montant_ligne"] for a in articles]
    somme = int(sum(lignes)) if lignes and all(m is not None for m in lignes) else None
    controle["somme_des_prix"] = somme
    if total is not None:
        controle["montant_retenu"], controle["origine_montant"] = total, "total_dit"
        if somme is not None and somme != total:
            controle["alerte"] = f"le total dit ({total}) n'est pas la somme des prix ({somme})"
    elif somme is not None:
        controle["montant_retenu"], controle["origine_montant"] = somme, "somme_des_prix"
    else:
        controle["montant_retenu"], controle["origine_montant"] = None, None
    return {"articles": articles, "controle_montant": controle}
