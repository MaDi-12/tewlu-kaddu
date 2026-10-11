"""Interface TEWLU Kaddu (Streamlit) :
    - Tester Kaddu                   : l'espace de test de l'équipe (vocal ou texte, wolof / pulaar / sérère)
    - Tableau de bord du commerçant  : ventes, crédits, clients et dettes, stock, bilan du jour lu par Kaddu
    - Revue des enregistrements      : audios, transcription, traduction GalsenAI, correction, TTS
    - Revue des conversations        : chaque discussion complète, à annoter pour améliorer les modèles
    - Métriques et données           : métriques, mots à valider, opérations enregistrées

Lancer (depuis la racine du dépôt) : streamlit run nlp/outils/revue.py
Lien à partager à l'équipe (test + tableau de bord seulement) : ajouter  ?vue=equipe  à l'adresse.
"""
import streamlit as st

st.set_page_config(page_title="TEWLU Kaddu", page_icon="🎙️", layout="wide")

# ?vue=equipe : retenu pour toute la session (le paramètre peut disparaître de l'adresse en changeant de page)
if st.query_params.get("vue") == "equipe":
    st.session_state.vue_equipe = True
vue_equipe = st.session_state.get("vue_equipe", False)
pages = [st.Page("vues/tester.py", title="Tester Kaddu", icon="💬", url_path="tester", default=True),
         st.Page("vues/tableau_de_bord.py", title="Tableau de bord du commerçant", icon="📊", url_path="tableau-de-bord")]
if not vue_equipe:
    pages += [st.Page("vues/revue_enregistrements.py", title="Revue des enregistrements", icon="🎧", url_path="revue"),
              st.Page("vues/conversations.py", title="Revue des conversations", icon="🗂️", url_path="conversations"),
              st.Page("vues/metriques.py", title="Métriques et données", icon="📈", url_path="metriques")]
try:
    navigation = st.navigation(pages, position="top" if vue_equipe else "sidebar")
except Exception:                       # Streamlit plus ancien, sans menu en haut
    navigation = st.navigation(pages)
navigation.run()
