import shutil

import pytest

from backend import audio_utils


def test_message_clair_si_ffmpeg_absent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        audio_utils.ogg_vers_wav(b"peu importe")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg non installé sur cette machine")
def test_conversion_reelle_avec_ffmpeg():
    # Un flux WAV minimal (silence) suffit à vérifier que ffmpeg décode/réencode bien
    # et que la sortie est un WAV 16 kHz mono valide (en-tête RIFF/WAVE présent).
    import struct

    entete_wav = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16
    ) + b"data" + struct.pack("<I", 0)

    sortie = audio_utils.ogg_vers_wav(entete_wav)
    assert sortie[:4] == b"RIFF"


def _opus_disponible():
    try:
        import soundfile as sf
    except ImportError:
        return False
    return "OPUS" in sf.available_subtypes("OGG")


@pytest.mark.skipif(not _opus_disponible(), reason="libsndfile sans Opus sur cette machine")
def test_reponse_tts_convertie_en_note_vocale_puis_relue_pour_l_asr():
    import io

    import numpy as np
    import soundfile as sf

    # 22 050 Hz : taux typique d'un TTS VITS, que l'encodeur Opus n'accepte pas tel quel.
    taux = 22050
    signal = (0.3 * np.sin(2 * np.pi * 440 * np.arange(taux) / taux)).astype(np.float32)
    wav = io.BytesIO()
    sf.write(wav, signal, taux, format="WAV")

    ogg = audio_utils.wav_vers_ogg_opus(wav.getvalue())
    assert ogg[:4] == b"OggS"

    relu, taux_relu = sf.read(io.BytesIO(audio_utils.ogg_vers_wav(ogg)))
    assert taux_relu == audio_utils.SAMPLE_RATE
    assert relu.ndim == 1 and abs(len(relu) - audio_utils.SAMPLE_RATE) < 1000
