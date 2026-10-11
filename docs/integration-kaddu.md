# Intégrer la couche Kaddu (WhatsApp, base de données)

Pour : **Khalifa** · Auteure de la couche : **Marème** · Mis à jour le 10 octobre 2026

Ce document explique comment appeler la couche de compréhension de Kaddu depuis le backend ou le bot
WhatsApp, et comment ranger les données : fichiers JSON pour démarrer, ou une vraie base de données.
**Rien dans `backend/` ni dans `contracts/` n'a été modifié** : les exemples de code ci-dessous sont à
ajouter de ton côté, sur une branche `kbd/...`.

---

## 1. Ce que fait la couche

```
note vocale WhatsApp
  -> (toi)   télécharger l'audio, POST /asr  (model=AIHubSN/M-Kiriku-ASR, language=wo|pu|se)
  -> (Kaddu) kaddu.traiter(numero, texte, langue)
               LLM (comprendre) -> atlas (vérifier, calculer) -> question ou confirmation
               « oui » -> opération enregistrée dans le carnet du commerçant
  -> (toi)   POST /tts (text=texte_tts, model=voix_tts) -> WAV -> OGG/Opus -> note vocale WhatsApp
```

- Une conversation = une opération, sur plusieurs messages (ex. : vente -> « tu veux dire soble ? » ->
  « waaw » -> confirmation -> « waaw » -> « c'est noté »).
- Les réponses de Kaddu viennent de modèles de phrases (`nlp/data/atlas/reponses.csv`), jamais du LLM.
- Sérère : Kaddu comprend le sérère mais **répond en wolof** (texte et voix wolof), faute de voix sérère
  (`pipeline.LANGUE_REPONSE`).
- Les montants sont toujours calculés par le code.

## 2. Démarrage

```bash
source ~/miniforge3/bin/activate kaddu      # ou ton venv : pip install openai pydantic num2words "text2num<3" requests
# Clé du LLM : une ligne OPENROUTER_API_KEY=... dans le fichier .env à la racine du dépôt
python nlp/test_atlas.py && python nlp/test_pipeline.py     # tests sans appel au LLM
python nlp/kaddu.py --langue wo                             # discuter avec Kaddu dans le terminal
```

## 3. Le point d'entrée : `Kaddu.traiter()`

```python
import sys; sys.path.insert(0, "nlp")       # ou : from nlp.kaddu import Kaddu depuis la racine
from kaddu import Kaddu

kaddu = Kaddu("nlp/data/whatsapp")          # créer UNE fois au démarrage du serveur

r = kaddu.traiter("221770000000", "Awa jënd na ñaari paket sukar, ñetti junni, daf koo leb", "wo")
```

### Entrée

| Paramètre | Type | Valeurs |
|---|---|---|
| `numero` | str | numéro WhatsApp du commerçant (identifie sa conversation et son carnet) |
| `texte` | str | transcription de l'ASR (ou message texte) |
| `langue` | str | `wo`, `ff`, `srr` — ou directement le code ASR `wo`, `pu`, `se` |
| `source` | str | facultatif, noté dans les métriques (défaut `whatsapp`) |

### Sortie (dict)

| Clé | Exemple | Usage |
|---|---|---|
| `reponse` | `"Awa dafa léeb deux paket sukar lu toolok quinze mille francs, maanaam ñetti junni. Waaw walla déet ?"` | texte à afficher / envoyer |
| `texte_tts` | même texte en minuscules, `x` initial -> `kh` | à envoyer tel quel à `POST /tts` |
| `voix_tts` | `AIHubSN/Kiriku-Wolof-TTS` | champ `model` de `POST /tts` (voix wolof pour le wolof **et** le sérère, voix pulaar pour le pulaar) |
| `langue_asr`, `modele_asr` | `pu`, `AIHubSN/M-Kiriku-ASR` | pour le prochain `POST /asr` de ce commerçant |
| `etat` | `confirmer`, `proposer_mot`, `attente_complement`, `termine`... | où en est la conversation |
| `termine` | `true` / `false` | la conversation est finie ; le message suivant en ouvre une nouvelle |
| `issue` | `enregistre`, `question`, `pas_enregistre`, `abandon`, `incomprehension`, `pas_disponible`... | comment elle s'est terminée |
| `operation_id` | `12` | id de l'opération enregistrée (seulement si `issue = enregistre`) |
| `nouvelle_conversation` | `true` | ce message a ouvert une nouvelle conversation |
| `modele_reponse`, `langue_phrase` | `confirmer_vente_credit`, `wo` | quelle phrase a été dite ; `fr` si la traduction manque encore |

### Règles de conversation déjà gérées

- Une conversation par numéro, gardée entre deux messages dans `dossier/conversations/<numero>.json`.
- Conversation terminée -> le message suivant en ouvre une nouvelle.
- Plus de **30 minutes** sans message -> on repart de zéro (`Kaddu(..., expiration_minutes=30)`).
- Changement de langue -> nouvelle conversation.
- Deux messages du même numéro en même temps : traités l'un après l'autre (verrou par numéro, dans un processus).
- `kaddu.oublier(numero)` abandonne la conversation en cours (le carnet n'est pas touché).
- Pour retrouver le vocal d'un message dans la revue : `kaddu.traiter(numero, texte, langue, meta={"audio": "chemin/du/vocal.ogg"})`
  (chemin relatif à `nlp/audios/`).

### Ce que Kaddu sait faire

Vente, vente à crédit, remboursement (avec annonce du reste dû), entrée de stock, question sur la dette
d'un client ou sur toutes les dettes, solde global (ventes du jour + crédits), annulation de la dernière
opération. Les dépenses répondent « pas encore disponible ».

## 4. Brancher sur le bot WhatsApp

Exemple de route à ajouter dans `backend/main.py` (à adapter à ton fournisseur WhatsApp : Meta Cloud API,
Twilio...). La langue du commerçant vient de son profil (choisie à l'inscription).

```python
# backend/main.py — à ajouter de ton côté
import sys, subprocess, tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nlp"))
from kaddu import Kaddu

kaddu = Kaddu(Path(__file__).resolve().parent.parent / "nlp" / "data" / "whatsapp")

@app.post("/traiter")
def traiter(numero: str = Form(...), texte: str = Form(...), langue: str = Form("wo")):
    """Texte (déjà transcrit) -> réponse de Kaddu. Pratique pour tester sans WhatsApp."""
    return kaddu.traiter(numero, texte, langue)

def repondre_a_une_note_vocale(numero: str, audio: bytes, langue: str) -> bytes:
    """Note vocale reçue -> note vocale de réponse (OGG/Opus, format des notes vocales WhatsApp)."""
    samples, _ = librosa.load(io.BytesIO(audio), sr=ASR_SAMPLE_RATE, mono=True)   # WhatsApp envoie de l'OGG/Opus
    processor, modele = get_asr("AIHubSN/M-Kiriku-ASR")
    texte = transcribe(processor, modele, samples, {"wo": "wo", "ff": "pu", "srr": "se"}.get(langue, langue))
    r = kaddu.traiter(numero, texte, langue)
    wav = text_to_speech(text=r["texte_tts"], model=r["voix_tts"]).body      # ta route /tts, appelée directement
    with tempfile.NamedTemporaryFile(suffix=".wav") as e, tempfile.NamedTemporaryFile(suffix=".ogg") as s:
        e.write(wav); e.flush()
        subprocess.run(["ffmpeg", "-y", "-i", e.name, "-c:a", "libopus", "-b:a", "24k", s.name], check=True)
        return open(s.name, "rb").read()
```

Conseils :
- Envoie aussi `r["reponse"]` en texte sous la note vocale : le commerçant peut relire le montant.
- Si `librosa` ne lit pas l'OGG reçu, convertis-le d'abord en WAV avec ffmpeg (même commande, dans l'autre sens).
- Un seul processus serveur suffit pour le hackathon. Avec plusieurs processus, mets l'état des conversations
  en base (section 6) : le verrou par numéro ne vaut que dans un processus.

## 4 bis. Détection automatique de la langue (WhatsApp et Telegram)

Plus besoin que le commerçant choisisse sa langue : l'ASR Kiriku la détecte. M-Kiriku-ASR a trois jetons de langue
(`<|wo|>`, `<|pu|>`, `<|se|>`) ; `nlp/detection_langue.py` fait un seul pas du décodeur et compare leurs probabilités.

**À ajouter dans le backend**, là où une note vocale est transcrite (le modèle est déjà chargé pour l'ASR) :

```python
from detection_langue import detecter_langue_audio      # nlp/ est déjà dans le chemin (kaddu_bridge)

processor, model = get_asr("AIHubSN/M-Kiriku-ASR")
det = detecter_langue_audio(processor, model, samples)    # samples : audio 16 kHz mono (numpy)
langue = kaddu.langue_pour(numero, det)                   # règle de prudence, voir ci-dessous
texte = transcribe(processor, model, samples, {"wo": "wo", "ff": "pu", "srr": "se"}[langue])
r = kaddu.traiter(numero, texte, langue, detection=det)
```

- **Transcrire avec `langue` (la langue choisie), pas avec `det["langue"]`**, pour que la transcription et la
  conversation restent dans la même langue.
- **Message tapé au clavier** : `kaddu.traiter(numero, texte, "auto")` garde la langue de la conversation, ou la
  dernière langue utilisée par ce commerçant (mémorisée dans `dossier/langues.json`).
- **Commandes explicites** (« wolof », « pulaar », « seereer ») : continuer à appeler `traiter(numero, texte, "ff")`,
  elles priment sur la détection.

**Règle** (`detection_langue.choisir_langue`) : Kaddu suit la **langue dominante** renvoyée par l'ASR, y compris en
pleine conversation — le commerçant peut changer de langue quand il veut, et la conversation **continue** dans la
nouvelle langue sans perdre l'opération en cours (`r["changement_langue"] == True`). Exceptions, où l'on garde la
langue précédente :
- audio court (moins de 2 s, `DUREE_MIN_S`) : « waaw », « eey », « 2000 » sont trop courts pour trancher ;
- confiance de la langue dominante inférieure à 40 % (`CONFIANCE_MIN`).

Mesuré le 10/10/2026 sur 7 vrais audios wolof : bonne langue dans 7 cas sur 7, probabilité 0,47 à 0,62, marge 0,13 à
0,32. Pas encore mesuré sur du pulaar et du sérère : lancer
`python nlp/outils/essai_detection.py chemin/des/audios/*.wav` sur des vocaux dans ces langues avant de s'y fier.

## 5. Ranger les données

### Option A — fichiers JSON (par défaut, rien à installer)

```python
kaddu = Kaddu("nlp/data/whatsapp")             # stockage="json"
```

```
nlp/data/whatsapp/
  conversations/<numero>.json   conversation en cours (état complet, voir Conversation.vers_dict())
  commercants/<numero>.json     carnet du commerçant : {"operations": [ {...}, ... ]}
  sessions.csv                  une ligne par conversation terminée (métriques)
  conversations.jsonl           chaque conversation COMPLÈTE (messages, réponses de Kaddu, ce que le LLM a compris,
                                audios) : relue et annotée dans la page « Revue des conversations »
  termes_a_valider.db           mots inconnus, pour Ndèye
```

Écriture atomique (fichier temporaire puis remplacement) : pas de fichier à moitié écrit en cas de coupure.
`Kaddu(..., stockage="sqlite")` fait la même chose avec une base SQLite par commerçant.

### Option B — une vraie base de données

`Conversation` n'appelle que **5 méthodes** sur le carnet (voir `pipeline.Stockage`). Il suffit d'écrire
une classe qui les fournit et de la donner à `Kaddu` :

```python
kaddu = Kaddu("nlp/data/whatsapp", fabrique_stockage=lambda numero: BasePostgres(connexion, numero))
```

| Méthode | Reçoit | Renvoie |
|---|---|---|
| `enregistrer(operation)` | le dict ci-dessous | l'id de l'opération |
| `derniere()` | — | `{"id", "intention", "client", "montant_fcfa"}` de la dernière opération active, ou `None` |
| `annuler(operation_id)` | id | rien (passe `statut` à `annulee` ; on n'efface jamais) |
| `dette(client)` | nom ou `None` | somme des `vente_credit` − somme des `remboursement` actifs (tous clients si `None`) |
| `ventes_du_jour()` | — | somme des `vente` + `vente_credit` actives du jour |

Le dict `operation` (même format pour toutes les bases, fabriqué par `pipeline.operation_a_enregistrer`) :

```json
{
  "date": "2026-10-10T09:14:02", "langue": "wo", "intention": "vente_credit", "client": "Awa",
  "montant_fcfa": 3000, "montant_tel_que_dit": "juróom-benni téeméer", "controle_montant": "lecture_ok",
  "transcription": "Awa jënd na ñaari paket sukar ... | waaw", "modele": "google/gemini-3.5-flash-lite",
  "nb_corrections": 0, "statut": "active",
  "articles": [{"produit_dit": "sukar", "produit": "Sucre", "statut_produit": "reconnu",
                "quantite": 2, "unite": "paket", "prix_fcfa": null}]
}
```

`intention` ∈ `vente`, `vente_credit`, `remboursement`, `achat` (entrée de stock). `montant_fcfa` peut être
`null` pour une entrée de stock sans prix. `statut_produit` : `reconnu` (dans l'atlas), `reconnu_apres_question`
(le commerçant a accepté le mot proposé), `nouveau` (gardé tel qu'il a été dit, envoyé à Ndèye).

Schéma proposé (PostgreSQL ; adapte les types pour MySQL / SQLite) :

```sql
CREATE TABLE commercants (
  id SERIAL PRIMARY KEY, numero TEXT UNIQUE NOT NULL, langue TEXT NOT NULL DEFAULT 'wo',  -- wo | ff | srr
  nom TEXT, cree_le TIMESTAMP DEFAULT now());

CREATE TABLE operations (
  id SERIAL PRIMARY KEY, commercant_id INT NOT NULL REFERENCES commercants(id),
  date TIMESTAMP NOT NULL, langue TEXT, intention TEXT NOT NULL, client TEXT,
  montant_fcfa INT, montant_tel_que_dit TEXT, controle_montant TEXT, transcription TEXT,
  modele TEXT, nb_corrections INT DEFAULT 0, statut TEXT NOT NULL DEFAULT 'active');   -- active | annulee
CREATE INDEX ON operations (commercant_id, statut, intention);

CREATE TABLE articles (
  id SERIAL PRIMARY KEY, operation_id INT NOT NULL REFERENCES operations(id),
  produit_dit TEXT, produit TEXT, statut_produit TEXT, quantite REAL, unite TEXT, prix_fcfa INT);

CREATE TABLE conversations (                       -- état des conversations en cours (option)
  numero TEXT PRIMARY KEY, etat JSONB NOT NULL, maj TIMESTAMP DEFAULT now());

CREATE TABLE termes_a_valider (
  id SERIAL PRIMARY KEY, terme TEXT, langue TEXT, categorie TEXT, exemple_phrase TEXT, sens_propose TEXT,
  occurrences INT DEFAULT 1, statut TEXT DEFAULT 'en_attente', forme_retenue TEXT,
  UNIQUE (terme, langue, categorie));
```

Squelette de la classe (psycopg 3) :

```python
class BasePostgres:
    def __init__(self, cx, numero):
        self.cx = cx
        self.cid = cx.execute("INSERT INTO commercants (numero) VALUES (%s) ON CONFLICT (numero) DO UPDATE "
                              "SET numero = EXCLUDED.numero RETURNING id", (numero,)).fetchone()[0]

    def enregistrer(self, op):
        oid = self.cx.execute("""INSERT INTO operations (commercant_id, date, langue, intention, client, montant_fcfa,
            montant_tel_que_dit, controle_montant, transcription, modele, nb_corrections)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (self.cid, op["date"], op["langue"], op["intention"], op["client"], op["montant_fcfa"],
             op["montant_tel_que_dit"], op["controle_montant"], op["transcription"], op["modele"],
             op["nb_corrections"])).fetchone()[0]
        for a in op["articles"]:
            self.cx.execute("""INSERT INTO articles (operation_id, produit_dit, produit, statut_produit, quantite,
                unite, prix_fcfa) VALUES (%s,%s,%s,%s,%s,%s,%s)""", (oid, a["produit_dit"], a["produit"],
                a["statut_produit"], a["quantite"], a["unite"], a["prix_fcfa"]))
        self.cx.commit()
        return oid

    def derniere(self):
        r = self.cx.execute("""SELECT id, intention, client, montant_fcfa FROM operations WHERE commercant_id = %s
            AND statut = 'active' ORDER BY id DESC LIMIT 1""", (self.cid,)).fetchone()
        return dict(zip(("id", "intention", "client", "montant_fcfa"), r)) if r else None

    def annuler(self, oid):
        self.cx.execute("UPDATE operations SET statut = 'annulee' WHERE id = %s AND commercant_id = %s", (oid, self.cid))
        self.cx.commit()

    def dette(self, client):
        r = self.cx.execute("""SELECT COALESCE(SUM(CASE WHEN intention = 'vente_credit' THEN montant_fcfa
            WHEN intention = 'remboursement' THEN -montant_fcfa END), 0) FROM operations
            WHERE commercant_id = %s AND statut = 'active' AND (%s::text IS NULL OR lower(client) = lower(%s))""",
            (self.cid, client, client)).fetchone()
        return int(r[0])

    def ventes_du_jour(self):
        r = self.cx.execute("""SELECT COALESCE(SUM(montant_fcfa), 0) FROM operations WHERE commercant_id = %s
            AND statut = 'active' AND intention IN ('vente', 'vente_credit') AND date::date = CURRENT_DATE""",
            (self.cid,)).fetchone()
        return int(r[0])
```

## 6. Garder l'état des conversations en base (plusieurs serveurs)

L'état d'une conversation est un dict JSON (`conv.vers_dict()`), qu'on recharge avec
`Conversation.depuis_dict(etat, carnet, file_termes, sessions)`. Avec la table `conversations` :

```python
from pipeline import Conversation
etat = lire_etat_en_base(numero)                     # SELECT etat FROM conversations WHERE numero = ...
conv = Conversation.depuis_dict(etat, carnet, file_termes, sessions) if etat else Conversation(modele, carnet, langue)
reponse = conv.recevoir(texte)
ecrire_etat_en_base(numero, conv.vers_dict())        # INSERT ... ON CONFLICT (numero) DO UPDATE
```

Pense à verrouiller la ligne pendant le traitement (`SELECT ... FOR UPDATE`) pour deux messages simultanés.

## 7. Mots à valider (pour Ndèye)

Les mots que l'atlas ne connaît pas ne bloquent jamais une opération : ils sont écrits tels quels et
rangés dans `termes_a_valider.db` (mot, langue, catégorie, phrase d'exemple, sens proposé par le LLM,
nombre de fois). La page « Métriques et données » de l'interface les affiche.

## 8. Contrat (`contracts/models.py`) : changements à décider ensemble

L'`ExtractionResponse` actuelle n'a qu'un seul produit. La couche produit déjà :
- une **liste d'articles** (mot dit, produit de l'atlas, statut, quantité, unité, prix) ;
- `champs_incertains` et `type_reponse` (confirme / refuse / corrige) ;
- `montant_fcfa` = total **calculé par le code**.

Proposition : aligner le contrat sur le dict `operation` de la section 5, sur une branche `contrat/...`.

## 9. À ne jamais committer

`.env` (clés), `nlp/data/whatsapp/`, `nlp/data/equipe/`, `nlp/data/*.db`, `nlp/data/sessions_*.csv`,
`nlp/audios/` (déjà couverts par `.gitignore`).
