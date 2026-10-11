"""Détection automatique de la langue d'une note vocale (wolof, pulaar, sérère) avec l'ASR Kiriku lui-même.

M-Kiriku-ASR est un Whisper qui a trois jetons de langue : <|wo|>, <|pu|>, <|se|>. Au lieu d'imposer la langue,
on fait UN pas du décodeur après <|startoftranscript|> et on compare la probabilité de ces trois jetons.
Pas de modèle en plus, et le sérère est couvert.

    from detection_langue import detecter_langue_audio, choisir_langue
    det = detecter_langue_audio(processor, model, audio_16k)   # {"langue": "ff", "confiance": 0.93, "probas": {...}}
    langue = choisir_langue(det, langue_conversation="wo", conversation_en_cours=True)

Changement de langue (choisir_langue) : le commerçant peut changer de langue à tout moment, même en pleine
conversation, sauf message court (< 2 s) ou confiance < 40 % : on garde alors la langue précédente.
"""
from __future__ import annotations

import math

# Codes Kaddu -> jetons de langue de M-Kiriku-ASR
JETONS = {"wo": "<|wo|>", "ff": "<|pu|>", "srr": "<|se|>"}
# Règle (décision de Marème, 10/10/2026) : on suit la langue DOMINANTE renvoyée par l'ASR, y compris au milieu
# d'une conversation (le commerçant peut changer de langue quand il veut), SAUF :
#   - message court (moins de DUREE_MIN_S secondes d'audio) : « waaw », « eey », « 2000 » sont trop courts pour
#     distinguer trois langues proches ;
#   - confiance de la langue dominante inférieure à CONFIANCE_MIN.
# Dans ces deux cas, on continue avec la langue utilisée précédemment.
# Mesuré sur 7 vrais audios wolof : bonne langue 7/7, confiance 0,47 à 0,62.
CONFIANCE_MIN = 0.40
DUREE_MIN_S = 2.0
DUREE_ANALYSE_S = 30        # Whisper analyse 30 s par passe : le début suffit pour la langue


def _softmax(valeurs: list[float]) -> list[float]:
    m = max(valeurs)
    e = [math.exp(v - m) for v in valeurs]
    s = sum(e)
    return [x / s for x in e]


def detecter_langue_audio(processor, model, audio, sampling_rate: int = 16000) -> dict:
    """Langue la plus probable d'un audio mono 16 kHz (tableau numpy), parmi wolof, pulaar et sérère.

    Renvoie {"langue": "wo" | "ff" | "srr", "confiance": 0..1, "probas": {"wo": .., "ff": .., "srr": ..}}.
    `processor` et `model` sont ceux que le backend a déjà chargés pour l'ASR (WhisperProcessor,
    WhisperForConditionalGeneration).
    """
    import torch

    tok = processor.tokenizer
    ids = {lang: tok.convert_tokens_to_ids(jeton) for lang, jeton in JETONS.items()}
    inconnu = tok.unk_token_id
    manquants = [JETONS[l] for l, i in ids.items() if i is None or i == inconnu]
    if manquants:
        raise ValueError(f"Jetons de langue absents du modèle : {manquants}")
    debut = tok.convert_tokens_to_ids("<|startoftranscript|>")

    duree = len(audio) / sampling_rate
    extrait = audio[: int(DUREE_ANALYSE_S * sampling_rate)]
    parametre = next(model.parameters())
    features = processor.feature_extractor(extrait, sampling_rate=sampling_rate, return_tensors="pt") \
        .input_features.to(parametre.device, dtype=parametre.dtype)
    with torch.no_grad():
        sortie = model(input_features=features,
                       decoder_input_ids=torch.tensor([[debut]], device=parametre.device))
    logits = sortie.logits[0, -1]
    return _resultat({lang: float(logits[i]) for lang, i in ids.items()}, duree)


def _resultat(logits_par_langue: dict[str, float], duree_s: float | None = None) -> dict:
    langues = list(logits_par_langue)
    probas = dict(zip(langues, _softmax([logits_par_langue[l] for l in langues])))
    meilleure = max(probas, key=probas.get)
    classees = sorted(probas.values(), reverse=True)
    return {"langue": meilleure, "confiance": round(probas[meilleure], 3),
            "marge": round(classees[0] - classees[1], 3), "probas": {l: round(p, 3) for l, p in probas.items()},
            "duree_s": round(duree_s, 2) if duree_s is not None else None}


def fiable(detection: dict | None) -> bool:
    """La détection est-elle utilisable ? Non si absente, si l'audio est court ou si la confiance est < 40 %."""
    if not detection:
        return False
    duree = detection.get("duree_s")
    if duree is not None and duree < DUREE_MIN_S:
        return False
    return detection["confiance"] >= CONFIANCE_MIN


def choisir_langue(detection: dict | None, langue_conversation: str | None = None,
                   conversation_en_cours: bool = False, langue_habituelle: str = "wo") -> str:
    """Décide de la langue à utiliser pour ce message.

    - détection fiable (audio d'au moins 2 s, confiance >= 40 %) : la langue dominante, même en pleine conversation
      (la conversation continue alors dans la nouvelle langue, sans perdre l'opération en cours) ;
    - sinon : la langue de la conversation en cours, ou à défaut la langue habituelle du commerçant.
    """
    if fiable(detection):
        return detection["langue"]
    if conversation_en_cours and langue_conversation:
        return langue_conversation
    return langue_habituelle
