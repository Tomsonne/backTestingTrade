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