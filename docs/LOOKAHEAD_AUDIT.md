# Audit temporel et look-ahead

Cet audit couvre le pipeline existant et les changements de source de données. Aucune règle de stratégie n'a été optimisée pendant la migration.

## Matrice de disponibilité V2

| Information | Timestamp utilisable | Garantie dans le moteur |
|---|---|---|
| M1 pair/DXY | clôture de la minute | borne exclusive; une M1 ouverte n'est pas téléchargée |
| High/low session précédente | fin réelle de cette session | `session_slice` est limité à `[start, end)` |
| Cassure M1 | clôture de sa bougie | l'entrée est postérieure; la tolérance ne change que le niveau |
| Pivot visuel | centre + `right_bars` | `pivot_available_index` et entrée B après confirmation |
| Label FAST | après la confirmation legacy de la séquence A | ouverture/close déjà disponible, jamais au centre du pivot |
| Label CONFIRMED | après `right_bars`, bougie suivante | `b_entry_index = bar + rb + 1` |
| M5 | fin du bloc de cinq minutes | le trigger est ramené à la première M1 disponible à cette heure |
| H1/H2/H4 | fin de la bougie HTF | `Zone.formed_at = open + durée` |
| ADR | veille au plus tard | plage journalière décalée avant rolling mean |
| DXY intrabar | clôture de la M1 de cassure incluse | cutoff exclusif `break + 1 minute` |
| DXY interbar | dernière M1 close avant la cassure | cutoff exclusif `break_time` |
| Invalidation DXY | données strictement avant l'entrée | second cutoff à `entry_time` |
| BID/ASK d'entrée | open d'entrée ou close précédent terminé | aucun close de la bougie en cours |
| SL/TP | M1 à partir de l'entrée | politique same-bar explicite, pas d'ordre tick inventé |
| Equity/risk | après sorties antérieures | candidats triés, position et budget évalués chronologiquement |

## Points vérifiés

- **Pivots M1** : un pivot historique n'est enregistré qu'après disponibilité de ses `right bars`. La variante B entre à l'ouverture suivant la confirmation `rb`; elle ne traite pas le centre du pivot comme connu avant cette confirmation.
- **Variante A** : le label est calculé sur une bougie clôturée et l'entrée reste décalée. Le contrôle de l'absence d'un label identique sur la bougie suivante est connu avant l'ouverture d'entrée utilisée par le moteur.
- **Indicateurs** : RSI, EMA/MACD, Momentum, CCI, Stoch, DIosc et les indicateurs volume optionnels utilisent uniquement la ligne courante et l'historique (`rolling`, `shift`, calculs récursifs). Aucun centrage futur n'a été trouvé.
- **Zones H2/H4** : une zone formée par une bougie HTF devient disponible à `index + durée`. La dernière bougie HTF incomplète est désormais exclue. L'invalidation est précalculée sur la série complète pour la performance, mais `find_active_zone` la compare à son timestamp réel et n'invalide pas une zone rétroactivement.
- **ADR** : la plage quotidienne est décalée d'un jour avant la moyenne; la journée courante n'entre pas dans son propre ADR.
- **Sessions** : les cassures utilisent la session courante et les extrema de la session précédente. La construction locale `Europe/Paris` est convertie une seule fois en UTC.
- **DXY** : la non-confirmation ne lit que les bougies antérieures à l'instant testé. Au moment d'une cassure M1, la bougie entière est considérée connue à sa clôture; les entrées surviennent ensuite.
- **Resampling** : toutes les bougies portent leur heure d'ouverture et ne deviennent exploitables par les zones qu'à leur heure de fin. Aucune bougie M1 manquante n'est forward-fillée.
- **Bougie en cours** : le downloader et le backtest utilisent une borne de fin exclusive à la minute courante; une M1 non clôturée n'est ni persistée comme complète ni consommée.
- **SL/TP même bougie** : l'ordre intrabougie reste inconnu en OHLC. Le comportement est explicite et conservateur par défaut (`SAME_BAR_POLICY=stop_first`), configurable en `tp_first`.
- **Entrée au close du signal** : le moteur lit la clôture de la bougie précédant le timestamp d'exécution. Il refuse ce mode si aucune bougie terminée n'existe.
- **Délai maximal** : il compare le break à l'heure du label/entrée déjà disponible; il ne recherche pas rétrospectivement un meilleur label.
- **Zone touchée/retests/expiration** : les touches sont comptées sur `[formed_at, entry_time)` et l'expiration est calculée depuis la disponibilité de la zone.
- **Risque et position unique** : les décisions sont prises sur la liste triée et ne connaissent que les trades simulés précédemment. Une sortie au même timestamp est traitée conservativement comme encore ouverte.

## Tests de preuve

- `tests/test_strategy_rules.py` : DST/overnight, cassure+tolerance, cutoff DXY, same-bar, limites de risque/position, disponibilité HTF.
- `tests/test_research_engine.py` : raisons de rejet structurées et timestamp réel de confirmation pivot.
- `tests/test_data_pipeline.py` : resampling, gaps, bougie courante, cache sans remplissage.
- `tests/test_legacy_golden.py` et `scripts/verify_legacy_golden.py` : toute variation économique de la séquence legacy change le digest.

## Points à comparer avec TradingView

1. L'ancrage H2/H4 reste celui du projet historique : minuit `Europe/Paris`. Il faut confirmer le fuseau et la session du symbole TradingView de référence.
2. Dukascopy fournit les bougies BID et ASK M1 séparément. `mid_h` et `mid_l` sont les moyennes des extrema de côté; ils ne sont pas reconstruits à partir de ticks synchrones. Une comparaison TradingView doit tenir compte de cette différence de feed.
3. Le DXY direct Dukascopy (`DOLLAR.IDX-USD`) a ses propres horaires et jours fériés. Il n'est pas nécessairement identique au symbole ICE/TV choisi visuellement.
4. Le DXY synthétique historique reconstruit son OHLC à partir des clôtures synchronisées. Il est conservé comme fallback, pas promu comme équivalent tick-by-tick du DXY direct.
5. Les indicateurs volume restent désactivés par défaut. Leur activation modifie le label et doit faire l'objet d'une comparaison séparée `legacy_no_volume` / `dukascopy_volume`.

Ces divergences sont exposées par configuration et dans le résultat du backtest; elles ne sont pas masquées par une modification silencieuse des règles.
