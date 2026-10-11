"""Page « Tableau de bord du commerçant » : tout son carnet en un coup d'œil.

- La période choisie (aujourd'hui, hier, 7 jours, 30 jours, ce mois, tout) s'applique à tout : chiffres, graphiques,
  produits, journal. Chaque chiffre est comparé à la même durée juste avant.
- Kaddu lit à voix haute ce qui a une phrase validée par Ndèye (reponses.csv) : le bilan du jour, le total des
  crédits, la dette de chaque client. Le sérère est lu en wolof (LANGUE_REPONSE), comme dans le pipeline.
- Un clic sur un client ouvre son compte. L'onglet « À relancer » liste les crédits qui traînent.
- Le « Carnet de démonstration » (60 jours d'activité réaliste) se crée en un clic, séparé des vrais carnets.
"""
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vues import commun as C  # noqa: E402

from pipeline import LANGUE_REPONSE, LANGUES, _montant, phrase_et_langue  # noqa: E402

BLEU, VIOLET, VERT, ORANGE = "#3b82f6", "#8b5cf6", "#22c55e", "#f59e0b"
JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre"]
RELANCE_JOURS = 7                       # un crédit non remboursé depuis plus de 7 jours est « à relancer »

C.style()
C.entete("Tableau de bord du commerçant", "Ce que la boutique a vendu, ce qu'on lui doit, et qui relancer.")


# ---------------------------------------------------------------------------
# Petits outils
# ---------------------------------------------------------------------------
def jour(o) -> date:
    try:
        return datetime.fromisoformat(o["date"]).date()
    except (TypeError, ValueError):
        return date.min


def heure(o) -> int:
    try:
        return datetime.fromisoformat(o["date"]).hour
    except (TypeError, ValueError):
        return 0


def date_fr(d: date, annee: bool = False) -> str:
    return f"{JOURS_FR[d.weekday()]} {d.day} {MOIS_FR[d.month - 1]}" + (f" {d.year}" if annee else "")


def date_courte(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}"


def mt(o) -> int:
    return int(o.get("montant_fcfa") or 0)


def somme(ops, *intentions) -> int:
    return sum(mt(o) for o in ops if o["intention"] in intentions)


def article(a) -> str:
    """« 10 saak Riz » : quantité, unité et produit (nom de l'atlas si reconnu)."""
    q = a.get("quantite")
    try:
        q = (str(int(q)) if float(q).is_integer() else str(q)) if q else ""
    except (TypeError, ValueError):
        q = str(q or "")
    return " ".join(x for x in (q, a.get("unite") or "", a.get("produit") or a.get("produit_dit") or "") if x)


def evolution(actuel: int, avant: int | None) -> str | None:
    """« +12 % » par rapport à la période d'avant (None si pas de comparaison possible)."""
    if avant is None:
        return None
    if avant == 0:
        return None if actuel == 0 else "nouveau"
    p = round((actuel - avant) * 100 / avant)
    return f"{p:+d} %"


def tableau(donnees, **kwargs):
    st.dataframe(donnees, use_container_width=True, hide_index=True, **kwargs)


def ecouter(texte: str, langue: str, cle: str, libelle: str = "🔊 Écouter") -> None:
    """Bouton qui fait lire `texte` par Kaddu (voix de la langue de réponse)."""
    if st.button(libelle, key=cle, use_container_width=True):
        try:
            with st.spinner("Kaddu parle..."):
                C.lecteur(C.tts_audio(texte, LANGUES[langue]["voix"]), autoplay=True)
        except RuntimeError as e:
            st.caption(f"🔇 {e}")


def graphique(chart) -> None:
    """Graphique Altair aux couleurs de l'interface (fond transparent, texte gris clair)."""
    chart = chart.configure(background="transparent").configure_view(strokeWidth=0) \
        .configure_axis(labelColor="#94a3b8", titleColor="#94a3b8", gridColor="rgba(148,163,184,0.15)",
                        domainColor="rgba(148,163,184,0.3)", labelFont="Outfit, sans-serif", titleFont="Outfit, sans-serif") \
        .configure_legend(labelColor="#e2e8f0", titleColor="#94a3b8", orient="top", labelFont="Outfit, sans-serif")
    st.altair_chart(chart, use_container_width=True)


