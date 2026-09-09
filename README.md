# RiseUp Trading Research Lab V2

RiseUp est un laboratoire local de recherche quantitative Forex permettant de configurer, exécuter, expliquer, conserver et comparer des backtests.

Le projet utilise principalement :

* données M1 Dukascopy BID/ASK ;
* moteur legacy A.0/A.1/B.0/B.1 protégé par golden ;
* moteur configurable `riseup-v2` ;
* SQLite pour l'historique des runs ;
* FastAPI + interface web locale ;
* validation stricte des données et règles anti look-ahead.

Aucune exécution réelle d'ordres n'est réalisée.

## Quick start

Python 3.12 est recommandé.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configuration recommandée :

```env
DATA_PROVIDER=dukascopy
DXY_SOURCE=dukascopy_direct
DATA_VALIDATION_MODE=strict
EXECUTION_PRICE_MODE=bid_ask
VOLUME_MODE=legacy_no_volume
```

Télécharger les données nécessaires :

```bash
python scripts/download_history.py \
  --all-required \
  --from 2024-01-01 \
  --to 2026-08-25
```

Vérifier la couverture locale :

```bash
python scripts/data_status.py
```

Lancer l'application :

```bash
AUTO_RUN_ON_STARTUP=false ENABLE_SCHEDULER=false \
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Puis ouvrir :

```text
http://127.0.0.1:8000
```

## Architecture

```text
Dukascopy M1 BID/ASK
        ↓
app/data/
validation + cache Parquet
        ↓
resampling M5 / H1 / H2 / H4
        ↓
┌───────────────────┬─────────────────────┐
│ Legacy A/B        │ Research riseup-v2  │
│ app/backtest.py   │ app/research/       │
└───────────────────┴─────────────────────┘
        ↓
SQLite
        ↓
FastAPI + interface web
```

Principaux composants :

* `app/data/` : providers, cache, validation, gaps et resampling ;
* `app/backtest.py` : moteur legacy protégé ;
* `app/research/config.py` : `StrategyConfig` Pydantic et hash reproductible ;
* `app/research/engine.py` : moteur configurable V2 ;
* `app/research/analysis.py` : statistiques et analyses ;
* `app/storage.py` : stockage SQLite additif ;
* `app/api.py` : API locale ;
* `app/static/index.html` : interface web ;
* `scripts/` : téléchargement, validation, CLI et outils de diagnostic.

## Legacy et Research V2

Les variantes historiques :

* A.0
* A.1
* B.0
* B.1

utilisent le moteur legacy exact.

Leur comportement ne doit pas être modifié silencieusement.

Les nouvelles expériences utilisent `riseup-v2` avec une configuration versionnée et reproductible.

Chaque run conserve notamment :

* configuration JSON ;
* hash ;
* version stratégie/application ;
* provider ;
* paramètres d'exécution ;
* timestamps ;
* résultats ;
* traces et rejets lorsque disponibles.

## StrategyConfig

Le moteur V2 permet de configurer notamment :

* paires ;
* sessions ;
* divergence DXY ;
* zones H1/H2/H4 ;
* triggers M1/M5 ;
* labels et indicateurs ;
* SL/TP ;
* risque ;
* position unique ;
* priorités de paire ;
* spread/slippage ;
* politique intrabar ;
* validation des données.

Les presets legacy sont immuables.

Une nouvelle définition de stratégie doit utiliser un preset V2 distinct.

## Données marché

La source canonique persistée est :

```text
M1 UTC BID/ASK
```

Les timeframes supérieurs sont dérivés du M1.

Schéma principal :

```text
bid_o bid_h bid_l bid_c
ask_o ask_h ask_l ask_c
mid_o mid_h mid_l mid_c
bid_volume ask_volume volume
```

`mid_*` est dérivé de BID/ASK.

Aucune minute manquante ne doit être :

* forward-fill ;
* interpolée ;
* reconstruite ;
* fabriquée.

Les données historiques locales sont conservées sous `data/` et ne sont pas versionnées.

## BID / ASK

Avec :

```env
EXECUTION_PRICE_MODE=bid_ask
```

les règles sont :

```text
LONG
entrée : ASK
sortie : BID

