"""Conversions audio entre WhatsApp et les modèles Kiriku.

Responsable : Khalifa (backend/).

  - entrée  : note vocale WhatsApp (OGG/Opus) -> WAV 16 kHz mono pour l'ASR ;
  - sortie  : WAV produit par le TTS Kiriku -> OGG/Opus pour la réponse vocale.
    WhatsApp Cloud API REFUSE le WAV (formats acceptés : ogg/opus, mp3, aac, amr,
    mp4) : sans cette conversion, toute réponse vocale échouait et retombait en texte.

libsndfile (via soundfile, >= 1.2) lit et écrit l'Opus nativement : c'est le
chemin par défaut, sans dépendance système. ffmpeg reste un repli quand la
libsndfile installée ne gère pas l'Opus (anciennes versions).
"""

from __future__ import annotations

import io
import shutil
import subprocess
from math import gcd

SAMPLE_RATE = 16000
# Taux d'échantillonnage acceptés par l'encodeur Opus.
_TAUX_OPUS = (8000, 12000, 16000, 24000, 48000)

FFMPEG_INTROUVABLE = (
    "Conversion audio impossible : libsndfile n'a pas su traiter ce fichier (format non géré ou "
    "audio corrompu) et ffmpeg, le repli, est introuvable sur cette machine. Installez ffmpeg "
    "(ex: `brew install ffmpeg` ou `apt-get install ffmpeg`) puis redémarrez le backend."
)


def _reechantillonner(samples, taux_source: int, taux_cible: int):
    import numpy as np

    if taux_source == taux_cible:
        return samples
    from scipy.signal import resample_poly

    diviseur = gcd(taux_source, taux_cible)
    return resample_poly(samples, taux_cible // diviseur, taux_source // diviseur).astype(np.float32)


def _ffmpeg(entree: bytes, arguments_sortie: list[str]) -> bytes:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(FFMPEG_INTROUVABLE)
    processus = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "-", *arguments_sortie, "-"],
        input=entree,
        capture_output=True,
        check=False,
    )
    if processus.returncode != 0:
        raise RuntimeError(f"Conversion audio impossible (ffmpeg) : {processus.stderr.decode(errors='replace')}")
    return processus.stdout


def ogg_vers_wav(audio_ogg: bytes) -> bytes:
    """OGG/Opus (ou tout format lisible par libsndfile) -> WAV 16 kHz mono (bytes)."""
    try:
        import soundfile as sf

        samples, taux = sf.read(io.BytesIO(audio_ogg), dtype="float32", always_2d=True)
        samples = _reechantillonner(samples.mean(axis=1), taux, SAMPLE_RATE)
        sortie = io.BytesIO()
        sf.write(sortie, samples, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return sortie.getvalue()
    except (ImportError, RuntimeError):
        # sf.LibsndfileError hérite de RuntimeError : format non géré par cette libsndfile.
        return _ffmpeg(audio_ogg, ["-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "wav"])


def wav_vers_ogg_opus(audio_wav: bytes) -> bytes:
    """WAV (sortie du TTS Kiriku) -> OGG/Opus mono, le format des notes vocales WhatsApp."""
    try:
        import soundfile as sf

        samples, taux = sf.read(io.BytesIO(audio_wav), dtype="float32", always_2d=True)
        samples = samples.mean(axis=1)
        if taux not in _TAUX_OPUS:
            samples, taux = _reechantillonner(samples, taux, 48000), 48000
        sortie = io.BytesIO()
        sf.write(sortie, samples, taux, format="OGG", subtype="OPUS")
        return sortie.getvalue()
    except (ImportError, RuntimeError):
        return _ffmpeg(audio_wav, ["-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-f", "ogg"])