# ---------------------------------------------------------------------------
# Choix du carnet, de la période et de la langue
# ---------------------------------------------------------------------------
carnets = C.carnets_disponibles()
h1, h2 = st.columns([1.2, 1])
noms = list(carnets)
if st.session_state.get("tb_carnet") not in noms:
    st.session_state.pop("tb_carnet", None)
if "tb_carnet" not in st.session_state and "Carnet de démonstration" in noms:
    st.session_state["tb_carnet"] = "Carnet de démonstration"
nom_carnet = h1.selectbox("Carnet", noms, key="tb_carnet")
langue_choisie = h2.selectbox("Langue de Kaddu", list(LANGUES), format_func=lambda l: LANGUES[l]["nom"],
                              key="tb_langue")
L = LANGUE_REPONSE.get(langue_choisie, langue_choisie)          # sérère -> réponses en wolof
PERIODES = ["Aujourd'hui", "Hier", "7 jours", "30 jours", "Ce mois", "Tout"]
periode = C.choix("Période", PERIODES, key="tb_periode")

with st.expander("🧪 Carnet de démonstration (60 jours d'activité inventée, pour montrer le tableau de bord)"):
    st.caption("Une boutique de quartier : ventes au comptant et à crédit, remboursements, entrées de stock, en "
               "wolof, pulaar et sérère, avec les noms de produits de l'atlas. Il est rangé à part "
               "(nlp/data/equipe/carnet_demo.db) et ne touche à aucun vrai carnet.")
    if st.button("Créer / recréer le carnet de démonstration", key="tb_demo"):
        import donnees_demo
        n = donnees_demo.generer()
        st.session_state["tb_carnet"] = "Carnet de démonstration"
        st.session_state["tb_demo_fait"] = n
        st.rerun()
if st.session_state.pop("tb_demo_fait", None):
    st.success("Carnet de démonstration créé : deux mois d'activité. Change de période pour comparer.")

if langue_choisie != L:
    st.caption(f"ℹ️ En {LANGUES[langue_choisie]['nom']}, Kaddu répond en {LANGUES[L]['nom']} "
               "(phrases et voix), comme sur WhatsApp.")

operations = C.lire_operations(*carnets[nom_carnet])
if not operations:
    st.markdown('<div class="k-vide"><div class="k-grand">📒</div>Ce carnet est vide pour l\'instant.<br>'
                'Enregistre des opérations depuis la page « Tester Kaddu », ou ouvre « Carnet de démonstration » '
                'ci-dessus pour voir à quoi ressemblent deux mois d\'activité.</div>', unsafe_allow_html=True)
    st.stop()

actives = [o for o in operations if o.get("statut", "active") == "active"]
aujourd_hui = date.today()
premier_jour = min((jour(o) for o in actives if jour(o) != date.min), default=aujourd_hui)

if periode == "Aujourd'hui":
    debut = fin = aujourd_hui
elif periode == "Hier":
    debut = fin = aujourd_hui - timedelta(days=1)
elif periode == "7 jours":
    debut, fin = aujourd_hui - timedelta(days=6), aujourd_hui
elif periode == "30 jours":
    debut, fin = aujourd_hui - timedelta(days=29), aujourd_hui
elif periode == "Ce mois":
    debut, fin = aujourd_hui.replace(day=1), aujourd_hui
else:
    debut, fin = min(premier_jour, aujourd_hui), aujourd_hui
nb_jours = (fin - debut).days + 1
if periode == "Tout":
    avant_debut = avant_fin = None
else:
    avant_fin = debut - timedelta(days=1)
    avant_debut = avant_fin - timedelta(days=nb_jours - 1)

