# Research engine

Les règles du `AGENTS.md` racine restent applicables.

## Scope

* `app/research/` contient `StrategyConfig`, presets et moteur `riseup-v2`.
* Ne pas modifier le legacy sauf demande explicite.
* Toute nouvelle logique doit rester configurable et reproductible.

## Anti look-ahead

* Pivot disponible seulement après `right_bars`.
* Conserver timestamp visuel et timestamp de disponibilité séparés.
* Une bougie HTF n’est utilisable qu’après clôture.
* ADR exclut la journée courante.
* Les extrema de session précédente lisent uniquement cette session.
* DXY utilise une borne temporelle exclusive.
* `signal_close` utilise uniquement une bougie terminée.

## Exécution

* Long : entrée ASK, sortie BID.
* Short : entrée BID, sortie ASK.
* TP+SL même M1 : politique explicite `stop_first` ou `tp_first`.
* Ne jamais utiliser des données futures pour décider rétroactivement d’un trade.

## Config / presets

* Toute option influençant les résultats passe par Pydantic.
* Conserver JSON exact, hash et version du run.
* Ne jamais changer silencieusement un preset existant.
* Toute nouvelle définition utilise `riseup-v2` et un preset dupliqué si nécessaire.

## Données / traces

* Ne jamais réparer ou fabriquer des données dans `app/research/`.
* Si les données sont invalides, échouer explicitement.
* Traces structurées et résumées ; éviter les logs par bougie sur plusieurs années.

## Validation

Tester selon le changement :

* pivots ;
* HTF ;
* ADR ;
* sessions ;
* DXY ;
* BID/ASK ;
* intrabar ;
* absence de look-ahead.

Faire le plus petit changement cohérent possible.
