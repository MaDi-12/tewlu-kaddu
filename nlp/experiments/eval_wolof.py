"""Expériences de compréhension du wolof — TEWLU Kaddu.

Compare 5 méthodes d'extraction sur le même jeu de phrases de test :

  regles        : baseline actuelle (nlp/extractor.py)
  traducteur    : GalsenAI wolof -> français, puis règles en français
  traduction_parfaite : plafond de la piste traduction (traduction de référence + règles en français)
  tinyaya_json  : TinyAya Wolof SFT, extraction directe en JSON (exemples dans le prompt)
  tinyaya_tool  : TinyAya Wolof SFT, appel d'outil "enregistrer_operation"
  hybride_ner   : règles + NER TinyAya pour retrouver le nom du client

Usage (depuis la racine du dépôt, sur une machine avec GPU de préférence) :
    python -m nlp.experiments.eval_wolof --methodes regles,traducteur
    python -m nlp.experiments.eval_wolof --methodes all --sortie nlp/experiments/resultats

Chaque méthode renvoie le même dictionnaire :
    {intention, client, produit, quantite, unite, montant, brut, latence_s}
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 0. Configuration
# ---------------------------------------------------------------------------

RACINE = Path(__file__).resolve().parents[2]
JEU_DE_TEST = RACINE / "nlp" / "data" / "phrases_test_wo_provisoire.csv"

TRADUCTEUR_ID = "galsenai/wolofToFrenchTranslator_nllb"   # NLLB-600M affiné wo<->fr
TINYAYA_REPO = "m-a-d-i/tinyaya-wolof-backup"               # emplacement provisoire (model card)
TINYAYA_SUB = "sft-ctr2000-full"

CHAMPS = ["intention", "client", "produit", "quantite", "unite", "montant"]
INTENTIONS = ["vente", "vente_credit", "remboursement", "achat", "depense",
              "question_dette", "question_bilan", "annulation", "incomprehension"]

_modeles: dict = {}   # chargement paresseux : un modèle n'est chargé que si une méthode en a besoin


def _device_dtype():
    import torch
    if torch.cuda.is_available():
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        return "cuda", dtype
    return "cpu", torch.float32


# ---------------------------------------------------------------------------
# 1. Jeu de test
# ---------------------------------------------------------------------------

def charger_jeu_de_test(chemin: Path = JEU_DE_TEST) -> list[dict]:
    with open(chemin, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def resultat_vide() -> dict:
    return {c: None for c in CHAMPS} | {"brut": "", "latence_s": 0.0}


# ---------------------------------------------------------------------------
# 2. Méthode "regles" : baseline actuelle du dépôt
# ---------------------------------------------------------------------------

def methode_regles(phrase: str) -> dict:
    from nlp.extractor import extract_information
    rep = extract_information(phrase, langue="wo")
    r = resultat_vide()
    r.update(intention=rep.intention, **rep.donnees.model_dump())
    r["brut"] = rep.model_dump_json()
    return r


# ---------------------------------------------------------------------------
# 3. Méthode "traducteur" : GalsenAI wo -> fr, puis règles en français
# ---------------------------------------------------------------------------

def traduire_wo_fr(phrase: str) -> str:
    """Code repris de la model card GalsenAI (préfixe texte), avec une longueur max plus grande :
    la carte utilise max_length=30, ce qui coupe les phrases longues."""
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    if "trad" not in _modeles:
        device, _ = _device_dtype()
        tok = AutoTokenizer.from_pretrained(TRADUCTEUR_ID)
        mod = AutoModelForSeq2SeqLM.from_pretrained(TRADUCTEUR_ID).to(device).eval()
        _modeles["trad"] = (tok, mod, device)
    tok, mod, device = _modeles["trad"]
    inputs = tok("translate Wolof to French: " + phrase, return_tensors="pt").to(device)
    with torch.no_grad():
        out = mod.generate(**inputs, max_new_tokens=128, num_beams=4)
    return tok.batch_decode(out, skip_special_tokens=True)[0]


PRODUITS_FR = {
    "sucre": "sucre", "riz": "riz", "huile": "huile", "lait": "lait", "pain": "pain",
    "oignon": "oignon", "café": "café", "cafe": "café", "thé": "thé", "savon": "savon",
    "farine": "farine", "sel": "sel", "poisson": "poisson", "viande": "viande", "poulet": "poulet",
    "oeuf": "oeufs", "œuf": "oeufs", "eau": "eau", "gaz": "gaz", "charbon": "charbon",
}
UNITES_FR = {
    "paquet": "paquet", "sac": "sac", "kilo": "kilo", "kg": "kilo", "litre": "litre",
    "bidon": "bidon", "boîte": "boite", "boite": "boite", "bouteille": "bouteille",
    "pot": "pot", "carton": "carton", "tas": "tas", "sachet": "sachet", "pain": None,
}
MOTS_NON_CLIENT = {"je", "j", "il", "elle", "on", "nous", "vous", "ils", "elles", "combien",
                   "qui", "annule", "efface", "mon", "ma", "le", "la", "les", "un", "une"}


def extraire_fr(texte_fr: str) -> dict:
    """Règles simples sur le français traduit. Volontairement basiques : on mesure
    surtout si la traduction conserve les informations utiles."""
    from text_to_num import alpha2digit
    r = resultat_vide()
    try:
        t = alpha2digit(texte_fr, "fr", threshold=0)   # text2num >= 3
    except TypeError:
        t = alpha2digit(texte_fr, "fr")                # text2num 2.x (sans Rust)    tl = t.lower()

    # Intention (ordre important : les questions et annulations d'abord)
    if re.search(r"\bannul|\beffac|\bsupprim", tl):
        r["intention"] = "annulation"
    elif "combien" in tl and re.search(r"\bdoi[st]|\bdette", tl):
        r["intention"] = "question_dette"
    elif re.search(r"\bqui\b.*\bdoi[st]", tl):
        r["intention"] = "question_dette"
    elif "combien" in tl and re.search(r"vend", tl):
        r["intention"] = "question_bilan"
    elif re.search(r"crédit|credit|dette|doit|paiera|payera|rembourser demain", tl):
        r["intention"] = "vente_credit"
    elif re.search(r"\b(transport|loyer|facture|électricité|electricite|taxe)\b", tl):
        r["intention"] = "depense"
    elif re.search(r"\b(a|m'a|ont) (payé|paye|remboursé|rembourse|envoyé|donné)", tl):
        r["intention"] = "remboursement"
    elif re.search(r"\bj'?\s?ai (vendu)|\bvendu", tl):
        r["intention"] = "vente"
    elif re.search(r"\bj'?\s?ai (acheté|achete)", tl):
        r["intention"] = "achat"
    elif re.search(r"\b(a|ont) (acheté|achete|pris)", tl):
        r["intention"] = "vente"
    else:
        r["intention"] = "incomprehension"

    # Montant : le plus grand nombre de la phrase ; quantité : un petit nombre suivi d'une unité
    nombres = [int(n.replace(" ", "")) for n in re.findall(r"\d[\d ]*\d|\d", t)]
    if nombres:
        r["montant"] = max(nombres) if max(nombres) >= 25 else None
    m = re.search(r"\b(\d{1,3})\s+(" + "|".join(UNITES_FR) + r")s?\b", tl)
    if m:
        r["quantite"] = float(m.group(1))
        r["unite"] = UNITES_FR[m.group(2)]

    for mot, norme in PRODUITS_FR.items():
        if re.search(rf"\b{mot}s?\b", tl):
            r["produit"] = norme
            break

    # Client : premier mot (ou deux) en majuscule en début de phrase, hors pronoms
    m = re.match(r"\s*((?:Tata|Tonton)\s+)?([A-ZÀ-Ý][a-zà-ÿ]+)", t)
    if m and m.group(2).lower() not in MOTS_NON_CLIENT:
        r["client"] = ((m.group(1) or "") + m.group(2)).strip()
    elif re.search(r"\bmon voisin|\bma voisine", tl):
        r["client"] = "voisin"
    return r


def methode_traducteur(phrase: str) -> dict:
    fr = traduire_wo_fr(phrase)
    r = extraire_fr(fr)
    r["brut"] = fr
    return r


# Plafond de la piste traduction : on donne aux règles françaises la traduction de référence
# (colonne traduction_fr). Si ce score est bas, ce sont les règles françaises qu'il faut
# améliorer ; s'il est haut mais "traducteur" est bas, c'est la traduction qui perd l'information.
_TRADUCTIONS_REF: dict[str, str] = {}


def methode_traduction_parfaite(phrase: str) -> dict:
    fr = _TRADUCTIONS_REF[phrase]
    r = extraire_fr(fr)
    r["brut"] = fr
    return r


# ---------------------------------------------------------------------------
# 4. Méthodes TinyAya Wolof SFT
# ---------------------------------------------------------------------------

def _tinyaya():
    if "tinyaya" not in _modeles:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        device, dtype = _device_dtype()
        tok = AutoTokenizer.from_pretrained(TINYAYA_REPO, subfolder=TINYAYA_SUB)
        mod = AutoModelForCausalLM.from_pretrained(
            TINYAYA_REPO, subfolder=TINYAYA_SUB, dtype=dtype, device_map="auto").eval()
        _modeles["tinyaya"] = (tok, mod)
    return _modeles["tinyaya"]


def generer(messages: list[dict], max_new_tokens: int = 200) -> str:
    import torch
    tok, mod = _tinyaya()
    inputs = tok.apply_chat_template(messages, add_generation_prompt=True,
                                     return_tensors="pt", return_dict=True).to(mod.device)
    with torch.no_grad():
        out = mod.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    return tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()


def _premier_json(texte: str) -> dict | None:
    m = re.search(r"\{.*\}", texte, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _depuis_dict(d: dict | None) -> dict:
    r = resultat_vide()
    if not d:
        r["intention"] = "incomprehension"
        return r
    for c in CHAMPS:
        r[c] = d.get(c)
    if r["intention"] not in INTENTIONS:
        r["intention"] = "incomprehension"
    try:
        r["montant"] = int(r["montant"]) if r["montant"] not in (None, "") else None
    except (TypeError, ValueError):
        r["montant"] = None
    return r


# 4a. Extraction directe en JSON, avec des exemples (absents du jeu de test)
SYSTEME_JSON = (
    "Tu extrais une opération commerciale d'une phrase en wolof. "
    "Réponds UNIQUEMENT avec un objet JSON contenant : intention, client, produit, quantite, unite, montant. "
    "intention est une valeur parmi : " + ", ".join(INTENTIONS) + ". "
    "produit et unite sont en français. montant est un entier en FCFA. "
    "Mets null si une information est absente. N'invente jamais un montant."
)
EXEMPLES_JSON = [
    ("Binta jënd na benn paket lait, junni, dafa ko bor.",
     {"intention": "vente_credit", "client": "Binta", "produit": "lait", "quantite": 1,
      "unite": "paquet", "montant": 1000}),
    ("Jaay naa ñaari kilo ceeb, 1200.",
     {"intention": "vente", "client": None, "produit": "riz", "quantite": 2,
      "unite": "kilo", "montant": 1200}),
    ("Ñaata la Ibou ma bor ?",
     {"intention": "question_dette", "client": "Ibou", "produit": None, "quantite": None,
      "unite": None, "montant": None}),
]


def methode_tinyaya_json(phrase: str) -> dict:
    messages = [{"role": "system", "content": SYSTEME_JSON}]
    for ex, sortie in EXEMPLES_JSON:
        messages += [{"role": "user", "content": ex},
                     {"role": "assistant", "content": json.dumps(sortie, ensure_ascii=False)}]
    messages.append({"role": "user", "content": phrase})
    brut = generer(messages)
    r = _depuis_dict(_premier_json(brut))
    r["brut"] = brut
    return r


# 4b. Appel d'outil : le contrat devient la définition de l'outil.
# A ADAPTER : présenter l'outil exactement comme dans les exemples "tool_use" de galsenai/wolof_sft.
OUTIL = {
    "name": "enregistrer_operation",
    "description": "Enregistre une opération du carnet de compte d'un commerçant.",
    "parameters": {
        "type": "object",
        "properties": {
            "intention": {"type": "string", "enum": INTENTIONS},
            "client": {"type": ["string", "null"]},
            "produit": {"type": ["string", "null"], "description": "en français"},
            "quantite": {"type": ["number", "null"]},
            "unite": {"type": ["string", "null"]},
            "montant": {"type": ["integer", "null"], "description": "FCFA"},
        },
        "required": ["intention"],
    },
}


def methode_tinyaya_tool(phrase: str) -> dict:
    systeme = ("Tu as accès à l'outil suivant. Appelle-le pour enregistrer l'opération décrite.\n"
               + json.dumps(OUTIL, ensure_ascii=False))
    brut = generer([{"role": "system", "content": systeme}, {"role": "user", "content": phrase}])
    m = re.search(r"<tool_call>(.*?)(</tool_call>|$)", brut, re.S)
    appel = _premier_json(m.group(1)) if m else _premier_json(brut)
    args = (appel or {}).get("arguments", appel)
    if isinstance(args, str):
        args = _premier_json(args)
    r = _depuis_dict(args)
    r["brut"] = brut
    return r


# 4c. Hybride : règles pour l'intention et les nombres + NER TinyAya pour le client.
# A ADAPTER : remplacer par la consigne NER exacte utilisée dans galsenai/wolof_sft.
CONSIGNE_NER = ("Lister les entités nommées de type personne (PER) dans cette phrase. "
                "Réponds en JSON : {\"PER\": [...]}.\nPhrase : ")


def extraire_personnes(sortie_ner: str) -> list[str]:
    """Parseur tolérant : JSON {"PER": [...]}, "Awa (PER)", "PER: Awa", "[PER Awa]"."""
    d = _premier_json(sortie_ner)
    if isinstance(d, dict):
        for cle in ("PER", "per", "personne", "PERSON"):
            if cle in d:
                v = d[cle]
                return [v] if isinstance(v, str) else list(v)
    noms = re.findall(r"([A-ZÀ-Ý][\wà-ÿ]+(?:\s[A-ZÀ-Ý][\wà-ÿ]+)?)\s*[\(\[:]\s*(?:B-|I-)?PER", sortie_ner)
    noms += re.findall(r"(?:B-|I-)?PER\s*[:\]]?\s*([A-ZÀ-Ý][\wà-ÿ]+(?:\s[A-ZÀ-Ý][\wà-ÿ]+)?)", sortie_ner)
    return list(dict.fromkeys(noms))


def methode_hybride_ner(phrase: str) -> dict:
    r = methode_regles(phrase)
    if r["client"] is None:
        brut = generer([{"role": "user", "content": CONSIGNE_NER + phrase}], max_new_tokens=80)
        personnes = extraire_personnes(brut)
        r["client"] = personnes[0] if personnes else None
        r["brut"] = brut
    return r


METHODES = {
    "regles": methode_regles,
    "traducteur": methode_traducteur,
    "traduction_parfaite": methode_traduction_parfaite,
    "tinyaya_json": methode_tinyaya_json,
    "tinyaya_tool": methode_tinyaya_tool,
    "hybride_ner": methode_hybride_ner,
}


# ---------------------------------------------------------------------------
# 5. Évaluation
# ---------------------------------------------------------------------------

def _norm(v) -> str:
    return "" if v in (None, "") else str(v).strip().lower()


def comparer(attendu: dict, obtenu: dict) -> dict:
    """1 = correct, 0 = faux, None = non évalué (pas de valeur attendue)."""
    s = {"intention": int(_norm(obtenu["intention"]) == _norm(attendu["intention"]))}
    if _norm(attendu["montant"]):
        s["montant"] = int(_norm(obtenu["montant"]) == str(int(float(attendu["montant"]))))
        s["montant_invente"] = None
    else:
        s["montant"] = None
        s["montant_invente"] = int(obtenu["montant"] is not None)   # le pire cas en finance
    if _norm(attendu["client"]):
        a, o = _norm(attendu["client"]), _norm(obtenu["client"])
        s["client"] = int(bool(o) and (a in o or o in a))
    else:
        s["client"] = None
    s["produit"] = (int(_norm(obtenu["produit"]) == _norm(attendu["produit"]))
                    if _norm(attendu["produit"]) else None)
    return s


def evaluer(methodes: list[str], dossier_sortie: Path) -> None:
    jeu = charger_jeu_de_test()
    _TRADUCTIONS_REF.update({ex["phrase"]: ex["traduction_fr"] for ex in jeu})
    dossier_sortie.mkdir(parents=True, exist_ok=True)
    resume = []

    for nom in methodes:
        lignes, scores = [], []
        for ex in jeu:
            t0 = time.perf_counter()
            try:
                obtenu = METHODES[nom](ex["phrase"])
            except Exception as e:                      # une phrase qui plante ne bloque pas l'expérience
                obtenu = resultat_vide() | {"intention": "erreur", "brut": repr(e)}
            obtenu["latence_s"] = round(time.perf_counter() - t0, 3)
            s = comparer(ex, obtenu)
            scores.append(s)
            lignes.append({"id": ex["id"], "difficulte": ex["difficulte"], "phrase": ex["phrase"],
                           **{f"attendu_{c}": ex[c] for c in CHAMPS},
                           **{f"obtenu_{c}": obtenu[c] for c in CHAMPS},
                           **{(k if k == "montant_invente" else f"ok_{k}"): v for k, v in s.items()},
                           "latence_s": obtenu["latence_s"], "brut": obtenu["brut"]})

        with open(dossier_sortie / f"detail_{nom}.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(lignes[0]))
            w.writeheader()
            w.writerows(lignes)

        def taux(cle):
            vals = [s[cle] for s in scores if s[cle] is not None]
            return f"{100 * sum(vals) / len(vals):.0f} % ({sum(vals)}/{len(vals)})" if vals else "-"

        resume.append({"methode": nom, "intention": taux("intention"), "montant": taux("montant"),
                       "montant_invente (plus bas = mieux)": taux("montant_invente"), "client": taux("client"),
                       "produit": taux("produit"),
                       "latence_moy_s": round(sum(l["latence_s"] for l in lignes) / len(lignes), 2)})

    with open(dossier_sortie / "resume.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(resume[0]))
        w.writeheader()
        w.writerows(resume)

    entete = "| " + " | ".join(resume[0]) + " |"
    print(entete)
    print("|" + "---|" * len(resume[0]))
    for r in resume:
        print("| " + " | ".join(str(v) for v in r.values()) + " |")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--methodes", default="regles,traducteur",
                   help="liste séparée par des virgules, ou 'all'")
    p.add_argument("--sortie", default=str(RACINE / "nlp" / "experiments" / "resultats"))
    a = p.parse_args()
    liste = list(METHODES) if a.methodes == "all" else a.methodes.split(",")
    evaluer(liste, Path(a.sortie))
