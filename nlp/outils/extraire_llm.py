"""Extrait l'opération d'un message (wolof, pulaar ou sérère) avec un LLM via OpenRouter, puis la contrôle
avec l'atlas. La consigne est commune aux trois langues ; seul le bloc « langue » change (nom de la langue,
exemples d'intentions, règles des montants, mots du marché tirés de l'atlas officiel).
Usage (tout le journal ASR, en wolof) : python nlp/outils/extraire_llm.py [modele]"""
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Literal, Optional

from openai import OpenAI
from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).parent.parent))
import atlas  # noqa: E402

MODELES = [
    "google/gemini-3.5-flash-lite",   # choisi pour TEWLU Kaddu
    "google/gemini-2.5-flash-lite",   # le moins cher
    "google/gemini-3.1-flash-lite",
    "google/gemini-3.8-flash",
    "google/gemini-3.1-pro-preview",  # le plus puissant, le plus cher
]


class Article(BaseModel):
    model_config = ConfigDict(extra="forbid")
    produit_tel_que_dit: Optional[str]
    sens_fr: Optional[str]
    quantite: Optional[float]
    unite: Optional[str]
    prix_fcfa: Optional[int]
    prix_tel_que_dit: Optional[str]
    prix_par_unite: bool


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type_reponse: Literal["premier_message", "confirme", "refuse", "corrige"]
    intention: Literal["vente", "vente_credit", "remboursement", "achat", "depense",
                       "question_dette", "question_bilan", "annulation", "incomprehension"]
    client: Optional[str]
    articles: list[Article]
    montant_fcfa: Optional[int]
    montant_tel_que_dit: Optional[str]
    champs_incertains: list[Literal["intention", "client", "articles", "montant"]]
    traduction_fr: str


NOMS_LANGUES = {"wo": "wolof", "ff": "pulaar (peul)", "srr": "sérère"}

REGLES = """Tu aides un petit commerçant sénégalais à tenir son carnet de compte.
Le message vient d'une transcription automatique d'une note vocale en {langue}, avec parfois des mots
français. La transcription peut contenir des erreurs.

Intentions :
- vente : vente payée tout de suite{ex_vente}.
- vente_credit : le client prend et paiera plus tard, il « doit »{ex_vente_credit}.
- remboursement : un client paie tout ou partie de sa dette{ex_remboursement}.
- question_dette : le commerçant DEMANDE combien on lui doit{ex_question_dette}.
  Avec un nom de client -> client = ce nom. « combien on me doit au total ? » -> client = null.
  Une question n'est JAMAIS une vente à crédit, même si elle contient « dette » ou un nom de client.
- question_bilan : « combien j'ai vendu aujourd'hui ? », « combien il me reste ? », le solde de tout
  le compte{ex_question_bilan}.
- annulation : effacer la dernière opération notée.
- achat : le commerçant achète du stock, une entrée de stock{ex_achat}.
- depense : une dépense (transport, facture, loyer...).
- incomprehension : seulement si le message ne correspond vraiment à rien de tout cela.

Règles pour les articles :
- Fais un article par produit. « 2 kilos de pomme de terre et 3 kilos d'oignon » = 2 articles.
- Recopie le nom du produit EXACTEMENT comme il est dit, sans le traduire ni le corriger
  (« {ex_mot} » reste « {ex_mot} », « pomme de terre » reste « pomme de terre »).
- sens_fr : ce que désigne ce mot, en français simple, si tu le sais (« {ex_mot} » -> « {ex_sens} »).
  null si tu ne sais pas. Ne mets jamais le sens à la place du mot dit : produit_tel_que_dit garde
  toujours le mot du commerçant.
- Si le commerçant donne à la fois son mot et sa traduction (« pallat, c'est-à-dire plat en
  français »), mets son mot dans produit_tel_que_dit et la traduction dans sens_fr.
- Recopie l'unité comme elle est dite (kilo, paket, saak, litre...). null si aucune.
- quantite : un nombre (« 10 sacs » -> 10).{ex_quantite}
- Si un produit n'est pas compréhensible, mets produit_tel_que_dit à null et ajoute "articles"
  dans champs_incertains. N'invente jamais un produit.
- Pas de produit (remboursement, question) : articles = [].

Règles pour les montants (ne fais AUCUN calcul d'addition : le programme additionne lui-même) :
{regles_montants}
- Un montant dit en chiffres ou en mots français est déjà en FCFA (exemples : « 2500 », « onze mille »).
- Si un prix est dit pour un produit (« le lait à 100 »), mets-le dans prix_fcfa de cet article et
  recopie ses mots exacts dans prix_tel_que_dit. prix_par_unite = true si c'est le prix d'une seule
  unité (« 100 chacun », « 100 le kilo »), false si c'est le prix de toute la ligne. Sans prix : null, false.
- montant_fcfa : seulement si un TOTAL est dit pour toute l'opération ; recopie ses mots exacts dans
  montant_tel_que_dit. Si seuls des prix par produit sont dits, montant_fcfa = null.
- N'invente jamais un montant.

Mots du marché en {langue} (atlas validé, « mot = sens ») : {mots_marche}

champs_incertains : liste des champs dont tu n'es pas sûr (mot mal transcrit, phrase ambiguë).
Liste vide si tout est clair. Il vaut mieux signaler un doute que deviner.

Donne aussi une traduction française courte du message.
"""

