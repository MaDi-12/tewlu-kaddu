import pytest

from backend import db, pipeline
from contracts.models import DonneesExtraction, ExtractionResponse

VENDEUR = "221770000000"


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "test.db")
    db.init_db(path)
    connection = db.get_connection(path)
    yield connection
    connection.close()


def _extraction(intention, langue="wo", est_complet=True, question_clarification=None, **donnees):
    requis_defaut = {"client": None, "produit": None, "quantite": None, "unite": None, "montant": None}
    requis_defaut.update(donnees)
    return ExtractionResponse(
        intention=intention,
        donnees=DonneesExtraction(**requis_defaut),
        langue=langue,
        methode="regles",
        confiance=0.9,
        est_complet=est_complet,
        champs_manquants=[] if est_complet else ["montant"],
        question_clarification=question_clarification,
    )


# ---------------------------------------------------------------------------
# iter_incoming_messages : parsing du payload webhook
# ---------------------------------------------------------------------------

def test_iter_incoming_messages_extrait_expediteur_et_message():
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{"from": "221770000000", "type": "text", "text": {"body": "salut"}}]
                }
            }]
        }]
    }
    resultats = list(pipeline.iter_incoming_messages(payload))
    assert resultats == [({"from": "221770000000", "type": "text", "text": {"body": "salut"}}, "221770000000")]


def test_iter_incoming_messages_ignore_les_accuses_de_statut():
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}
    assert list(pipeline.iter_incoming_messages(payload)) == []


# ---------------------------------------------------------------------------
# _traiter_texte : machine à états de confirmation (coeur du module)
# ---------------------------------------------------------------------------

def test_operation_sans_client_part_direct_en_attente_de_confirmation(conn, monkeypatch):
    monkeypatch.setattr(
        pipeline, "extract_information",
        lambda texte, langue, contexte_client_id: _extraction("vente", montant=1500),
    )
    reponse = pipeline._traiter_texte(conn, VENDEUR, "j'ai vendu 2 pots de lait, 1500", "wo")

    assert reponse == "1500 F. Waaw walla déet ?"
    assert db.get_pending(conn, VENDEUR) is not None


def test_waaw_enregistre_effectivement_loperation_en_attente(conn, monkeypatch):
    monkeypatch.setattr(
        pipeline, "extract_information",
        lambda texte, langue, contexte_client_id: _extraction("vente_credit", client="Awa", montant=3000),
    )
    pipeline._traiter_texte(conn, VENDEUR, "Awa a pris du sucre à crédit, 3000", "wo")

    reponse = pipeline._traiter_texte(conn, VENDEUR, "waaw", "wo")

    assert "3000" in reponse
    assert db.get_pending(conn, VENDEUR) is None
    assert db.solde_client(conn, VENDEUR, "Awa") == 3000


def test_deet_abandonne_sans_rien_enregistrer(conn, monkeypatch):
    monkeypatch.setattr(
        pipeline, "extract_information",
        lambda texte, langue, contexte_client_id: _extraction("vente_credit", client="Awa", montant=3000),
    )
    pipeline._traiter_texte(conn, VENDEUR, "Awa a pris du sucre à crédit, 3000", "wo")

    reponse = pipeline._traiter_texte(conn, VENDEUR, "déet", "wo")

    assert db.get_pending(conn, VENDEUR) is None
    assert db.solde_client(conn, VENDEUR, "Awa") is None  # jamais créé


def test_reponse_ambigue_relance_sans_toucher_a_la_base(conn, monkeypatch):
    monkeypatch.setattr(
        pipeline, "extract_information",
        lambda texte, langue, contexte_client_id: _extraction("vente", montant=1500),
    )
    pipeline._traiter_texte(conn, VENDEUR, "j'ai vendu du sucre, 1500", "wo")

    # La deuxième note vocale ne ressemble ni à "waaw" ni à "déet" ni à une correction utile :
    # on reste en attente, l'opération initiale n'est toujours pas enregistrée.
    reponse = pipeline._traiter_texte(conn, VENDEUR, "une phrase hors sujet", "wo")

    assert db.get_pending(conn, VENDEUR) is not None


