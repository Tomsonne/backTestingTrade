# TRACE — validation du 8 septembre 2026

Ce document conserve la validation historique de l'implémentation TRACE.

Il ne définit pas le comportement actuel de TRACE.

La spécification opérationnelle est :

`docs/TRACE.md`

## Contexte

Validation réalisée localement le 8 septembre 2026.

Configuration principale :

* Legacy A.0
* période : 1er février → 25 août 2026 inclus
* validation : TRACE
* prix : BID/ASK
* DXY : direct
* application : 2.1.0

Aucun ancien run FAILED n'a été supprimé.

## Runs

Run initial :

`c1490ebd-d446-4107-a69a-0cf5889769f4`

Durée approximative :

`22 s`

Run final après les améliorations de traçabilité :

`bb118db8-630c-44c0-9a8c-147929f4f117`

Nom :

`TRACE recette finale A.0 2026-02–08`

Durée :

`22,67 s`

Les deux runs ont été conservés.

Aucune erreur d'intégrité référentielle SQLite n'a été détectée.

## Données manquantes observées

| Symbole   | M1 manquantes | Trous physiques |
| --------- | ------------: | --------------: |
| EUR_USD   |           908 |             702 |
| GBP_USD   |         1 016 |             712 |
| DXY       |         4 634 |           3 348 |
| **Total** |     **6 558** |       **4 762** |

## Résultats

75 candidats legacy A.0 ont été évalués.

Les 75 candidats sont dégradés.

56 trades :

* 35 `DEGRADED`
* 21 `GAP_RESOLVED`
* 0 `COMPLETE`
* 0 `INDETERMINATE`

Les positions traversent 60 événements `ASSUMED_NO_EXIT`.

Aucun TP ou SL n'a été inféré dans ce run historique.

## Performances

| Mesure                   |         ALL |          CLEAN |
| ------------------------ | ----------: | -------------: |
| Trades déterminés        |          56 |              0 |
| Wins / losses / timeouts | 20 / 33 / 3 |              — |
| Win rate hors timeouts   |     37,74 % | non calculable |
| Total R                  |       +7,87 |    aucun trade |
| Espérance                |   +0,1405 R | non calculable |
| Profit factor            |      1,2352 | non calculable |
| Rendement composé        |    +17,98 % |    aucun trade |
| Drawdown réalisé maximal |     10,68 % |    aucun trade |

Le résultat CLEAN vide ne signifie pas que la performance est nulle.

Aucun trade ne satisfait ici l'exigence `COMPLETE` avec les dépendances récursives conservatrices utilisées par TRACE.

## Exemple historique réel

EUR_USD BUY.

Entrée :

`25 mars 2026 à 22:13 UTC`

Gap :

`26 mars 2026 à 04:38 UTC`

Première M1 disponible :

`04:39 UTC`

BID OPEN :

`1,15701`

Résolution du gap :

`ASSUMED_NO_EXIT`

Le SL est ensuite réellement observé à :

`11:28 UTC`

Résultat :

`-1 R`

Le gap et la perte observée sont conservés comme événements distincts.

## Cas synthétiques de tests

Configuration BUY :

* entrée : `1,1002`
* SL : `1,0987`
* TP : `1,1032`
* M1 manquantes : `07:01–07:02 UTC`

Première bougie réelle simulée à `07:03 UTC`.

### TP inféré

BID OPEN :

`1,1040`

Résultat :

`TP_INFERRED (+2 R)`

### SL inféré

BID OPEN :

`1,0980`

Résultat :

`SL_INFERRED (-1 R)`

### Aucune sortie inférée

BID OPEN :

`1,1002`

Résultat :

`ASSUMED_NO_EXIT`

Les cas SELL symétriques ainsi que les deux politiques same-bar sont également testés.

## Tests

Avant TRACE :

`43 tests`

Nouveaux cas TRACE :

`39`

Suite complète :

`82 tests passent`

Les nouveaux tests couvrent notamment :

* BUY / SELL
* MID / BID / ASK
* coûts
* priorité de l'OPEN après gap
* gaps multiples
* fermetures de marché
* absence de prochaine observation
* session
* DXY
* HTF
* indicateurs
* non-contamination hors contexte
* absence de reconstruction
* données invalides
* ALL / CLEAN
* migration SQLite
* API
* exports
* égalité économique sans gap

Un avertissement préexistant de dépréciation Starlette/httpx reste présent.

## Golden legacy

La validation TRACE n'a pas modifié le résultat économique legacy.

Référence :

* setups : `235`
* trades : `181`

Digest :

`fa831ede4d255aa410c99f9d09ce5fe9f8c9caa015058b25db9543cea717555e`

La référence complète est documentée dans :

`docs/LEGACY_GOLDEN.md`

## Vérification UI

Le navigateur a vérifié :

* panneau de qualité ;
* comparaison ALL/CLEAN ;
* filtre CLEAN vide ;
* 21 lignes `GAP_RESOLVED` ;
* détail d'un événement réel.

La vérification a également conduit à :

* regrouper les nombreux gaps de contexte dans un volet repliable ;
* afficher les gaps de gestion en premier ;
* protéger le filtre contre l'affichage d'une réponse périmée.

## Conclusion de la validation

Au 8 septembre 2026 :

* TRACE fonctionne sans reconstruire les M1 absentes ;
* les migrations SQLite restent additives ;
* les anciens runs sont conservés ;
* le golden legacy reste identique ;
* les cas hypothétiques sont distingués des observations réelles ;
* la suite complète passe avec 82 tests.

Cette validation est historique.

Toute évolution ultérieure de TRACE doit être comparée à la spécification actuelle dans `docs/TRACE.md` et aux tests du dépôt.
