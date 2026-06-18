import os
import sqlite3
import logging
from typing import Any
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import json

import requests
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE = os.getenv("ADMIN_ROLE", "Admin")
WM_API_URL = os.getenv("WM_API_URL", "https://worldcup26.ir/get/games")
TEAM_API_URL = os.getenv("TEAM_API_URL", "https://worldcup26.ir/get/teams")
UPDATE_MINUTES = int(os.getenv("UPDATE_MINUTES", "10"))
TIP_CLOSE_MINUTES = int(os.getenv("TIP_CLOSE_MINUTES", "5"))
GUILD_ID = os.getenv("GUILD_ID", "").strip()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt. Bitte in Railway unter Variables eintragen.")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

db = sqlite3.connect("blauwals_wm_geschenke.db")
cur = db.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS spiele (
    id TEXT PRIMARY KEY,
    name TEXT,
    heim TEXT,
    auswaerts TEXT,
    startzeit TEXT,
    status TEXT,
    tore_heim INTEGER,
    tore_auswaerts INTEGER
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS tipps (
    user_id INTEGER,
    spiel_id TEXT,
    tore_heim INTEGER,
    tore_auswaerts INTEGER,
    PRIMARY KEY (user_id, spiel_id)
)
""")

db.commit()

PREISE = {
    1: "Haus",
    2: "Wohnung",
    3: "Auto",
    4: "Geld",
    5: "Geld"
}


def is_admin(member: discord.Member) -> bool:
    if getattr(member.guild_permissions, "administrator", False):
        return True
    return any(role.name == ADMIN_ROLE for role in getattr(member, "roles", []))


def get_field(data: dict[str, Any], names: list[str], default=None):
    for name in names:
        if name in data and data[name] not in (None, ""):
            return data[name]
    return default


def parse_score(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def parse_game_datetime(raw_value: str):
    if not raw_value:
        return None

    value = str(raw_value).strip()

    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            # API-Zeit ohne Zeitzone wird als UTC behandelt und nach Deutschland umgerechnet.
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

    return None


def berlin_datetime_text(raw_value: str) -> str:
    dt = parse_game_datetime(raw_value)
    if not dt:
        return str(raw_value) if raw_value else "Zeit offen"

    berlin = dt.astimezone(ZoneInfo("Europe/Berlin"))
    return berlin.strftime("%d.%m.%Y %H:%M Uhr deutsche Zeit")


def is_before_tip_deadline(raw_value: str) -> bool:
    dt = parse_game_datetime(raw_value)
    if not dt:
        return True

    berlin = dt.astimezone(ZoneInfo("Europe/Berlin"))
    now_berlin = datetime.now(ZoneInfo("Europe/Berlin"))
    deadline = berlin - timedelta(minutes=TIP_CLOSE_MINUTES)

    return now_berlin < deadline


def normalize_game(game: dict[str, Any]) -> dict[str, Any]:
    spiel_id = str(get_field(game, ["id", "_id", "match_id", "game_id", "matchNumber"]))

    heim = str(get_field(game, [
        "home_team_name_en", "home_team_name", "homeTeamName",
        "home_team", "homeTeam", "team1", "home"
    ], "Unbekannt"))

    auswaerts = str(get_field(game, [
        "away_team_name_en", "away_team_name", "awayTeamName",
        "away_team", "awayTeam", "team2", "away"
    ], "Unbekannt"))

    finished_value = str(get_field(game, ["finished", "is_finished"], "FALSE")).upper()
    time_elapsed = str(get_field(game, ["time_elapsed", "status", "match_status", "state", "phase"], "notstarted")).lower()

    if finished_value == "TRUE":
        status = "finished"
    elif time_elapsed not in ("notstarted", "scheduled", "", "none", "null"):
        status = "live"
    else:
        status = "scheduled"

    startzeit = str(get_field(game, ["local_date", "date", "datetime", "start_time", "time", "kickoff"], ""))

    raw_home_score = parse_score(get_field(game, [
        "home_score", "homeScore", "score1", "team1_score", "goals_home", "homeGoals"
    ]))
    raw_away_score = parse_score(get_field(game, [
        "away_score", "awayScore", "score2", "team2_score", "goals_away", "awayGoals"
    ]))

    if status in ("live", "finished"):
        tore_heim = raw_home_score
        tore_auswaerts = raw_away_score
    else:
        tore_heim = None
        tore_auswaerts = None

    return {
        "id": spiel_id,
        "name": f"{heim} vs {auswaerts}",
        "heim": heim,
        "auswaerts": auswaerts,
        "startzeit": startzeit,
        "status": status,
        "tore_heim": tore_heim,
        "tore_auswaerts": tore_auswaerts
    }


def update_games_from_api() -> int:
    response = requests.get(WM_API_URL, timeout=20)
    response.raise_for_status()
    data = response.json()

    if isinstance(data, dict):
        games = data.get("games") or data.get("data") or data.get("matches") or data.get("response") or data.get("results") or []
    else:
        games = data

    count = 0

    for raw_game in games:
        if not isinstance(raw_game, dict):
            continue

        game = normalize_game(raw_game)

        if game["id"] in ("None", "", "null"):
            continue

        cur.execute("""
        INSERT OR REPLACE INTO spiele
        (id, name, heim, auswaerts, startzeit, status, tore_heim, tore_auswaerts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            game["id"], game["name"], game["heim"], game["auswaerts"],
            game["startzeit"], game["status"], game["tore_heim"], game["tore_auswaerts"]
        ))

        count += 1

    db.commit()
    return count


