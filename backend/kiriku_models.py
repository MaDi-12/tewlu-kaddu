"""Chargement et inférence des modèles Kiriku (ASR Whisper + TTS Coqui).

Extrait de main.py pour être appelable directement par le pipeline WhatsApp
(backend/pipeline.py) sans repasser par un aller-retour HTTP sur le process
qui les sert déjà. main.py garde les routes /asr et /tts comme des clients
HTTP de ce module, pour l'outillage de debug (Swagger, curl) et le dashboard.

Les imports de torch/transformers/TTS/librosa sont volontairement différés à
l'intérieur des fonctions : ce module expose aussi TTS_MODELS/ASR_MODELS, des
constantes pures lues par des routes légères (webhook, /models) et par les
tests. Les forcer au chargement du module obligerait tout le pipeline
WhatsApp (et sa suite de tests) à installer ces dépendances lourdes même
quand les appels réels aux modèles sont mockés.
"""

from __future__ import annotations

import gc
import io
import math
import os
import re
import threading

TTS_MODELS = {
    "AIHubSN/Kiriku-Wolof-TTS": "Kiriku Wolof TTS",
    "AIHubSN/Kiriku-Pulaar-TTS": "Kiriku Pulaar TTS",
}

# languages: custom Whisper language tokens (<|wo|>, <|pu|>, <|se|>) accepted by the model.
# None means the model is trained to run without a forced language token.
ASR_MODELS = {
    "AIHubSN/M-Kiriku-ASR": {"label": "M-Kiriku ASR (Multilingue)", "languages": ["wo", "pu", "se"]},
    "AIHubSN/Kiriku-Wolof-ASR": {"label": "Kiriku Wolof ASR", "languages": None},
}

ASR_SAMPLE_RATE = 16000
CHUNK_SEC, OVERLAP_SEC = 30, 5
MAX_NEW_TOKENS = 440

# Only one model of each kind is kept in memory at a time.
_tts = {"id": None, "synth": None}
_asr = {"id": None, "processor": None, "model": None}
_tts_lock = threading.Lock()
_asr_lock = threading.Lock()
_device = {"value": None}


def _get_device():
    if _device["value"] is None:
        import torch
        _device["value"] = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
    return _device["value"]


def _free_memory():
    import torch
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


def get_tts(model_id: str):
    from huggingface_hub import snapshot_download
    from TTS.utils.synthesizer import Synthesizer

    if _tts["id"] != model_id:
        _tts.update(id=None, synth=None)
        _free_memory()

        print(f"Loading TTS model {model_id}...")
        # Coqui TTS 0.19 checkpoints contain pickled config objects; torch>=2.6 defaults
        # torch.load to weights_only=True, which refuses them. Must be set before torch loads.
        os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
        path = snapshot_download(repo_id=model_id, token=os.environ.get("HF_TOKEN"))
        # VITS runs on CPU: fast enough, and some of its ops are not supported on MPS.
        synth = Synthesizer(
            tts_checkpoint=os.path.join(path, "model.pth"),
            tts_config_path=os.path.join(path, "config.json"),
            use_cuda=False,
        )
        _tts.update(id=model_id, synth=synth)
    return _tts["synth"]


def get_asr(model_id: str):
    import torch
    from huggingface_hub import snapshot_download
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    if _asr["id"] != model_id:
        _asr.update(id=None, processor=None, model=None)
        _free_memory()

        print(f"Loading ASR model {model_id}...")
        path = snapshot_download(repo_id=model_id, token=os.environ.get("HF_TOKEN"))
        processor = WhisperProcessor.from_pretrained(path)
        dtype = torch.float16 if _get_device() in ("mps", "cuda") else torch.float32
        model = WhisperForConditionalGeneration.from_pretrained(path, torch_dtype=dtype).to(_get_device())
        model.eval()
        _asr.update(id=model_id, processor=processor, model=model)
    return _asr["processor"], _asr["model"]


