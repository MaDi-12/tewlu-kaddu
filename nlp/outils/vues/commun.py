"""Éléments partagés par les pages Streamlit : chemins, appels ASR / TTS, style (dans l'esprit de l'interface
de Khalifa : fond ardoise, police Outfit, dégradé bleu -> violet, cartes en verre)."""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import requests
import streamlit as st

NLP = Path(__file__).resolve().parent.parent.parent          # .../nlp
sys.path.insert(0, str(NLP))
sys.path.insert(0, str(NLP / "outils"))
from pipeline import ASR_MODELE, LANGUES, VOIX_TTS, texte_pour_tts  # noqa: E402

API = "http://localhost:8000"
DATA = NLP / "data"
DOSSIER_AUDIOS = NLP / "audios"

# Données de l'espace de test de l'équipe : tout est dans nlp/data/equipe/ (rien n'impacte le reste)
EQUIPE = DATA / "equipe"
EQUIPE_CARNET = EQUIPE / "carnet_equipe.db"
EQUIPE_SESSIONS = EQUIPE / "sessions_equipe.csv"
EQUIPE_TERMES = EQUIPE / "termes_a_valider_equipe.db"
EQUIPE_JOURNAL = EQUIPE / "journal_vocaux.csv"
EQUIPE_AUDIOS = DOSSIER_AUDIOS / "equipe"
EQUIPE_DEMO = EQUIPE / "carnet_demo.db"          # carnet de démonstration (outils/donnees_demo.py)

# Archives des conversations complètes (pipeline.Conversation.archiver), une par carnet
ARCHIVES_CONVERSATIONS = {
    "Espace de test de l'équipe": EQUIPE / "conversations_equipe.jsonl",
    "Base de test de Marème": DATA / "conversations_test.jsonl",
    "Telegram (bot)": DATA / "telegram" / "conversations.jsonl",
    "WhatsApp / Telegram (Kaddu)": DATA / "whatsapp" / "conversations.jsonl",
}
TOUS_LES_CARNETS = "Tous les carnets (page de test + Telegram + WhatsApp)"


def archiver_conversation(d: dict, chemin: Path, source: str) -> None:
    """Ajoute une conversation terminée (dictionnaire Conversation.vers_dict()) à une archive JSONL,
    au format lu par la page « Revue des conversations »."""
    import json
    from datetime import datetime
    from pipeline import LANGUE_REPONSE
    extraction = d.get("extraction") or {}
    ligne = {"id": f"{source}-{d.get('debut')}", "debut": d.get("debut"),
             "fin": datetime.now().isoformat(timespec="seconds"), "source": source, "langue": d.get("langue"),
             "langue_reponse": LANGUE_REPONSE.get(d.get("langue"), d.get("langue")), "modele": d.get("modele"),
             "issue": d.get("issue"), "intention": extraction.get("intention"),
             "nb_corrections": d.get("nb_corrections"), "nb_complements": d.get("nb_complements"),
             "duree_llm_s": round(d.get("duree_llm") or 0, 1), "operation_id": d.get("operation_id"),
             "historique": d.get("historique", [])}
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "a", encoding="utf-8") as f:
        f.write(json.dumps(ligne, ensure_ascii=False, default=str) + "\n")


