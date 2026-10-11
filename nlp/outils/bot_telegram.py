"""Bot Telegram de TEWLU Kaddu (version autonome, pour les tests des organisateurs).

Le commerçant envoie une note vocale ou un message écrit ; le bot transcrit le vocal avec l'ASR Kiriku
(API des modèles, backend sur le port 8000), passe le texte à Kaddu, puis répond en texte ET en note vocale
(TTS Kiriku). Aucune adresse publique n'est nécessaire : le bot interroge Telegram (long polling).

Prérequis
    1. Créer un bot avec @BotFather sur Telegram (/newbot) et copier le jeton.
    2. Ajouter dans le fichier .env à la racine du projet :   TELEGRAM_TOKEN=123456:ABC...
    3. Lancer l'API des modèles :   cd backend && uvicorn main:app --port 8000
    4. ffmpeg installé (conversion des notes vocales).

Lancement (depuis la racine du dépôt) :
    python nlp/outils/bot_telegram.py

Commandes dans Telegram : /start, /wolof, /pulaar, /seereer, /aide. Les mots « wolof », « pulaar », « seereer »
envoyés seuls changent aussi la langue.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import requests

NLP = Path(__file__).resolve().parent.parent
RACINE = NLP.parent
sys.path.insert(0, str(NLP))
sys.path.insert(0, str(NLP / "outils"))

from kaddu import Kaddu  # noqa: E402
from pipeline import ASR_MODELE, LANGUES  # noqa: E402

API_MODELES = os.environ.get("KADDU_API", "http://localhost:8000")
DOSSIER = NLP / "data" / "telegram"
FICHIER_LANGUES = DOSSIER / "langues_telegram.json"
COMMANDES_LANGUE = {"wolof": "wo", "pulaar": "ff", "peul": "ff", "seereer": "srr", "serer": "srr", "sérère": "srr"}
CLAVIER = {"keyboard": [[{"text": "Wolof"}, {"text": "Pulaar"}, {"text": "Seereer"}]],
           "resize_keyboard": True, "is_persistent": True}
AIDE = ("Bienvenue sur TEWLU Kaddu, votre carnet de compte à la voix.\n\n"
        "1. Choisissez votre langue avec les boutons : Wolof, Pulaar ou Seereer.\n"
        "2. Envoyez une note vocale : une vente, une vente à crédit, un remboursement, une entrée de stock, "
        "ou une question (« combien Awa me doit ? »).\n"
        "3. Kaddu répète ce qu'il a compris : répondez « waaw » / « eey » / « iyo » (oui) ou « déet » / "
        "« alaa » (non), ou corrigez.\n\n"
        "Vous pouvez aussi écrire vos messages.")


def lire_env() -> None:
    """Charge le .env de la racine (TELEGRAM_TOKEN, OPENROUTER_API_KEY) sans dépendance supplémentaire."""
    fichier = RACINE / ".env"
    if fichier.exists():
        for ligne in fichier.read_text(encoding="utf-8").splitlines():
            if "=" in ligne and not ligne.strip().startswith("#"):
                cle, valeur = ligne.split("=", 1)
                os.environ.setdefault(cle.strip(), valeur.strip().strip('"').strip("'"))


class Telegram:
    def __init__(self, jeton: str):
        self.api = f"https://api.telegram.org/bot{jeton}"
        self.fichiers = f"https://api.telegram.org/file/bot{jeton}"

    def appel(self, methode: str, **params):
        r = requests.post(f"{self.api}/{methode}", timeout=70, **params)
        r.raise_for_status()
        return r.json()["result"]

    def messages(self, decalage: int | None):
        return self.appel("getUpdates", data={"timeout": 50, "offset": decalage or ""})

    def ecrire(self, chat: int, texte: str, clavier: bool = False) -> None:
        donnees = {"chat_id": chat, "text": texte}
        if clavier:
            donnees["reply_markup"] = json.dumps(CLAVIER)
        self.appel("sendMessage", data=donnees)

    def en_train(self, chat: int, action: str = "typing") -> None:
        try:
            self.appel("sendChatAction", data={"chat_id": chat, "action": action})
        except requests.RequestException:
            pass

    def telecharger(self, file_id: str) -> bytes:
        chemin = self.appel("getFile", data={"file_id": file_id})["file_path"]
        r = requests.get(f"{self.fichiers}/{chemin}", timeout=60)
        r.raise_for_status()
        return r.content

    def vocal(self, chat: int, ogg: bytes) -> None:
        self.appel("sendVoice", data={"chat_id": chat}, files={"voice": ("kaddu.ogg", ogg, "audio/ogg")})


def ffmpeg(entree: bytes, suffixe_entree: str, arguments: list[str], suffixe_sortie: str) -> bytes:
    """Conversion audio avec ffmpeg (ogg/opus de Telegram -> wav 16 kHz, et wav du TTS -> ogg/opus)."""
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / f"in{suffixe_entree}", Path(d) / f"out{suffixe_sortie}"
        src.write_bytes(entree)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), *arguments, str(dst)], check=True)
        return dst.read_bytes()


def transcrire(wav: bytes, langue: str) -> str:
    r = requests.post(f"{API_MODELES}/asr", files={"audio": ("vocal.wav", wav, "audio/wav")},
                      data={"model": ASR_MODELE, "language": LANGUES[langue]["asr"]}, timeout=600)
    r.raise_for_status()
    texte = r.json().get("text", "")
    for jeton in ("<|wo|>", "<|pu|>", "<|se|>"):
        texte = texte.replace(jeton, "")
    return texte.strip()


def synthese(texte_tts: str, voix: str) -> bytes:
    r = requests.post(f"{API_MODELES}/tts", data={"text": texte_tts, "model": voix}, timeout=600)
    r.raise_for_status()
    return r.content


def lire_langues() -> dict:
    try:
        return json.loads(FICHIER_LANGUES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ecrire_langues(langues: dict) -> None:
    FICHIER_LANGUES.write_text(json.dumps(langues, ensure_ascii=False, indent=1), encoding="utf-8")


def repondre(tg: Telegram, kaddu: Kaddu, chat: int, texte: str, langue: str) -> None:
    r = kaddu.traiter(f"tg{chat}", texte, langue, source="telegram")
    tg.ecrire(chat, r["reponse"])
    tg.en_train(chat, "record_voice")
    try:
        tg.vocal(chat, ffmpeg(synthese(r["texte_tts"], r["voix_tts"]), ".wav",
                              ["-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1"], ".ogg"))
    except Exception as e:                                       # le texte est déjà parti : on ne bloque pas
        print(f"[tts] {e}")


def traiter_message(tg: Telegram, kaddu: Kaddu, langues: dict, msg: dict) -> None:
    chat = msg["chat"]["id"]
    langue = langues.get(str(chat), "wo")
    texte = (msg.get("text") or "").strip()

    if texte.startswith("/start") or texte.startswith("/aide"):
        tg.ecrire(chat, AIDE, clavier=True)
        return
    mot = texte.lstrip("/").strip().lower()
    if mot in COMMANDES_LANGUE:
        langues[str(chat)] = COMMANDES_LANGUE[mot]
        ecrire_langues(langues)
        nom = LANGUES[COMMANDES_LANGUE[mot]]["nom"]
        suite = " Kaddu vous répondra en wolof." if COMMANDES_LANGUE[mot] == "srr" else ""
        tg.ecrire(chat, f"Langue : {nom}.{suite} Envoyez votre note vocale.", clavier=True)
        return

    son = msg.get("voice") or msg.get("audio")
    if son:
        tg.en_train(chat)
        wav = ffmpeg(tg.telecharger(son["file_id"]), ".ogg", ["-ar", "16000", "-ac", "1"], ".wav")
        texte = transcrire(wav, langue)
        if not texte:
            tg.ecrire(chat, "Je n'ai rien entendu. Rapprochez-vous du micro et réessayez.")
            return
        tg.ecrire(chat, f"🎙️ {texte}")
    if texte:
        tg.en_train(chat)
        repondre(tg, kaddu, chat, texte, langue)


def main() -> None:
    lire_env()
    jeton = os.environ.get("TELEGRAM_TOKEN")
    if not jeton:
        sys.exit("TELEGRAM_TOKEN manquant : ajoutez TELEGRAM_TOKEN=... dans le fichier .env à la racine du projet.")
    try:
        requests.get(f"{API_MODELES}/models", timeout=5).raise_for_status()
    except requests.RequestException:
        sys.exit(f"L'API des modèles ne répond pas sur {API_MODELES} : lancez  cd backend && uvicorn main:app --port 8000")
    DOSSIER.mkdir(parents=True, exist_ok=True)
    tg, kaddu, langues = Telegram(jeton), Kaddu(DOSSIER), lire_langues()
    moi = tg.appel("getMe")
    print(f"Bot @{moi['username']} prêt. Ctrl+C pour arrêter.")
    decalage = None
    while True:
        try:
            for maj in tg.messages(decalage):
                decalage = maj["update_id"] + 1
                msg = maj.get("message")
                if not msg:
                    continue
                try:
                    traiter_message(tg, kaddu, langues, msg)
                except Exception:
                    traceback.print_exc()
                    tg.ecrire(msg["chat"]["id"], "Désolé, un problème technique est survenu. Réessayez dans un instant.")
        except requests.RequestException as e:
            print(f"[réseau] {e} ; nouvel essai dans 5 s")
            time.sleep(5)
        except KeyboardInterrupt:
            print("Arrêt du bot.")
            break


if __name__ == "__main__":
    main()
