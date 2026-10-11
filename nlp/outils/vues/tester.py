"""Page « Tester Kaddu » : l'espace de test de l'équipe (Ndèye, Khalifa, Marème...).

On parle à Kaddu comme un commerçant, en vocal ou en texte, en wolof, pulaar ou sérère. Kaddu répond à voix
haute. Tout ce qui est fait ici va dans nlp/data/equipe/ (carnet, sessions, mots à valider, vocaux) : rien
n'impacte la base de test de Marème ni le reste du projet.
"""
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vues import commun as C  # noqa: E402

import atlas  # noqa: E402
from extraire_llm import MODELES  # noqa: E402
from pipeline import BASE_TEST, LANGUE_REPONSE, LANGUE_SECOURS, LANGUES, SESSIONS, BaseTest, Conversation, VOIX_TTS  # noqa: E402

C.style()
ss = st.session_state

CARNETS = {"equipe": ("Carnet de l'équipe", C.EQUIPE_CARNET, C.EQUIPE_SESSIONS, C.EQUIPE_TERMES),
           "mareme": ("Base de test de Marème", BASE_TEST, SESSIONS, C.DATA / "termes_a_valider.db")}


def ressources(carnet: str):
    """Une connexion par personne connectée (plusieurs testeurs en même temps sans conflit)."""
    cle = f"t_res_{carnet}"
    if cle not in ss:
        _, base, _, termes = CARNETS[carnet]
        C.EQUIPE.mkdir(parents=True, exist_ok=True)
        ss[cle] = (BaseTest(base), atlas.FileTermes(termes))
    return ss[cle]


def journal_vocal(fichier: str, langue: str, transcription: str) -> None:
    import csv
    nouveau = not C.EQUIPE_JOURNAL.exists()
    with open(C.EQUIPE_JOURNAL, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "fichier", "modele", "langue", "transcription", "duree_s"])
        if nouveau:
            w.writeheader()
        w.writerow({"date": datetime.now().isoformat(timespec="seconds"), "fichier": fichier,
                    "modele": C.ASR_MODELE, "langue": LANGUES[langue]["asr"], "transcription": transcription,
                    "duree_s": ""})


# ---------------------------------------------------------------------------
# En-tête et réglages
# ---------------------------------------------------------------------------
C.entete("Kaddu", "Parle à Kaddu comme un commerçant : il comprend, répète pour confirmer, puis note.",
         badge="Espace de test")

# Phrase envoyée depuis la page « Revue des enregistrements » : on se met dans sa langue
if ss.get("t_message_a_envoyer"):
    ss.t_langue = ss.t_message_a_envoyer[1]

r1, r2, r3 = st.columns([1.6, 1, 1], vertical_alignment="bottom")
with r1:
    langue = C.choix("Langue du commerçant", list(LANGUES), key="t_langue",
                     format_func=lambda c: LANGUES[c]["nom"], default="wo")
with r2:
    voix_par_defaut = LANGUES[langue]["voix"]
    if ss.get("t_langue_prec") != langue:            # changement de langue : voix par défaut de la langue
        ss.t_voix = voix_par_defaut
    voix = st.selectbox("Voix de Kaddu", list(VOIX_TTS), key="t_voix",
                        help="Pas de voix sérère : Kaddu répond au sérère en wolof, avec la voix wolof.")
with r3:
    lecture_auto = st.toggle("Lire les réponses à voix haute", value=True, key="t_auto")

SECOURS = {"wo": "Wolof", "fr": "Français"}
if langue in LANGUE_REPONSE:
    secours = LANGUE_SECOURS
    st.caption(f"Kaddu comprend le {LANGUES[langue]['nom'].lower()} et répond en "
               f"{LANGUES[LANGUE_REPONSE[langue]]['nom'].lower()} (texte et voix).")
elif langue != "wo":
    secours = st.radio("Phrases pas encore traduites dans cette langue : les dire en", list(SECOURS),
                       format_func=SECOURS.get, horizontal=True, key="t_secours",
                       index=list(SECOURS).index(LANGUE_SECOURS),
                       help="En attendant les phrases de Ndèye. Pense à choisir la voix wolof si tu veux les "
                            "entendre avec la bonne prononciation.")
