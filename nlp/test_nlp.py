from nlp.extractor import extract_information


def test_simple_credit_phrase():
    text = "Awa jënd na sukar 3000 dafa ko bor"
    res = extract_information(text, langue="wo")
    
    print("Résultat pour :", text)
    print(res.model_dump_json(indent=2))
    
    assert res.intention == "vente_credit"
    assert res.donnees.montant == 3000
    assert res.donnees.produit == "sucre"


if __name__ == "__main__":
    test_simple_credit_phrase()