def punkte(tipp_h: int, tipp_a: int, echt_h, echt_a) -> int:
    if echt_h is None or echt_a is None:
        return 0

    if tipp_h == echt_h and tipp_a == echt_a:
        return 3

    tipp_diff = tipp_h - tipp_a
    echt_diff = echt_h - echt_a

    if tipp_diff == 0 and echt_diff == 0:
        return 1
    if tipp_diff > 0 and echt_diff > 0:
        return 1
    if tipp_diff < 0 and echt_diff < 0:
        return 1

    return 0


def tippen_erlaubt(status: str, tore_heim, tore_auswaerts, startzeit: str = "") -> bool:
    if tore_heim is not None or tore_auswaerts is not None:
        return False

    s = str(status).lower()
    gesperrt = ["live", "playing", "in_progress", "finished", "ended", "fulltime", "completed"]

    if any(word in s for word in gesperrt):
        return False

    return is_before_tip_deadline(startzeit)


def make_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=0x3498DB)
    embed.set_footer(text="Blauwals WM Geschenke")
    return embed


def get_scoreboard_filtered(only_germany: bool = False) -> list[tuple[int, int]]:
    if only_germany:
        cur.execute("""
        SELECT t.user_id, t.tore_heim, t.tore_auswaerts, s.tore_heim, s.tore_auswaerts
        FROM tipps t
        JOIN spiele s ON t.spiel_id = s.id
        WHERE lower(s.heim) LIKE '%germany%' OR lower(s.auswaerts) LIKE '%germany%'
           OR lower(s.heim) LIKE '%deutschland%' OR lower(s.auswaerts) LIKE '%deutschland%'
        """)
    else:
        cur.execute("""
        SELECT t.user_id, t.tore_heim, t.tore_auswaerts, s.tore_heim, s.tore_auswaerts
        FROM tipps t
        JOIN spiele s ON t.spiel_id = s.id
        """)

    scores = {}
    for user_id, tipp_h, tipp_a, echt_h, echt_a in cur.fetchall():
        scores[user_id] = scores.get(user_id, 0) + punkte(tipp_h, tipp_a, echt_h, echt_a)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def result_points_for_table(team: str, heim: str, auswaerts: str, th, ta) -> tuple[int, int, int, int, int]:
    if th is None or ta is None:
        return (0, 0, 0, 0, 0)

    if team == heim:
        gf, ga = th, ta
    elif team == auswaerts:
        gf, ga = ta, th
    else:
        return (0, 0, 0, 0, 0)

    if gf > ga:
        return (1, 1, 0, 0, 3)
    if gf == ga:
        return (1, 0, 1, 0, 1)
    return (1, 0, 0, 1, 0)


def get_scoreboard() -> list[tuple[int, int]]:
    return get_scoreboard_filtered(False)


@tasks.loop(minutes=UPDATE_MINUTES)
async def auto_update_games():
    try:
        count = update_games_from_api()
        logging.info("WM-Spiele automatisch aktualisiert: %s", count)
    except Exception as e:
        logging.exception("Fehler beim automatischen WM-Update: %s", e)


