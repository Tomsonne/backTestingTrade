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
109 passed
1 warning Starlette/httpx préexistant
```

Golden legacy : dernière vérification précédente, non relancé pour ce chantier data.

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

Déjà présent :

- retries bornés et exponential backoff ;
- `Retry-After` en secondes ou date HTTP, avec délai partagé entre les workers du même client ;
- une seule requête HTTP en vol par client, espacée d'au moins une seconde par défaut (`--request-interval`, configuration validée par Pydantic) ;
- réparation ciblée via `--repair-gaps`, avec lecture des Parquet par mois, indépendamment de `completed_days` ;
- ajout des seules M1 absentes dans la fenêtre demandée, sans remplacer les observations existantes, même avec `--force` ;
- validation des observations avant écriture, contrôle des gaps après réparation et sortie CLI non nulle si des gaps persistent ;
- reprise depuis les bougies réellement persistées et remise en cohérence du manifeste après interruption d'une réparation ;
- logs résumés ; `gap_report.py --details` ou `--json` pour les détails explicites.

Exemple de réparation (bornes UTC ; une date `--to` inclut la journée entière) :

```bash
python scripts/download_history.py --symbols EUR_USD --from 2024-01-02 --to 2024-01-03 --repair-gaps
```

L'endpoint historique utilisé renvoie une journée par côté BID/ASK : seules les journées touchées sont téléchargées, puis filtrées aux minutes absentes. Les fermetures prévues et jours fériés des métadonnées ne sont pas réparés comme des gaps.

Ces comportements sont vérifiés par tests simulés ; aucune réparation du cache historique local ni mesure réelle de baisse des 429 n'a été effectuée pendant ce chantier.

Travail restant / à améliorer :

- mesurer le débit et les 429 sur une réparation réelle bornée, puis ajuster l'intervalle si nécessaire ;
- réparer et valider les gaps historiques encore présents ;
- éviter plusieurs téléchargements simultanés dans des processus distincts : le limiteur et les écritures du cache ne sont pas coordonnés entre processus.

## Do not optimize yet

Ne pas optimiser les règles de stratégie tant que la qualité des données historiques longue période n’est pas validée.

## References

- legacy → `docs/LEGACY_GOLDEN.md`
- causalité → `docs/LOOKAHEAD_AUDIT.md`
- TRACE → `docs/TRACE.md`
