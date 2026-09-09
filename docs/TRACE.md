# TRACE — politique de données manquantes

TRACE permet d'exécuter un backtest en présence de M1 manquantes sans reconstruire, interpoler ou fabriquer le chemin des prix.

STRICT reste la validation par défaut. PERMISSIVE conserve sa compatibilité historique.

TRACE ne télécharge ni ne répare automatiquement les données.

## Utilisation

UI :

`New Backtest → Execution & Validation → Validation → TRACE`

CLI :

```bash
python scripts/run_research.py --validation trace
```

Lancer un nouveau run après changement de politique de validation.

## Principe

TRACE distingue les données réellement observées des résultats dépendant d'un gap.

Statuts :

| Statut          | Signification                                                                 |
| --------------- | ----------------------------------------------------------------------------- |
| `COMPLETE`      | Aucun gap identifié dans les dépendances suivies                              |
| `DEGRADED`      | Contexte incomplet mais calculable, sans hypothèse de sortie dans un gap      |
| `GAP_RESOLVED`  | Un gap de gestion a nécessité une résolution explicite                        |
| `INDETERMINATE` | Contexte indispensable absent ou position sans observation future exploitable |

Un résultat `GAP_RESOLVED` reste hypothétique.

TRACE est une politique de recherche, pas une reconstruction tick-by-tick du marché.

## Invariants

* Toutes les bornes sont UTC.
* Aucune M1 manquante n'est forward-fillée.
* Aucun prix ou volume n'est inventé.
* Aucun gap n'est supprimé silencieusement.
* Les fermetures de marché attendues ne sont pas considérées comme des gaps.
* Un gap ne dégrade que les dépendances temporelles réellement utilisées.
* Un gap postérieur à l'entrée ne dégrade pas rétroactivement le setup.
* Les gaps rencontrés pendant une position relèvent de la gestion du trade.

## Architecture

| Fichier                         | Rôle                                              |
| ------------------------------- | ------------------------------------------------- |
| `app/data/gaps.py`              | Catalogue et intersections des gaps               |
| `app/data/dukascopy.py`         | Validation des données et politiques STRICT/TRACE |
| `app/research/data_quality.py`  | Dépendances, statuts et rapport qualité           |
| `app/research/gap_execution.py` | Résolution des positions après un gap             |
| `app/research/engine.py`        | Intégration V2                                    |
| `app/backtest.py`               | Intégration legacy                                |
| `app/research/analysis.py`      | Cohortes ALL/CLEAN                                |
| `app/storage.py`                | Persistance additive                              |
| `app/api.py`                    | API data-quality                                  |
| `app/static/index.html`         | Interface et détails qualité                      |

## Résolution d'une position après un gap

La méthode est :

`NEXT_AVAILABLE_CANDLE`

La première vraie bougie disponible après le gap est utilisée comme première observation.

### BUY

L'exécution de sortie utilise le BID.

* OPEN ≥ TP → `TP_INFERRED`
* OPEN ≤ SL → `SL_INFERRED`
* sinon → `ASSUMED_NO_EXIT`

### SELL

L'exécution de sortie utilise l'ASK.

* OPEN ≤ TP → `TP_INFERRED`
* OPEN ≥ SL → `SL_INFERRED`
* sinon → `ASSUMED_NO_EXIT`

L'OPEN de reprise est prioritaire sur le high/low de cette même bougie pour déterminer une éventuelle sortie inférée.

Une sortie inférée est horodatée à la première observation réelle disponible, jamais à une heure fictive située dans le gap.

Après `ASSUMED_NO_EXIT`, le moteur continue avec les vraies bougies disponibles et les règles OHLC habituelles.

Si aucune observation suivante n'existe dans l'horizon chargé :

`INDETERMINATE`

Dans ce cas, `r_multiple`, `exit_price`, `exit_time` et le rendement ne sont pas fabriqués.

Une nouvelle position ne doit pas être ouverte tant que la fermeture de la position précédente reste indéterminée.

## Dépendances

Les gaps peuvent affecter notamment :

* `SESSION_OPEN`
* `SESSION_LIQUIDITY`
* `HTF_ZONE`
* `DXY_DIVERGENCE`
* `TRIGGER`
* `LABEL_CONFIRMATION`
* `INDICATOR`
* `ENTRY`
* `TRADE_MANAGEMENT`
* `STOP_LOSS`
* `TAKE_PROFIT`
* `WARMUP`
* `OTHER`

Les pivots, EMA/RMA, OBV et certaines zones ont une mémoire historique. TRACE peut donc conserver une attribution prudente à des gaps anciens lorsqu'une condition activée dépend de cet historique.

Les timeframes supérieurs continuent d'être calculés à partir des M1 réellement présentes. TRACE signale les dépendances incomplètes mais ne complète jamais artificiellement les bougies HTF.

## DXY

Lorsque DXY est activé, TRACE suit les gaps correspondant aux fenêtres DXY réellement consultées.

Lorsque DXY est désactivé, ses gaps ne doivent pas contaminer la qualité du setup.

Le DXY synthétique historique conserve son comportement existant et n'est pas considéré comme un équivalent tick-by-tick du DXY direct.

## SQLite

Les migrations TRACE sont additives et idempotentes.

Tables :

* `run_data_gaps`
* `trade_data_quality`
* `setup_data_quality`
* `missing_data_events`

Aucun ancien run, ancienne table ou fichier de prix ne doit être supprimé ou remplacé automatiquement.

La politique enregistrée est :

`TRACE_NEXT_AVAILABLE_CANDLE_V1`

## API et exports

API :

* `GET /api/backtests/{id}/data-quality`
* `GET /api/backtests/{id}/trades?quality=...`
* `GET /api/backtests/{id}/stats`

Filtres qualité :

* `ALL`
* `CLEAN`
* `COMPLETE`
* `DEGRADED`
* `GAP_RESOLVED`
* `INDETERMINATE`

Exports :

* `trades.csv`
* `rejected.csv`
* `stats.csv`
* `gaps.csv`
* `quality.json`
* `run.json`
* `config.json`

## ALL et CLEAN

`ALL` utilise les trades déterminés et exclut les `INDETERMINATE` des performances numériques.

`CLEAN` ne conserve que les trades `COMPLETE`.

CLEAN n'est pas un nouveau backtest : le moteur ne recherche pas de nouveaux trades après suppression des trades dégradés.

## Limites

TRACE ne prouve pas qu'un trade aurait eu exactement le même résultat en présence des données manquantes.

`TP_INFERRED`, `SL_INFERRED` et `ASSUMED_NO_EXIT` sont des conventions de recherche explicites.

TRACE ne certifie pas à lui seul l'absence globale de look-ahead.

Pour les règles de causalité et de disponibilité temporelle, consulter :

`docs/LOOKAHEAD_AUDIT.md`

Les validations historiques de TRACE sont conservées dans :

`docs/validations/`
