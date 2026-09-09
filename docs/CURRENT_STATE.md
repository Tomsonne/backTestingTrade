# RiseUp — Current State

Version application : 2.1.0

## Stable

- Legacy A.0/A.1/B.0/B.1 protégé par golden.
- Research engine riseup-v2.
- M1 UTC BID/ASK canonique.
- Validation STRICT / PERMISSIVE / TRACE.
- Data-quality TRACE persistée dans SQLite.
- API et interface de recherche opérationnelles.
- 82 tests passent lors de la dernière validation TRACE.

## Current data issue

L'historique Dukascopy local contient encore des gaps.

Des HTTP 429 apparaissent pendant certaines réparations historiques.

## Current priority

Fiabiliser le pipeline historique :

- rate limiting ;
- Retry-After ;
- backoff ;
- reprise idempotente ;
- réparation ciblée des gaps.

## Do not optimize yet

Ne pas optimiser les règles de stratégie tant que la qualité
des données historiques longue période n'est pas validée.

## References

Legacy :
docs/LEGACY_GOLDEN.md

Temporal / look-ahead :
docs/LOOKAHEAD_AUDIT.md

TRACE :
docs/TRACE.md