dans_periode = [o for o in actives if debut <= jour(o) <= fin]
avant = None if avant_debut is None else [o for o in actives if avant_debut <= jour(o) <= avant_fin]
comparaison = {"Aujourd'hui": "hier", "Hier": "avant-hier", "7 jours": "les 7 jours d'avant",
               "30 jours": "les 30 jours d'avant", "Ce mois": f"les {nb_jours} jours d'avant"}.get(periode)

if debut == fin:
    libelle_periode = date_fr(debut)
else:
    libelle_periode = f"du {date_fr(debut)} au {date_fr(fin)}"

# --- Clients et dettes : sur tout l'historique (une dette ne dépend pas de la période)
clients = defaultdict(lambda: {"nom": "", "pris": 0, "rendu": 0, "derniere": None, "dernier_credit": None,
                               "dernier_paiement": None, "ops": []})
for o in sorted(actives, key=lambda o: o.get("date") or ""):
    if o.get("client"):
        c = clients[o["client"].strip().casefold()]
        c["nom"] = c["nom"] or o["client"].strip()
        c["ops"].append(o)
        c["derniere"] = o["date"]
        if o["intention"] == "vente_credit":
            c["pris"] += mt(o)
            c["dernier_credit"] = jour(o)
        elif o["intention"] == "remboursement":
            c["rendu"] += mt(o)
            c["dernier_paiement"] = jour(o)
for c in clients.values():
    c["reste"] = max(c["pris"] - c["rendu"], 0)
credits_en_cours = sum(c["reste"] for c in clients.values())
debiteurs = sorted((c for c in clients.values() if c["reste"] > 0), key=lambda c: -c["reste"])

# ---------------------------------------------------------------------------
# Chiffres clés de la période
# ---------------------------------------------------------------------------
ventes = somme(dans_periode, "vente", "vente_credit")
comptant = somme(dans_periode, "vente")
a_credit = somme(dans_periode, "vente_credit")
rembourse = somme(dans_periode, "remboursement")
encaisse = comptant + rembourse
nb_ventes = sum(o["intention"] in ("vente", "vente_credit") for o in dans_periode)
panier = ventes // nb_ventes if nb_ventes else 0
credit_net = a_credit - rembourse                    # ce que les crédits ont augmenté sur la période

if avant is not None:
    v_av = somme(avant, "vente", "vente_credit")
    e_av = somme(avant, "vente") + somme(avant, "remboursement")
    n_av = sum(o["intention"] in ("vente", "vente_credit") for o in avant)
    p_av = v_av // n_av if n_av else 0
else:
    v_av = e_av = n_av = p_av = None