else:
    secours = LANGUE_SECOURS

with st.expander("⚙️ Réglages avancés"):
    a1, a2, a3 = st.columns(3)
    modele = a1.selectbox("Modèle de compréhension (LLM)", MODELES, key="t_modele")
    technique = a2.toggle("Mode technique (détails LLM et atlas)", key="t_technique")
    carnet = a3.radio("Carnet utilisé", list(CARNETS), format_func=lambda c: CARNETS[c][0], key="t_carnet",
                      help="Le carnet de l'équipe est séparé de la base de test de Marème.")

base, file_termes = ressources(carnet)
sessions = CARNETS[carnet][2]

# Changer de langue pendant une conversation en commence une nouvelle
conv: Conversation | None = ss.get("t_conv")
if conv and conv.langue != langue:
    conv = ss.t_conv = None
ss.t_langue_prec = langue
ss.setdefault("t_audios", {})          # (conversation, index du message) -> audio
ss.setdefault("t_n", 0)                # numéro de conversation (pour les clés des audios)


def nouvelle_conversation(source: str) -> Conversation:
    ss.t_n += 1
    return Conversation(modele, base, langue=langue, source=source, file_termes=file_termes, sessions=sessions)


def envoyer(texte: str, source: str = "texte", audio_commercant: bytes | None = None) -> None:
    """Envoie un message à Kaddu (nouvelle conversation si besoin) et prépare la lecture de la réponse."""
    conv = ss.get("t_conv")
    if not conv or conv.etat == "termine":
        conv = nouvelle_conversation(source)
    conv.secours = secours
    sauvegarde = conv.vers_dict()
    try:
        with st.spinner("Kaddu réfléchit..."):
            conv.recevoir(texte)
    except Exception as e:                     # clé OpenRouter absente, réseau... : on revient à l'état d'avant
        ss.t_erreur = f"Kaddu n'a pas pu répondre : {e}"
        if sauvegarde["historique"]:
            ss.t_conv = Conversation.depuis_dict(sauvegarde, base, file_termes, sessions)
        return
    ss.t_conv = conv
    if audio_commercant:
        ss.t_audios[(ss.t_n, len(conv.historique) - 2)] = audio_commercant
    ss.t_jouer = (ss.t_n, len(conv.historique) - 1) if lecture_auto else None


# Envoi de la phrase venue de la page « Revue des enregistrements »
if ss.get("t_message_a_envoyer"):
    texte_revue, _ = ss.pop("t_message_a_envoyer")
    ss.t_conv = None
    envoyer(texte_revue, source="revue")
    conv = ss.t_conv

# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
gauche, droite = st.columns([2.15, 1], gap="large")

