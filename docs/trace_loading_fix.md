# Correctif ciblé TRACE — chargement et couverture

## ROOT CAUSE

Run analysé : `861f2305-a20d-4ecb-a10c-f62396a14073`, dans la base de `/Users/thomasrousseau/Desktop/trade`.

La configuration réellement enregistrée contient `validation.mode = "strict"` et l'application est en version `2.0.0`. Cette copie ne possédait ni TRACE dans `DataValidationConfig`, ni option TRACE dans son sélecteur, ni `app/data/gaps.py`. Elle différait de la copie du workspace Codex où TRACE avait été développé.

L'ancien `DukascopyProvider.get()` n'appelait **pas** `raise_for_errors()` inconditionnellement. Il le faisait sous `if self.validation_mode == "strict"`. Le constructeur recevait sa policy de `Settings` via `create_provider()`. Le moteur V2 recopiait aussi `config.validation.mode` dans `provider.validation_mode` avant le `get()`. Pour ce run, cette valeur était STRICT : l'exception de la ligne 303 était donc conforme à la configuration persistée. Le traceback seul ne prouve pas que TRACE avait atteint le provider.

Le serveur trouvé sur 8001 utilisait encore le workspace Codex (PID 58794). Il a été arrêté puis remplacé par un processus démarré dans Desktop/trade. Vérification en lecture seule : PID 82833, cwd Desktop/trade; `/api/config` expose `app_version=2.0.1-trace-loading`, `code_root=/Users/thomasrousseau/Desktop/trade` et les trois policies.

## OLD FLOW

TRACE attendu par l'utilisateur → copie Desktop sans prise en charge TRACE → configuration persistée STRICT → runtime settings STRICT → provider.validation_mode STRICT → provider.get() → report.raise_for_errors() → DataCoverageError → FAILED.

## NEW FLOW

Sélecteur TRACE → JSON `validation.mode="trace"` validé → wrapper de chargement propre au run créé **avant tout chargement** → DukascopyProvider.get(..., validation_policy="trace") → rapport de validation exposé dans `validation_reports[symbol]` → contrôle d'intégrité bloquant, couverture non bloquante → catalogue créé → vraies données retournées → moteur inchangé.

La policy explicite par appel ne modifie pas le mode par défaut d'un provider partagé. Le wrapper copie les attributs de configuration du provider pour isoler les runs. Les sources EUR_USD, GBP_USD, DXY et les six composants du DXY synthétique empruntent le même contrôle. Une DataIntegrityError du DXY direct ne déclenche pas le fallback réservé aux DataCoverageError.

- STRICT : erreurs d'intégrité → DataIntegrityError; minutes manquantes → DataCoverageError.
- TRACE : erreurs d'intégrité → DataIntegrityError; minutes manquantes → rapport/catalogue et poursuite.
- PERMISSIVE : comportement historique warning-only conservé.
- Policy inconnue : InvalidValidationPolicy, jamais d'acceptation silencieuse.

Les contrôles existants d'OHLC, valeurs, doublons et ordre sont conservés. Les timestamps NaT sont explicitement refusés. Aucun remplissage, aucune interpolation ni bougie inventée. Les fermetures prévues par le calendrier et les jours fériés locaux sont exclus des minutes de marché manquantes.

## FILES MODIFIED

- `app/data/base.py` : types d'erreurs distincts.
- `app/data/validation.py` : policies explicites, contrôle non-coverage, compteurs et bornes de couverture, contexte de log du run.
- `app/data/dukascopy.py` : policy explicite par appel, rapport conservé et logs.
- `app/data/gaps.py` : nouveau wrapper de chargement, catalogue et ventilation des absences.
- `app/config.py`, `app/research/config.py` : valeur TRACE autorisée.
- `app/research/engine.py` : configuration du chargement avant tout get, rapport et statistiques DATA/coverage.
- `app/jobs.py` : version et logs corrélés au run.
- `app/main.py` : identité du processus au démarrage et logs INFO.
- `app/api.py` : identité de la copie chargée dans /api/config.
- `app/static/index.html` : choix TRACE et panneau de couverture dans le détail du run.
- `pytest.ini`, `tests/test_trace_loading.py` : collecte should_* et tests.
- `README.md`, ce document : usage et preuves.

Aucun changement des fonctions de divergence, sessions, zones, triggers, entrées, SL/TP, risque ou R. La comparaison AST du moteur avant/après ne montre qu'une modification de la fonction préexistante `run_research_backtest` (chargement et rattachement du rapport). Les fichiers backtest.py, dxy.py, sessions.py, zones.py, m1_divergence.py, indicators.py et research/analysis.py sont inchangés octet pour octet. Les données de marché, secrets et anciens runs ne sont pas remplacés.

## TESTS

Tests demandés présents :

