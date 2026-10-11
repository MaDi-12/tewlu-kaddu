"""Phrases de confirmation et de bilan — TEWLU Kaddu.

Responsable : Khalifa (backend/).

Principe (Dossier-projet.md §5, "Choix important") : les réponses vocales
utilisent des gabarits de phrases fixes remplis avec des chiffres, jamais du
texte généré librement. Le TTS est plus fiable sur des phrases connues, et on
évite qu'un montant soit inventé.

Statut de chaque gabarit :
  - "doc"       : copié verbatim d'un exemple du Dossier-projet.md (parcours
                  d'Aminata, §3), donc déjà writé par l'équipe projet.
  - "a_valider" : écrit pour faire tourner la démo de bout en bout, PAS encore
                  relu par un locuteur natif. Aucun exemple de phrase pulaar ou
                  sérère n'existe dans les documents sources, et aucun membre
                  de l'équipe backend ne parle ces langues : plutôt que
                  d'improviser une grammaire pulaar/sérère qui serait ensuite
                  prononcée à voix haute à de vrais commerçants, LANGUE_VOIX
                  fait retomber ces deux langues sur la voix et les gabarits
                  wolof — exactement ce que le dossier prévoit déjà pour le
                  sérère (§6 : "la réponse est en wolof"), étendu ici au pulaar
                  par la même prudence. L'ASR reste lui branché sur la vraie
                  langue parlée (voir backend/pipeline.py) : seule la VOIX DE
                  RÉPONSE est concernée par ce repli.

À valider avant la démo, par ordre de priorité : tout gabarit "a_valider",
par Ndèye Maguette (seule locutrice native pulaar/sérère) et/ou un locuteur
wolof natif pour les gabarits wolof non issus du dossier.
"""

from __future__ import annotations

import unicodedata

from contracts.models import ExtractionResponse, Langue

# Langue effectivement utilisée pour la voix de réponse (gabarit + modèle TTS).
# L'ASR, lui, utilise toujours la vraie langue parlée par le commerçant.
LANGUE_VOIX: dict[str, str] = {"wo": "wo", "ff": "wo", "srr": "wo"}


def _normaliser(texte: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return sans_accents.strip().lower()


MOTS_OUI = {"waaw", "oui", "yes", "ok", "okay", "waw"}
MOTS_NON = {"deet", "non", "no", "nope"}


def detecter_confirmation(texte: str) -> bool | None:
    """True = confirmé ('waaw'), False = refusé ('déet'), None = pas une réponse de confirmation."""
    mot = _normaliser(texte)
    if mot in MOTS_OUI:
        return True
    if mot in MOTS_NON:
        return False
    return None


# Changement de langue par message ("pulaar", "langue seereer"...) : la langue
# choisie sert ensuite à l'ASR, à la compréhension et à la voix de réponse.
_NOMS_LANGUES = {"wo": "Wolof", "ff": "Pulaar", "srr": "Seereer"}
_MOTS_LANGUE = {
    "wo": {"wolof", "wo"},
    "ff": {"pulaar", "pular", "peul", "fulfulde", "ff", "pu"},
    "srr": {"seereer", "sereer", "serer", "serere", "srr", "se"},
}


def detecter_langue_demandee(texte: str) -> str | None:
    """'pulaar', 'langue pulaar' ou '/langue ff' -> 'ff' ; tout autre message -> None."""
    mots = _normaliser(texte).strip(" .!/").split()
    if len(mots) == 2 and mots[0] in ("langue", "/langue", "lakk", "language"):
        mots = mots[1:]
    if len(mots) != 1:
        return None
    for code, variantes in _MOTS_LANGUE.items():
        if mots[0] in variantes:
            return code
    return None


def phrase_langue_choisie(langue: str) -> str:
    """Accusé de changement de langue (texte seulement, à traduire : 'a_valider')."""
    return f"{_NOMS_LANGUES[langue]} ✓"


# (langue_voix, intention) -> (statut, gabarit)
_CONFIRMATIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("wo", "vente"): ("a_valider", "{montant} F. Waaw walla déet ?"),
    # "Dama jend fukki saaku ceeb." -> "10 sakou ceeb. Waaw walla déet ?" (Dossier-projet.md §3, 8h)
    ("wo", "achat"): ("doc", "{montant} F, jënd. Waaw walla déet ?"),
    ("wo", "depense"): ("a_valider", "{montant} F, depans. Waaw walla déet ?"),
    # "Awa jënd na ... dafa ko leb." -> "Awa, 3 000 F, la leb?. Waaw walla déet ?" (§3, 10h)
    ("wo", "vente_credit"): ("doc", "{client}, {montant} F, la leb. Waaw walla déet ?"),
    ("wo", "remboursement"): ("a_valider", "{client} fey {montant} F. Waaw walla déet ?"),
    ("wo", "annulation"): ("a_valider", "Dinaa far ligéey bi mujj. Waaw walla déet ?"),
}

