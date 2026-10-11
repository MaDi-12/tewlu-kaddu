"""Contrat d'échange TEWLU Kaddu — source de vérité unique.

Ce contrat définit ce qui circule entre :
  - le pipeline audio / WhatsApp (KBD) -> le moteur de compréhension (Marème)
  - le moteur de compréhension (Marème) -> l'orchestrateur & la BDD (KBD)

Toute modification de ce fichier passe obligatoirement par une branche
`contrat/...` approuvée par les deux membres.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

VERSION_CONTRAT = "1.0.0"


class _Strict(BaseModel):
    """Modèle strict : tout champ inconnu est strictement rejeté."""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_serialization_defaults_required=True
    )


# ---------------------------------------------------------------------------
# Types de base
# ---------------------------------------------------------------------------

Langue = Literal["wo", "ff", "srr"]

Intention = Literal[
    "vente",              # Vente payée comptant
    "vente_credit",       # Vente à crédit (dette client)
    "remboursement",      # Paiement / règlement de dette
    "achat",              # Achat de stock marchandise
    "depense",            # Charge courante (transport, facture, loyer...)
    "question_dette",     # "Combien Awa me doit ?"
    "question_bilan",     # "Combien j'ai vendu aujourd'hui ?"
    "annulation",         # Annulation de la dernière opération
    "incomprehension"     # Phrase inintelligible ou hors domaine
]

MethodeExtraction = Literal["regles", "traducteur", "llm", "mock"]


# ---------------------------------------------------------------------------
# Entrée (KBD -> Marème) : ce qui sort de l'ASR Kiriku
# ---------------------------------------------------------------------------

class ExtractionRequest(_Strict):
    """Texte issu de la transcription vocale Whisper."""
    transcription: str = Field(
        ...,
        description="Transcription brute de la note vocale",
        examples=["Awa jënd na ñaari paket sukar, ñetti junni, dafa ko bor"]
    )
    langue: Langue = Field(
        "wo",
        description="Code langue détecté ou sélectionné (wo, ff, srr)"
    )
    contexte_client_id: str | None = Field(
        None,
        description="Identifiant ou téléphone du commerçant pour lier son répertoire client local"
    )


# ---------------------------------------------------------------------------
# Sortie (Marème -> KBD) : l'extraction standardisée
# ---------------------------------------------------------------------------

class DonneesExtraction(_Strict):
    client: str | None = Field(
        None,
        description="Nom ou désignation du client (ex: Awa)",
        examples=["Awa"]
    )
    produit: str | None = Field(
        None,
        description="Nom du produit normalisé",
        examples=["sucre"]
    )
    quantite: float | None = Field(
        None,
        description="Quantité en nombre (ex: 2.0)",
        examples=[2.0]
    )
    unite: str | None = Field(
        None,
        description="Unité de mesure (paquet, sac, kilo, pot, tas...)",
        examples=["paquet"]
    )
    montant: int | None = Field(
        None,
        description="Montant total en FCFA (chiffre entier)",
        examples=[3000]
    )


class ExtractionResponse(_Strict):
    """Format de sortie unique garanti (Section 4.3 du document TEWLU)."""
    intention: Intention
    donnees: DonneesExtraction
    langue: Langue
    methode: MethodeExtraction
    confiance: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Score de confiance entre 0 et 1"
    )
    
    # Contrôle de complétude (Étape 6 de l'architecture)
    est_complet: bool = Field(
        ...,
        description="True si tous les champs obligatoires de l'intention sont présents"
    )
    champs_manquants: list[str] = Field(
        default_factory=list,
        description="Liste des champs requis manquants (ex: ['montant'])"
    )
    question_clarification: str | None = Field(
        None,
        description="Texte de la question ciblée à poser au commerçant si incomplet (ex: 'Ñaata la ?')"
    )
