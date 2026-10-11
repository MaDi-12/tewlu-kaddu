# TEWLU Kaddu — livraison v6 (10 octobre 2026)

## Installer
1. Dézipper ce dossier PAR-DESSUS ~/Documents/tewlu_kaddu (les fichiers du même nom sont remplacés).
2. Supprimer ce qui ne sert plus :
       cd ~/Documents/tewlu_kaddu
       git rm --cached -q nlp/outils/importer_atlas_ndeye.py 2>/dev/null; rm -f nlp/outils/importer_atlas_ndeye.py
       rmdir -- -c -y conda-forge ffmpeg install nlp/audiostouch nlp/outils/transcrire_dossier.pyconda   # dossiers vides
3. Vérifier :
       source ~/miniforge3/bin/activate kaddu
       pip install -U streamlit openpyxl          # micro et pages : Streamlit récent
       python nlp/outils/importer_atlas_officiel.py
       python nlp/test_atlas.py && python nlp/test_pipeline.py      # 11 + 20 tests
4. Lancer (backend de Khalifa sur le port 8000 d'abord) :
       streamlit run nlp/outils/revue.py
   Lien pour l'équipe / Ndèye (page de test seule, sans menu) : ajouter ?vue=equipe à l'adresse
   (avec cloudflared : https://xxxx.trycloudflare.com/?vue=equipe).

## Ce qui change
- Atlas : uniquement l'atlas officiel de Ndèye (nlp/data/atlas/source/), importé en CSV.
- Pulaar et sérère dans tout le pipeline (ASR pu/se, consigne LLM, montants, oui/non, phrases de Phases Tests,
  voix pulaar pour le sérère).
- Streamlit en 3 pages : Tester Kaddu (équipe) · Revue des enregistrements · Métriques et données.
- nlp/kaddu.py + docs/integration-kaddu.md pour Khalifa (WhatsApp, JSON ou base de données).
- docs/contexte-projet.md : contexte complet à coller dans une nouvelle session.
- docs/ndeye/TEWLU_Kaddu_pour_Ndeye.xlsx : à envoyer à Ndèye.