def carnets_disponibles() -> dict:
    """Carnets lisibles par le tableau de bord : {nom affiché: (type, chemin)}."""
    from pipeline import BASE_TEST
    carnets = {TOUS_LES_CARNETS: ("tous", None),
               "Carnet de l'équipe": ("sqlite", EQUIPE_CARNET), "Base de test de Marème": ("sqlite", BASE_TEST)}
    if EQUIPE_DEMO.exists():
        carnets["Carnet de démonstration"] = ("sqlite", EQUIPE_DEMO)
    for canal, nom in (("telegram", "Telegram"), ("whatsapp", "WhatsApp")):   # carnets des messageries
        dossier = DATA / canal / "commercants"
        if dossier.exists():
            for f in sorted(dossier.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
                if f.suffix in (".json", ".db"):
                    carnets[f"{nom} · {f.stem}"] = ("json" if f.suffix == ".json" else "sqlite", f)
    return carnets


def lire_operations(type_carnet: str, chemin: Path) -> list[dict]:
    """Toutes les opérations d'un carnet (actives et annulées), avec leurs articles, au même format.
    type_carnet « tous » : le carnet de la page de test et tous les carnets Telegram et WhatsApp réunis
    (champ « carnet » ajouté à chaque opération pour savoir d'où elle vient)."""
    if type_carnet == "tous":
        reunies = []
        for nom, (t, c) in carnets_disponibles().items():
            if t != "tous" and (nom == "Carnet de l'équipe" or nom.startswith(("Telegram", "WhatsApp"))):
                for o in lire_operations(t, c):
                    o["carnet"] = nom
                    reunies.append(o)
        return sorted(reunies, key=lambda o: o.get("date") or "")
    if not chemin.exists():
        return []
    if type_carnet == "json":
        import json
        return json.loads(chemin.read_text(encoding="utf-8")).get("operations", [])
    import sqlite3
    db = sqlite3.connect(chemin)
    db.row_factory = sqlite3.Row
    ops = [dict(r) for r in db.execute("SELECT * FROM operations ORDER BY id")]
    articles = {}
    for r in db.execute("SELECT * FROM articles"):
        articles.setdefault(r["operation_id"], []).append(dict(r))
    for o in ops:
        o["articles"] = articles.get(o["id"], [])
    db.close()
    return ops

# Scénarios du document « Phases Tests Trilingue » de Ndèye Maguette (recopiés tels quels)
SCENARIOS = {
    "wo": [("Entrée de stock", "Jend naa fukki saak ceeb."),
           ("Vente à crédit", "Awa jënd na ñaari paket sukar, juroom béni téemére, daf koo leb."),
           ("Remboursement", "Awa fey na ñaari teeméer."),
           ("Dette d'un client", "Ñaata la Awa ame thi bor ?"),
           ("Solde global", "lima diay tay thi ñaata la tole?")],
    "ff": [("Entrée de stock", "Mi soodi saakuji sappo maaro"),
           ("Vente à crédit", "Hawa sodi paketa didi sukara téemédé tati, ko niamliido dum"),
           ("Remboursement", "Hawa yobiima téemeedé ɗiɗi"),
           ("Dette d'un client", "No foti Hawa jogui é ñamande"),
           ("Solde global", "No foti hedi?")],
    "srr": [("Entrée de stock", "Jikaam Xarbaxay saak maalo."),
            ("Vente à crédit", "Hawa jikà paketa dik sukur, teeméte bétou fa léng, Ka rawine."),
            ("Remboursement", "Hawa rabda teeméte dik."),
            ("Dette d'un client", "Lat né Hawa Pod noumo ?"),
            ("Solde global", "podnum fop jeggam?")],
}

INTENTIONS_FR = {"vente": "Vente", "vente_credit": "Vente à crédit", "remboursement": "Remboursement",
                 "achat": "Entrée de stock", "depense": "Dépense"}
ISSUES_FR = {"enregistre": ("✅", "Opération enregistrée"), "question": ("💬", "Réponse donnée"),
             "annulation_faite": ("🗑️", "Opération effacée"), "pas_enregistre": ("↩️", "Rien n'a été noté"),
             "abandon_par_commercant": ("↩️", "Rien n'a été noté"), "annulation_refusee": ("↩️", "Rien n'a été effacé"),
             "annulation_rien": ("ℹ️", "Rien à effacer"), "abandon": ("⚠️", "Trop de corrections : abandon"),
             "incomprehension": ("❓", "Message pas compris"), "reponse_llm_illisible": ("❓", "Message pas compris"),
             "pas_disponible": ("🚧", "Pas encore disponible"), "interrompue": ("⏸️", "Interrompue"),
             "expiree": ("⌛", "Laissée en plan (expirée)")}


def lire_csv(chemin: Path) -> list[dict]:
    if not chemin.exists():
        return []
    with open(chemin, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def nettoyer(texte: str) -> str:
    return re.sub(r"<\|\w+\|>", "", texte or "").strip()


def francs(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ") + " F"


@st.cache_data(show_spinner=False, max_entries=300)
def tts_audio(texte: str, voix: str) -> bytes:
    """Audio WAV du TTS Kiriku (texte préparé : minuscules, x -> kh). Lève RuntimeError si le backend refuse."""
    try:
        r = requests.post(f"{API}/tts", data={"text": texte_pour_tts(texte), "model": VOIX_TTS[voix]}, timeout=600)
    except requests.RequestException:
        raise RuntimeError("Backend injoignable : lance le backend de Khalifa (uvicorn, port 8000).")
    if not r.ok:
        raise RuntimeError(f"Erreur TTS {r.status_code} : {r.text[:200]}")
    return r.content


def transcrire(audio_bytes: bytes, langue_asr: str) -> str | None:
    """Vocal -> texte avec l'ASR Kiriku (langue wo / pu / se). Affiche l'erreur et renvoie None si échec."""
    try:
        r = requests.post(f"{API}/asr", files={"audio": ("vocal.wav", audio_bytes, "audio/wav")},
                          data={"model": ASR_MODELE, "language": langue_asr}, timeout=600)
    except requests.RequestException:
        st.error("Backend injoignable : lance le backend de Khalifa (uvicorn, port 8000).")
        return None
    if not r.ok:
        st.error(f"Erreur ASR {r.status_code} : {r.text[:200]}")
        return None
    return nettoyer(r.json()["text"])


@st.cache_resource(show_spinner="Chargement du modèle Kiriku pour la détection de langue (une seule fois)...")
def _modele_detection():
    """M-Kiriku-ASR chargé dans Streamlit, uniquement pour détecter la langue des vocaux de la page de test.
    Indépendant du backend : si la détection marche ici mais pas sur Telegram, le souci est côté backend."""
    import os
    try:
        import torch
        from huggingface_hub import snapshot_download
        from transformers import WhisperForConditionalGeneration, WhisperProcessor
    except ImportError as e:
        raise RuntimeError(f"Il manque une bibliothèque pour la détection ({e.name}) : "
                           "pip install torch transformers librosa") from e
    try:
        from dotenv import load_dotenv
        load_dotenv(NLP.parent / "backend" / ".env")
    except ImportError:
        pass
    chemin = snapshot_download(ASR_MODELE, token=os.environ.get("HF_TOKEN"))
    processor = WhisperProcessor.from_pretrained(chemin)
    appareil = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    model = WhisperForConditionalGeneration.from_pretrained(chemin).to(appareil).eval()
    return processor, model


def detecter_langue(audio_bytes: bytes) -> dict:
    """Langue d'un vocal (wolof / pulaar / sérère) : {"langue", "confiance", "marge", "probas", "duree_s"}.
    Lève RuntimeError avec un message lisible si la détection n'est pas possible sur cette machine."""
    import io
    from detection_langue import detecter_langue_audio
    try:
        import librosa
    except ImportError as e:
        raise RuntimeError("Il manque librosa pour lire le vocal : pip install librosa") from e
    processor, model = _modele_detection()
    audio, _ = librosa.load(io.BytesIO(audio_bytes), sr=16000, mono=True)
    return detecter_langue_audio(processor, model, audio)


def lecteur(audio: bytes, autoplay: bool = False) -> None:
    try:
        st.audio(audio, format="audio/wav", autoplay=autoplay)
    except TypeError:                     # ancienne version de Streamlit sans autoplay
        st.audio(audio, format="audio/wav")


def choix(label: str, options: list, key: str, format_func=str, default=None):
    """Boutons segmentés si la version de Streamlit les a, sinon boutons radio horizontaux."""
    default = options[0] if default is None else default
    if hasattr(st, "segmented_control"):
        kwargs = {} if key in st.session_state else {"default": default}
        valeur = st.segmented_control(label, options, key=key, format_func=format_func, **kwargs)
        return valeur if valeur is not None else default
    kwargs = {} if key in st.session_state else {"index": options.index(default)}
    return st.radio(label, options, key=key, format_func=format_func, horizontal=True, **kwargs)


STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');
:root { --k-bleu: #3b82f6; --k-violet: #8b5cf6; --k-carte: rgba(30, 41, 59, 0.72);
        --k-bord: rgba(255, 255, 255, 0.10); --k-texte: #f8fafc; --k-gris: #94a3b8; }
html, body, .stApp, .stMarkdown, button, input, textarea, select, label { font-family: 'Outfit', sans-serif !important; }
.stApp {
  background:
    radial-gradient(circle at 8% -5%, rgba(59, 130, 246, 0.16) 0, transparent 38%),
    radial-gradient(circle at 95% 105%, rgba(139, 92, 246, 0.16) 0, transparent 40%),
    #0f172a;
}
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 2.2rem; max-width: 1180px; }
.k-hero { margin: 0 0 1.2rem 0; }
.k-hero h1 { font-size: 2.5rem; font-weight: 700; margin: 0; line-height: 1.15;
  background: linear-gradient(135deg, var(--k-bleu), var(--k-violet));
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.k-hero p { color: var(--k-gris); font-size: 1.05rem; margin: .25rem 0 0 0; }
.k-badge { display: inline-block; padding: .15rem .65rem; border-radius: 999px; font-size: .78rem; font-weight: 600;
  background: rgba(59, 130, 246, .15); color: #93c5fd; border: 1px solid rgba(59, 130, 246, .35); margin-left: .5rem;
  vertical-align: middle; -webkit-text-fill-color: #93c5fd; }
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {
  background: var(--k-carte); border: 1px solid var(--k-bord) !important; border-radius: 20px;
  backdrop-filter: blur(12px); }
[data-testid="stChatMessage"] { background: rgba(15, 23, 42, 0.55); border: 1px solid var(--k-bord);
  border-radius: 16px; padding: .75rem 1rem; }
.stButton > button, .stFormSubmitButton > button { border-radius: 12px; font-weight: 600; }
.stButton > button[kind="primary"] { background: linear-gradient(135deg, var(--k-bleu), var(--k-violet)); border: 0; }
.k-carte-titre { font-size: .8rem; letter-spacing: .08em; text-transform: uppercase; color: var(--k-gris);
  font-weight: 600; margin-bottom: .35rem; }
.k-chiffre { font-size: 1.7rem; font-weight: 700; color: var(--k-texte); line-height: 1.1; }
.k-sous { color: var(--k-gris); font-size: .85rem; }
.k-op { display: flex; justify-content: space-between; gap: .5rem; padding: .45rem 0;
  border-bottom: 1px solid var(--k-bord); font-size: .92rem; }
.k-op:last-child { border-bottom: 0; }
.k-op .k-gris, .k-gris { color: var(--k-gris); }
.k-barre { text-decoration: line-through; opacity: .55; }
.k-issue { padding: .7rem 1rem; border-radius: 14px; background: rgba(34, 197, 94, .12);
  border: 1px solid rgba(34, 197, 94, .35); color: #bbf7d0; font-weight: 600; }
.k-issue.neutre { background: rgba(148, 163, 184, .12); border-color: rgba(148, 163, 184, .35); color: #e2e8f0; }
.k-vide { text-align: center; padding: 2.2rem 1rem 1.2rem 1rem; color: var(--k-gris); }
.k-vide .k-grand { font-size: 2.6rem; }
.k-note-fr { color: #fbbf24; font-size: .8rem; }
</style>
"""


def style() -> None:
    st.markdown(STYLE, unsafe_allow_html=True)


def entete(titre: str, sous_titre: str, badge: str | None = None) -> None:
    b = f'<span class="k-badge">{badge}</span>' if badge else ""
    st.markdown(f'<div class="k-hero"><h1>{titre}{b}</h1><p>{sous_titre}</p></div>', unsafe_allow_html=True)