with st.container(border=True):
    st.markdown(f'<div class="k-carte-titre">{periode} · {libelle_periode}'
                + (f' · comparé à {comparaison}' if comparaison else '') + '</div>', unsafe_allow_html=True)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Ventes", C.francs(ventes), evolution(ventes, v_av),
              help=f"Comptant {C.francs(comptant)} + à crédit {C.francs(a_credit)}."
                   + (f" Comparé à {comparaison}." if comparaison else ""))
    k2.metric("Argent rentré dans la caisse", C.francs(encaisse), evolution(encaisse, e_av),
              help=f"Ventes au comptant {C.francs(comptant)} + remboursements reçus {C.francs(rembourse)}.")
    k3.metric("Crédits en cours", C.francs(credits_en_cours),
              (f"{'+' if credit_net > 0 else ''}{C.francs(credit_net)} sur la période" if credit_net else None),
              delta_color="inverse",
              help=f"Ce que les clients doivent aujourd'hui, toutes périodes confondues ({len(debiteurs)} clients). "
                   f"Sur la période : {C.francs(a_credit)} donnés à crédit, {C.francs(rembourse)} remboursés.")
    k4.metric("Nombre de ventes", nb_ventes, None if n_av is None else (nb_ventes - n_av or None),
              help=f"Panier moyen : {C.francs(panier)}" + (f" (avant : {C.francs(p_av)})" if p_av else "") + ".")

    # Une phrase « en bref », lisible par tout le monde
    if not dans_periode:
        dernier = max((jour(o) for o in actives), default=None)
        st.info(f"Rien n'a été noté ({libelle_periode})."
                + (f" Dernière opération : {date_fr(dernier)}. Choisis « Tout » ou une période plus longue."
                   if dernier and dernier != date.min else ""))
    else:
        bref = [f"**{nb_ventes} ventes** pour **{C.francs(ventes)}**"
                + (f", dont **{round(a_credit * 100 / ventes)} % à crédit**" if ventes else "") + "."]
        par_jour_total = defaultdict(int)
        for o in dans_periode:
            if o["intention"] in ("vente", "vente_credit"):
                par_jour_total[jour(o)] += mt(o)
        if nb_jours > 1 and par_jour_total:
            meilleur = max(par_jour_total, key=par_jour_total.get)
            bref.append(f"Meilleur jour : **{date_fr(meilleur)}** ({C.francs(par_jour_total[meilleur])}), "
                        f"en moyenne **{C.francs(ventes // nb_jours)}** par jour.")
        produits_qte = defaultdict(int)
        for o in dans_periode:
            if o["intention"] in ("vente", "vente_credit"):
                for a in o.get("articles", []):
                    produits_qte[a.get("produit") or a.get("produit_dit") or "?"] += a.get("prix_fcfa") or 0
        if produits_qte and max(produits_qte.values()) > 0:
            top = max(produits_qte, key=produits_qte.get)
            bref.append(f"Ce qui rapporte le plus : **{top}**.")
        if rembourse:
            bref.append(f"Remboursements reçus : **{C.francs(rembourse)}**.")
        st.markdown(" ".join(bref))

# ---------------------------------------------------------------------------
# Kaddu le dit à voix haute (seulement les phrases validées)
# ---------------------------------------------------------------------------
with st.container(border=True):
    st.markdown(f'<div class="k-carte-titre">🔊 Kaddu le dit en {LANGUES[L]["nom"]}</div>', unsafe_allow_html=True)
    ventes_jour = somme([o for o in actives if jour(o) == aujourd_hui], "vente", "vente_credit")
    a1, a2, a3 = st.columns(3)
    with a1:
        st.markdown('<div class="k-sous">Bilan du jour</div>', unsafe_allow_html=True)
        t, _ = phrase_et_langue("reponse_bilan", L, ventes=_montant(ventes_jour, L),
                                dettes=_montant(credits_en_cours, L), montant=_montant(ventes_jour, L))
        st.markdown(t)
        ecouter(t, L, "tb_ec_bilan", "🔊 Ce que j'ai vendu aujourd'hui")
    with a2:
        st.markdown('<div class="k-sous">Total des crédits</div>', unsafe_allow_html=True)
        t, _ = phrase_et_langue("reponse_dettes_total", L, montant=_montant(credits_en_cours, L))
        st.markdown(t)
        ecouter(t, L, "tb_ec_total", "🔊 Ce qu'on me doit")
    with a3:
        st.markdown('<div class="k-sous">Le client qui doit le plus</div>', unsafe_allow_html=True)
        if debiteurs:
            c = debiteurs[0]
            t, _ = phrase_et_langue("reponse_dette", L, client=c["nom"], montant=_montant(c["reste"], L))
            st.markdown(t)
            ecouter(t, L, "tb_ec_top", f"🔊 Combien me doit {c['nom']}")
        else:
            st.markdown("Personne ne te doit rien. 🎉")
    if periode not in ("Aujourd'hui",):
        st.caption("Le bilan lu à voix haute porte sur aujourd'hui : il n'y a pas encore de phrase validée pour une "
                   "semaine ou un mois. Les chiffres de la période sont au-dessus et dans les graphiques.")

