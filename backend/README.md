# Module Backend / Audio / WhatsApp — TEWLU Kaddu

Responsable : **Khalifa (KBD)**

## Rôle de ce dossier

1. **Modèles Kiriku** (`kiriku_models.py`) : chargement et inférence ASR (Whisper fine-tuné) et TTS (Coqui VITS), avec un cache mémoire d'un modèle à la fois.
2. **Pipeline WhatsApp** (`pipeline.py`) : note vocale → ASR → compréhension → TTS → note vocale + texte. La compréhension passe par l'un de deux moteurs (`NLU_MOTEUR` dans `.env`) :
   - **`kaddu`** : la couche de Marème (`nlp/kaddu.py` : LLM + atlas, conversation et confirmation, phrases de `reponses.csv`, wolof/pulaar/sérère), branchée par `kaddu_bridge.py`. C'est le moteur de la démo ;
   - **`regles`** : `nlp.extractor` + la machine à états "waaw"/"déet" de `pipeline.py`, sans clé ni réseau ;
   - **`auto`** (défaut) : `kaddu` si `OPENROUTER_API_KEY` est trouvée, sinon `regles`. `GET /` indique le moteur actif.
3. **Carnet Kaddu** (`carnet.py`) : les 5 méthodes de stockage attendues par Kaddu (`docs/integration-kaddu.md` §5), au-dessus de `db.py`. Ce que Kaddu enregistre alimente donc la dette client, le bilan du soir et `GET /carnet`.
4. **Persistance** (`db.py`) : SQLite — clients, dettes, journal des opérations (une annulation passe l'opération au statut `annulee`, rien n'est effacé), confirmations en attente du moteur à règles.
5. **Clients de messagerie** (`whatsapp_client.py`, `telegram_client.py` + `telegram_bot.py`) : réception, téléchargement de média, envoi de texte/audio. `pipeline.canal_pour` choisit le canal d'après l'identifiant du commerçant.
6. **Audio** (`audio_utils.py`) : OGG/Opus (WhatsApp) → WAV 16 kHz pour l'ASR, et WAV du TTS → OGG/Opus pour la réponse (WhatsApp refuse le WAV). Via libsndfile, `ffmpeg` en repli.
7. **Gabarits de phrases** (`templates.py`) : réponses vocales à texte fixe (jamais de texte généré librement, cf. docstring du fichier).
8. **Bilan du soir** (`scheduler.py`) : job cron qui envoie automatiquement le bilan du jour (`db.bilan_jour`) à chaque commerçant ayant eu de l'activité.
9. **Routes de démo et de debug** (`main.py`), pour tout tester sans WhatsApp :
   - `POST /traiter` (`numero`, `texte`, `langue` facultative) : même compréhension que le webhook ;
   - `POST /traiter-vocal` (`numero`, `audio`, `langue`, `avec_audio`) : ASR → compréhension → TTS (WAV en base64) ;
   - `GET /carnet/{numero}` : journal, dettes en cours, bilan du jour ;
   - `/asr`, `/tts`, `/models` : les modèles seuls.

Le commerçant change de langue en envoyant simplement `wolof`, `pulaar` ou `seereer` (ou `langue pulaar`) : la langue sert ensuite à l'ASR, à Kaddu et à la voix.

## Démarrage

