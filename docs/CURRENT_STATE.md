# RiseUp — Current State

Last verified: 2026-09-10

Application version: `2.1.0`

## Stable

- Legacy A.0/A.1/B.0/B.1 protégé par golden.
- Research engine `riseup-v2`.
- M1 UTC BID/ASK canonique.
- Validation `strict`, `trace`, `permissive`.
- Data-quality TRACE persistée dans SQLite.
- API et interface research opérationnelles.
- Documentation séparée par responsabilité.

## Last validation

```text
pytest:
82 passed
1 warning Starlette/httpx préexistant
```

Golden legacy :

```text
status: PASS
candidate_count: 235
trade_count: 181
digest:
fa831ede4d255aa410c99f9d09ce5fe9f8c9caa015058b25db9543cea717555e
differences: []
```

## Current data issue

Le cache Dukascopy contient encore des gaps historiques.

Des HTTP 429 peuvent apparaître pendant certaines réparations longues.

## Current priority

Fiabiliser le pipeline historique :

- rate limiting ;
- `Retry-After` ;
- exponential backoff ;
- reprise idempotente ;
- réparation ciblée des gaps ;
- validation après réparation.

## Do not optimize yet

Ne pas optimiser les règles de stratégie tant que la qualité des données historiques longue période n’est pas validée.

## References

- legacy → `docs/LEGACY_GOLDEN.md`
- causalité → `docs/LOOKAHEAD_AUDIT.md`
- TRACE → `docs/TRACE.md`