# Contribuer à TEWLU Kaddu

Le détail de la méthodologie est dans [docs/methode-de-travail.md](docs/methode-de-travail.md).

L'essentiel pour le hackathon :

1. **On ne pousse jamais directement sur `main`** : une tâche = une branche courte (`kbd/…`, `mar/…`, `contrat/…`) = une Pull Request.
2. **`main` est toujours vert, stable et démontrable**.
3. **On relit la PR de l'autre dans les 2 heures**.
4. **Le contrat (`contracts/models.py`) ne change que par une PR `contrat/…` approuvée par Khalifa et Marème**.
5. **Aucun secret dans Git** : `.env.example` documente les clés requises, `.env` reste strictement local.
6. **Isolation stricte** : Khalifa est responsable de `backend/`, Marème est responsable de `nlp/`, Ndèye Maguette alimente `nlp/data/`.