# ---------------------------------------------------------------------------
# Graphiques
# ---------------------------------------------------------------------------
if dans_periode:
    import altair as alt
    import pandas as pd

    g1, g2 = st.columns([1.5, 1])
    with g1, st.container(border=True):
        lignes = []
        if nb_jours == 1:
            st.markdown('<div class="k-carte-titre">Ventes heure par heure</div>', unsafe_allow_html=True)
            for h in range(6, 23):
                for nom, intention in (("Comptant", "vente"), ("À crédit", "vente_credit")):
                    lignes.append({"quand": f"{h}h", "ordre": h, "type": nom,
                                   "montant": sum(mt(o) for o in dans_periode
                                                  if o["intention"] == intention and heure(o) == h)})
            x = alt.X("quand:N", sort=alt.SortField("ordre"), title=None, axis=alt.Axis(labelAngle=0))
        else:
            st.markdown('<div class="k-carte-titre">Ventes jour par jour</div>', unsafe_allow_html=True)
            d = debut
            while d <= fin:
                for nom, intention in (("Comptant", "vente"), ("À crédit", "vente_credit")):
                    lignes.append({"quand": date_courte(d), "ordre": d.toordinal(), "type": nom,
                                   "jour": date_fr(d),
                                   "montant": sum(mt(o) for o in dans_periode
                                                  if o["intention"] == intention and jour(o) == d)})
                d += timedelta(days=1)
            x = alt.X("quand:N", sort=alt.SortField("ordre"), title=None,
                      axis=alt.Axis(labelAngle=-45 if nb_jours > 10 else 0,
                                    labelOverlap=True))
        df = pd.DataFrame(lignes)
        info = [alt.Tooltip("jour:N" if "jour" in df else "quand:N", title="Quand"),
                alt.Tooltip("type:N", title="Type"), alt.Tooltip("montant:Q", title="Montant (F)", format=",d")]
        graphique(alt.Chart(df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=x, y=alt.Y("sum(montant):Q", title="FCFA", axis=alt.Axis(format="~s")),
            color=alt.Color("type:N", title=None, scale=alt.Scale(domain=["Comptant", "À crédit"],
                                                                  range=[BLEU, VIOLET])),
            order=alt.Order("type:N", sort="descending"), tooltip=info).properties(height=280))

    with g2, st.container(border=True):
        st.markdown('<div class="k-carte-titre">Ce qui se vend le mieux</div>', unsafe_allow_html=True)
        produits = defaultdict(lambda: {"montant": 0, "fois": 0})
        for o in dans_periode:
            if o["intention"] in ("vente", "vente_credit"):
                for a in o.get("articles", []):
                    p = produits[a.get("produit") or a.get("produit_dit") or "?"]
                    p["montant"] += a.get("prix_fcfa") or 0
                    p["fois"] += 1
        if produits:
            dfp = pd.DataFrame([{"produit": k, "montant": v["montant"], "fois": v["fois"]}
                                for k, v in produits.items()]).sort_values("montant", ascending=False).head(8)
            graphique(alt.Chart(dfp).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color=BLEU).encode(
                x=alt.X("montant:Q", title=None, axis=alt.Axis(format="~s")),
                y=alt.Y("produit:N", sort="-x", title=None),
                tooltip=[alt.Tooltip("produit:N", title="Produit"),
                         alt.Tooltip("montant:Q", title="Montant (F)", format=",d"),
                         alt.Tooltip("fois:Q", title="Nombre de ventes")]).properties(height=280))
        else:
            st.caption("Les produits n'ont pas été dits dans les ventes de la période.")

    if nb_jours > 1:
        with st.container(border=True):
            st.markdown('<div class="k-carte-titre">Ce qu\'on te doit, au fil des jours</div>',
                        unsafe_allow_html=True)
            evo, d = [], debut
            while d <= fin:
                pris = sum(mt(o) for o in actives if o["intention"] == "vente_credit" and jour(o) <= d)
                rendu = sum(mt(o) for o in actives if o["intention"] == "remboursement" and jour(o) <= d)
                evo.append({"quand": date_courte(d), "ordre": d.toordinal(), "jour": date_fr(d),
                            "crédits": max(pris - rendu, 0)})
                d += timedelta(days=1)
            dfe = pd.DataFrame(evo)
            base = alt.Chart(dfe).encode(
                x=alt.X("quand:N", sort=alt.SortField("ordre"), title=None,
                        axis=alt.Axis(labelAngle=-45 if nb_jours > 10 else 0, labelOverlap=True)),
                y=alt.Y("crédits:Q", title="FCFA", axis=alt.Axis(format="~s")),
                tooltip=[alt.Tooltip("jour:N", title="Jour"),
                         alt.Tooltip("crédits:Q", title="Crédits en cours (F)", format=",d")])
            graphique((base.mark_area(color=VIOLET, opacity=0.18) + base.mark_line(color=VIOLET, strokeWidth=2.5)
                       + base.mark_point(color=VIOLET, filled=True, size=30)).properties(height=200))

