"""Transcrit tous les audios d'un dossier avec le backend, et garde tout dans un CSV."""
import csv
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path

import requests

API = "http://localhost:8000/asr"
MODELE = "AIHubSN/M-Kiriku-ASR"
LANGUE = "wo"  # wo, pu ou se

dossier = Path(sys.argv[1])
journal = Path("nlp/data/journal_asr.csv")
champs = ["date", "fichier", "modele", "langue", "transcription", "duree_s"]
formats = {".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac"}

def en_wav(audio: Path) -> Path:
    """Convertit n'importe quel audio en WAV 16 kHz mono (format attendu par Whisper)."""
    if audio.suffix.lower() == ".wav":
        return audio
    dossier_wav = audio.parent / "wav"
    dossier_wav.mkdir(exist_ok=True)
    wav = dossier_wav / (audio.stem + ".wav")
    if not wav.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio),
                        "-ar", "16000", "-ac", "1", str(wav)], check=True)
    return wav

deja_faits = set()
if journal.exists():
    with open(journal, encoding="utf-8") as f:
        deja_faits = {(l["fichier"], l["modele"], l["langue"]) for l in csv.DictReader(f)}

nouveau = not journal.exists()
with open(journal, "a", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=champs)
    if nouveau:
        w.writeheader()
    for audio in sorted(dossier.iterdir()):
        if audio.suffix.lower() not in formats:
            continue
        if (audio.name, MODELE, LANGUE) in deja_faits:
            print("déjà transcrit :", audio.name)
            continue
        debut = time.time()
        with open(en_wav(audio), "rb") as a:
            r = requests.post(API, files={"audio": a},
                              data={"model": MODELE, "language": LANGUE}, timeout=900)
        if not r.ok:
            print(f"ERREUR {audio.name} : {r.status_code} {r.text}")
            continue
        texte = r.json()["text"]
        duree = round(time.time() - debut, 1)
        w.writerow({"date": datetime.now().isoformat(timespec="seconds"), "fichier": audio.name,
                    "modele": MODELE, "langue": LANGUE, "transcription": texte, "duree_s": duree})
        f.flush()
        print(f"{audio.name} ({duree} s) -> {texte}")