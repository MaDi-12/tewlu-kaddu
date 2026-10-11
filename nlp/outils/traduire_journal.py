"""Traduit en français chaque transcription du journal avec le traducteur GalsenAI."""
import csv
import re
from pathlib import Path

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

MODELE = "galsenai/wolofToFrenchTranslator_nllb"
entree = Path("nlp/data/journal_asr.csv")
sortie = Path("nlp/data/journal_traduit.csv")

tok = AutoTokenizer.from_pretrained(MODELE)
mod = AutoModelForSeq2SeqLM.from_pretrained(MODELE).eval()


def traduire(texte):
    # Préfixe repris de la fiche du modèle GalsenAI
    inputs = tok("translate Wolof to French: " + texte, return_tensors="pt")
    with torch.no_grad():
        out = mod.generate(**inputs, max_new_tokens=128, num_beams=4)
    return tok.batch_decode(out, skip_special_tokens=True)[0]


with open(entree, encoding="utf-8") as f:
    lecteur = csv.DictReader(f)
    champs = lecteur.fieldnames + ["traduction_fr"]
    lignes = list(lecteur)

for l in lignes:
    texte = re.sub(r"<\|\w+\|>", "", l["transcription"]).strip()
    l["traduction_fr"] = traduire(texte)
    print(l["fichier"], "|", l["transcription"], "->", l["traduction_fr"])

with open(sortie, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=champs)
    w.writeheader()
    w.writerows(lignes)
print("Résultats dans", sortie)