with gauche:
    with st.container(border=True):
        if ss.get("t_erreur"):
            st.error(ss.pop("t_erreur"))
        conv = ss.get("t_conv")
        if not conv:
            oui, non = LANGUES[langue]["oui"], LANGUES[langue]["non"]
            st.markdown(f"""<div class="k-vide"><div class="k-grand">🎙️</div>
                <div style="font-size:1.15rem;color:#f8fafc;font-weight:600;margin:.4rem 0 .2rem">
                Envoie un vocal ou écris un message en {LANGUES[langue]['nom'].lower()}</div>
                <div>Exemple : une vente à crédit, un remboursement, « combien Awa me doit ? »<br>
                Quand Kaddu répète l'opération, réponds <b>{oui}</b> (oui) ou <b>{non}</b> (non), ou corrige.</div>
                </div>""", unsafe_allow_html=True)
        else:
            for i, msg in enumerate(conv.historique):
                cle_audio = (ss.t_n, i)
                if msg["role"] == "commercant":
                    with st.chat_message("user", avatar="🧑🏾‍🌾"):
                        audio = ss.t_audios.get(cle_audio)
                        st.markdown(("🎙️ " if audio else "") + msg["texte"])
                        if audio:
                            C.lecteur(audio)
                    continue
                with st.chat_message("assistant", avatar="🟣"):
                    st.markdown(f"**{msg['texte']}**")
                    details = msg.get("details") or {}
                    dite_en = details.get("langue_phrase")
                    if dite_en and dite_en != getattr(conv, "langue_reponse", conv.langue):
                        nom_dite = "français" if dite_en == "fr" else LANGUES[dite_en]["nom"].lower()
                        st.markdown(f'<span class="k-note-fr">Phrase pas encore traduite en '
                                    f'{LANGUES[conv.langue]["nom"].lower()} : dite en {nom_dite}.</span>',
                                    unsafe_allow_html=True)
                    jouer = ss.get("t_jouer") == cle_audio
                    if jouer or cle_audio in ss.t_audios:
                        try:
                            if cle_audio not in ss.t_audios:
                                with st.spinner("Kaddu parle..."):
                                    ss.t_audios[cle_audio] = C.tts_audio(msg["texte"], voix)
                            C.lecteur(ss.t_audios[cle_audio], autoplay=jouer)
                        except RuntimeError as e:
                            st.caption(f"🔇 {e}")
                        if jouer:
                            ss.t_jouer = None
                    elif st.button("🔊 Écouter", key=f"ecouter_{ss.t_n}_{i}"):
                        ss.t_jouer = cle_audio
                        st.rerun()
                    if technique and details:
                        controle = (details.get("atlas") or {}).get("controle_montant", {})
                        if controle.get("alerte"):
                            st.warning(f"Attention : {controle['alerte']}")
                        if controle.get("statut") == "corrige_par_regles":
                            st.warning(f"Montant corrigé par les règles : le LLM avait dit {controle['montant_llm']}, "
                                       f"les règles lisent {controle['montants_regles'][0]}")
                        with st.expander(f"Détails ({details.get('modele_reponse')})"):
                            st.json(details)

            if conv.etat == "termine":
                icone, texte_issue = C.ISSUES_FR.get(conv.issue, ("ℹ️", conv.issue))
                classe = "" if conv.issue in ("enregistre", "annulation_faite", "question") else " neutre"
                st.markdown(f'<div class="k-issue{classe}">{icone} {texte_issue}'
                            + (f' · {conv.nb_corrections} correction(s)' if conv.nb_corrections else "")
                            + '</div>', unsafe_allow_html=True)

        # -- répondre ----------------------------------------------------------------
        st.write("")
        en_cours = conv is not None and conv.etat != "termine"
        if en_cours:
            q1, q2, q3 = st.columns([1, 1, 2.2])
            if q1.button(f"✅ {LANGUES[langue]['oui']}", use_container_width=True, key="t_oui"):
                envoyer(LANGUES[langue]["oui"])
                st.rerun()
            if q2.button(f"❌ {LANGUES[langue]['non']}", use_container_width=True, key="t_non"):
                envoyer(LANGUES[langue]["non"])
                st.rerun()
            q3.caption("Ou réponds par la voix / par écrit pour corriger (montant, client, produit...).")

        if hasattr(st, "audio_input"):
            vocal = st.audio_input("🎙️ Parler à Kaddu", key=f"t_vocal_{ss.get('t_n_vocal', 0)}")
        else:
            vocal = None
            st.warning("Ta version de Streamlit est trop ancienne pour le micro : lance  pip install -U streamlit")
        if vocal is not None:
            audio = vocal.getvalue()
            C.EQUIPE_AUDIOS.mkdir(parents=True, exist_ok=True)
            nom = f"equipe_{langue}_{datetime.now():%Y%m%d_%H%M%S}.wav"
            (C.EQUIPE_AUDIOS / nom).write_bytes(audio)
            with st.spinner(f"Transcription ({LANGUES[langue]['nom']})..."):
                texte_vocal = C.transcrire(audio, LANGUES[langue]["asr"])
            ss.t_n_vocal = ss.get("t_n_vocal", 0) + 1       # vide l'enregistreur pour le vocal suivant
            if texte_vocal:
                journal_vocal(f"equipe/{nom}", langue, texte_vocal)
                envoyer(texte_vocal, source=f"equipe/{nom}", audio_commercant=audio)
                st.rerun()
            elif texte_vocal == "":
                st.warning("Rien n'a été entendu. Rapproche-toi du micro et réessaie.")

        ecrit = st.chat_input("Écrire un message à Kaddu...", key="t_ecrit")
        if ecrit and ecrit.strip():
            envoyer(ecrit.strip())
            st.rerun()

