"""Essaie la détection de langue sur de vrais audios, avec le vrai modèle M-Kiriku-ASR.
Usage (depuis la racine du dépôt) : python nlp/outils/essai_detection.py nlp/audios/live/*.wav"""
import os
import sys
from pathlib import Path

import librosa
import torch
from dotenv import load_dotenv
from huggingface_hub import snapshot_download
from transformers import WhisperForConditionalGeneration, WhisperProcessor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from detection_langue import detecter_langue_audio  # noqa: E402

load_dotenv("backend/.env")
chemin = snapshot_download("AIHubSN/M-Kiriku-ASR", token=os.environ.get("HF_TOKEN"))
processor = WhisperProcessor.from_pretrained(chemin)
model = WhisperForConditionalGeneration.from_pretrained(chemin).eval()
for fichier in sys.argv[1:]:
    audio, _ = librosa.load(fichier, sr=16000, mono=True)
    d = detecter_langue_audio(processor, model, audio)
    print(f"{Path(fichier).name:45} -> {d['langue']:4} ({d['confiance']:.2f}, marge {d['marge']:.2f})  {d['probas']}")
