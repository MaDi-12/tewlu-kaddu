"""Page « Revue des enregistrements » (Marème) : l'ancienne vue, sans le pipeline.

Réécouter chaque audio, lire la transcription de l'ASR et la traduction GalsenAI, corriger la transcription,
la faire relire par le TTS. Le bouton « Tester avec Kaddu » envoie la phrase à la page Tester Kaddu.
"""
import csv
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vues import commun as C  # noqa: E402

from pipeline import LANGUES, VOIX_TTS  # noqa: E402

JOURNAL_ASR = C.DATA / "journal_asr.csv"
JOURNAL_TRADUIT = C.DATA / "journal_traduit.csv"
JOURNAL_LIVE = C.DATA / "journal_live.csv"
CORRECTIONS = C.DATA / "corrections.csv"
CODES_ASR = {v["asr"]: k for k, v in LANGUES.items()}

C.style()


def enregistrer_correction(fichier, texte, note):
    corrections = {c["fichier"]: c for c in C.lire_csv(CORRECTIONS)}
    corrections[fichier] = {"fichier": fichier, "transcription_corrigee": texte, "note": note}
    with open(CORRECTIONS, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["fichier", "transcription_corrigee", "note"])
        w.writeheader()
        w.writerows(corrections.values())


C.entete("Revue des enregistrements", "Réécouter, lire la transcription et la traduction, corriger, faire relire.")

lignes = (C.lire_csv(JOURNAL_TRADUIT) or C.lire_csv(JOURNAL_ASR)) + C.lire_csv(JOURNAL_LIVE) \
    + C.lire_csv(C.EQUIPE_JOURNAL)
if not lignes:
    st.warning("Aucun journal trouvé. Lance d'abord : python nlp/outils/transcrire_dossier.py")
    st.stop()

corrections = {c["fichier"]: c for c in C.lire_csv(CORRECTIONS)}
fichier = st.sidebar.radio("Audio", [l["fichier"] for l in lignes],
                           format_func=lambda f: ("✅ " if f in corrections else "")
                           + ("🎙️ " if f.startswith(("live/", "equipe/")) else "") + f)
ligne = next(l for l in lignes if l["fichier"] == fichier)
correction = corrections.get(fichier, {})
langue = CODES_ASR.get(ligne.get("langue") or "wo", "wo")

col_g, col_d = st.columns(2, gap="large")

with col_g:
    with st.container(border=True):
        st.subheader("1. Ce qui a été dit")
        wav = C.DOSSIER_AUDIOS / "wav" / (Path(fichier).stem + ".wav")
        original = wav if wav.exists() else C.DOSSIER_AUDIOS / fichier
        if original.exists():
            st.audio(str(original))
        else:
            st.info(f"Audio introuvable : {original}")

        st.subheader("2. Ce que l'ASR a compris")
        transcription = C.nettoyer(ligne["transcription"])
        st.text_area("Transcription (lecture seule)", transcription, height=120, disabled=True)
        st.caption(f"Modèle : {ligne.get('modele', '')} · langue : {ligne.get('langue', '')} · "
                   f"{ligne.get('duree_s', '')} s")

        if ligne.get("traduction_fr"):
            st.subheader("3. Traduction GalsenAI")
            st.text_area("Traduction (lecture seule)", ligne["traduction_fr"], height=100, disabled=True)

with col_d:
    with st.container(border=True):
        st.subheader("4. Correction")
        texte = st.text_area("Ce qui aurait dû être transcrit",
                             correction.get("transcription_corrigee", transcription), height=120,
                             key=f"correction_{fichier}")
        note = st.text_input("Note (ex. : a dit 10 000, affiché 5 000)", correction.get("note", ""),
                             key=f"note_{fichier}")
        if st.button("Enregistrer la correction"):
            enregistrer_correction(fichier, texte, note)
            st.success("Correction enregistrée dans nlp/data/corrections.csv")

        st.subheader("5. Faire relire par le TTS")
        source = st.radio("Texte à lire", ["Transcription de l'ASR", "Ma correction"], horizontal=True)
        voix = st.selectbox("Voix", list(VOIX_TTS), index=list(VOIX_TTS).index(LANGUES[langue]["voix"]))
        if st.button("Écouter"):
            with st.spinner("Synthèse en cours..."):
                try:
                    C.lecteur(C.tts_audio(transcription if source == "Transcription de l'ASR" else texte, voix))
                except RuntimeError as e:
                    st.error(str(e))

    with st.container(border=True):
        st.subheader("6. Tester avec Kaddu")
        depart = st.radio("Phrase à envoyer", ["Transcription de l'ASR", "Ma correction"], horizontal=True,
                          key="depart")
        st.caption(f"Ouvre la page Tester Kaddu en {LANGUES[langue]['nom'].lower()} avec cette phrase.")
        if st.button("▶️ Tester avec Kaddu", type="primary"):
            st.session_state.t_message_a_envoyer = (transcription if depart == "Transcription de l'ASR" else texte,
                                                    langue)
            st.switch_page("vues/tester.py")