# ---------------------------------------------------------------------------
# Onglets
# ---------------------------------------------------------------------------
a_relancer = [c for c in debiteurs
              if c["dernier_credit"] and (aujourd_hui - (c["dernier_paiement"] or c["dernier_credit"])).days
              >= RELANCE_JOURS]
t_clients, t_relance, t_stock, t_journal = st.tabs([
    f"👥 Clients ({len(clients)})", f"⏰ À relancer ({len(a_relancer)})", "📦 Stock", "📒 Journal"])

with t_clients:
    if not clients:
        st.caption("Aucun client nommé pour l'instant (les ventes au comptant sans nom ne sont pas rattachées).")
    else:
        import pandas as pd
        liste = sorted(clients.values(), key=lambda c: (-c["reste"], c["nom"]))
        dfc = pd.DataFrame([{"Client": c["nom"], "Reste dû": c["reste"], "Pris à crédit": c["pris"],
                             "Remboursé": c["rendu"],
                             "Dernière visite": date_fr(datetime.fromisoformat(c["derniere"]).date())
                             if c["derniere"] else ""} for c in liste])
        config = {"Reste dû": st.column_config.ProgressColumn(
                      "Reste dû (F)", format="%d", min_value=0, max_value=max(1, max(c["reste"] for c in liste))),
                  "Pris à crédit": st.column_config.NumberColumn("Pris à crédit (F)", format="%d"),
                  "Remboursé": st.column_config.NumberColumn("Remboursé (F)", format="%d")}
        st.caption("👆 Clique sur un client pour ouvrir son compte.")
        choisi = None
        try:
            sel = st.dataframe(dfc, use_container_width=True, hide_index=True, column_config=config,
                               on_select="rerun", selection_mode="single-row", key="tb_clients",
                               height=min(36 * (len(liste) + 1) + 4, 600))
            lignes_sel = sel.selection.rows if sel is not None else []
            if lignes_sel:
                choisi = liste[lignes_sel[0]]
        except TypeError:                   # Streamlit trop ancien : pas de sélection dans le tableau
            tableau(dfc, column_config=config)
            nom = st.selectbox("Ouvrir le compte de", [c["nom"] for c in liste])
            choisi = clients[nom.casefold()]

        if choisi:
            c = choisi
            with st.container(border=True):
                st.markdown(f"### {c['nom']}")
                m1, m2, m3 = st.columns(3)
                m1.metric("Reste dû", C.francs(c["reste"]))
                m2.metric("Pris à crédit", C.francs(c["pris"]))
                m3.metric("Remboursé", C.francs(c["rendu"]))
                cle = "reponse_dette" if c["reste"] > 0 else "reponse_dette_rien"
                t, _ = phrase_et_langue(cle, L, client=c["nom"], montant=_montant(c["reste"], L))
                p1, p2 = st.columns([2, 1])
                p1.markdown(f"**{t}**")
                with p2:
                    ecouter(t, L, f"tb_ec_client_{c['nom']}")
                tableau([{"date": date_fr(jour(o)) + " " + (o.get("date") or "")[11:16],
                          "opération": C.INTENTIONS_FR.get(o["intention"], o["intention"]),
                          "montant": C.francs(mt(o)),
                          "produits": ", ".join(article(a) for a in o.get("articles", []))}
                         for o in reversed(c["ops"])])

