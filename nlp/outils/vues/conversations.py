"""Page « Revue des conversations » : relire chaque discussion complète avec Kaddu, l'annoter, l'exporter.

Chaque conversation (terminée, interrompue ou expirée) est archivée en entier par pipeline.Conversation.archiver()
dans un fichier conversations_*.jsonl : messages du commerçant (avec leur vocal), réponses de Kaddu, ce que le LLM
a compris à chaque tour, contrôles de l'atlas. Les annotations servent à corriger et améliorer ASR, LLM et atlas.
"""
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vues import commun as C  # noqa: E402

from pipeline import LANGUES  # noqa: E402

C.style()
C.entete("Revue des conversations",
         "Chaque discussion complète avec Kaddu : ce que le commerçant a dit, ce que Kaddu a compris et répondu.")

VERDICTS = ["À revoir", "Correcte", "Erreur de transcription (ASR)", "Erreur de compréhension (LLM)",
            "Erreur de montant", "Mot inconnu ou mal rapproché (atlas)", "Réponse de Kaddu à corriger", "Autre"]
CHAMPS_ANNOTATION = ["id", "verdict", "transcription_correcte", "comprehension_correcte", "note", "date"]


def lire_archive(chemin: Path) -> list[dict]:
    if not chemin.exists():
        return []
    conversations = []
    with open(chemin, encoding="utf-8") as f:
        for ligne in f:
            ligne = ligne.strip()
            if ligne:
                try:
                    conversations.append(json.loads(ligne))
                except json.JSONDecodeError:
                    pass
    return conversations[::-1]                        # les plus récentes d'abord


def chemin_annotations(archive: Path) -> Path:
    return archive.with_name(archive.stem + "_annotations.csv")


def lire_annotations(archive: Path) -> dict[str, dict]:
    return {a["id"]: a for a in C.lire_csv(chemin_annotations(archive))}


def enregistrer_annotation(archive: Path, annotation: dict) -> None:
    annotations = lire_annotations(archive)
    annotations[annotation["id"]] = annotation
    with open(chemin_annotations(archive), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CHAMPS_ANNOTATION)
        w.writeheader()
        w.writerows(annotations.values())


# ---------------------------------------------------------------------------
# Choix des données et filtres
# ---------------------------------------------------------------------------
archives = {nom: chemin for nom, chemin in C.ARCHIVES_CONVERSATIONS.items()}
f1, f2, f3, f4 = st.columns([1.5, 1, 1, 1])
source = f1.selectbox("Conversations de", list(archives))
archive = archives[source]
conversations = lire_archive(archive)
annotations = lire_annotations(archive)

if not conversations:
    st.info(f"Aucune conversation archivée pour l'instant ({archive.relative_to(C.NLP.parent)}). Les conversations "
            "sont archivées à leur fin, ou quand on en commence une nouvelle.")
    st.stop()

langues = sorted({c.get("langue") or "wo" for c in conversations})
filtre_langues = f2.multiselect("Langues", langues, default=langues,
                                format_func=lambda l: LANGUES.get(l, {}).get("nom", l))
issues = sorted({c.get("issue") or "" for c in conversations})
filtre_issues = f3.multiselect("Issue", issues, default=issues,
                               format_func=lambda i: C.ISSUES_FR.get(i, ("", i))[1] or i)
filtre_annot = f4.selectbox("Annotation", ["Toutes", "Pas encore annotées", "Annotées", "Avec erreur"])


def garder(c):
    if (c.get("langue") or "wo") not in filtre_langues or (c.get("issue") or "") not in filtre_issues:
        return False
    a = annotations.get(c["id"])
    if filtre_annot == "Pas encore annotées":
        return a is None
    if filtre_annot == "Annotées":
        return a is not None
    if filtre_annot == "Avec erreur":
        return a is not None and a["verdict"] not in ("Correcte", "À revoir")
    return True


visibles = [c for c in conversations if garder(c)]
n_annot = sum(c["id"] in annotations for c in conversations)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Conversations", len(conversations))
m2.metric("Affichées", len(visibles))
m3.metric("Annotées", f"{n_annot} / {len(conversations)}")
m4.metric("Avec erreur", sum(a["verdict"] not in ("Correcte", "À revoir") for a in annotations.values()))

d1, d2 = st.columns(2)
d1.download_button("⬇️ Exporter les conversations (JSONL)", archive.read_bytes(), file_name=archive.name,
                   mime="application/json", use_container_width=True)