# Exemples par langue. Wolof : consigne d'origine. Pulaar et sérère : phrases du document
# « Phases Tests Trilingue » de Ndèye Maguette et formes de l'atlas officiel.
EXEMPLES = {
    "wo": {
        "ex_vente": " (« jaay naa ñaari mburu 300 »)",
        "ex_vente_credit": " (« Awa jël na sukar, dafa ma bor », « Awa jënd na ñaari paket sukar, daf koo leb »)",
        "ex_remboursement": " (« Awa fey na 2000 ci bor bi », « Awa fey na ñaari teeméer »)",
        "ex_question_dette": ". « Ñaata la Awa ma bor ? », « Awa ñaata la ma ameel ? », « Ñaata la Awa ame thi bor ? »"
                             " -> client = Awa. « Ñaata la ñu ma bor lépp ? » -> client = null",
        "ex_question_bilan": " (« ñaata laa jaay tey ? », « lima diay tay thi ñaata la tole ? »)",
        "ex_achat": " (« Jënd naa fukki saak ceeb »)",
        "ex_mot": "soble", "ex_sens": "oignon", "ex_quantite": " « fukki saak » = 10 sacs.",
        "regles_montants": """- Un montant dit avec des nombres wolof est compté en dërëm (1 dërëm = 5 FCFA) : multiplie par 5.
  Exemples : « fanweer » = 30 dërëm = 150 FCFA ; « ñaar-fukk » = 20 dërëm = 100 FCFA ;
  « téeméer » = 100 dërëm = 500 FCFA ; « ñaari téeméer » = 1 000 FCFA ;
  « juróom-benni téeméer » = 3 000 FCFA ; « junni » = 1 000 dërëm = 5 000 FCFA ;
  « ñetti junni » = 15 000 FCFA.""",
    },
    "ff": {
        "ex_vente": " (verbe yeeyde)",
        "ex_vente_credit": " (« Hawa sodi paketa didi sukara téemédé tati, ko niamliido dum » : Hawa a pris"
                           " 2 paquets de sucre à crédit ; ñamandé = dette)",
        "ex_remboursement": " (« Hawa yobiima téemeedé ɗiɗi » : Hawa a remboursé 1 000 F ; verbe yoɓde)",
        "ex_question_dette": ". « No foti Hawa jogui é ñamande ? », « No foti é ñamande Hawa o jogui tone ? »"
                             " -> client = Hawa. « No foti e ñamande ndioguimi ? » -> client = null",
        "ex_question_bilan": " (« No foti hedi ? » : combien il me reste)",
        "ex_achat": " (« Mi soodi saakuji sappo maaro », « Komi thioɗo saakuji sappo maaro » : j'ai acheté"
                    " 10 sacs de riz)",
        "ex_mot": "basalle", "ex_sens": "oignon", "ex_quantite": " « saakuji sappo » = 10 sacs (sappo = 10).",
        "regles_montants": """- Un montant dit avec des nombres pulaar est compté en unités de 5 FCFA (buuɗi) : multiplie par 5.
  Exemples : « buuɗi sappo » = 10 x 5 = 50 FCFA ; « nogass » = 20 x 5 = 100 FCFA ;
  « thiapantati » = 30 x 5 = 150 FCFA ; « teemedéré » = 500 FCFA ; « teemédé ɗiɗi » = 1 000 FCFA ;
  « teemédé jeegom » = 3 000 FCFA ; « ujunéré » = 5 000 FCFA ; « ujunaajé ɗiɗi » = 10 000 FCFA.
  Les parties sont reliées par « e » : « teemédé ɗiɗi e nogass » = 1 100 FCFA.""",
    },
    "srr": {
        "ex_vente": " (verbe jikwa)",
        "ex_vente_credit": " (« Hawa jikà paketa dik sukur, teeméte bétou fa léng, Ka rawine » : Hawa a pris"
                           " 2 paquets de sucre à 3 000 F à crédit ; lat = dette)",
        "ex_remboursement": " (« Hawa rabda teeméte dik » : Hawa a remboursé 1 000 F)",
        "ex_question_dette": ". « Lat né Hawa pod noumo ? » -> client = Hawa",
        "ex_question_bilan": " (« podnum fop jeggam ? » : combien j'ai en tout)",
        "ex_achat": " (« Jikaam xarbaxay saak maalo » : j'ai acheté 10 sacs de riz)",
        "ex_mot": "maalo", "ex_sens": "riz",
        "ex_quantite": " En quantité, « xarbaxay » = 10 (« xarbaxay saak » = 10 sacs).",
        "regles_montants": """- Un montant dit avec des nombres sérères est compté en unités de 5 FCFA : multiplie par 5.
  Exemples : « xabétakh » = 25 FCFA ; « xarbaxay » = 10 x 5 = 50 FCFA ; « xarbéndik » = 100 FCFA ;
  « teeméde » (ou « teeméte ») = 500 FCFA ; « teeméde dik » = 1 000 FCFA ;
  « teeméde bétou fa leng » = 3 000 FCFA ; « thiouni » = 5 000 FCFA ; « thiouni dik » = 10 000 FCFA.
  Les parties sont reliées par « fo » : « teeméde dik fo xarbéndik » = 1 100 FCFA.""",
    },
}