@bot.event
async def on_ready():
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        logging.info("Slash-Commands für Guild %s synchronisiert.", GUILD_ID)
    else:
        await bot.tree.sync()
        logging.info("Globale Slash-Commands synchronisiert.")

    if not auto_update_games.is_running():
        auto_update_games.start()

    logging.info("Blauwals WM Geschenke ist online als %s", bot.user)


@bot.tree.command(name="ping", description="Testet, ob der Bot läuft")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Blauwals WM Geschenke läuft!")


@bot.tree.command(name="update", description="Admin: WM-Spiele sofort aus dem Internet aktualisieren")
async def update(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Dafür brauchst du Adminrechte.", ephemeral=True)
        return

    await interaction.response.defer()

    try:
        count = update_games_from_api()
        await interaction.followup.send(f"✅ {count} WM-Spiele wurden aktualisiert.")
    except Exception as e:
        await interaction.followup.send(f"❌ Fehler beim Update: `{e}`")


@bot.tree.command(name="debug_api", description="Admin: Zeigt API-Rohdaten zur Fehlersuche")
async def debug_api(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Dafür brauchst du Adminrechte.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        games_response = requests.get(WM_API_URL, timeout=20)
        games_response.raise_for_status()
        games_data = games_response.json()

        if isinstance(games_data, dict):
            games = games_data.get("games") or games_data.get("data") or games_data.get("matches") or games_data.get("response") or []
        else:
            games = games_data

        first_game = games[0] if games else {}

        text = "**GAME KEYS:**\n```" + str(list(first_game.keys()))[:900] + "```\n"
        text += "**FIRST GAME:**\n```json\n" + json.dumps(first_game, ensure_ascii=False, indent=2)[:2200] + "\n```"

        await interaction.followup.send(text[:3900], ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Debug Fehler: `{e}`", ephemeral=True)


@bot.tree.command(name="spiele", description="Zeigt alle WM-Spiele mit Seiten")
async def spiele(interaction: discord.Interaction, seite: int = 1):
    pro_seite = 20

    cur.execute("SELECT COUNT(*) FROM spiele")
    total = cur.fetchone()[0]

    if total == 0:
        await interaction.response.send_message("Noch keine Spiele geladen. Ein Admin kann `/update` nutzen.")
        return

    max_seiten = (total + pro_seite - 1) // pro_seite

    if seite < 1:
        seite = 1
    if seite > max_seiten:
        seite = max_seiten

    offset = (seite - 1) * pro_seite

    cur.execute("""
    SELECT id, heim, auswaerts, startzeit, status, tore_heim, tore_auswaerts
    FROM spiele
    ORDER BY startzeit
    LIMIT ? OFFSET ?
    """, (pro_seite, offset))

    rows = cur.fetchall()

    text = f"**Seite {seite}/{max_seiten}** — Insgesamt **{total}** Spiele\n"
    text += "Nutze `/spiele seite:2`, `/spiele seite:3` usw.\n\n"

    for spiel_id, heim, auswaerts, startzeit, status, th, ta in rows:
        ergebnis = "offen" if th is None or ta is None else f"{th}:{ta}"
        text += f"`{spiel_id}` **{heim} vs {auswaerts}** | {ergebnis} | {status} | {berlin_datetime_text(startzeit)}\n"

    await interaction.response.send_message(embed=make_embed("WM-Spiele", text[:3900]))


@bot.tree.command(name="tipp", description="Gib deinen Tipp ab - nur einmal pro Spiel")
async def tipp(interaction: discord.Interaction, spiel_id: str, tore_heim: int, tore_auswaerts: int):
    if tore_heim < 0 or tore_auswaerts < 0:
        await interaction.response.send_message("❌ Tore dürfen nicht negativ sein.", ephemeral=True)
        return

    cur.execute("""
    SELECT heim, auswaerts, status, tore_heim, tore_auswaerts, startzeit
    FROM spiele
    WHERE id = ?
    """, (spiel_id,))

    spiel = cur.fetchone()

    if not spiel:
        await interaction.response.send_message("❌ Dieses Spiel gibt es nicht. Nutze `/spiele`.", ephemeral=True)
        return

    heim, auswaerts, status, th, ta, startzeit = spiel

    if not tippen_erlaubt(status, th, ta, startzeit):
        await interaction.response.send_message(
            f"❌ Für dieses Spiel kann man nicht mehr tippen. Tipps schließen {TIP_CLOSE_MINUTES} Minuten vor Anpfiff.",
            ephemeral=True
        )
        return

    cur.execute("""
    SELECT tore_heim, tore_auswaerts
    FROM tipps
    WHERE user_id = ? AND spiel_id = ?
    """, (interaction.user.id, spiel_id))

    vorhandener_tipp = cur.fetchone()

    if vorhandener_tipp:
        alt_h, alt_a = vorhandener_tipp
        await interaction.response.send_message(
            f"❌ Du hast für **{heim} vs {auswaerts}** schon getippt: **{alt_h}:{alt_a}**.\n"
            "Dieser Bot hat Schutz aktiv: Jeder Tipp zählt nur einmal und kann nicht geändert werden.",
            ephemeral=True
        )
        return

    cur.execute("""
    INSERT INTO tipps
    (user_id, spiel_id, tore_heim, tore_auswaerts)
    VALUES (?, ?, ?, ?)
    """, (interaction.user.id, spiel_id, tore_heim, tore_auswaerts))

    db.commit()

    await interaction.response.send_message(
        f"✅ Dein endgültiger Tipp für **{heim} vs {auswaerts}** wurde gespeichert: **{tore_heim}:{tore_auswaerts}**.\n"
        "⚠️ Achtung: Dieser Tipp kann nicht mehr geändert werden.",
        ephemeral=True
    )


@bot.tree.command(name="tippen", description="Zeigt eine einfache Tipp-Anleitung")
async def tippen(interaction: discord.Interaction):
    cur.execute("""
    SELECT id, heim, auswaerts, startzeit
    FROM spiele
    WHERE status = 'scheduled'
    ORDER BY startzeit
    LIMIT 10
    """)

    rows = cur.fetchall()

    text = (
        "**So gibst du deinen Tipp ab:**\n\n"
        "`/tipp spiel_id:<ID> tore_heim:<Tore> tore_auswaerts:<Tore>`\n\n"
        "**Beispiel:**\n"
        "`/tipp spiel_id:1 tore_heim:2 tore_auswaerts:1`\n\n"
        f"⚠️ Tipps schließen **{TIP_CLOSE_MINUTES} Minuten vor Anpfiff**.\n"
        "⚠️ Jeder Tipp ist endgültig und kann nicht geändert werden.\n\n"
        "**Nächste tippbare Spiele:**\n"
    )

    if rows:
        for spiel_id, heim, auswaerts, startzeit in rows:
            text += f"`{spiel_id}` **{heim} vs {auswaerts}** | {berlin_datetime_text(startzeit)}\n"
    else:
        text += "Aktuell keine tippbaren Spiele geladen. Nutze `/update`."

    await interaction.response.send_message(embed=make_embed("Tippen", text[:3900]), ephemeral=True)


@bot.tree.command(name="meine_tipps", description="Zeigt deine abgegebenen Tipps")
async def meine_tipps(interaction: discord.Interaction):
    cur.execute("""
    SELECT s.heim, s.auswaerts, t.tore_heim, t.tore_auswaerts, s.startzeit
    FROM tipps t
    JOIN spiele s ON t.spiel_id = s.id
    WHERE t.user_id = ?
    ORDER BY s.startzeit
    """, (interaction.user.id,))

    rows = cur.fetchall()

    if not rows:
        await interaction.response.send_message("Du hast noch keine Tipps abgegeben.", ephemeral=True)
        return

    text = ""
    for heim, auswaerts, th, ta, startzeit in rows:
        text += f"**{heim} vs {auswaerts}** → {th}:{ta} | {berlin_datetime_text(startzeit)}\n"

    await interaction.response.send_message(embed=make_embed("Deine Tipps", text[:3900]), ephemeral=True)


@bot.tree.command(name="punktesystem", description="Erklärt das Punktesystem und die Wertungen")
async def punktesystem(interaction: discord.Interaction):
    text = (
        "**Exaktes Ergebnis:** 3 Punkte\n"
        "Beispiel: Tipp 2:1, Ergebnis 2:1.\n\n"
        "**Richtige Tendenz:** 1 Punkt\n"
        "Beispiel: Tipp 2:0, Ergebnis 1:0. Auch richtiges Unentschieden mit falscher Torzahl gibt 1 Punkt.\n\n"
        "**Falsch:** 0 Punkte\n\n"
        f"**Schutz:** Jeder Spieler kann pro Spiel nur **einmal** tippen. Tipps schließen **{TIP_CLOSE_MINUTES} Minuten vor Anpfiff**.\n\n"
        "**Wertungen:**\n"
        "`/rangliste` = komplette WM\n"
        "`/rangliste_deutschland` = nur Deutschland-Spiele\n"
        "`/gewinner` = Gewinner komplette WM\n"
        "`/gewinner_deutschland` = Gewinner Deutschland-Wertung"
    )
    await interaction.response.send_message(embed=make_embed("Punktesystem", text))


@bot.tree.command(name="rangliste", description="Zeigt die aktuelle Rangliste für die komplette WM")
async def rangliste(interaction: discord.Interaction):
    top = get_scoreboard_filtered(False)

    if not top:
        await interaction.response.send_message("Noch keine Tipps vorhanden.")
        return

    text = ""
    for platz, (user_id, score) in enumerate(top[:10], start=1):
        user = await bot.fetch_user(user_id)
        preis = f" — Gewinn: **{PREISE[platz]}**" if platz in PREISE else ""
        text += f"**#{platz}** {user.mention} — `{score}` Punkte{preis}\n"

    await interaction.response.send_message(embed=make_embed("Rangliste komplette WM", text))


@bot.tree.command(name="rangliste_deutschland", description="Zeigt die Rangliste nur für Deutschland-Spiele")
async def rangliste_deutschland(interaction: discord.Interaction):
    top = get_scoreboard_filtered(True)

    if not top:
        await interaction.response.send_message("Noch keine Tipps für Deutschland-Spiele vorhanden.")
        return

    text = ""
    for platz, (user_id, score) in enumerate(top[:10], start=1):
        user = await bot.fetch_user(user_id)
        preis = f" — Gewinn: **{PREISE[platz]}**" if platz in PREISE else ""
        text += f"**#{platz}** {user.mention} — `{score}` Punkte{preis}\n"

    await interaction.response.send_message(embed=make_embed("Rangliste Deutschland", text))


@bot.tree.command(name="gewinner", description="Zeigt die Top 5 Gewinner der kompletten WM")
async def gewinner(interaction: discord.Interaction):
    top = get_scoreboard_filtered(False)[:5]

    if not top:
        await interaction.response.send_message("Noch keine Gewinner vorhanden.")
        return

    text = ""
    for platz, (user_id, score) in enumerate(top, start=1):
        user = await bot.fetch_user(user_id)
        text += f"**Platz {platz}:** {user.mention} — `{score}` Punkte — **{PREISE[platz]}**\n"

    await interaction.response.send_message(embed=make_embed("Gewinner komplette WM", text))


@bot.tree.command(name="gewinner_deutschland", description="Zeigt die Top 5 Gewinner nur für Deutschland-Spiele")
async def gewinner_deutschland(interaction: discord.Interaction):
    top = get_scoreboard_filtered(True)[:5]

    if not top:
        await interaction.response.send_message("Noch keine Gewinner für Deutschland-Spiele vorhanden.")
        return

    text = ""
    for platz, (user_id, score) in enumerate(top, start=1):
        user = await bot.fetch_user(user_id)
        text += f"**Platz {platz}:** {user.mention} — `{score}` Punkte — **{PREISE[platz]}**\n"

    await interaction.response.send_message(embed=make_embed("Gewinner Deutschland", text))


@bot.tree.command(name="gewinn_verteilen", description="Admin: Postet die finalen Gewinner der kompletten WM")
async def gewinn_verteilen(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Dafür brauchst du Adminrechte.", ephemeral=True)
        return

    top = get_scoreboard_filtered(False)[:5]

    if not top:
        await interaction.response.send_message("Noch keine Tipps vorhanden.")
        return

    text = "**🎉 Finale Gewinner — komplette WM 🎉**\n\n"
    for platz, (user_id, score) in enumerate(top, start=1):
        user = await bot.fetch_user(user_id)
        text += f"**Platz {platz}:** {user.mention} — `{score}` Punkte — **{PREISE[platz]}**\n"

    text += "\nBitte die Gewinne IC durch die Projektleitung vergeben."
    await interaction.response.send_message(embed=make_embed("Gewinne verteilen", text))


@bot.tree.command(name="gewinn_verteilen_deutschland", description="Admin: Postet die Gewinner der Deutschland-Wertung")
async def gewinn_verteilen_deutschland(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Dafür brauchst du Adminrechte.", ephemeral=True)
        return

    top = get_scoreboard_filtered(True)[:5]

    if not top:
        await interaction.response.send_message("Noch keine Tipps für Deutschland-Spiele vorhanden.")
        return

    text = "**🇩🇪🎉 Finale Gewinner — Deutschland-Wertung 🎉**\n\n"
    for platz, (user_id, score) in enumerate(top, start=1):
        user = await bot.fetch_user(user_id)
        text += f"**Platz {platz}:** {user.mention} — `{score}` Punkte — **{PREISE[platz]}**\n"

    text += "\nBitte die Gewinne IC durch die Projektleitung vergeben."
    await interaction.response.send_message(embed=make_embed("Gewinne Deutschland verteilen", text))


@bot.tree.command(name="preise", description="Zeigt die Preise für Platz 1 bis 5")
async def preise(interaction: discord.Interaction):
    text = (
        "**Platz 1:** Haus\n"
        "**Platz 2:** Wohnung\n"
        "**Platz 3:** Auto\n"
        "**Platz 4:** Geld\n"
        "**Platz 5:** Geld"
    )
    await interaction.response.send_message(embed=make_embed("Preise", text))


@bot.tree.command(name="regeln", description="Zeigt die Regeln vom WM-Tippspiel")
async def regeln(interaction: discord.Interaction):
    text = (
        "1. Jeder Spieler kann pro Spiel nur **einmal** tippen.\n"
        "2. Ein Tipp kann nach Abgabe **nicht geändert** werden.\n"
        f"3. Tipps sind nur bis **{TIP_CLOSE_MINUTES} Minuten vor Anpfiff** möglich.\n"
        "4. Exaktes Ergebnis = **3 Punkte**.\n"
        "5. Richtige Tendenz = **1 Punkt**.\n"
        "6. Falsch = **0 Punkte**.\n"
        "7. Es gibt zwei Wertungen: komplette WM und nur Deutschland-Spiele.\n"
        "8. Platz 1 bis 5 bekommen am Ende die Preise."
    )
    await interaction.response.send_message(embed=make_embed("Regeln", text))


@bot.tree.command(name="deutschland", description="Zeigt alle Spiele mit Deutschland")
async def deutschland(interaction: discord.Interaction):
    cur.execute("""
    SELECT id, heim, auswaerts, startzeit, status, tore_heim, tore_auswaerts
    FROM spiele
    WHERE lower(heim) LIKE '%germany%' OR lower(auswaerts) LIKE '%germany%'
       OR lower(heim) LIKE '%deutschland%' OR lower(auswaerts) LIKE '%deutschland%'
    ORDER BY startzeit
    LIMIT 25
    """)

    rows = cur.fetchall()

    if not rows:
        await interaction.response.send_message(
            "Ich finde aktuell keine Deutschland-Spiele. Wahrscheinlich sind sie in der API noch nicht zugeordnet.",
            ephemeral=True
        )
        return

    text = ""
    for spiel_id, heim, auswaerts, startzeit, status, th, ta in rows:
        ergebnis = "offen" if th is None or ta is None else f"{th}:{ta}"
        text += f"`{spiel_id}` **{heim} vs {auswaerts}** | {ergebnis} | {status} | {berlin_datetime_text(startzeit)}\n"

    await interaction.response.send_message(embed=make_embed("Deutschland-Spiele", text[:3900]))


@bot.tree.command(name="tabelle", description="Zeigt eine einfache Tabelle aus fertigen Spielen")
async def tabelle(interaction: discord.Interaction):
    cur.execute("""
    SELECT heim, auswaerts, tore_heim, tore_auswaerts, status
    FROM spiele
    WHERE status = 'finished'
    """)

    rows = cur.fetchall()

    if not rows:
        await interaction.response.send_message("Noch keine fertigen Spiele für eine Tabelle vorhanden.")
        return

    table = {}

    for heim, auswaerts, th, ta, status in rows:
        for team in (heim, auswaerts):
            table.setdefault(team, {"sp": 0, "s": 0, "u": 0, "n": 0, "pkt": 0})

        for team in (heim, auswaerts):
            sp, s, u, n, pkt = result_points_for_table(team, heim, auswaerts, th, ta)
            table[team]["sp"] += sp
            table[team]["s"] += s
            table[team]["u"] += u
            table[team]["n"] += n
            table[team]["pkt"] += pkt

    sorted_table = sorted(table.items(), key=lambda x: (x[1]["pkt"], x[1]["s"]), reverse=True)

    text = "**Team | Sp | S | U | N | Pkt**\n"
    for team, row in sorted_table[:25]:
        text += f"**{team}** | {row['sp']} | {row['s']} | {row['u']} | {row['n']} | **{row['pkt']}**\n"

    await interaction.response.send_message(embed=make_embed("Tabelle", text[:3900]))


@bot.tree.command(name="heute", description="Zeigt WM-Spiele von heute in deutscher Zeit")
async def heute(interaction: discord.Interaction):
    today = datetime.now(ZoneInfo("Europe/Berlin")).date()

    cur.execute("""
    SELECT id, heim, auswaerts, startzeit, status, tore_heim, tore_auswaerts
    FROM spiele
    ORDER BY startzeit
    """)

    rows = []
    for row in cur.fetchall():
        dt = parse_game_datetime(row[3])
        if dt and dt.astimezone(ZoneInfo("Europe/Berlin")).date() == today:
            rows.append(row)

    if not rows:
        await interaction.response.send_message("Heute sind keine WM-Spiele eingetragen.")
        return

    text = ""
    for spiel_id, heim, auswaerts, startzeit, status, th, ta in rows[:25]:
        ergebnis = "offen" if th is None or ta is None else f"{th}:{ta}"
        text += f"`{spiel_id}` **{heim} vs {auswaerts}** | {ergebnis} | {status} | {berlin_datetime_text(startzeit)}\n"

    await interaction.response.send_message(embed=make_embed("WM-Spiele heute", text[:3900]))


@bot.tree.command(name="morgen", description="Zeigt WM-Spiele von morgen in deutscher Zeit")
async def morgen(interaction: discord.Interaction):
    tomorrow = datetime.now(ZoneInfo("Europe/Berlin")).date() + timedelta(days=1)

    cur.execute("""
    SELECT id, heim, auswaerts, startzeit, status, tore_heim, tore_auswaerts
    FROM spiele
    ORDER BY startzeit
    """)

    rows = []
    for row in cur.fetchall():
        dt = parse_game_datetime(row[3])
        if dt and dt.astimezone(ZoneInfo("Europe/Berlin")).date() == tomorrow:
            rows.append(row)

    if not rows:
        await interaction.response.send_message("Morgen sind keine WM-Spiele eingetragen.")
        return

    text = ""
    for spiel_id, heim, auswaerts, startzeit, status, th, ta in rows[:25]:
        ergebnis = "offen" if th is None or ta is None else f"{th}:{ta}"
        text += f"`{spiel_id}` **{heim} vs {auswaerts}** | {ergebnis} | {status} | {berlin_datetime_text(startzeit)}\n"

    await interaction.response.send_message(embed=make_embed("WM-Spiele morgen", text[:3900]))


@bot.tree.command(name="live", description="Zeigt aktuell laufende WM-Spiele")
async def live(interaction: discord.Interaction):
    cur.execute("""
    SELECT id, heim, auswaerts, startzeit, status, tore_heim, tore_auswaerts
    FROM spiele
    WHERE status = 'live'
    ORDER BY startzeit
    """)

    rows = cur.fetchall()

    if not rows:
        await interaction.response.send_message("Aktuell läuft laut API kein WM-Spiel.")
        return

    text = ""
    for spiel_id, heim, auswaerts, startzeit, status, th, ta in rows[:25]:
        ergebnis = "offen" if th is None or ta is None else f"{th}:{ta}"
        text += f"`{spiel_id}` **{heim} vs {auswaerts}** | {ergebnis} | {status} | {berlin_datetime_text(startzeit)}\n"

    await interaction.response.send_message(embed=make_embed("Live-Spiele", text[:3900]))


bot.run(TOKEN)