Depuis la **racine du dépôt** (`backend` s'importe comme un package, au même titre que `contracts` et `nlp`) :

```bash
python -m venv backend/venv && source backend/venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env   # renseigner HF_TOKEN, WHATSAPP_*
uvicorn backend.main:app --reload
```

Pour la démo avec Kaddu : renseigner `OPENROUTER_API_KEY` dans `backend/.env` (sinon le moteur à règles prend le relais). `ffmpeg` n'est plus obligatoire si `soundfile` embarque libsndfile ≥ 1.2 (cas des wheels récentes) ; il sert de repli.

Essai rapide sans WhatsApp :

```bash
curl -X POST localhost:8000/traiter -F numero=221770000000 -F "texte=Awa jënd na ñaari paket sukar, ñetti junni, dafa ko leb"
curl -X POST localhost:8000/traiter -F numero=221770000000 -F texte=waaw
curl localhost:8000/carnet/221770000000
```

## Tester son code

```bash
pip install -r backend/requirements-dev.txt   # pytest + respx, SANS torch/TTS/transformers
pytest backend/tests
```

Tous les appels à Kiriku et à WhatsApp sont mockés dans `backend/tests` : la suite tourne sans `HF_TOKEN`, sans modèle téléchargé et sans connexion WhatsApp. `audio_utils` teste en plus, quand `ffmpeg` est disponible sur la machine, une vraie conversion (sinon le test est *skip*).

## Telegram (repli de WhatsApp, le plus simple pour la démo)

1. Sur Telegram, écrire à **@BotFather** → `/newbot` → copier le token.
2. Le mettre dans `backend/.env` : `TELEGRAM_BOT_TOKEN="123456:ABC..."`.
3. Lancer le serveur (`uvicorn backend.main:app`) : les logs affichent « Bot Telegram @… à l'écoute » et `GET /` renvoie `"telegram_actif": true`.
4. Ouvrir le bot, `/start`, puis envoyer une note vocale.

Pas de ngrok ni d'URL publique : le serveur va chercher les messages chez Telegram (long polling, `telegram_bot.py`). Même pipeline que WhatsApp (ASR → compréhension → note vocale OGG/Opus + texte), même bilan du soir. Un commerçant Telegram est enregistré sous `tg-<chat_id>` (carnet séparé de son numéro WhatsApp, à consulter avec `GET /carnet/tg-<chat_id>`).

## Tester le webhook en local (ngrok)

Meta exige une URL HTTPS publique pour le webhook :

```bash
ngrok http 8000
# déclarer https://xxxx.ngrok.io/webhook/whatsapp dans la configuration Meta,
# avec WHATSAPP_VERIFY_TOKEN = la valeur mise dans backend/.env
```

## Limites connues (à lire avant la démo)

- **Moteur `kaddu`** : demande une clé OpenRouter et du réseau ; chaque message coûte un appel LLM (quelques secondes). Les dépenses répondent « pas encore disponible ». Les phrases dont la traduction manque dans `reponses.csv` sortent en français (`details.langue_phrase == "fr"`).
- **Moteur `regles`** (repli) : `nlp.extractor` ne renseigne jamais `donnees.client` et ne détecte ni `achat` ni `depense` ; ses gabarits pulaar et sérère n'existent pas (repli sur la voix et les gabarits wolof, `templates.LANGUE_VOIX`, cf. `templates.statut_couverture()`).
- Le bilan du soir (`scheduler.py`) utilise encore les gabarits wolof de `templates.py`, pas les phrases de Kaddu.
- Moteur `regles` seulement : pas de verrou sur l'état de confirmation en attente (Kaddu, lui, verrouille par numéro) : deux notes vocales envoyées coup sur coup par le même commerçant avant la fin du traitement de la première pourraient course-condition sur `pending_extractions`. Acceptable pour la démo (un seul commerçant à la fois dans les tests utilisateurs prévus), à revoir avant une mise en production réelle.
- Le bilan vocal automatique (`scheduler.py`, `BILAN_HEURE` dans `.env`, 21h par défaut) se déclenche à l'heure du **serveur**, pas de celle de chaque commerçant : pas de notion de fuseau horaire par vendeur dans le schéma actuel. Sans impact pour la démo (tous les commerçants visés sont à Dakar).
- Un seul processus serveur (`uvicorn` sans `--workers`) : le verrou par commerçant de Kaddu et le filtre anti-doublon des webhooks (`pipeline.deja_recu`) vivent en mémoire, et deux processus feraient tourner deux boucles Telegram (Telegram renvoie une erreur 409 au second).
- Telegram traite les messages un par un, dans l'ordre : pendant qu'une note vocale passe dans l'ASR et le LLM (~15-20 s), les messages des autres commerçants attendent.