def test_clarification_complete_loperation_puis_redemande_confirmation(conn, monkeypatch):
    appels = [
        _extraction("vente_credit", client=None, montant=None, est_complet=False, question_clarification="Kan la ?"),
        _extraction("incomprehension", client="Awa"),
    ]

    def fausse_extraction(texte, langue, contexte_client_id):
        return appels.pop(0)

    monkeypatch.setattr(pipeline, "extract_information", fausse_extraction)

    premiere = pipeline._traiter_texte(conn, VENDEUR, "quelqu'un a pris à crédit", "wo")
    assert premiere == "Kan la ?"

    deuxieme = pipeline._traiter_texte(conn, VENDEUR, "Awa", "wo")
    # Le champ client est maintenant comblé mais le montant manque toujours :
    # la question de clarification reste affichée (celle fournie par nlp au tour courant).
    assert deuxieme == "Kan la ?"


def test_question_dette_client_connu(conn, monkeypatch):
    db.get_or_create_client(conn, VENDEUR, "Awa")
    monkeypatch.setattr(pipeline, "extract_information",
                         lambda texte, langue, contexte_client_id: _extraction("question_dette", client="Awa"))

    reponse = pipeline._traiter_texte(conn, VENDEUR, "Combien Awa me doit ?", "wo")
    assert reponse == "Awa amul benn bor."


def test_annulation_restaure_le_solde_du_client(conn, monkeypatch):
    monkeypatch.setattr(
        pipeline, "extract_information",
        lambda texte, langue, contexte_client_id: _extraction("vente_credit", client="Awa", montant=3000),
    )
    pipeline._traiter_texte(conn, VENDEUR, "Awa a pris à crédit, 3000", "wo")
    pipeline._traiter_texte(conn, VENDEUR, "waaw", "wo")
    assert db.solde_client(conn, VENDEUR, "Awa") == 3000

    monkeypatch.setattr(pipeline, "extract_information",
                         lambda texte, langue, contexte_client_id: _extraction("annulation"))
    reponse = pipeline._traiter_texte(conn, VENDEUR, "annule la dernière opération", "wo")

    assert db.solde_client(conn, VENDEUR, "Awa") == 0
    assert reponse == "Baax na, far naa ko."


def test_nlp_actuel_ne_renseigne_jamais_le_client_limite_connue(conn):
    """Documente une limite connue et hors scope de backend/ (voir docstring de pipeline.py) :
    nlp.extractor.extract_information ne renseigne jamais `donnees.client`, même quand le
    texte contient clairement un nom. Ce test échouera (en bien !) le jour où Marème le corrige,
    ce qui est le signal attendu pour l'enlever."""
    reponse = pipeline._traiter_texte(conn, VENDEUR, "Awa jënd na sukar, ñetti junni, dafa ko bor", "wo")
    pending = db.get_pending(conn, VENDEUR)

    assert pending.donnees.client is None
    assert "client" in pending.champs_manquants
    assert reponse == pending.question_clarification


# ---------------------------------------------------------------------------
# handle_incoming_message : orchestration complète (E/S mockées)
# ---------------------------------------------------------------------------

class _Espion:
    """Capture les appels faits aux clients externes (Kiriku, WhatsApp) sans réseau."""

    def __init__(self):
        self.audios_envoyes = []
        self.textes_envoyes = []

    async def envoyer_audio(self, wa_id, wav):
        self.audios_envoyes.append((wa_id, wav))

    async def envoyer_texte(self, wa_id, texte):
        self.textes_envoyes.append((wa_id, texte))


