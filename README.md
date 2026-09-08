# RiseUp Trading Research Lab V2

RiseUp est un laboratoire local de recherche quantitative Forex, sans exécution réelle d'ordres. Il permet de configurer, lancer, expliquer, conserver et comparer des backtests depuis le navigateur ou la ligne de commande. Les données historiques viennent par défaut d'un cache M1 Dukascopy public, sans clé API ni compte broker. Twelve Data reste disponible comme fallback de migration.

Les définitions historiques A.0/A.1/B.0/B.1 sont immuables et empruntent le moteur legacy exact. Les expériences V2 utilisent un `StrategyConfig` versionné : une modification de règle ne peut donc pas changer silencieusement un résultat historique.

## Architecture

```text
Dukascopy public Historical Data Export / Jetta
→ parser BID + ASK
→ validation UTC
→ cache Parquet M1 mensuel
→ resampling M5 / H1 / H2 / H4
→ moteur legacy exact OU moteur Research V2 configurable
→ SQLite : runs + trades + rejets + traces + statistiques
→ API FastAPI + interface web locale
```

- `app/data/` : frontière provider, cache Parquet, validation et resampling.
- `app/backtest.py` : moteur historique conservé pour le golden A/B.
- `app/research/config.py` : schéma Pydantic strict et hash reproductible.
- `app/research/engine.py` : sessions, divergence, zones, labels, exécution et risque configurables.
- `app/research/analysis.py` : statistiques, equity, dimensions et IS/OOS.
- `app/storage.py` : migrations SQLite additives et historique permanent.
- `app/api.py`, `app/static/index.html` : API et application monopage.
- `scripts/` : téléchargement, import, statut, CLI de recherche et golden.