def _mots_marche(langue: str) -> str:
    """« ceeb = riz ; sikir = sucre ; ... » : produits et unités de l'atlas officiel pour cette langue."""
    mots = []
    for r in atlas._lire("lexique.csv"):
        if r["langue"] == langue and r["categorie"] in ("produit", "unité"):
            formes = [r["forme"], *atlas._variantes(r["variantes"])]
            mots.append(f"{' / '.join(formes)} = {r['concept'].lower()}")
    return " ; ".join(mots) or "(aucun)"


def consigne(langue: str = "wo") -> str:
    """Consigne complète pour une langue (wo, ff, srr)."""
    return REGLES.format(langue=NOMS_LANGUES[langue], mots_marche=_mots_marche(langue), **EXEMPLES[langue])


_client = None


def cle_openrouter():
    """Cherche la clé : d'abord dans le terminal (export), sinon dans un fichier .env."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    racine = Path(__file__).parent.parent.parent
    for env in [racine / ".env", racine / "nlp" / ".env", racine / "backend" / ".env"]:
        if env.exists():
            for ligne in env.read_text(encoding="utf-8").splitlines():
                ligne = ligne.strip().removeprefix("export ").strip()
                if ligne.startswith("OPENROUTER_API_KEY="):
                    return ligne.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("Clé OPENROUTER_API_KEY introuvable : ajoute la ligne "
                       "OPENROUTER_API_KEY=ta_cle dans le fichier .env à la racine du projet.")


def _schema():
    """Schéma JSON sans références ($defs) : plus sûr pour tous les modèles."""
    schema = Extraction.model_json_schema()
    defs = schema.pop("$defs", {})

    def remplacer(noeud):
        if isinstance(noeud, dict):
            if "$ref" in noeud:
                return remplacer(defs[noeud["$ref"].split("/")[-1]])
            return {k: remplacer(v) for k, v in noeud.items()}
        if isinstance(noeud, list):
            return [remplacer(v) for v in noeud]
        return noeud
    return remplacer(schema)


def _appeler(consigne, modele):
    global _client
    if _client is None:
        _client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=cle_openrouter())
    rep = _client.chat.completions.create(
        model=modele, temperature=0,
        response_format={"type": "json_schema",
                         "json_schema": {"name": "extraction", "strict": True,
                                         "schema": _schema()}},
        messages=[{"role": "user", "content": consigne}])
    brut = rep.choices[0].message.content or ""
    try:
        return Extraction.model_validate_json(brut), brut
    except Exception:
        return None, brut


def extraire(texte, modele, langue="wo"):
    """Premier message. Renvoie (extraction, réponse brute) ; extraction vaut None si illisible."""
    return _appeler(consigne(langue) + "\ntype_reponse = premier_message.\nMessage : " + texte, modele)


def corriger(precedente, texte, modele, historique="", langue="wo"):
    """Réponse du commerçant pendant la conversation. Le LLM reçoit toute la conversation et ce qu'il
    avait compris (le contexte n'est jamais perdu), dit quel type de réponse c'est, et met à jour."""
    texte_consigne = (consigne(langue)
                + "\nConversation jusqu'ici (Kaddu est l'assistant) :\n" + (historique or "(aucune)")
                + "\n\nCe que tu avais compris de l'opération :\n"
                + json.dumps(precedente.model_dump(), ensure_ascii=False)
                + """

Le commerçant vient de répondre à la dernière question de Kaddu. Choisis type_reponse :
- confirme : il dit oui, c'est bien ça, même avec d'autres mots (« waaw », « waaw loolu la »,
  « waaw, bind ko », « eey », « ko ɗum », « iyo », « oui c'est ça ») et sans rien changer ;
- refuse : il dit non, ce n'est pas ça, sans donner la bonne information ;
- corrige : il change ou ajoute une information, même s'il commence par « waaw » ou « déet ».
Pour confirme et refuse, renvoie l'opération précédente sans la changer. Pour corrige, garde tout ce
qu'il ne corrige pas et renvoie l'opération complète mise à jour ; s'il redit toute la phrase, repars
de sa nouvelle phrase.
Réponse du commerçant : """ + texte)
    return _appeler(texte_consigne, modele)


def controler(extraction, texte, file_termes=None, langue="wo"):
    """Passe l'extraction dans l'atlas de la langue (voir atlas.rapprocher)."""
    return atlas.rapprocher(extraction.model_dump(), langue, texte, file_termes)


if __name__ == "__main__":
    modele = sys.argv[1] if len(sys.argv) > 1 else MODELES[0]
    entree = Path("nlp/data/journal_asr.csv")
    sortie = Path(f"nlp/data/journal_llm_{modele.split('/')[-1]}.csv")  # un fichier par modèle
    file_termes = atlas.FileTermes()  # nlp/data/termes_a_valider.db

    with open(entree, encoding="utf-8") as f:
        lignes = list(csv.DictReader(f))

    champs = ["fichier", "modele", "transcription", "intention", "client", "articles",
              "montant_fcfa", "montant_tel_que_dit", "controle_montant", "montants_regles",
              "champs_incertains", "traduction_fr", "erreur"]
    with open(sortie, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=champs)
        w.writeheader()
        for l in lignes:
            texte = re.sub(r"<\|\w+\|>", "", l["transcription"]).strip()
            e, brut = extraire(texte, modele)
            ligne = {"fichier": l["fichier"], "modele": modele, "transcription": texte}
            if e:
                c = controler(e, texte, file_termes)
                articles = " + ".join(
                    f"{a['quantite'] or '?'} {a['unite_dite'] or ''} {a['produit_dit']} "
                    f"[{a['produit']} : {a['statut_produit']}]" for a in c["articles"])
                ligne.update({"intention": e.intention, "client": e.client, "articles": articles,
                              "montant_fcfa": e.montant_fcfa, "montant_tel_que_dit": e.montant_tel_que_dit,
                              "controle_montant": c["controle_montant"]["statut"],
                              "montants_regles": c["controle_montant"]["montants_regles"],
                              "champs_incertains": ", ".join(e.champs_incertains),
                              "traduction_fr": e.traduction_fr})
                print(f"{l['fichier']} -> {e.intention} | client={e.client} | {articles} | "
                      f"montant={e.montant_fcfa} ({ligne['controle_montant']}) | "
                      f"doutes={e.champs_incertains} | {e.traduction_fr}")
            else:
                ligne["erreur"] = brut
                print(f"{l['fichier']} -> réponse illisible : {brut}")
            w.writerow(ligne)
            f.flush()
    print("Résultats dans", sortie)
