# 🔮 Crypto Signal Agent — Guide d'installation

## Ce que fait ce script
- ✅ Surveille 200 cryptos en temps réel (CoinCap API)
- ✅ Détecte les volumes anormaux et t'alerte sur Telegram instantanément
- ✅ Scrape Reddit (CryptoMoonShots, altcoin, SatoshiStreetBets) pour les signaux sociaux
- ✅ Utilise Claude pour analyser et synthétiser les données
- ✅ Envoie une newsletter HTML par email tous les matins à 8h

---

## ÉTAPE 1 — Télécharger les fichiers

Crée un dossier sur ton bureau appelé `crypto_agent` et mets-y ces 3 fichiers :
- `crypto_agent.py`
- `config.py`
- `requirements.txt`

---

## ÉTAPE 2 — Remplir config.py

Ouvre `config.py` dans VS Code et remplis :

### Token Telegram
Remplace `METS_TON_TOKEN_ICI` par le token que t'a donné BotFather.

### Clé Claude API
Remplace `METS_TA_CLE_CLAUDE_ICI` par ta clé Claude (commence par `sk-ant-`).

### Email Gmail
Pour utiliser Gmail, tu ne peux pas utiliser ton vrai mot de passe.
Il faut créer un "mot de passe d'application" :

1. Va sur myaccount.google.com
2. Sécurité → Validation en deux étapes (active si pas déjà fait)
3. Sécurité → Mots de passe des applications
4. Sélectionne "Autre" → tape "CryptoAgent" → Générer
5. Google te donne un mot de passe de 16 caractères (ex: `abcd efgh ijkl mnop`)
6. Copie ce mot de passe SANS les espaces → mets-le dans `EMAIL_PASSWORD`

---

## ÉTAPE 3 — Installer les dépendances

Ouvre un terminal dans VS Code (Terminal → New Terminal) et tape :

```
pip install -r requirements.txt
```

---

## ÉTAPE 4 — Lancer le script

Dans le terminal VS Code, depuis le dossier crypto_agent :

```
python crypto_agent.py
```

Tu devrais voir :
```
🚀 Crypto Signal Agent démarré !
[Telegram] Message envoyé ✅
⏰ Surveillance active — check toutes les 15 min
📧 Newsletter quotidienne programmée à 08:00
```

Et tu reçois un message Telegram de confirmation !

---

## ÉTAPE 5 — Laisser tourner

Le script doit rester ouvert dans VS Code pour fonctionner.
Si tu fermes VS Code, il s'arrête.

Pour le faire tourner 24/7 sans que ton PC soit allumé en permanence,
on peut le déployer sur Railway (gratuit) — demande à Claude quand tu es prêt.

---

## Que faire si ça ne marche pas ?

**Erreur Telegram** → Vérifie que le token est correct et que tu as envoyé au moins un message à ton bot

**Erreur Email** → Vérifie que tu utilises bien le mot de passe d'application Gmail (pas ton vrai mdp)

**Erreur Claude** → Vérifie que ta clé commence par `sk-ant-` et que tu as du crédit sur ton compte Anthropic
