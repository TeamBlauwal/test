# Blauwals WM Geschenke - POINTSFIX

## Neu

Manuelle Punkte wiederherstellen:
- /punkte_setzen
- /punkte_add
- /punkte_liste
- /punkte_reset
- /db_info

## Railway Volume

Damit Punkte und Tipps nicht mehr bei Deploys verschwinden:

Railway → dein Projekt → Volumes → Add Volume

Mount Path:
`/app/data`

Railway Variable setzen:
`DB_PATH=/app/data/blauwals_wm_geschenke.db`

## Alte Punkte wieder geben

Beispiel:
`/punkte_setzen user:@Capybara punkte:10 wertung:gesamt`

Deutschland-Wertung:
`/punkte_setzen user:@Capybara punkte:5 wertung:deutschland`

## Quali-Spiele

Der Bot lädt die Spiele aus `WM_API_URL`.
Wenn die API Quali-Spiele liefert, zeigt der Bot sie an.
Wenn die API nur WM-Endrunden-Spiele liefert, kann der Bot keine Quali-Spiele anzeigen.
