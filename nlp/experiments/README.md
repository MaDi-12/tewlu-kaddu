# Expériences wolof — compréhension du langage

Compare plusieurs méthodes d'extraction sur le même jeu de test (`nlp/data/phrases_test_wo_provisoire.csv`).

| Méthode | Ce qu'elle teste | GPU |
|---|---|---|
| `regles` | Baseline actuelle (`nlp/extractor.py`) | non |
| `traduction_parfaite` | Plafond de la piste traduction : traduction de référence + règles en français | non |
| `traducteur` | `galsenai/wolofToFrenchTranslator_nllb` puis règles en français | conseillé |
| `tinyaya_json` | TinyAya Wolof SFT, extraction JSON avec 3 exemples | oui |
| `tinyaya_tool` | TinyAya Wolof SFT, appel d'outil `enregistrer_operation` | oui |
| `hybride_ner` | Règles + NER TinyAya pour le nom du client | oui |

## Lancer (Colab ou machine GPU)

```bash
pip install -r nlp/experiments/requirements-experiences.txt
huggingface-cli login          # accès au dépôt TinyAya
python -m nlp.experiments.eval_wolof --methodes regles,traduction_parfaite      # sans GPU, quelques secondes
python -m nlp.experiments.eval_wolof --methodes traducteur
python -m nlp.experiments.eval_wolof --methodes tinyaya_json,tinyaya_tool,hybride_ner
```

Sorties dans `nlp/experiments/resultats/` : `resume.csv` (une ligne par méthode) et `detail_<methode>.csv` (une ligne par phrase, avec la sortie brute du modèle).

## À adapter avant de lancer les méthodes TinyAya

- `CONSIGNE_NER` : copier la consigne exacte des exemples `ner` de `galsenai/wolof_sft`.
- `OUTIL` / `methode_tinyaya_tool` : présenter l'outil comme dans les exemples `tool_use` du même jeu.

## Limites

- Le jeu de test est **provisoire** (20 phrases) : à relire par une locutrice native, puis remplacer par les phrases de Ndèye Maguette.
- Les règles en français ont été écrites en regardant ces 20 traductions : le score `traduction_parfaite` est optimiste.
- Les phrases sont du texte écrit : il faudra refaire la mesure sur de vraies transcriptions ASR.
