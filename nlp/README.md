# Module NLP — TEWLU Kaddu

Responsable : **Marème** · Données linguistiques : **Ndèye Maguette**

Kaddu comprend une note vocale de commerçant en **wolof (wo), pulaar (ff) ou sérère (srr)**, répète
l'opération pour confirmation, puis l'enregistre.

```
texte de l'ASR -> LLM (comprendre, JSON strict) -> atlas (vérifier, calculer) -> question ou confirmation
               -> « oui » : enregistré | « non » ou correction : le LLM reprend avec tout le contexte
```

## Fichiers

| Fichier | Rôle |
|---|---|
| `kaddu.py` | **Point d'entrée pour l'intégration** : `Kaddu.traiter(numero, texte, langue)` (voir `docs/integration-kaddu.md`) |
| `pipeline.py` | `Conversation` (machine d'états), modèles de réponse, oui / non par langue, `BaseTest` (SQLite) |
| `stockage_json.py` | `BaseJSON` : le carnet d'un commerçant dans un fichier JSON |
| `atlas.py` | Lexique, lecture des montants (chiffres, français, langues locales), rapprochement avec l'atlas |
| `outils/extraire_llm.py` | Consigne du LLM par langue, schéma JSON, `extraire()` / `corriger()` |
| `outils/revue.py` | Interface Streamlit (3 pages : Tester Kaddu, Revue des enregistrements, Métriques) |
| `outils/importer_atlas_officiel.py` | Convertit l'atlas officiel de Ndèye (xlsx) en CSV dans `data/atlas/` |
| `outils/excel_pour_ndeye.py` | Fabrique l'Excel des phrases et questions pour Ndèye |
| `data/atlas/source/` | **Source de vérité** : atlas officiel validé par Ndèye + document « Phases Tests » |
| `data/atlas/*.csv` | Généré par l'import (sauf `reponses.csv`, les phrases de Kaddu) |

## Commandes (depuis la racine du dépôt)

```bash
source ~/miniforge3/bin/activate kaddu
python nlp/outils/importer_atlas_officiel.py     # après chaque nouvelle version de l'atlas de Ndèye
python nlp/test_atlas.py && python nlp/test_pipeline.py
streamlit run nlp/outils/revue.py                # interface ; lien pour l'équipe : ajouter ?vue=equipe
cloudflared tunnel --url http://localhost:8501   # partager l'interface (adresse https temporaire)
python nlp/kaddu.py --langue ff                  # discuter avec Kaddu dans le terminal
```

Le backend de Khalifa (`backend/main.py`, port 8000) doit tourner pour l'ASR et le TTS.
La clé `OPENROUTER_API_KEY` se met dans le `.env` à la racine (jamais committé).

## Règles de collaboration
- Branches `mar/...` ; rien dans `backend/` ; tout changement de `contracts/models.py` validé avec Khalifa.