_ACKS: dict[tuple[str, str], tuple[str, str]] = {
    ("wo", "vente"): ("a_valider", "Baax na."),
    ("wo", "achat"): ("a_valider", "Baax na."),
    ("wo", "depense"): ("a_valider", "Baax na."),
    ("wo", "vente_credit"): ("a_valider", "{client} am na {nouveau_solde} F ci bor."),
    # "Awa fey na junni." -> "Awa fay nala 1 000 F. mou dess 2 000 F." (§3, 14h)
    ("wo", "remboursement"): ("doc", "{client} fay nala {montant} F. Mu dess {nouveau_solde} F."),
    ("wo", "annulation"): ("a_valider", "Baax na, far naa ko."),
}

_AUTRES: dict[tuple[str, str], tuple[str, str]] = {
    # "Ñaata la Awa ma bor ?" -> "Awa amel nala 2 000 F ci souba ba tay." (§3, 17h)
    ("wo", "question_dette_connue"): ("doc", "{client} amel nala {solde} F ci bor."),
    ("wo", "question_dette_zero"): ("a_valider", "{client} amul benn bor."),
    ("wo", "question_dette_inconnu"): ("a_valider", "Xamuma {client}."),
    ("wo", "bilan"): (
        "a_valider",
        "Tay, jaay nga {total_ventes} F. Dañu la bor {total_credits} F. Dañu la fey {total_remboursements} F.",
    ),
    ("wo", "annulation_vide"): ("a_valider", "Amul benn ligéey bu far."),
    ("wo", "incomprehension"): ("a_valider", "Déggëma. Waxaat ko ci yoon wu bees."),
    ("wo", "relance_confirmation"): ("a_valider", "Waaw walla déet ?"),
    ("wo", "refus_pending"): ("a_valider", "Baax na, waxaatuma ko. Wax ko bu bees."),
}


def _chercher(table: dict[tuple[str, str], tuple[str, str]], langue: str, cle: str) -> str:
    langue_voix = LANGUE_VOIX.get(langue, "wo")
    paire = table.get((langue_voix, cle)) or table.get(("wo", cle))
    if paire is None:
        raise KeyError(f"Aucun gabarit pour ({langue_voix}, {cle})")
    return paire[1]


def phrase_confirmation(extraction: ExtractionResponse) -> str:
    """Répète l'opération comprise et demande 'waaw walla déet' avant tout enregistrement.

    Si l'extraction est incomplète, la question posée est celle que nlp.extractor a déjà
    produite (contrat ExtractionResponse.question_clarification) : Khalifa ne réinvente
    pas la formulation de clarification, qui est la responsabilité de Marème.
    """
    if not extraction.est_complet:
        return extraction.question_clarification or _chercher(_AUTRES, extraction.langue, "incomprehension")
    gabarit = _chercher(_CONFIRMATIONS, extraction.langue, extraction.intention)
    return gabarit.format(montant=extraction.donnees.montant, client=extraction.donnees.client)


def phrase_ack(extraction: ExtractionResponse, nouveau_solde: int | None) -> str:
    """Annonce après le 'waaw', une fois l'opération réellement enregistrée en base."""
    gabarit = _chercher(_ACKS, extraction.langue, extraction.intention)
    return gabarit.format(
        montant=extraction.donnees.montant,
        client=extraction.donnees.client,
        nouveau_solde=nouveau_solde,
    )


def phrase_relance_confirmation(langue: Langue) -> str:
    """La réponse n'était ni 'waaw' ni 'déet' : on repose la question sans réenregistrer."""
    return _chercher(_AUTRES, langue, "relance_confirmation")


def phrase_refus_pending(langue: Langue) -> str:
    """Réponse à un 'déet' : l'opération en attente est abandonnée, rien n'est enregistré."""
    return _chercher(_AUTRES, langue, "refus_pending")


def phrase_question_dette(langue: Langue, client: str, solde: int | None) -> str:
    if solde is None:
        return _chercher(_AUTRES, langue, "question_dette_inconnu").format(client=client)
    if solde == 0:
        return _chercher(_AUTRES, langue, "question_dette_zero").format(client=client)
    return _chercher(_AUTRES, langue, "question_dette_connue").format(client=client, solde=solde)


def phrase_bilan(langue: Langue, bilan: dict) -> str:
    return _chercher(_AUTRES, langue, "bilan").format(**bilan)


def phrase_annulation(langue: Langue, operation_annulee) -> str:
    if operation_annulee is None:
        return _chercher(_AUTRES, langue, "annulation_vide")
    return _chercher(_ACKS, langue, "annulation")


def phrase_incomprehension(langue: Langue) -> str:
    return _chercher(_AUTRES, langue, "incomprehension")


def statut_couverture() -> dict[str, list[str]]:
    """Audit : liste les gabarits encore 'a_valider', pour la revue avant démo."""
    non_valides = []
    for table in (_CONFIRMATIONS, _ACKS, _AUTRES):
        for (langue, cle), (statut, _) in table.items():
            if statut == "a_valider":
                non_valides.append(f"{langue}:{cle}")
    return {"a_valider": sorted(non_valides)}


if __name__ == "__main__":
    import json
    print(json.dumps(statut_couverture(), ensure_ascii=False, indent=2))