@pytest.fixture(autouse=True)
def base_de_donnees_par_defaut(tmp_path, monkeypatch):
    """handle_incoming_message ouvre ses propres connexions via db.session() / db.DB_PATH."""
    path = str(tmp_path / "handle.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db(path)


@pytest.mark.anyio
async def test_message_texte_bout_en_bout_envoie_une_note_vocale(monkeypatch):
    espion = _Espion()
    monkeypatch.setattr(pipeline.whatsapp_client, "envoyer_audio", espion.envoyer_audio)
    monkeypatch.setattr(pipeline.whatsapp_client, "envoyer_texte", espion.envoyer_texte)
    monkeypatch.setattr(pipeline.kiriku_models, "synthesize", lambda texte, modele: b"faux-wav")
    monkeypatch.setattr(pipeline.audio_utils, "wav_vers_ogg_opus", lambda wav: b"faux-ogg")
    monkeypatch.setattr(
        pipeline, "extract_information",
        lambda texte, langue, contexte_client_id: _extraction("vente", montant=1500),
    )

    await pipeline.handle_incoming_message({"type": "text", "text": {"body": "vendu 1500"}}, VENDEUR)

    # La note vocale est envoyée en OGG/Opus (WhatsApp refuse le WAV), puis le même texte
    # pour que le commerçant puisse relire le montant.
    assert espion.audios_envoyes == [(VENDEUR, b"faux-ogg")]
    assert espion.textes_envoyes == [(VENDEUR, "1500 F. Waaw walla déet ?")]


@pytest.mark.anyio
async def test_message_audio_est_transcrit_puis_traite(monkeypatch):
    espion = _Espion()
    monkeypatch.setattr(pipeline.whatsapp_client, "envoyer_audio", espion.envoyer_audio)
    monkeypatch.setattr(pipeline.whatsapp_client, "envoyer_texte", espion.envoyer_texte)
    monkeypatch.setattr(pipeline.whatsapp_client, "telecharger_media",
                         lambda media_id: _async_retour(b"audio-ogg-brut"))
    monkeypatch.setattr(pipeline.audio_utils, "ogg_vers_wav", lambda ogg: b"audio-wav")
    monkeypatch.setattr(pipeline.kiriku_models, "recognize_wav_bytes",
                         lambda wav, modele, langue: "vendu 1500")
    monkeypatch.setattr(pipeline.kiriku_models, "synthesize", lambda texte, modele: b"faux-wav")
    monkeypatch.setattr(pipeline.audio_utils, "wav_vers_ogg_opus", lambda wav: b"faux-ogg")
    monkeypatch.setattr(
        pipeline, "extract_information",
        lambda texte, langue, contexte_client_id: _extraction("vente", montant=1500),
    )

    message = {"type": "audio", "audio": {"id": "media-123", "mime_type": "audio/ogg"}}
    await pipeline.handle_incoming_message(message, VENDEUR)

    assert espion.audios_envoyes == [(VENDEUR, b"faux-ogg")]


@pytest.mark.anyio
async def test_echec_tts_retombe_sur_un_message_texte(monkeypatch):
    espion = _Espion()
    monkeypatch.setattr(pipeline.whatsapp_client, "envoyer_audio", espion.envoyer_audio)
    monkeypatch.setattr(pipeline.whatsapp_client, "envoyer_texte", espion.envoyer_texte)

    def synthese_en_panne(texte, modele):
        raise RuntimeError("HF_TOKEN manquant")

    monkeypatch.setattr(pipeline.kiriku_models, "synthesize", synthese_en_panne)
    monkeypatch.setattr(
        pipeline, "extract_information",
        lambda texte, langue, contexte_client_id: _extraction("vente", montant=1500),
    )

    await pipeline.handle_incoming_message({"type": "text", "text": {"body": "vendu 1500"}}, VENDEUR)

    assert espion.audios_envoyes == []
    assert espion.textes_envoyes == [(VENDEUR, "1500 F. Waaw walla déet ?")]


@pytest.mark.anyio
async def test_type_de_message_non_supporte_envoie_une_consigne(monkeypatch):
    espion = _Espion()
    monkeypatch.setattr(pipeline.whatsapp_client, "envoyer_texte", espion.envoyer_texte)

    await pipeline.handle_incoming_message({"type": "image", "image": {"id": "img-1"}}, VENDEUR)

    assert len(espion.textes_envoyes) == 1
    assert espion.textes_envoyes[0][0] == VENDEUR


async def _async_retour(valeur):
    return valeur


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_les_jetons_de_langue_whisper_sont_retires_de_la_transcription():
    from backend.kiriku_models import nettoyer_transcription

    # Sortie réelle de M-Kiriku-ASR (skip_special_tokens ne retire pas <|wo|>).
    assert nettoyer_transcription("<|wo|> waaw") == "waaw"
    assert nettoyer_transcription(" <|se|><|transcribe|> iyo  ") == "iyo"
    assert nettoyer_transcription("Awa fey na 1000") == "Awa fey na 1000"