# ---------------------------------------------------------------------------
# Carnet et scénarios
# ---------------------------------------------------------------------------
with droite:
    if st.button("✨ Nouvelle conversation", type="primary", use_container_width=True, disabled=conv is None):
        ss.t_conv = None
        st.rerun()

    with st.container(border=True):
        st.markdown(f'<div class="k-carte-titre">{CARNETS[carnet][0]}</div>', unsafe_allow_html=True)
        m1, m2 = st.columns(2)
        m1.markdown(f'<div class="k-sous">Ventes du jour</div><div class="k-chiffre">'
                    f'{C.francs(base.ventes_du_jour())}</div>', unsafe_allow_html=True)
        m2.markdown(f'<div class="k-sous">Crédits en cours</div><div class="k-chiffre">'
                    f'{C.francs(max(base.dette(None), 0))}</div>', unsafe_allow_html=True)
        ops, _ = base.tables()
        st.write("")
        if not ops:
            st.markdown('<div class="k-sous">Aucune opération pour l\'instant.</div>', unsafe_allow_html=True)
        lignes = []
        for o in ops[:7]:
            _id, _date, lang, intention, client, montant, *_reste, statut = o
            nom = C.INTENTIONS_FR.get(intention, intention.replace("_", " "))
            barre = ' class="k-barre"' if statut == "annulee" else ""
            lignes.append(f'<div class="k-op"><span{barre}>{nom}'
                          f'{" · " + client if client else ""} <span class="k-gris">· {LANGUES.get(lang, {}).get("nom", lang)}</span></span>'
                          f'<span{barre}>{C.francs(montant) if montant is not None else "—"}</span></div>')
        st.markdown("".join(lignes), unsafe_allow_html=True)
        if carnet == "equipe":
            with st.popover("Remettre le carnet à zéro", use_container_width=True):
                st.caption("Efface toutes les opérations du carnet de l'équipe (pas les vocaux ni les métriques).")
                if st.button("Oui, tout effacer", key="t_vider"):
                    base.vider()
                    ss.t_conv = None
                    st.rerun()

    with st.container(border=True):
        st.markdown('<div class="k-carte-titre">Scénarios de test (Ndèye)</div>', unsafe_allow_html=True)
        st.caption("Phrases du document « Phases Tests Trilingue ». Clique pour les envoyer en texte.")
        for i, (titre, phrase_test) in enumerate(C.SCENARIOS[langue]):
            if st.button(f"{titre} — {phrase_test}", key=f"t_scen_{langue}_{i}", use_container_width=True):
                ss.t_conv = None
                envoyer(phrase_test, source="scenario")
                st.rerun()

    with st.expander("Comment tester ?"):
        st.markdown(f"""
1. Choisis la **langue** en haut (la voix change toute seule ; le sérère utilise la voix pulaar).
2. Appuie sur le **micro**, parle, puis arrête : Kaddu transcrit et répond. Tu peux aussi écrire.
3. Kaddu répète l'opération : réponds **{LANGUES[langue]['oui']}** / **{LANGUES[langue]['non']}**, ou corrige
   (« non, c'est 2 000 »).
4. Le carnet à droite montre ce qui a été noté. **Nouvelle conversation** pour recommencer.

Les phrases en **orange** n'ont pas encore de traduction dans la langue : Kaddu les dit en wolof
(ou en français, au choix, sous les réglages de langue).
""")
