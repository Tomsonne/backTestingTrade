# Data layer

Les règles du `AGENTS.md` racine restent applicables.

## Scope

* `app/data/` est l’unique frontière des données marché.
* M1 UTC BID/ASK est canonique.
* Les timeframes supérieurs sont dérivés.
* Ne pas modifier la logique trading depuis ce répertoire.

## Invariants

* Timestamps timezone-aware UTC.
* Sessions/timezones via `zoneinfo`, jamais offset fixe.
* Ne jamais forward-fill, interpoler, fabriquer une bougie ou du volume.
* Ne jamais masquer un gap réel.
* Préserver BID/ASK ; MID reste dérivé.
* Une bougie HTF n’est utilisable qu’après clôture.

## Téléchargement / repair

* Téléchargements reprenables, idempotents et déterministes.
* Pour `429`/erreurs réseau : limiter la concurrence, retry + backoff, respecter `Retry-After`.
* Réparer uniquement les plages manquantes.
* Valider avant écriture et revérifier après réparation.
* Ne jamais supprimer un historique valide pour masquer une réparation incomplète.

## Performance / logs

* Éviter scans complets, re-downloads inutiles et logs massifs.
* Préférer fenêtres, cache et résumés de gaps.
* Ne jamais logger secrets, datasets complets ou listes massives de timestamps.

## Validation

Tester selon le changement :

* timezone/DST ;
* BID/ASK ;
* agrégation ;
* gaps/week-ends ;
* doublons ;
* reprise/retry.

Faire le plus petit changement cohérent possible.