if chemin_annotations(archive).exists():
    d2.download_button("⬇️ Exporter les annotations (CSV)", chemin_annotations(archive).read_bytes(),
                       file_name=chemin_annotations(archive).name, mime="text/csv", use_container_width=True)

if not visibles:
    st.warning("Aucune conversation ne correspond aux filtres.")
    st.stop()


def libelle(c):
    premier = next((m["texte"] for m in c.get("historique", []) if m["role"] == "commercant"), "")
    date = (c.get("debut") or "")[5:16].replace("T", " ")
    marque = "✅ " if c["id"] in annotations else ""
    nom_langue = LANGUES.get(c.get("langue"), {}).get("nom", c.get("langue"))
    return f"{marque}{date} · {nom_langue} · {c.get('intention') or '?'} · {c.get('issue')} · {premier[:50]}"


choisie = st.selectbox("Conversation", visibles, format_func=libelle)

# ---------------------------------------------------------------------------
# La conversation
# ---------------------------------------------------------------------------
gauche, droite = st.columns([1.6, 1], gap="large")
with gauche:
    with st.container(border=True):
        icone, texte_issue = C.ISSUES_FR.get(choisie.get("issue"), ("ℹ️", choisie.get("issue")))
        st.caption(f"{icone} {texte_issue} · langue : {LANGUES.get(choisie.get('langue'), {}).get('nom')} · "
                   f"réponses en {LANGUES.get(choisie.get('langue_reponse'), {}).get('nom', '?')} · "
                   f"corrections : {choisie.get('nb_corrections')} · temps LLM : {choisie.get('duree_llm_s')} s · "
                   f"source : {choisie.get('source')}")
        for i, msg in enumerate(choisie.get("historique", [])):
            details = msg.get("details") or {}
            if msg["role"] == "commercant":
                with st.chat_message("user", avatar="🧑🏾‍🌾"):
                    audio = details.get("audio")
                    st.markdown(("🎙️ " if audio else "") + msg["texte"])
                    if audio and (C.DOSSIER_AUDIOS / audio).exists():
                        st.audio(str(C.DOSSIER_AUDIOS / audio))
                    if details.get("detection"):
                        d = details["detection"]
                        st.caption(f"Langue détectée : {d.get('langue')} ({d.get('confiance')})")
            else:
                with st.chat_message("assistant", avatar="🟣"):
                    st.markdown(f"**{msg['texte']}**")
                    if details:
                        llm = details.get("llm") or {}
                        resume = []
                        if llm:
                            resume.append(f"intention : {llm.get('intention')}")
                            if llm.get("client"):
                                resume.append(f"client : {llm.get('client')}")
                            if llm.get("montant_fcfa") is not None:
                                resume.append(f"montant : {C.francs(llm.get('montant_fcfa'))}")
                        st.caption(" · ".join(resume + [f"phrase : {details.get('modele_reponse')}"]))
                        with st.expander("Détails (LLM, atlas)"):
                            st.json(details)

with droite:
    with st.container(border=True):
        st.markdown('<div class="k-carte-titre">Annotation</div>', unsafe_allow_html=True)
        existante = annotations.get(choisie["id"], {})
        with st.form(f"annot_{choisie['id']}"):
            verdict = st.selectbox("Verdict", VERDICTS,
                                   index=VERDICTS.index(existante["verdict"]) if existante.get("verdict") in VERDICTS else 0)
            transcription = st.text_area("Ce que le commerçant a vraiment dit (si l'ASR s'est trompé)",
                                         existante.get("transcription_correcte", ""), height=90)
            comprehension = st.text_area("Ce que Kaddu aurait dû comprendre ou répondre",
                                         existante.get("comprehension_correcte", ""), height=90)
            note = st.text_input("Note", existante.get("note", ""))
            if st.form_submit_button("Enregistrer l'annotation", type="primary", use_container_width=True):
                enregistrer_annotation(archive, {"id": choisie["id"], "verdict": verdict,
                                                 "transcription_correcte": transcription,
                                                 "comprehension_correcte": comprehension, "note": note,
                                                 "date": datetime.now().isoformat(timespec="seconds")})
                st.success("Annotation enregistrée.")
                st.rerun()
        st.caption(f"Annotations : {chemin_annotations(archive).relative_to(C.NLP.parent)}")
