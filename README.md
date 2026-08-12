# RiseUp Backtester v0.2 — Twelve Data

Cette version n'utilise plus OANDA. Elle récupère les prix Forex via **Twelve Data** avec une simple clé API; aucun compte de trading n'est nécessaire.

## 1. Obtenir la clé

1. Créer un compte Twelve Data.
2. Ouvrir le dashboard / API Keys.
3. Copier la clé API.
4. Copier `.env.example` en `.env` puis remplir uniquement côté serveur:

```env
TWELVE_DATA_API_KEY=ta_cle_ici
```

Ne publie jamais la clé dans GitHub ni dans le JavaScript du navigateur.

## 2. Lancer le site

```bash
cp .env.example .env
# mettre la clé dans .env
docker compose up --build
```

Ouvrir ensuite `http://localhost:8000`.

## 3. Automation

Le serveur relance l'actualisation **chaque heure à :07, du lundi au vendredi**. Après le premier backfill, seules les nouvelles bougies sont téléchargées et mises en cache en Parquet.

## 4. Les 4 variantes

- **A.0**: label M1 fixé après une bougie supplémentaire + zone H2.
- **A.1**: même confirmation A + zone H4.
- **B.0**: pivot M1 confirmé avec `rb=5` + zone H2.
- **B.1**: même confirmation B + zone H4.

Règles communes: cassure du High/Low de la session précédente, non-confirmation opposée du DXY, présence dans une zone H2/H4, trigger M1, EURUSD 15/30 pips, GBPUSD 20/40 pips, risque 2% puis 1% après une perte.

## 5. DXY

Le DXY est reconstruit avec la formule ICE à partir des six paires Twelve Data:

`50.14348112 × EURUSD^-0.576 × USDJPY^0.136 × GBPUSD^-0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036`

Ce n'est pas un flux ICE tick-by-tick; c'est une reconstruction M1 synchronisée.

## 6. Limitation importante: volume Forex

Twelve Data documente `time_series` Forex comme une série **Open/High/Low/Close**; le volume est fourni aux instruments non-currency, pas aux paires de devises. Le deuxième Pine Script original utilise pourtant quatre indicateurs dépendant du volume: **OBV, VW-MACD, CMF et MFI**.

Cette version ne fabrique pas un faux volume. Elle exclut donc ces quatre composantes et calcule le label avec:

- RSI
- MACD
- MACD Histogram
- Momentum
- CCI
- Stochastic
- DIosc

Le numéro du label peut donc être de 1 à 7 au lieu de 1 à 11, et les signaux peuvent différer du script TradingView original. C'est la principale différence à garder en tête.

## 7. Spread

`/time_series` ne fournit pas l'historique bid/ask. Le spread est donc configurable:

```env
EURUSD_SPREAD_PIPS=0
GBPUSD_SPREAD_PIPS=0
```

0 pip est volontairement neutre mais rend les résultats plus optimistes. Une fois le moteur validé, il faudra tester plusieurs hypothèses de spread.

## 8. Limites du plan gratuit

Le Basic gratuit Twelve Data est soumis à un quota de crédits. Le client du projet respecte un plafond configurable de 8 crédits/minute et découpe l'historique M1 en blocs de 3 jours afin de rester sous 5000 points par réponse.

Pour le premier test, garde `BACKTEST_START=2026-02-01`. Le premier remplissage peut prendre du temps à cause du quota minute, mais ensuite les mises à jour sont petites et automatiques.

## 9. Déploiement permanent

Le projet est dockerisé et peut tourner sur un VPS ou un hébergeur de conteneurs persistant. Il faut conserver le dossier `data/` et définir `TWELVE_DATA_API_KEY` comme secret côté serveur.

## 10. Ce qu'il reste à confirmer pour une reproduction RiseUp exacte

- horaire exact de la session ASIA;
- définition historique exacte des zones H2/H4;
- moment précis où le DXY invalide une divergence;
- minimum de divergences exigé par le label M1;
- et surtout le flux de volume/tick-volume utilisé par le Pine Script original.
# backTestingTrade
