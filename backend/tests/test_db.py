import pytest

from backend import db
from contracts.models import DonneesExtraction, ExtractionResponse

VENDEUR = "221770000000"


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "test.db")
    db.init_db(path)
    connection = db.get_connection(path)
    yield connection
    connection.close()


def _extraction(intention, **donnees) -> ExtractionResponse:
    return ExtractionResponse(
        intention=intention,
        donnees=DonneesExtraction(**donnees),
        langue="wo",
        methode="regles",
        confiance=0.9,
        est_complet=True,
        champs_manquants=[],
        question_clarification=None,
    )


def test_vente_credit_cree_la_dette(conn):
    extraction = _extraction("vente_credit", client="Awa", montant=3000)
    resultat = db.enregistrer_operation(conn, VENDEUR, extraction)

    assert resultat.nouveau_solde == 3000
    assert db.solde_client(conn, VENDEUR, "Awa") == 3000


def test_remboursement_reduit_la_dette_sans_descendre_sous_zero(conn):
    db.enregistrer_operation(conn, VENDEUR, _extraction("vente_credit", client="Awa", montant=3000))
    resultat = db.enregistrer_operation(
        conn, VENDEUR, _extraction("remboursement", client="Awa", montant=5000)
    )

    assert resultat.nouveau_solde == 0


def test_find_client_tolere_une_orthographe_approchante(conn):
    db.get_or_create_client(conn, VENDEUR, "Awa Diop")
    trouve = db.find_client(conn, VENDEUR, "Awa Diob")  # faute ASR plausible

    assert trouve is not None
    assert trouve["nom"] == "Awa Diop"


def test_clients_isoles_par_vendeur(conn):
    db.get_or_create_client(conn, "vendeur_a", "Awa")
    assert db.find_client(conn, "vendeur_b", "Awa") is None


def test_vente_simple_ne_touche_aucun_client(conn):
    resultat = db.enregistrer_operation(conn, VENDEUR, _extraction("vente", montant=1500))
    assert resultat.client_nom is None
    assert resultat.nouveau_solde is None


def test_annuler_derniere_operation_restaure_le_solde(conn):
    db.enregistrer_operation(conn, VENDEUR, _extraction("vente_credit", client="Awa", montant=3000))
    annulee = db.annuler_derniere_operation(conn, VENDEUR)

    assert annulee["intention"] == "vente_credit"
    assert db.solde_client(conn, VENDEUR, "Awa") == 0


def test_annuler_sans_operation_renvoie_none(conn):
    assert db.annuler_derniere_operation(conn, VENDEUR) is None


def test_bilan_jour_agrege_par_intention(conn):
    db.enregistrer_operation(conn, VENDEUR, _extraction("vente", montant=1500))
    db.enregistrer_operation(conn, VENDEUR, _extraction("vente_credit", client="Awa", montant=3000))
    db.enregistrer_operation(conn, VENDEUR, _extraction("remboursement", client="Awa", montant=1000))

    bilan = db.bilan_jour(conn, VENDEUR)

    assert bilan["total_ventes"] == 1500
    assert bilan["total_credits"] == 3000
    assert bilan["total_remboursements"] == 1000
    assert bilan["nb_operations"] == 3


def test_pending_extraction_cycle_complet(conn):
    extraction = _extraction("vente_credit", client="Awa", montant=3000)
    assert db.get_pending(conn, VENDEUR) is None

    db.save_pending(conn, VENDEUR, extraction)
    relue = db.get_pending(conn, VENDEUR)
    assert relue.donnees.client == "Awa"

    db.clear_pending(conn, VENDEUR)
    assert db.get_pending(conn, VENDEUR) is None


def test_langue_preferee_par_defaut_wolof(conn):
    assert db.get_or_create_vendeur(conn, VENDEUR) == "wo"
    db.set_langue_preferee(conn, VENDEUR, "ff")
    assert db.get_or_create_vendeur(conn, VENDEUR) == "ff"


def test_lister_vendeurs(conn):
    assert db.lister_vendeurs(conn) == []
    db.get_or_create_vendeur(conn, "221770000000")
    db.get_or_create_vendeur(conn, "221780000000")
    assert sorted(db.lister_vendeurs(conn)) == ["221770000000", "221780000000"]


def test_annulation_garde_l_operation_et_la_sort_du_bilan(conn):
    db.enregistrer_operation(conn, VENDEUR, _extraction("vente", montant=1500))
    db.enregistrer_operation(conn, VENDEUR, _extraction("vente", montant=700))
    db.annuler_derniere_operation(conn, VENDEUR)

    assert [op["statut"] for op in db.lister_operations(conn, VENDEUR)] == ["annulee", "active"]
    assert db.bilan_jour(conn, VENDEUR)["total_ventes"] == 1500
    # Une deuxième annulation vise l'opération active précédente, pas la même.
    assert db.annuler_derniere_operation(conn, VENDEUR)["montant"] == 1500
    assert db.annuler_derniere_operation(conn, VENDEUR) is None
