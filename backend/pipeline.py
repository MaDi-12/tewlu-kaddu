"""Orchestration voix -> voix du pipeline WhatsApp — TEWLU Kaddu.

Responsable : Khalifa (backend/).

C'est la tâche du planning (3-5 oct.) : "Intégrer l'ASR et le TTS Kiriku dans
le pipeline WhatsApp (note vocale -> transcription -> réponse vocale)".

Flux (docs/Dossier-projet.md §3 et architecture technique §5) :
    webhook -> media WhatsApp -> ASR Kiriku -> compréhension -> TTS Kiriku -> WhatsApp

Compréhension : deux moteurs, choisis par backend.kaddu_bridge.moteur_actif() :
  - "kaddu"  : couche de Marème (nlp/kaddu.py) — LLM + atlas, conversation et
               confirmation gérées par Kaddu, écritures dans backend.db via
               backend.carnet. C'est le moteur visé pour la démo ;
  - "regles" : nlp.extractor + la machine à états ci-dessous (_traiter_texte),
               repli quand aucune clé LLM n'est configurée.

Règle non négociable du dossier : "aucune donnée n'est enregistrée sans le
waaw du commerçant". Toute intention qui affecte l'argent (vente, crédit,
remboursement, achat, dépense) passe systématiquement par une confirmation
mise en attente (backend.db.pending_extractions) avant d'être écrite.

Dépendance connue sur nlp/ (hors scope de ce module, propriété de Marème) :
nlp.extractor.extract_information ne renseigne JAMAIS le champ `client`
aujourd'hui (voir nlp/extractor.py, "Valeurs par défaut" : `client = None`
n'est réassigné nulle part). Tant que ce n'est pas fait côté NLU, les
confirmations de vente à crédit / remboursement n'auront jamais de nom de
client, même si ce module est prêt à l'exploiter dès que le champ existera.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Iterator

from backend import audio_utils, db, kaddu_bridge, kiriku_models, telegram_client, templates, whatsapp_client
from backend.kaddu_bridge import Reponse
from contracts.models import ExtractionResponse, Langue
from nlp.extractor import extract_information

logger = logging.getLogger("tewlu.pipeline")

# Intentions qui affectent l'argent du commerçant : jamais enregistrées sans "waaw".
INTENTIONS_A_CONFIRMER = {"vente", "vente_credit", "remboursement", "achat", "depense"}

# Champs obligatoires par intention (docs/api/contrat-v1.md §2) : recopiés ici car
# c'est le contrat documenté, pas un détail d'implémentation de nlp/extractor.py
# (que backend/ ne doit pas inspecter pour rester isolé, cf. CONTRIBUTING.md).
CHAMPS_OBLIGATOIRES: dict[str, list[str]] = {
    "vente": ["montant"],
    "vente_credit": ["client", "montant"],
    "remboursement": ["client", "montant"],
    "achat": ["montant"],
    "depense": ["montant"],
    "question_dette": ["client"],
    "question_bilan": [],
    "annulation": [],
    "incomprehension": [],
}

# Token de langue attendu par M-Kiriku-ASR (<|wo|>, <|pu|>, <|se|>) pour chaque
# code langue du contrat (wo, ff, srr) : les deux nomenclatures diffèrent.
LANGUE_CONTRAT_VERS_TOKEN_ASR = {"wo": "wo", "ff": "pu", "srr": "se"}
ASR_MODEL_ID = "AIHubSN/M-Kiriku-ASR"

# Modèle TTS à utiliser pour la langue de VOIX retenue (après repli éventuel,
# voir backend.templates.LANGUE_VOIX). Pas d'entrée "srr" : le dossier ne
# prévoit aucun TTS sérère, donc LANGUE_VOIX ne produit jamais "srr" ici.
TTS_MODEL_PAR_LANGUE_VOIX = {
    "wo": "AIHubSN/Kiriku-Wolof-TTS",
    "ff": "AIHubSN/Kiriku-Pulaar-TTS",
}


def iter_incoming_messages(payload: dict) -> Iterator[tuple[dict, str]]:
    """Parcourt un payload de webhook WhatsApp et extrait (message, wa_id_commerçant).

    Ignore silencieusement les accusés de statut (livré/lu) et toute entrée
    malformée : un webhook Meta peut regrouper plusieurs changements par appel.
    """
    for entree in payload.get("entry", []):
        for changement in entree.get("changes", []):
            valeur = changement.get("value", {})
            for message in valeur.get("messages", []):
                expediteur = message.get("from")
                if expediteur:
                    yield message, expediteur


# Meta peut renvoyer le même webhook (réseau lent, redémarrage) : sans ce filtre,
# un "waaw" rejoué enregistrerait deux fois la même vente.
_MESSAGES_VUS: OrderedDict[str, None] = OrderedDict()
_MESSAGES_VUS_MAX = 2000


def deja_recu(message: dict) -> bool:
    """True si ce message WhatsApp (champ `id`) a déjà été pris en charge."""
    message_id = message.get("id")
    if not message_id:
        return False
    if message_id in _MESSAGES_VUS:
        return True
    _MESSAGES_VUS[message_id] = None
    if len(_MESSAGES_VUS) > _MESSAGES_VUS_MAX:
        _MESSAGES_VUS.popitem(last=False)
    return False


def _fusionner_extraction(pending: ExtractionResponse, nouvelle: ExtractionResponse) -> ExtractionResponse:
    """Comble les champs manquants de `pending` avec ceux non nuls de `nouvelle`.

    Utilisé quand le commerçant répond à une question de clarification
    ("Ñaata la ?") par une nouvelle note vocale plutôt que par "waaw"/"déet" :
    on garde l'intention et les champs déjà connus, on complète le reste.
    """
    donnees = pending.donnees.model_copy()
    for champ in ("client", "produit", "quantite", "unite", "montant"):
        if getattr(donnees, champ) is None:
            valeur_nouvelle = getattr(nouvelle.donnees, champ)
            if valeur_nouvelle is not None:
                setattr(donnees, champ, valeur_nouvelle)

    requis = CHAMPS_OBLIGATOIRES.get(pending.intention, [])
    manquants = [c for c in requis if getattr(donnees, c) is None]

    return ExtractionResponse(
        intention=pending.intention,
        donnees=donnees,
        langue=pending.langue,
        methode=pending.methode,
        confiance=min(pending.confiance, nouvelle.confiance),
        est_complet=not manquants,
        champs_manquants=manquants,
        question_clarification=(nouvelle.question_clarification or pending.question_clarification) if manquants else None,
    )


def canal_pour(vendeur_id: str):
    """Module d'envoi/réception du commerçant : Telegram ("tg-<chat_id>") ou WhatsApp.

    Les deux modules exposent telecharger_media, envoyer_texte et envoyer_audio.
    """
    return telegram_client if telegram_client.est_telegram(vendeur_id) else whatsapp_client


def modele_tts_regles(langue: Langue) -> str:
    """Voix du moteur à règles (gabarits wolof, cf. templates.LANGUE_VOIX)."""
    langue_voix = templates.LANGUE_VOIX.get(langue, "wo")
    return TTS_MODEL_PAR_LANGUE_VOIX.get(langue_voix, TTS_MODEL_PAR_LANGUE_VOIX["wo"])


async def repondre(
    vendeur_wa_id: str,
    texte: str,
    langue: Langue,
    texte_tts: str | None = None,
    modele_tts: str | None = None,
) -> None:
    """Répond en note vocale (TTS Kiriku -> OGG/Opus), puis toujours en texte.

    Le texte part même quand la voix a marché : le commerçant peut relire le
    montant (conseil de docs/integration-kaddu.md §4). Si la voix échoue
    (HF_TOKEN absent, modèle indisponible, panne), le texte reste : jamais de
    silence — cf. §7 du dossier.
    """
    try:
        wav = await asyncio.to_thread(
            kiriku_models.synthesize, texte_tts or texte, modele_tts or modele_tts_regles(langue)
        )
        ogg = await asyncio.to_thread(audio_utils.wav_vers_ogg_opus, wav)
        await canal_pour(vendeur_wa_id).envoyer_audio(vendeur_wa_id, ogg)
    except Exception:
        logger.exception("Synthèse ou envoi vocal impossible pour %s, réponse en texte seulement", vendeur_wa_id)
    await canal_pour(vendeur_wa_id).envoyer_texte(vendeur_wa_id, texte)


def transcrire(audio: bytes, langue: Langue) -> str:
    """Audio (OGG/Opus WhatsApp, WAV...) -> texte, avec le token de langue M-Kiriku-ASR."""
    audio_wav = audio_utils.ogg_vers_wav(audio)
    token_langue = LANGUE_CONTRAT_VERS_TOKEN_ASR.get(langue, "wo")
    return kiriku_models.recognize_wav_bytes(audio_wav, ASR_MODEL_ID, token_langue)


async def _transcrire_audio(message: dict, vendeur_wa_id: str, langue: Langue) -> str:
    audio_ogg = await canal_pour(vendeur_wa_id).telecharger_media(message["audio"]["id"])
    return await asyncio.to_thread(transcrire, audio_ogg, langue)


def comprendre(vendeur_wa_id: str, texte: str, langue: Langue) -> Reponse:
    """Texte transcrit -> réponse à envoyer, avec le moteur actif (bloquant : à appeler hors boucle)."""
    if kaddu_bridge.moteur_actif() == "kaddu":
        return kaddu_bridge.traiter(vendeur_wa_id, texte, langue)
    with db.session() as conn:
        db.get_or_create_vendeur(conn, vendeur_wa_id)
        phrase = _traiter_texte(conn, vendeur_wa_id, texte, langue)
    return Reponse(texte=phrase, texte_tts=phrase, modele_tts=modele_tts_regles(langue), moteur="regles")


def _traiter_texte(conn, vendeur_wa_id: str, texte: str, langue: Langue) -> str:
    """Logique pure (pas d'E/S réseau) : texte transcrit -> phrase de réponse à prononcer.

    Séparée de `handle_incoming_message` pour être testable sans mocker
    WhatsApp/Kiriku à chaque assertion sur la machine à états.
    """
    pending = db.get_pending(conn, vendeur_wa_id)

    if pending is not None:
        confirmation = templates.detecter_confirmation(texte)
        if confirmation is True:
            resultat = db.enregistrer_operation(conn, vendeur_wa_id, pending)
            db.clear_pending(conn, vendeur_wa_id)
            return templates.phrase_ack(pending, resultat.nouveau_solde)
        if confirmation is False:
            db.clear_pending(conn, vendeur_wa_id)
            return templates.phrase_refus_pending(langue)

        nouvelle = extract_information(texte, langue=langue, contexte_client_id=vendeur_wa_id)
        fusionnee = _fusionner_extraction(pending, nouvelle)
        db.save_pending(conn, vendeur_wa_id, fusionnee)
        return templates.phrase_confirmation(fusionnee)

    extraction = extract_information(texte, langue=langue, contexte_client_id=vendeur_wa_id)

    if extraction.intention == "question_dette":
        client = extraction.donnees.client
        if not client:
            return templates.phrase_incomprehension(langue)
        solde = db.solde_client(conn, vendeur_wa_id, client)
        return templates.phrase_question_dette(langue, client, solde)

    if extraction.intention == "question_bilan":
        bilan = db.bilan_jour(conn, vendeur_wa_id)
        return templates.phrase_bilan(langue, bilan)

    if extraction.intention == "annulation":
        annulee = db.annuler_derniere_operation(conn, vendeur_wa_id)
        return templates.phrase_annulation(langue, annulee)

    if extraction.intention == "incomprehension":
        return templates.phrase_incomprehension(langue)

    # vente, vente_credit, remboursement, achat, depense : jamais enregistrées
    # directement, cf. module docstring.
    assert extraction.intention in INTENTIONS_A_CONFIRMER
    db.save_pending(conn, vendeur_wa_id, extraction)
    return templates.phrase_confirmation(extraction)


async def handle_incoming_message(message: dict, vendeur_wa_id: str) -> None:
    """Point d'entrée pour chaque message reçu (webhook WhatsApp ou polling Telegram)."""
    canal = canal_pour(vendeur_wa_id)
    try:
        with db.session() as conn:
            langue = db.get_or_create_vendeur(conn, vendeur_wa_id)

        type_message = message.get("type")
        if type_message == "audio":
            texte = await _transcrire_audio(message, vendeur_wa_id, langue)
        elif type_message == "text":
            texte = message.get("text", {}).get("body", "")
        else:
            await canal.envoyer_texte(
                vendeur_wa_id,
                "Waxal ko ci baat walla ci yoon (note vocale ou texte).",
            )
            return

        texte = texte.strip()
        if not texte:
            await repondre(vendeur_wa_id, templates.phrase_incomprehension(langue), langue)
            return

        nouvelle_langue = templates.detecter_langue_demandee(texte)
        if nouvelle_langue:
            with db.session() as conn:
                db.set_langue_preferee(conn, vendeur_wa_id, nouvelle_langue)
            await canal.envoyer_texte(vendeur_wa_id, templates.phrase_langue_choisie(nouvelle_langue))
            return

        # Les écritures en base sont commitées dans `comprendre`, avant tout envoi
        # réseau : un échec d'envoi ne rejoue jamais une opération déjà enregistrée.
        reponse = await asyncio.to_thread(comprendre, vendeur_wa_id, texte, langue)
        await repondre(vendeur_wa_id, reponse.texte, langue, reponse.texte_tts, reponse.modele_tts)
    except Exception:
        logger.exception("Échec du traitement du message WhatsApp de %s", vendeur_wa_id)
        try:
            # Le commerçant ne doit jamais recevoir un silence pur : même en cas de panne
            # (ASR, téléchargement média, base de données...), on tente un message d'erreur.
            await canal.envoyer_texte(
                vendeur_wa_id, "Am na njàngat. Waxaat ko ci yoon wu bees (erreur technique, réessayez)."
            )
        except Exception:
            logger.exception("Impossible d'envoyer même le message d'erreur à %s", vendeur_wa_id)
