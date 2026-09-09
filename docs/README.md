# Documentation RiseUp

Ne lire que les documents nécessaires à la tâche.

| Besoin | Document |
|---|---|
| État actuel et chantier actif | `CURRENT_STATE.md` |
| Régression moteur legacy | `LEGACY_GOLDEN.md` |
| Causalité / look-ahead / timestamps | `LOOKAHEAD_AUDIT.md` |
| Gaps / TRACE / data quality | `TRACE.md` |
| Preuves et recettes historiques | `validations/` |

## Règle pour agents

Ne pas charger tous les documents par défaut.

- tâche data simple → commencer par `app/data/AGENTS.md`
- tâche research/stratégie → commencer par `app/research/AGENTS.md`
- changement legacy → lire `LEGACY_GOLDEN.md`
- changement temporel → lire `LOOKAHEAD_AUDIT.md`
- changement TRACE/gaps → lire `TRACE.md`
- investigation d’une ancienne régression → seulement alors consulter `validations/`