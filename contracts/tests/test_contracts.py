import json
from pathlib import Path
import pytest
from contracts.models import ExtractionResponse, ExtractionRequest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def test_examples_are_valid():
    example_files = list(EXAMPLES_DIR.glob("*.json"))
    assert len(example_files) >= 3, "Au moins 3 exemples doivent exister"
    
    for filepath in example_files:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        obj = ExtractionResponse.model_validate(data)
        assert obj.intention in [
            "vente", "vente_credit", "remboursement", "achat",
            "depense", "question_dette", "question_bilan", "annulation", "incomprehension"
        ]


def test_extra_fields_forbidden():
    valid_data = {
        "intention": "vente",
        "donnees": {
            "client": "Awa",
            "produit": "riz",
            "quantite": 1.0,
            "unite": "sac",
            "montant": 15000,
        },
        "langue": "wo",
        "methode": "regles",
        "confiance": 0.9,
        "est_complet": True,
        "champs_manquants": [],
        "question_clarification": None,
        "champ_inconnu": "interdit"  # doit lever ValidationError
    }
    with pytest.raises(Exception):
        ExtractionResponse.model_validate(valid_data)
