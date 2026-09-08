# Golden backtest legacy

La fixture `tests/fixtures/legacy_golden.json` a été capturée avant le refactor Research Lab V2 à partir du cache Dukascopy local. Le contrôle rejoue le moteur `app/backtest.py` inchangé; il ne passe pas par une réimplémentation approximative des variantes.

## Référence immuable

- période UTC effective : `2026-01-31T23:00:00+00:00` → `2026-08-25T19:12:00+00:00`;
- setups : 235;
- trades : 181;
- SHA-256 économique : `fa831ede4d255aa410c99f9d09ce5fe9f8c9caa015058b25db9543cea717555e`;
- A.0 : 56 trades, 20 wins, 33 losses, 7.87 R;
- A.1 : 55 trades, 19 wins, 32 losses, 6.14 R;
- B.0 : 35 trades, 17 wins, 18 losses, 16 R;
- B.1 : 35 trades, 16 wins, 18 losses, 14.8733333333 R.

Le digest couvre, dans l'ordre, variante, paire, direction, timestamps/prix d'entrée et sortie, outcome et R. Les métadonnées ajoutées par V2 n'affectent pas le digest.

## Vérification

```bash
python scripts/verify_legacy_golden.py
```

Le mode par défaut reproduit le run capturé en validation permissive : les gaps restent présents et loggés en résumé, mais aucune bougie n'est inventée. `--strict-data` permet en plus d'exiger une couverture sans minute ouverte manquante; il peut légitimement bloquer avant le calcul si le cache actuel contient un trou.

Mesure du 2 septembre 2026 sur le poste local, cache chaud : 13,38 s et environ 496,1 Mo de RSS maximale pour le moteur complet. Cette valeur est informative et dépend du matériel/cache; le critère de régression obligatoire est l'identité du résultat, pas la vitesse.