- `should_return_available_data_when_trace_has_missing_m1_candles` (EUR_USD, GBP_USD, DXY, AUD_USD).
- `should_raise_data_coverage_error_when_strict_has_missing_m1_candles`.
- `should_continue_research_engine_when_trace_provider_reports_gaps` (vrai DukascopyProvider.get et validateur, seule la lecture du cache est substituée par le dataset de test).

Autres cas : sept corruptions, permissif préservé, policy inconnue, mois absents, cache vide, composants DXY synthétiques, DXY direct corrompu sans fallback, égalité de tous les champs des trades sans gap et validation du schéma.

Suite Desktop complète : **64 passed, 0 failed** (43 préexistants + 21 cas ajoutés). Un avertissement de dépréciation Starlette/httpx préexistant. Syntaxe JavaScript vérifiée; git diff --check sans erreur.

## REAL RUN

Contrôle complémentaire du moteur sur le **vrai cache Desktop** et la configuration exacte du run en échec, avec pour seule modification `validation.mode="trace"` : terminé en 1,413 seconde, 1 trade. Exécution en mémoire, aucun nouveau run enregistré par ce contrôle.

Logs observés :

```text
[run=backend-check-861f2305] [data=EUR_USD] Validation policy: TRACE; available=28704 missing=468696 coverage=5.7708%
[run=backend-check-861f2305] [data=EUR_USD] Coverage validation: NON-BLOCKING; missing coverage accepted by TRACE
[run=backend-check-861f2305] [data=EUR_USD] Gap catalogue: created; gaps=135 missing=468696
[run=backend-check-861f2305] [data=EUR_USD] Backtest: continued past data loading
[run=backend-check-861f2305] [data=DXY] Validation policy: TRACE; available=423501 missing=9500 coverage=97.8060%
BACKEND CHECK CALCULATING_STATISTICS 88%
```

DataCoverageError : NO. Passage du chargement : YES. Fin du moteur : COMPLETED. **Ce contrôle ne remplace pas la recette demandée par lancement dans l'interface.** À cette étape, aucun navigateur de contrôle n'est connecté (`agent.browsers.list()` retourne `[]`). La relance via l'interface reste à confirmer; la correction n'est pas déclarée entièrement terminée tant que cette confirmation manque.

## COVERAGE

EUR_USD, UTC, fin demandée exclusive :

| Mesure | Valeur |
| --- | --- |
| Requested start | 2025-04-27 22:00 UTC |
| Requested end | 2026-08-25 22:00 UTC |
| First available candle | 2026-02-01 22:00 UTC |
| Last available candle | 2026-02-27 21:59 UTC |
| Expected market minutes | 497 400 |
| Available market minutes | 28 704 |
| Missing market minutes | 468 696 |
| Coverage | 5,770808 % |

Le manifeste du cache confirme exactement ces bornes disponibles et 28 704 lignes : **EUR_USD ne possède que février 2026 dans cette copie**. Ce n'est pas le même historique que celui analysé dans le workspace Codex.

Décomposition exacte des minutes manquantes :

- Avant la première bougie du cache : **285 660** minutes.
- Après la dernière bougie du cache : **182 940** minutes.
- Petits trous internes (≤ 5 minutes) : **96** minutes, **67 événements**.
- Total : **468 696** minutes.

Le catalogue contient 135 intervalles physiques. Plus grand intervalle continu d'ouverture absent : **7 200 minutes**, du 4 mai 2025 à 21:00 au 9 mai 2025 à 20:59 UTC. Les week-ends découpent naturellement les absences de plusieurs mois en intervalles d'environ une semaine; ce maximum ne signifie pas que seulement une semaine manque.

393 portions de jours UTC et 66 portions de semaines demandées sont sans bougie. Mois complets intérieurs absents : **mai 2025 à janvier 2026**, puis **mars à juillet 2026**. Les portions demandées d'avril 2025 et d'août 2026 sont également entièrement absentes. Les listes détaillées dans le rapport portent explicitement sur la portion demandée de chaque période, pas sur ses parties hors requête.

DXY direct : 423 501 minutes disponibles, 9 500 manquantes, couverture 97,8060 %. Le run équivalent ne charge pas GBP_USD car sa configuration ne demande que EUR_USD; GBP_USD et les autres symboles sont couverts par les tests du provider.

### Limites conservées

Ce correctif laisse la stratégie décider sur ses données disponibles et n'ajoute aucun setup dans les mois sans données. Il ne modifie pas les règles DXY de rejet ni le modèle de sortie des positions en présence de gaps. Le rapport de couverture ne certifie donc ni la causalité des règles historiques ni la fiabilité des performances sur 5,77 % de la période. Les contrôles de données corrompues et les erreurs de format restent bloquants en TRACE.
