# Blauwals WM Geschenke - Railway Version

Discord Bot für das WM-Tippspiel von Bluesea Roleplay.

## Commands

- /ping - testet ob der Bot läuft
- /update - Admin lädt WM-Spiele sofort neu
- /spiele - zeigt Spiele
- /tipp - Tipp abgeben
- /meine_tipps - eigene Tipps anzeigen
- /rangliste - Rangliste
- /gewinner - Top 5 mit Preisen

## Railway Upload

1. ZIP entpacken.
2. Ordner auf GitHub hochladen.
3. Railway öffnen.
4. New Project → Deploy from GitHub Repo.
5. Dein Repository auswählen.
6. In Railway auf Variables gehen.
7. Variablen eintragen:

DISCORD_TOKEN=dein_token
ADMIN_ROLE=Admin
WM_API_URL=https://worldcup26.ir/get/games
TEAM_API_URL=https://worldcup26.ir/get/teams
UPDATE_MINUTES=10

Optional:
GUILD_ID=deine_discord_server_id

8. Deploy starten.

## Discord Bot einladen

Im Discord Developer Portal:
1. App öffnen.
2. OAuth2 / Installation öffnen.
3. Scopes auswählen:
   - bot
   - applications.commands
4. Permissions:
   - Send Messages
   - Use Slash Commands
   - Read Message History
5. Invite-Link öffnen und Bot auf Server einladen.

Wichtig: Discord hostet den Code nicht. Der Bot läuft dauerhaft bei Railway.
