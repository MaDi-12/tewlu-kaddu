"""Moteur de compréhension du langage (NLU) — TEWLU Kaddu.

Responsable : Marème.
Fournisseur données : Ndèye Maguette.

Ce module extrait l'intention et les entités (client, produit, montant, quantité)
à partir du texte transcrit par l'ASR Kiriku.
"""

from __future__ import annotations

import re
from contracts.models import (
    DonneesExtraction,
    ExtractionResponse,
    Intention,
    Langue,
    MethodeExtraction,
)


def extract_information(
    transcription: str,
    langue: Langue = "wo",
    contexte_client_id: str | None = None,
) -> ExtractionResponse:
    """Point d'entrée principal pour la compréhension de texte.

    Args:
        transcription: Phrase issue de Whisper ASR (ex: "Awa jënd na ñaari paket sukar 3000").
        langue: "wo" (wolof), "ff" (pulaar), "srr" (sérère).
        contexte_client_id: Identifiant optionnel pour lier le carnet client.

    Returns:
        ExtractionResponse conforme au contrat Pydantic strict.
    """
    text_clean = transcription.strip()
    text_lower = text_clean.lower()

    # Valeurs par défaut
    intention: Intention = "incomprehension"
    client: str | None = None
    produit: str | None = None
    quantite: float | None = None
    unite: str | None = None
    montant: int | None = None
    methode: MethodeExtraction = "regles"
    confiance: float = 0.80

    # 1. Détection simple des intentions basées sur les mots-clés Wolof (baseline)
    if any(k in text_lower for k in ["bor", "credit", "crédit", "léb"]):
        intention = "vente_credit"
    elif any(k in text_lower for k in ["fey", "fay", "rembours", "payer"]):
        intention = "remboursement"
    elif any(k in text_lower for k in ["jaay", "jay", "jend", "jënd", "vendu"]):
        intention = "vente"
    elif any(k in text_lower for k in ["ñata", "ñaata", "combien"]) and "bor" in text_lower:
        intention = "question_dette"
    elif any(k in text_lower for k in ["ñata", "ñaata", "combien", "bilan", "jaay na"]):
        intention = "question_bilan"
    elif any(k in text_lower for k in ["annule", "far", "bayil"]):
        intention = "annulation"

    # 2. Détection basique de montants en chiffres (ex: "3000", "5000 f")
    match_chiffres = re.search(r"\b(\d+)\s*(f|cfa|fcfa)?\b", text_lower)
    if match_chiffres:
        try:
            montant = int(match_chiffres.group(1))
        except ValueError:
            pass

    # 3. Détection basique de produits et unités (baseline d'exemple)
    if "sukar" in text_lower or "sucre" in text_lower:
        produit = "sucre"
    elif "ceeb" in text_lower or "riz" in text_lower:
        produit = "riz"
    elif "diw" in text_lower or "huile" in text_lower:
        produit = "huile"

    if "paket" in text_lower or "paquet" in text_lower:
        unite = "paquet"
    elif "saag" in text_lower or "sac" in text_lower:
        unite = "sac"
    elif "kilo" in text_lower:
        unite = "kilo"

    # 4. Contrôle des champs obligatoires (Étape 6 du document d'architecture)
    champs_manquants: list[str] = []
    question_clarification: str | None = None

    if intention in ("vente", "vente_credit", "remboursement", "achat", "depense"):
        if montant is None:
            champs_manquants.append("montant")
            question_clarification = "Ñaata la ?" if langue == "wo" else "Combien ?"

    if intention in ("vente_credit", "remboursement", "question_dette"):
        if client is None:
            champs_manquants.append("client")
            if not question_clarification:
                question_clarification = "Kan la ?" if langue == "wo" else "Quel client ?"

    est_complet = (len(champs_manquants) == 0 and intention != "incomprehension")

    return ExtractionResponse(
        intention=intention,
        donnees=DonneesExtraction(
            client=client,
            produit=produit,
            quantite=quantite,
            unite=unite,
            montant=montant,
        ),
        langue=langue,
        methode=methode,
        confiance=confiance,
        est_complet=est_complet,
        champs_manquants=champs_manquants,
        question_clarification=question_clarification,
    )
