# RiseUp Research Lab — guide pour agents

## Architecture

<<<<<<< HEAD
* `app/backtest.py` : moteur legacy A.0/A.1/B.0/B.1 protégé par le golden.
* `app/research/` : StrategyConfig, presets, moteur V2, analyses et fenêtres de recherche.
* `app/data/` : unique frontière des données marché. M1 UTC BID/ASK est canonique ; les timeframes supérieurs sont dérivés.
* `app/storage.py` : SQLite additif. Un run historique ne doit jamais être remplacé ou supprimé automatiquement.
* `app/api.py` + `app/static/index.html` : application locale research-only.

Les `AGENTS.md` présents dans les sous-répertoires complètent ces règles pour leur périmètre.

Pour l’état courant et le chantier actif, consulter `docs/CURRENT_STATE.md` lorsqu’il existe.

## Travail des agents

* Lire uniquement les fichiers et la documentation pertinents pour la tâche.
* Commencer par le plus petit périmètre raisonnable.
* Ne pas refactorer hors scope.
* N’explorer d’autres modules que si une dépendance réelle l’exige.
* Faire le plus petit changement cohérent possible.
=======
- `app/backtest.py` est le moteur legacy A.0/A.1/B.0/B.1 protégé par le golden.
- `app/research/` contient StrategyConfig, presets, moteur V2, analyses et fenêtres de recherche.
- `app/data/` est l'unique frontière des données de marché. M1 UTC BID/ASK est canonique; les timeframes supérieurs sont dérivés.
- `app/storage.py` gère SQLite de façon additive. Un run historique ne doit jamais être remplacé ou supprimé automatiquement.
- `app/api.py` et `app/static/index.html` forment l'application locale research-only.
>>>>>>> 853804b008cb85b1a2c913966f2c28e9a257535a

## Commandes

```bash
python -m pytest -q
python scripts/verify_legacy_golden.py
python scripts/data_status.py
python scripts/run_research.py --list-presets
AUTO_RUN_ON_STARTUP=false ENABLE_SCHEDULER=false uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Conventions

<<<<<<< HEAD
* Préserver `EUR_USD`, `GBP_USD`, `DXY` et les colonnes BID/ASK/MID documentées.
* Timestamps internes timezone-aware UTC ; sessions via IANA/`zoneinfo`, jamais offset fixe.
* Valider toute configuration avec Pydantic et conserver JSON exact/hash/version dans le run.
* Utiliser `logging` avec `run_id` ; ne pas logger les listes complètes de gaps ou traces.
* Migrations SQLite additives et transactionnelles. Ne jamais effacer un ancien run pour en sauver un nouveau.

## Anti look-ahead et règles trading

* Pivot disponible seulement après ses `right_bars` ; séparer timestamp visuel et timestamp de disponibilité.
* Bougie HTF utilisable seulement après clôture.
* ADR exclut la journée courante.
* Les extrema de session précédente lisent uniquement cette session.
* DXY utilise une borne temporelle exclusive.
* `signal_close` utilise le close d’une bougie déjà terminée au timestamp d’exécution.
* Ne jamais forward-fill un prix, fabriquer une bougie ou fabriquer du volume.
* Long : entrée ASK, sortie BID. Short : entrée BID, sortie ASK.
* TP+SL dans la même M1 : politique explicite `stop_first` ou `tp_first`.
* Ne jamais changer silencieusement une définition legacy.
* Toute nouvelle logique utilise `riseup-v2` et un preset dupliqué si nécessaire.

## Fichiers sensibles

`.env`, `data/`, Parquet, SQLite, exports et credentials restent locaux et ignorés par Git.

Ne jamais les inclure dans une fixture, un log ou un commit.

Les fichiers éventuels sous `sources/` sont en lecture seule.

## Définition de terminé

Une modification est terminée lorsque :

* les tests ciblés et globaux passent ;
* le JavaScript reste valide si l’UI change ;
* le golden passe si le legacy est touché ;
* les erreurs restent visibles ;
* la documentation correspond au comportement ;
* la revue du diff ne révèle ni fuite temporelle, ni mutation destructive, ni secret.
=======
- Préserver les noms canoniques `EUR_USD`, `GBP_USD`, `DXY` et les colonnes BID/ASK/MID documentées.
- Timestamps internes timezone-aware UTC; heures de session via IANA/`zoneinfo`, jamais via offset fixe.
- Valider toute configuration avec Pydantic et conserver le JSON exact/hash/version dans le run.
- Utiliser `logging` avec `run_id`; ne pas imprimer les listes complètes de gaps ou de traces.
- Les migrations SQLite sont additives et transactionnelles. Ne jamais effacer un ancien run pour en sauver un nouveau.

## Anti look-ahead et règles trading

- Un pivot n'est disponible qu'après ses `right_bars`; conserver séparés timestamp visuel et timestamp de disponibilité.
- Une bougie HTF n'est utilisable qu'après sa clôture. ADR doit exclure la journée courante.
- Les extrema de session précédente ne lisent que cette session; DXY utilise une borne temporelle exclusive.
- Une entrée `signal_close` doit employer le close d'une bougie déjà terminée au timestamp d'exécution.
- Ne jamais forward-fill un prix, fabriquer une bougie ou fabriquer du volume.
- BID/ASK : long entre à l'ASK et sort au BID; short entre au BID et sort à l'ASK.
- Le cas TP+SL dans la même M1 reste ambigu et doit conserver une politique explicite `stop_first` ou `tp_first`.
- Ne jamais changer silencieusement une définition legacy. Toute nouvelle logique utilise `riseup-v2` et un preset dupliqué.

## Fichiers sensibles

`.env`, `data/`, Parquet, SQLite, exports et credentials restent locaux et ignorés par Git. Ne jamais les inclure dans une fixture, un log ou un commit. Les fichiers synchronisés éventuels sous `sources/` sont en lecture seule.

## Définition de terminé

Une modification est terminée lorsque les tests ciblés et globaux passent, le JavaScript reste valide si l'UI change, le golden passe si le legacy est touché, les erreurs sont visibles, la documentation correspond au comportement et la revue du diff n'a trouvé ni fuite temporelle, ni mutation destructive, ni secret.
>>>>>>> 853804b008cb85b1a2c913966f2c28e9a257535a
