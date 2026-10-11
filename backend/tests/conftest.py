import pytest


@pytest.fixture(autouse=True)
def moteur_regles_par_defaut(monkeypatch):
    """Les tests ne doivent jamais appeler le vrai LLM, même si une clé OpenRouter
    traîne dans l'environnement du poste : moteur à règles sauf test explicite."""
    monkeypatch.setenv("NLU_MOTEUR", "regles")


@pytest.fixture(autouse=True)
def sans_bot_telegram(monkeypatch):
    """backend/.env peut contenir un vrai TELEGRAM_BOT_TOKEN : le lifespan de
    TestClient ne doit jamais lancer le polling contre l'API réelle."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