def transcribe(processor, model, audio, language: str | None) -> str:
    import torch

    device = _get_device()
    dtype = torch.float16 if device in ("mps", "cuda") else torch.float32

    decoder_input_ids = None
    if language:
        tok = processor.tokenizer
        ids = [tok.convert_tokens_to_ids(t) for t in
               ("<|startoftranscript|>", f"<|{language}|>", "<|transcribe|>", "<|notimestamps|>")]
        decoder_input_ids = torch.tensor([ids], device=device)

    # Whisper handles 30s per pass: chunk longer audio with overlap.
    sr = ASR_SAMPLE_RATE
    duration = len(audio) / sr
    step = CHUNK_SEC - OVERLAP_SEC
    n_chunks = max(1, math.ceil((duration - OVERLAP_SEC) / step))

    texts = []
    for i in range(n_chunks):
        start = i * step
        chunk = audio[int(start * sr):int(min(start + CHUNK_SEC, duration) * sr)]
        if len(chunk) < sr * 0.5:
            continue

        features = processor.feature_extractor(
            chunk, sampling_rate=sr, return_tensors="pt"
        ).input_features.to(device, dtype=dtype)

        kwargs = {"max_new_tokens": MAX_NEW_TOKENS}
        if decoder_input_ids is not None:
            kwargs["decoder_input_ids"] = decoder_input_ids
        with torch.no_grad():
            generated = model.generate(features, **kwargs)
        texts.append(nettoyer_transcription(processor.batch_decode(generated, skip_special_tokens=True)[0]))

    return " ".join(t for t in texts if t)


def synthesize(text: str, model_id: str) -> bytes:
    """Texte -> WAV (bytes), normalisé en volume. Lève KeyError si model_id inconnu."""
    import numpy as np
    import scipy.io.wavfile

    if model_id not in TTS_MODELS:
        raise KeyError(model_id)
    with _tts_lock:
        synth = get_tts(model_id)
        wav = synth.tts(text=text)

    wav = np.asarray(wav, dtype=np.float32)
    peak = np.abs(wav).max()
    if peak > 0:
        wav = wav / peak * 0.95
    buffer = io.BytesIO()
    scipy.io.wavfile.write(buffer, rate=synth.output_sample_rate, data=(wav * 32767).astype(np.int16))
    return buffer.getvalue()


def recognize(audio, model_id: str, language: str | None) -> str:
    """Audio mono 16kHz (np.ndarray) -> texte. Lève KeyError/ValueError sur modèle/langue invalides."""
    if model_id not in ASR_MODELS:
        raise KeyError(model_id)
    allowed = ASR_MODELS[model_id]["languages"]
    if allowed is None:
        language = None
    elif language not in allowed:
        raise ValueError(f"Langue invalide pour {model_id} : {language} (attendu : {allowed})")

    with _asr_lock:
        processor, model = get_asr(model_id)
        return transcribe(processor, model, audio, language)


# Les jetons de langue ajoutés au fine-tuning (<|wo|>, <|pu|>, <|se|>) ne sont pas
# marqués "spéciaux" dans le tokenizer : skip_special_tokens les laisse dans le texte.
# Sans ce nettoyage, un "waaw" arrive en "<|wo|> waaw" et la confirmation échoue.
_JETONS_WHISPER = re.compile(r"<\|[^|>]*\|>")


def nettoyer_transcription(texte: str) -> str:
    return " ".join(_JETONS_WHISPER.sub(" ", texte).split())


def recognize_wav_bytes(audio_wav: bytes, model_id: str, language: str | None) -> str:
    """WAV (bytes, n'importe quel taux d'échantillonnage) -> texte."""
    import librosa

    samples, _ = librosa.load(io.BytesIO(audio_wav), sr=ASR_SAMPLE_RATE, mono=True)
    return recognize(samples, model_id, language)