La source implémentée est l'API publique utilisée par l'[exporteur historique officiel Dukascopy](https://www.dukascopy.com/swiss/english/marketwatch/historical/). L'exporteur annonce des prix BID/ASK et des volumes, et ne demande pas de clé.

## Installation

Python 3.12 est recommandé.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

La configuration historique recommandée est déjà présente dans `.env.example` :

```env
DATA_PROVIDER=dukascopy
DXY_SOURCE=dukascopy_direct
DATA_VALIDATION_MODE=strict
EXECUTION_PRICE_MODE=bid_ask
VOLUME_MODE=legacy_no_volume
```

Les commandes Python chargent automatiquement le `.env` local sans remplacer les variables déjà exportées. Les secrets et artefacts locaux (`.env`, Parquet, SQLite) ne sont jamais versionnés.

## Interface web et backtests

Après avoir téléchargé les données :

```bash
AUTO_RUN_ON_STARTUP=false ENABLE_SCHEDULER=false \
  uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Ouvrir `http://127.0.0.1:8000`. Les pages couvrent Dashboard, New Backtest, Runs, Compare, Trades, Rejected Setups, Data Quality, Research/Robustness et Settings.

Un lancement crée immédiatement un UUID `QUEUED`, puis expose les étapes `LOADING_DATA`, `VALIDATING_DATA`, `BUILDING_SESSIONS`, `BUILDING_ZONES`, `CALCULATING_INDICATORS`, `GENERATING_SETUPS`, `SIMULATING_TRADES`, `CALCULATING_STATISTICS`, `SAVING_RESULTS`, `COMPLETED` ou `FAILED`. L'exception complète reste côté serveur; le message utile est affiché dans l'interface.

La CLI persistante utilise exactement le même moteur :

```bash
python scripts/run_research.py --list-presets
python scripts/run_research.py --preset "Legacy A.0" \
  --from 2026-02-01 --to 2026-08-25 --name "Reference A.0"
python scripts/run_research.py --config strategy.json
```

## StrategyConfig et presets

Chaque run enregistre le JSON complet, son SHA-256, le provider, les instruments, le modèle d'exécution, la version stratégie/application, le commit Git, la couverture, les warnings et les timestamps. La validation interdit notamment une période inversée, SL/TP non positifs, risque incohérent, timeframe inconnu, session de durée nulle ou liste de paires vide.

Les presets intégrés sont `Legacy A.0`, `Legacy A.1`, `Legacy B.0` et `Legacy B.1`. Ils ne peuvent être ni modifiés ni supprimés : les dupliquer crée une variante V2. Pour un run legacy, le nom, la période, la politique de validation et le split IS/OOS peuvent varier, mais toute modification d'une règle de trading est refusée avec un message explicite. `Clone configuration` conserve ainsi le moteur historique exact; `Sauvegarder sous…` détache volontairement la configuration vers V2. Les presets personnels peuvent être sauvegardés, renommés, remplacés ou supprimés.

### Sessions

Les sessions sont ordonnées, activables, ajoutables et supprimables. Chaque nom, début et fin est sérialisé; `Europe/Paris` est interprété avec `zoneinfo`, y compris DST et sessions overnight. La session précédente peut être chronologique ou nommée.

### Divergence DXY

La cassure high/low, la tolérance, le mode intrabar/interbar, l'invalidation tardive, les délais et le comportement en cas de gap DXY sont explicites. Chaque cutoff est exclusif : aucune bougie postérieure à la décision n'est lue.

### Zones HTF

H1, H2 et H4 peuvent être combinés en mode `ANY` ou `ALL`, avec priorité, ADR, direction, distance, touch/retests, expiration et mitigation wick/close. Une zone n'existe pour le moteur qu'après clôture de sa bougie HTF source.

### Labels et indicateurs

Les triggers `NONE`, `FAST`, `CONFIRMED` et `CUSTOM` acceptent M1/M5. `left_bars`, `right_bars`, cut-through, délai, minimum, indicateurs obligatoires/optionnels sont enregistrés. Les indicateurs prix sont RSI, MACD, MACD Hist, Momentum, CCI, Stochastic et DI Oscillator. OBV, VW-MACD, CMF et MFI ne sont disponibles que lorsque le volume Dukascopy est explicitement activé; aucun volume n'est fabriqué.

### Risk, exits et exécution

SL/TP sont configurés par paire. Le capital, les risques des premier/deuxième trades, la limite quotidienne, le stop après gain, le second trade après perte, la position unique et la priorité de paire sont appliqués chronologiquement. L'exécution choisit `mid` ou BID/ASK réel, spread additionnel, slippage et politique same-bar `stop_first`/`tp_first`.

## Historique, traces et exports

SQLite conserve tous les runs; un nouveau run ne remplace jamais un ancien. Chaque trade contient les niveaux, timestamps, DXY, label, indicateurs, zones, prix BID/ASK, spread, SL/TP, risque, sortie, R, PnL et equity. Chaque setup rejeté conserve une raison stable (`NO_PAIR_BREAK`, `DXY_CONFIRMED`, `DXY_DATA_MISSING`, `NO_ZONE`, `NO_LABEL`, etc.) et une trace PASS/FAIL/SKIPPED consultable.

Les exports disponibles par run sont : trades CSV, rejets CSV, statistiques CSV, configuration JSON et run JSON complet.

## Analyse, IS/OOS et robustesse

Les résultats incluent win rate, total/average/median R, profit factor, expectancy, return, max drawdown, streaks et equity curve. Les découpes couvrent jour, semaine, mois, trimestre, année, paire, direction, session, weekday, zone, trigger, preset, label count, rang du trade, spread et heure d'entrée.

Le split IS/OOS ne réalise aucune optimisation automatique. Le walk-forward crée des fenêtres train/test persistantes et agrège uniquement leurs trades OOS. La sensibilité est bornée à 25 combinaisons explicites, retourne une grille numérique et signale un pic historique isolé. L'Overfitting Guard combine nombre de runs, paramètres libres, variantes, dégradation IS/OOS et stabilité; `LOW/MEDIUM/HIGH` est un indicateur de discipline, pas une prévision.

## Historical Data — Dukascopy

### Télécharger

`--to` est inclusif lorsqu'il reçoit uniquement une date. La commande suivante demande donc toute la journée du 25 août 2026, en excluant automatiquement toute bougie M1 encore ouverte :

```bash
python scripts/download_history.py \
  --all-required \
  --from 2024-01-01 \
  --to 2026-08-25
```

Sans `--symbols`, les instruments requis par `DXY_SOURCE` sont également utilisés. En mode direct : EURUSD, GBPUSD et DXY. En mode synthétique : les six composants ICE nécessaires sont téléchargés.

Téléchargement ciblé et validation stricte :

```bash
python scripts/download_history.py \
  --symbols EUR_USD GBP_USD DXY \
  --from 2024-01-01 \
  --to 2026-08-25 \
  --interval 1min \
  --validate
```

Le downloader est idempotent. Il mémorise les jours UTC terminés, reprend après interruption et ne récupère que les jours absents. Pour retélécharger une période connue :

```bash
python scripts/download_history.py \
  --symbols EUR_USD \
  --from 2024-01-02 \
  --to 2024-01-02 \
  --force \
  --validate
```

Une seconde exécution sur une période déjà complète ne contacte pas Dukascopy. Les téléchargements et les backtests sont séparés : après remplissage du cache, les backtests peuvent être lancés hors ligne.

### Voir la couverture locale

```bash
python scripts/data_status.py
```

Le rapport affiche l'instrument fournisseur, la période, le nombre de M1, les jours complets/partiels et les minutes manquantes inattendues.

### Importer un export CSV manuel

L'exporteur Dukascopy produit un fichier par côté. Importer les fichiers BID et ASK; le premier reste en attente jusqu'à l'arrivée de son pendant, puis les deux alimentent le même cache canonique que le downloader automatique.

```bash
python scripts/import_dukascopy_csv.py \
  path/to/EUR-USD_1_Minute_BID_UTC.csv \
  path/to/EUR-USD_1_Minute_ASK_UTC.csv
```

Si le nom ne permet pas la détection :

```bash
python scripts/import_dukascopy_csv.py path/to/file.csv \
  --symbol EUR_USD \
  --side BID \
  --timezone UTC
```

## Cache et reproductibilité

Le M1 est la seule source canonique persistée :

```text
data/dukascopy/EUR_USD/M1/2024-01.parquet
data/dukascopy/EUR_USD/M1/manifest.json
data/dukascopy/EUR_USD/M1/instrument.json
```

Le partitionnement mensuel évite un fichier monolithique et des milliers de petits fichiers. Chaque manifeste contient le provider, le symbole, la période, le nombre de lignes et un SHA-256 par partition. Chaque run calcule aussi une `data_revision` déterministe à partir des partitions couvrant sa période et ses instruments. Les écritures Parquet et JSON utilisent un remplacement atomique; les timestamps sont triés et les doublons sont résolus de façon déterministe.

Pour figer un backtest, renseigner aussi une fin explicite :

```env
BACKTEST_START=2024-01-01
BACKTEST_END=2025-01-01
```

À données, paramètres et commit identiques, le résultat ne dépend alors plus de l'heure d'exécution.

## Schéma canonique

Chaque ligne est indexée par un `timestamp` timezone-aware en UTC et conserve :

```text
bid_o bid_h bid_l bid_c
ask_o ask_h ask_l ask_c
mid_o mid_h mid_l mid_c
bid_volume ask_volume volume
```

`mid_*` est défini colonne par colonne comme `(bid_* + ask_*) / 2`. BID et ASK provenant de fichiers M1 agrégés séparément, les extrema mid ne sont pas des extrema recalculés tick par tick; cette limite est documentée et aucune minute absente n'est remplie artificiellement.

## Bid/Ask et spread

Avec `EXECUTION_PRICE_MODE=bid_ask` :

- BUY : entrée ASK, SL/TP et sortie BID;
- SELL : entrée BID, SL/TP et sortie ASK.

Si BID/ASK n'existe pas (fallback Twelve Data), le moteur revient au mode mid historique et aux spreads fixes `EURUSD_SPREAD_PIPS` / `GBPUSD_SPREAD_PIPS`. `EXECUTION_PRICE_MODE=mid` permet une comparaison legacy explicite.

## Volume

Dukascopy précise dans sa [FAQ Historical Price Feed](https://www.dukascopy.com/swiss/english/about/faq/?mob=0) que le volume d'une bougie est la somme des volumes du meilleur Bid ou du meilleur Ask pendant la période. Ce n'est pas présenté comme le volume réel échangé. Les deux mesures sont conservées séparément; `volume` pointe par compatibilité vers le volume BID, côté par défaut de l'exporteur officiel.

La stratégie ne change pas silencieusement :

- `VOLUME_MODE=legacy_no_volume` conserve le label prix-only historique;
- `VOLUME_MODE=dukascopy_volume` utilise le volume BID et réactive OBV, VW-MACD, CMF et MFI pour une comparaison contrôlée.

## DXY

Le symbole direct vérifié dans l'exporteur et ses métadonnées est :

```text
DOLLAR.IDX/USD  → code API DOLLAR.IDX-USD
```

`DXY_SOURCE=dukascopy_direct` utilise cet historique. `DXY_SOURCE=synthetic` conserve `app/dxy.py` et la formule ICE existante à partir de EUR/USD, USD/JPY, GBP/USD, USD/CAD, USD/SEK et USD/CHF. Si le direct est demandé mais absent localement, le moteur tente le fallback synthétique et enregistre la source réellement utilisée dans le résultat.

## UTC, sessions et resampling

Les données sont toujours stockées en UTC. Les sessions sont ensuite construites en `SESSION_TIMEZONE=Europe/Paris` avec `zoneinfo`, donc les bascules DST changent correctement l'offset UTC sans changer les heures murales BLUE/RED/ASIA.

M5/H1/H2/H4 sont reconstruits depuis M1 : premier open, maximum high, minimum low, dernier close, somme des volumes. L'ancrage H2/H4 existant est maintenant explicite :

```env
HTF_ANCHOR_TIMEZONE=Europe/Paris
```

Il correspond à des groupes ancrés à minuit dans ce fuseau, comme avant la migration. Son équivalence exacte avec le feed TradingView reste un point métier à confirmer.

## Validation stricte

Le validateur contrôle : UTC, ordre, doublons, OHLC, NaN/prix non positifs, volumes négatifs et minutes manquantes. Les week-ends, les horaires vérifiés des instruments, la maintenance quotidienne du DXY et les jours fériés fournis dans les métadonnées Dukascopy sont classés comme fermetures attendues.

Aucun `ffill()` n'est appliqué aux prix. En mode strict, une minute ouverte manquante interrompt le backtest avec la première période concernée. Le mode `permissive` existe pour l'exploration mais n'est pas recommandé pour les résultats officiels.

## Lancer le backtest

Après téléchargement :

```bash
python run_backtest.py
```

Puis, pour l'application web :

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

ou :

```bash
docker compose up --build
```

Le scheduler conserve les règles existantes. `AUTO_RUN_ON_STARTUP=false` est recommandé tant que la couverture locale n'a pas été vérifiée.

## Twelve Data fallback

Pour l'ancien provider :

```env
DATA_PROVIDER=twelvedata
TWELVE_DATA_API_KEY=ta_cle_serveur
DXY_SOURCE=synthetic
EXECUTION_PRICE_MODE=mid
```

La clé reste uniquement côté serveur. Le provider passe par la même frontière de données que Dukascopy; le moteur de stratégie n'est pas dupliqué.

## Tests

```bash
python -m pytest -q
python scripts/verify_legacy_golden.py
```

La suite couvre le parser config, presets, persistance, historique de runs, API, sessions DST/overnight, session précédente, cassure, cutoff DXY, timing des pivots, labels, resampling, zones HTF clôturées, gaps, cache idempotent/partiel, import CSV, BID/ASK, same-bar, risque, position unique, limite quotidienne, IS/OOS et fenêtres walk-forward.

Le golden long rejoue la période locale du 31 janvier au 25 août 2026. Il vérifie 235 setups, 181 trades et le SHA-256 de la séquence économique complète; il est séparé de `pytest` afin que la suite reste rapide et portable sans les centaines de milliers de M1. Voir [la référence golden](docs/legacy_golden.md).

## API

FastAPI documente automatiquement le schéma sur `/docs`. Routes principales :

- `GET /api/config/schema`, `GET|POST /api/presets`, `PUT|DELETE /api/presets/{id}`;
- `POST|GET /api/backtests`, détail/statut/trades/rejected/stats par UUID;
- `POST /api/backtests/compare` et exports sous `/api/backtests/{id}/export/{kind}`;
- `GET /api/data/status`;
- `POST /api/research/walk-forward` et `/research/walk-forward/aggregate`;
- `POST /api/research/sensitivity`, `/research/sensitivity/results` et `/research/risk`.

Les anciens `/api/config`, `/api/result` et `/api/run` restent disponibles.

## Troubleshooting

- `Backtest aborted ... missing M1 candle(s)` : réparer/télécharger la période ou choisir consciemment le mode permissif. Le permissif avertit mais ne crée aucune bougie.
- `Permissive gap limit exceeded` : la somme des minutes inattendues dépasse la limite de l'expérience; augmenter la limite seulement après inspection de Data Quality.
- Cache vide : exécuter `python scripts/data_status.py`, puis `download_history.py` sur la période et les instruments voulus.
- DXY direct incomplet : télécharger `DXY`, ou activer explicitement le fallback synthétique et ses six composants.
- Run `FAILED` : ouvrir son détail; le message utilisateur et la traceback serveur sont conservés dans SQLite.
- Comparaison TradingView différente : contrôler feed, timezone/ancrage HTF, BID/ASK, symbole DXY et volume avant de conclure à une erreur stratégique.

## Limites connues

- Le moteur utilise des OHLC M1, pas des ticks : l'ordre TP/SL intrabar est donc une hypothèse explicite.
- Un backtest V2 construit son warm-up d'indicateurs/ADR à l'intérieur de la période chargée. Les premiers jours peuvent ne produire aucune zone; une période de recherche doit inclure le warm-up désiré. Les entrées sont néanmoins limitées aux dates choisies et une session précédente partiellement hors plage est rejetée.
- Il n'existe pas encore de cache persistant d'indicateurs. Dans un run, chaque matrice/timeframe est calculé une seule fois et réutilisé; un cache inter-runs a été écarté pour V2 tant qu'un profilage n'en démontre pas le gain face au risque d'invalidation incorrecte.
- Les volumes Dukascopy représentent l'activité au meilleur BID/ASK telle que décrite par le fournisseur, pas un volume centralisé Forex.
- Le score d'overfitting et la stabilité de sensibilité sont des heuristiques de recherche; aucune validité prédictive n'est revendiquée.

## Sécurité Git

`.env`, `data/`, `*.parquet` et `*.sqlite3` sont ignorés. Cette migration retire également `.env` et `data/riseup.sqlite3` de l'index Git sans supprimer les copies locales. Seuls le code, la documentation et de petites fixtures synthétiques doivent être versionnés.

Voir aussi [l'audit look-ahead](docs/lookahead_audit.md).