SHORT
entrée : BID
sortie : ASK
```

Le mode `mid` reste disponible pour les comparaisons historiques explicites.

## DXY

Deux sources sont disponibles :

```text
dukascopy_direct
synthetic
```

Le DXY direct utilise le symbole Dukascopy correspondant.

Le fallback synthétique conserve la formule historique basée sur les composants Forex nécessaires.

La source réellement utilisée est enregistrée dans le run.

## Sessions et temps

Les timestamps internes sont timezone-aware UTC.

Les sessions utilisent `zoneinfo` et une timezone IANA telle que :

```text
Europe/Paris
```

Les changements DST sont donc gérés sans offset UTC fixe.

M5/H1/H2/H4 sont dérivés des M1 et ne deviennent utilisables qu'après clôture.

## Validation des données

Trois politiques existent :

### STRICT

```text
strict
```

Toute minute ouverte manquante ou donnée invalide bloquante interrompt le backtest.

C'est le mode recommandé pour les résultats officiels.

### TRACE

```text
trace
```

Permet une recherche avec gaps documentés sans fabriquer de prix.

TRACE distingue :

* `COMPLETE`
* `DEGRADED`
* `GAP_RESOLVED`
* `INDETERMINATE`

Voir :

[`docs/TRACE.md`](docs/TRACE.md)

### PERMISSIVE

```text
permissive
```

Mode conservé pour compatibilité avec les anciennes expériences.

Dans Research V2, `permissive_gap_limit_minutes` vaut `5` par défaut et reste configurable. La limite est cumulative par paire sur la fenêtre validée ; le run bloque si elle est dépassée.

Il ne fournit pas la même traçabilité détaillée que TRACE.

## CLI Research

Lister les presets :

```bash
python scripts/run_research.py --list-presets
```

Lancer un preset :

```bash
python scripts/run_research.py \
  --preset "Legacy A.0" \
  --from 2026-02-01 \
  --to 2026-08-25 \
  --name "Reference A.0"
```

Lancer avec TRACE :

```bash
python scripts/run_research.py \
  --preset "Legacy A.0" \
  --from 2026-02-01 \
  --to 2026-08-25 \
  --validation trace \
  --name "Recherche TRACE"
```

Lancer une configuration JSON :

```bash
python scripts/run_research.py --config strategy.json
```

## Import Dukascopy manuel

Les exports BID et ASK peuvent être importés :

```bash
python scripts/import_dukascopy_csv.py \
  path/to/EUR-USD_1_Minute_BID_UTC.csv \
  path/to/EUR-USD_1_Minute_ASK_UTC.csv
```

Les données importées alimentent le même cache canonique que le downloader.

## Tests

Suite principale :

```bash
python -m pytest -q
```

Golden legacy :

```bash
python scripts/verify_legacy_golden.py
```

Le golden protège l'identité économique des variantes A.0/A.1/B.0/B.1.

Référence complète :

[`docs/LEGACY_GOLDEN.md`](docs/LEGACY_GOLDEN.md)

## Anti look-ahead

Le moteur applique notamment les principes suivants :

* pivot disponible seulement après confirmation ;
* bougie HTF utilisable seulement après clôture ;
* ADR sans journée courante ;
* cutoff DXY causal ;
* entrée `signal_close` basée sur une bougie terminée ;
* décisions de risque évaluées chronologiquement ;
* aucune reconstruction de prix manquant.

Audit détaillé :

[`docs/LOOKAHEAD_AUDIT.md`](docs/LOOKAHEAD_AUDIT.md)

## API

FastAPI expose sa documentation locale sur :

```text
/docs
```

Les routes principales couvrent :

* configuration ;
* presets ;
* création et lecture des backtests ;
* trades et setups rejetés ;
* statistiques ;
* qualité des données ;
* comparaison ;
* exports ;
* analyses de recherche.

## Twelve Data

Twelve Data reste disponible comme fallback de migration :

```env
DATA_PROVIDER=twelvedata
TWELVE_DATA_API_KEY=...
DXY_SOURCE=synthetic
EXECUTION_PRICE_MODE=mid
```

Le moteur de stratégie reste indépendant du provider.

## Sécurité Git

Ne jamais versionner :

```text
.env
data/
*.parquet
*.sqlite3
exports locaux
credentials
```

Seuls le code, la documentation et les petites fixtures de test doivent être présents dans Git.

## Documentation

Pour éviter de charger inutilement tout le contexte du projet, consulter uniquement la documentation pertinente :

| Besoin                      | Document                                             |
| --------------------------- | ---------------------------------------------------- |
| État actuel du projet       | [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md)     |
| Index documentation         | [`docs/README.md`](docs/README.md)                   |
| Golden legacy               | [`docs/LEGACY_GOLDEN.md`](docs/LEGACY_GOLDEN.md)     |
| Audit temporel / look-ahead | [`docs/LOOKAHEAD_AUDIT.md`](docs/LOOKAHEAD_AUDIT.md) |
| Politique TRACE             | [`docs/TRACE.md`](docs/TRACE.md)                     |
| Validations historiques     | [`docs/validations/`](docs/validations/)             |

## Limites importantes

* Les backtests utilisent des OHLC M1, pas des ticks.
* L'ordre TP/SL dans une même M1 reste une hypothèse explicite.
* Dukascopy et TradingView peuvent différer par feed, timezone, DXY et construction des bougies.
* Le Forex n'a pas de volume centralisé ; les volumes Dukascopy représentent l'activité de leur feed.
* Les analyses de robustesse et d'overfitting sont des outils de recherche, pas des garanties prédictives.
