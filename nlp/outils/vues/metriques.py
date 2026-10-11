"""Page « Métriques et données » : métriques des conversations, mots à valider, opérations enregistrées.
On choisit les données : celles de l'espace de test de l'équipe, ou la base de test de Marème."""
import sys
from collections import Counter
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vues import commun as C  # noqa: E402

import atlas  # noqa: E402
from pipeline import BASE_TEST, LANGUES, SESSIONS, BaseTest  # noqa: E402

C.style()
C.entete("Métriques et données", "Ce que donnent les tests : conversations, montants, mots à valider.")

SOURCES = {"Espace de test de l'équipe": (C.EQUIPE_SESSIONS, C.EQUIPE_CARNET, C.EQUIPE_TERMES),
           "Base de test de Marème": (SESSIONS, BASE_TEST, C.DATA / "termes_a_valider.db")}
s1, s2 = st.columns([1.4, 1])
choix_source = s1.radio("Données", list(SOURCES), horizontal=True)
fichier_sessions, fichier_base, fichier_termes = SOURCES[choix_source]

sessions = C.lire_csv(fichier_sessions)
langues_presentes = sorted({s.get("langue") or "wo" for s in sessions})
filtre = s2.multiselect("Langues", langues_presentes, default=langues_presentes,
                        format_func=lambda c: LANGUES.get(c, {}).get("nom", c))
sessions = [s for s in sessions if (s.get("langue") or "wo") in filtre]
operations = [s for s in sessions if s["intention"] in ("vente", "vente_credit", "remboursement", "achat", "depense")]


def part(n, total):
    return f"{n} ({100 * n / total:.0f} %)" if total else "0"


with st.container(border=True):
    if not sessions:
        st.info("Aucune conversation terminée pour l'instant.")
    else:
        n_op = len(operations)
        premier_coup = sum(s["issue"] == "enregistre" and s["nb_corrections"] == "0" for s in operations)
        apres_correction = sum(s["issue"] == "enregistre" and s["nb_corrections"] != "0" for s in operations)
        abandons = sum(s["issue"] == "abandon" for s in sessions)
        incompris = sum(s["issue"] in ("incomprehension", "reponse_llm_illisible") for s in sessions)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Conversations", len(sessions))
        m2.metric("Opérations justes du premier coup", part(premier_coup, n_op))
        m3.metric("Justes après correction", part(apres_correction, n_op))
        m4.metric("Abandons / incompris", f"{abandons} / {incompris}")

        produits = {k: sum(int(s[f"produits_{k}"] or 0) for s in sessions) for k in ("reconnus", "proches", "nouveaux")}
        montants = {k: sum(s["controle_montant"] == k for s in operations)
                    for k in ("lecture_ok", "corrige_par_regles", "ecart", "non_verifiable")}
        m5, m6, m7, m8 = st.columns(4)
        total_produits = sum(produits.values())
        m5.metric("Produits reconnus par l'atlas", part(produits["reconnus"], total_produits))
        m6.metric("Produits proches / nouveaux", f"{produits['proches']} / {produits['nouveaux']}")
        m7.metric("Montants : bien lus / corrigés par les règles",
                  f"{montants['lecture_ok']} / {montants['corrige_par_regles']}")
        durees = [float(s["duree_llm_s"]) for s in sessions if s.get("duree_llm_s")]
        m8.metric("Temps LLM moyen", f"{sum(durees) / len(durees):.1f} s" if durees else "-")

        par_langue = Counter(s.get("langue") or "wo" for s in sessions)
        st.caption("Conversations par langue : " + " · ".join(
            f"{LANGUES.get(l, {}).get('nom', l)} {n}" for l, n in par_langue.items()))
        with st.expander(f"Toutes les conversations ({fichier_sessions.relative_to(C.NLP.parent)})"):
            st.dataframe(sessions)

with st.expander("Mots à valider par Ndèye", expanded=True):
    if fichier_termes.exists():
        st.dataframe([{"mot": t[0], "sens proposé par le LLM": t[5], "langue": t[1], "catégorie": t[2],
                       "fois": t[3], "exemple": t[4]} for t in atlas.FileTermes(fichier_termes).lister()])
    else:
        st.caption("Aucun mot pour l'instant.")

with st.expander("Opérations enregistrées"):
    if fichier_base.exists():
        ops, arts = BaseTest(fichier_base).tables()
        st.dataframe([dict(zip(["id", "date", "langue", "intention", "client", "montant", "montant dit",
                                "contrôle montant", "messages", "modèle", "corrections", "statut"], o)) for o in ops])
        st.dataframe([dict(zip(["id", "opération", "produit dit", "produit", "statut", "quantité", "unité", "prix"], a))
                      for a in arts])
    else:
        st.caption("Aucune opération pour l'instant.")