with t_relance:
    if not a_relancer:
        st.markdown(f'<div class="k-vide"><div class="k-grand">✅</div>Aucun crédit ne traîne depuis plus de '
                    f'{RELANCE_JOURS} jours.</div>', unsafe_allow_html=True)
    else:
        st.caption(f"Clients qui doivent encore et n'ont rien remboursé depuis au moins {RELANCE_JOURS} jours, "
                   "du plus ancien au plus récent.")
        for c in sorted(a_relancer, key=lambda c: c["dernier_paiement"] or c["dernier_credit"]):
            depuis = (aujourd_hui - (c["dernier_paiement"] or c["dernier_credit"])).days
            with st.container(border=True):
                r1, r2, r3 = st.columns([1.4, 2, 1])
                r1.markdown(f"**{c['nom']}**<br><span class='k-gris'>doit {C.francs(c['reste'])}</span>",
                            unsafe_allow_html=True)
                quoi = "dernier remboursement" if c["dernier_paiement"] else "pris à crédit"
                r2.markdown(f"<span class='k-gris'>{quoi} il y a</span> **{depuis} jours**<br>"
                            f"<span class='k-gris'>{date_fr(c['dernier_paiement'] or c['dernier_credit'])}</span>",
                            unsafe_allow_html=True)
                with r3:
                    t, _ = phrase_et_langue("reponse_dette", L, client=c["nom"], montant=_montant(c["reste"], L))
                    ecouter(t, L, f"tb_ec_rel_{c['nom']}")

with t_stock:
    achats = [o for o in dans_periode if o["intention"] == "achat"]
    if not achats:
        st.caption(f"Aucune entrée de stock ({libelle_periode}).")
    else:
        st.metric("Dépensé en marchandises sur la période", C.francs(somme(achats, "achat")))
        tableau([{"date": date_fr(jour(o)) + " " + (o.get("date") or "")[11:16],
                  "produits": ", ".join(article(a) for a in o.get("articles", [])),
                  "montant": C.francs(mt(o)) if mt(o) else "—"}
                 for o in sorted(achats, key=lambda o: o.get("date") or "", reverse=True)])

with t_journal:
    f1, f2, f3 = st.columns([1.2, 1, 1])
    types = f1.multiselect("Type", list(C.INTENTIONS_FR), format_func=C.INTENTIONS_FR.get, key="tb_j_types",
                           placeholder="Tous les types")
    noms_clients = sorted({o["client"].strip() for o in operations if o.get("client")})
    client_f = f2.selectbox("Client", ["Tous"] + noms_clients, key="tb_j_client")
    langue_f = f3.selectbox("Langue parlée", ["Toutes"] + list(LANGUES), key="tb_j_langue",
                            format_func=lambda l: l if l == "Toutes" else LANGUES[l]["nom"])
    voir_annulees = st.toggle("Afficher les opérations annulées", value=False, key="tb_j_annulees")
    liste = [o for o in operations
             if debut <= jour(o) <= fin
             and (voir_annulees or o.get("statut", "active") == "active")
             and (not types or o["intention"] in types)
             and (client_f == "Tous" or (o.get("client") or "").strip() == client_f)
             and (langue_f == "Toutes" or o.get("langue") == langue_f)]
    st.caption(f"{len(liste)} opération(s) · {libelle_periode}.")
    tableau([{"date": date_fr(jour(o)) + " " + (o.get("date") or "")[11:16],
              "opération": C.INTENTIONS_FR.get(o["intention"], o["intention"])
              + (" (annulée)" if o.get("statut", "active") != "active" else ""),
              "client": o.get("client") or "",
              "montant": C.francs(mt(o)) if o.get("montant_fcfa") is not None else "—",
              "produits": ", ".join(article(a) for a in o.get("articles", [])),
              "langue": LANGUES.get(o.get("langue"), {}).get("nom", o.get("langue")),
              "ce qui a été dit": o.get("transcription") or ""}
             for o in sorted(liste, key=lambda o: o.get("date") or "", reverse=True)])
