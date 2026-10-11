from backend import templates
from contracts.models import DonneesExtraction, ExtractionResponse


def _extraction(intention, langue="wo", est_complet=True, question_clarification=None, **donnees):
    return ExtractionResponse(
        intention=intention,
        donnees=DonneesExtraction(**donnees),
        langue=langue,
        methode="regles",
        confiance=0.9,
        est_complet=est_complet,
        champs_manquants=[] if est_complet else ["montant"],
        question_clarification=question_clarification,
    )


def test_detecter_confirmation_reconnait_waaw_et_deet():
    assert templates.detecter_confirmation("Waaw") is True
    assert templates.detecter_confirmation("déet") is False
    assert templates.detecter_confirmation("DÉET") is False
    assert templates.detecter_confirmation("oui") is True
    assert templates.detecter_confirmation("je ne sais pas") is None


def test_confirmation_vente_credit_reprend_le_gabarit_du_dossier():
    extraction = _extraction("vente_credit", client="Awa", montant=3000)
    assert templates.phrase_confirmation(extraction) == "Awa, 3000 F, la leb. Waaw walla déet ?"


def test_confirmation_incomplete_utilise_la_question_de_marome():
    extraction = _extraction(
        "vente_credit", client="Awa", est_complet=False, question_clarification="Ñaata la ?"
    )
    assert templates.phrase_confirmation(extraction) == "Ñaata la ?"


def test_ack_remboursement_annonce_le_nouveau_solde():
    extraction = _extraction("remboursement", client="Awa", montant=1000)
    assert templates.phrase_ack(extraction, nouveau_solde=2000) == "Awa fay nala 1000 F. Mu dess 2000 F."


def test_pulaar_et_serere_retombent_sur_la_voix_wolof():
    extraction_ff = _extraction("vente_credit", langue="ff", client="Awa", montant=3000)
    extraction_srr = _extraction("vente_credit", langue="srr", client="Awa", montant=3000)
    attendu = "Awa, 3000 F, la leb. Waaw walla déet ?"
    assert templates.phrase_confirmation(extraction_ff) == attendu
    assert templates.phrase_confirmation(extraction_srr) == attendu


def test_question_dette_trois_cas():
    assert templates.phrase_question_dette("wo", "Awa", 2000) == "Awa amel nala 2000 F ci bor."
    assert templates.phrase_question_dette("wo", "Awa", 0) == "Awa amul benn bor."
    assert templates.phrase_question_dette("wo", "Awa", None) == "Xamuma Awa."


def test_bilan_remplit_tous_les_totaux():
    bilan = {
        "total_ventes": 45000, "total_credits": 12000, "total_remboursements": 3000,
        "total_achats": 0, "total_depenses": 0, "nb_operations": 5,
    }
    phrase = templates.phrase_bilan("wo", bilan)
    assert "45000" in phrase and "12000" in phrase and "3000" in phrase


def test_annulation_vide_vs_reussie():
    assert templates.phrase_annulation("wo", None) == "Amul benn ligéey bu far."
    assert templates.phrase_annulation("wo", {"intention": "vente"}) == "Baax na, far naa ko."


def test_statut_couverture_signale_les_gabarits_non_valides():
    rapport = templates.statut_couverture()
    assert "wo:vente" in rapport["a_valider"]
    # Le gabarit de confirmation vente_credit est "doc" (copié du dossier) ;
    # seul l'ack vente_credit (nouveau solde annoncé) est encore "a_valider".
    assert templates._CONFIRMATIONS[("wo", "vente_credit")][0] == "doc"
