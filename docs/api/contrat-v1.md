# Contrat d'Interface & API — v1.0.0

**Projet** : TEWLU Kaddu — Track Banque & Finance (Kiriku Voice Challenge)  
**Date d'effet** : 4 octobre 2026  
**Source de vérité** : `contracts/models.py`  
**Équipe** : Khalifa Babacar DIOUF (KBD) · Marème · Ndèye Maguette

---

## 1. Principes non négociables

1. **Format unique garanti** : quelle que soit la méthode utilisée par Marème (règles locales, traducteur GalsenAI, ou LLM), la sortie doit être strictement conforme au modèle Pydantic `ExtractionResponse`.
2. **Champs inconnus strictement interdits (`extra="forbid"`)** : aucun champ non documenté ne doit être retourné.
3. **Le moteur NLU ne fait pas d'E/S audio** : Marème reçoit uniquement du texte (`str`) et du code langue (`wo`, `ff`, `srr`). La voix (ASR et TTS) et WhatsApp sont gérés exclusivement par Khalifa.
4. **Gestion de l'incomplétude** : si une information obligatoire manque, l'objet retourné indique `est_complet = false`, liste les `champs_manquants`, et propose une `question_clarification`.

---

## 2. Définition des Intentions et Champs Obligatoires

| Intention | Description | Champs obligatoires | Exemple type |
|---|---|---|---|
| `vente` | Vente payée comptant | `montant` | *"Jaay naa 2 paket sukar, 1500"* |
| `vente_credit` | Vente à crédit (dette client) | `client`, `montant` | *"Awa jënd na 2 paket sukar, 3000 dafa ko bor"* |
| `remboursement` | Remboursement d'une dette | `client`, `montant` | *"Awa fey na 1000"* |
| `achat` | Achat de marchandise / stock | `montant` | *"Jënd naa 10 saag ceeb, 150000"* |
| `depense` | Dépense courante (transport, loyer...) | `montant` | *"Fey naa transport bi, 2000"* |
| `question_dette` | Demande du solde d'un client | `client` | *"Ñaata la ma Awa bor ?"* |
| `question_bilan` | Bilan des ventes | aucun (période par défaut = jour) | *"Ñaata laa jaay tey ?"* |
| `annulation` | Annulation de la dernière action | aucun | *"Annule-le" / "Faral li mujj"* |
| `incomprehension` | Phrase non comprise / hors domaine | aucun | *"Je n'ai pas compris"* |

---

## 3. Schéma de Sortie (`ExtractionResponse`)

```json
{
  "intention": "vente_credit",
  "donnees": {
    "client": "Awa",
    "produit": "sucre",
    "quantite": 2.0,
    "unite": "paquet",
    "montant": 3000
  },
  "langue": "wo",
  "methode": "regles",
  "confiance": 0.95,
  "est_complet": true,
  "champs_manquants": [],
  "question_clarification": null
}
```

### Cas incomplet (Exemple : montant manquant)
```json
{
  "intention": "vente_credit",
  "donnees": {
    "client": "Awa",
    "produit": "sucre",
    "quantite": 2.0,
    "unite": "paquet",
    "montant": null
  },
  "langue": "wo",
  "methode": "regles",
  "confiance": 0.85,
  "est_complet": false,
  "champs_manquants": ["montant"],
  "question_clarification": "Ñaata la ?"
}
```

---

## 4. Évolution du Contrat

Toute modification (ajout de champ, changement d'un type d'intention) doit :
1. Être discutée et validée par Khalifa et Marème.
2. Faire l'objet d'une branche dédiée `contrat/<description>`.
3. Valider les tests `pytest contracts/tests/`.
