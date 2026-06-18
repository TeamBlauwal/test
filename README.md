# Blauwals WM Geschenke - FOOTBALL-DATA VERSION

## Warum?

Die alte API `worldcup26.ir` hatte SSL-Probleme.
Diese Version kann Football-Data.org nutzen.

## Railway Variables

Pflicht:
DISCORD_TOKEN=dein_discord_token
GUILD_ID=deine_server_id
API_PROVIDER=football-data
FOOTBALL_DATA_TOKEN=dein_football_data_token
FOOTBALL_DATA_COMPETITIONS=WC
FOOTBALL_DATA_SEASON=2026

Empfohlen:
DB_PATH=/app/data/blauwals_wm_geschenke.db
TIP_CLOSE_MINUTES=5
UPDATE_MINUTES=10

## Railway Volume

Damit Punkte und Tipps nicht verschwinden:

Railway → Volumes → Add Volume
Mount Path:
/app/data

Variable:
DB_PATH=/app/data/blauwals_wm_geschenke.db

## Quali-Spiele

Football-Data hat Competition-Codes. Für die WM-Endrunde:
WC

Für UEFA WM-Qualifikation laut Football-Data Lookup:
QUFA

Test:
FOOTBALL_DATA_COMPETITIONS=WC,QUFA

Ob du Zugriff auf QUFA hast, hängt von deinem Football-Data Account/API-Zugang ab.

## Commands

/update
/api_status
/debug_api
/spiele seite:1
/punkte_setzen
/rangliste
