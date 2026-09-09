# RiseUp Research Lab — guide pour agents

## Architecture

- `app/backtest.py` : moteur legacy A.0/A.1/B.0/B.1 protégé par golden.
- `app/research/` : `StrategyConfig`, presets et moteur `riseup-v2`.
- `app/data/` : unique frontière des données marché ; M1 UTC BID/ASK est canonique.
- `app/storage.py` : SQLite additif ; aucun run historique ne doit être remplacé automatiquement.
- `app/api.py` + `app/static/index.html` : application locale research-only.

Les `AGENTS.md` des sous-répertoires complètent ces règles.

## Travail des agents

- Lire uniquement les fichiers et documents nécessaires à la tâche.
- Commencer par le plus petit périmètre raisonnable.
- Ne pas refactorer hors scope.
- N’explorer d’autres modules que si une dépendance réelle l’exige.
- Faire le plus petit changement cohérent possible.
- Ne pas charger une validation historique sauf si elle est utile à une régression.

## Documentation ciblée

Lire uniquement selon la tâche :

- état actuel / chantier actif → `docs/CURRENT_STATE.md`
- legacy / golden → `docs/LEGACY_GOLDEN.md`
- causalité / look-ahead → `docs/LOOKAHEAD_AUDIT.md`
- gaps / TRACE / data quality → `docs/TRACE.md`
- preuves historiques → `docs/validations/`

## Invariants

- Préserver `EUR_USD`, `GBP_USD`, `DXY`.
- Préserver BID/ASK/MID documentés.
- Timestamps internes timezone-aware UTC.
- Sessions via IANA/`zoneinfo`, jamais offset fixe.
- Toute configuration influençant les résultats passe par Pydantic.
- Conserver JSON exact, hash et version du run.
- Migrations SQLite additives et transactionnelles.
- Ne jamais effacer un ancien run pour en sauver un nouveau.
- Ne jamais forward-fill ou fabriquer prix, bougie ou volume.

## Trading / causalité

- Pivot disponible seulement après ses `right_bars`.
- Bougie HTF utilisable seulement après clôture.
- ADR exclut la journée courante.
- DXY utilise une borne temporelle exclusive.
- `signal_close` utilise une bougie déjà terminée.
- Long : entrée ASK, sortie BID.
- Short : entrée BID, sortie ASK.
- TP+SL même M1 : politique explicite `stop_first` ou `tp_first`.
- Ne jamais changer silencieusement une définition legacy.
- Toute nouvelle logique de stratégie utilise `riseup-v2`.

## Fichiers sensibles

Ne jamais versionner ou logger :

- `.env`
- `data/`
- Parquet
- SQLite
- exports locaux
- credentials

## Validation

Pendant l’implémentation, lancer uniquement les tests ciblés pertinents.

Avant de terminer une modification de code :

```bash
python -m pytest -q
```

Si le legacy est touché :

```bash
python scripts/verify_legacy_golden.py
```

Pour une modification uniquement documentaire, ne pas lancer la suite complète sauf nécessité.

Si l’UI change, vérifier également que le JavaScript reste valide.

Une tâche est terminée lorsque le comportement demandé fonctionne, les tests appropriés passent, les erreurs restent visibles et le diff ne contient ni fuite temporelle, ni mutation destructive, ni secret.