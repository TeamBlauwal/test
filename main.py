import os
import sqlite3
import logging
from typing import Any

import requests
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# =========================
# Blauwals WM Geschenke
# Railway-ready Discord Bot
# =========================

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE = os.getenv("ADMIN_ROLE", "Admin")
WM_API_URL = os.getenv("WM_API_URL", "https://worldcup26.ir/get/games")
UPDATE_MINUTES = int(os.getenv("UPDATE_MINUTES", "10"))
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
    # Erlaubt /update für alle mit Discord-Administratorrecht
    # oder zusätzlich für User mit der Rolle aus ADMIN_ROLE.
    if getattr(member.guild_permissions, "administrator", False):
        return True

    return any(role.name == ADMIN_ROLE for role in getattr(member, "roles", []))


def get_field(data: dict[str, Any], names: list[str], default=None):
    for name in names:
        if name in data and data[name] not in (None, ""):
            return data[name]
    return default


def parse_team(value):
    if isinstance(value, dict):
        return (
            value.get("name")
            or value.get("name_en")
            or value.get("team")
            or value.get("title")
            or value.get("country")
            or "Unbekannt"
        )
    return str(value) if value else "Unbekannt"


def parse_score(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def normalize_game(game: dict[str, Any]) -> dict[str, Any]:
    spiel_id = str(get_field(game, ["id", "_id", "match_id", "game_id", "matchNumber"]))

    heim_raw = get_field(game, ["home_team", "homeTeam", "team1", "home", "homeTeamName"])
    aus_raw = get_field(game, ["away_team", "awayTeam", "team2", "away", "awayTeamName"])

    heim = parse_team(heim_raw)
    auswaerts = parse_team(aus_raw)

    status = str(get_field(game, ["status", "match_status", "state", "phase"], "scheduled"))
    startzeit = str(get_field(game, ["date", "datetime", "start_time", "time", "kickoff"], ""))

    tore_heim = parse_score(get_field(game, [
        "home_score", "homeScore", "score1", "team1_score", "goals_home", "homeGoals"
    ]))

    tore_auswaerts = parse_score(get_field(game, [
        "away_score", "awayScore", "score2", "team2_score", "goals_away", "awayGoals"
    ]))

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
        games = data.get("games") or data.get("data") or data.get("matches") or data.get("response") or []
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
            game["id"],
            game["name"],
            game["heim"],
            game["auswaerts"],
            game["startzeit"],
            game["status"],
            game["tore_heim"],
            game["tore_auswaerts"]
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


def tippen_erlaubt(status: str, tore_heim, tore_auswaerts) -> bool:
    if tore_heim is not None or tore_auswaerts is not None:
        return False

    s = str(status).lower()
    gesperrt = ["live", "playing", "in_progress", "finished", "ended", "fulltime", "completed"]
    return not any(word in s for word in gesperrt)


def make_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=0x3498DB)
    embed.set_footer(text="Blauwals WM Geschenke")
    return embed


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
        logging.info("Globale Slash-Commands synchronisiert. Das kann bei Discord manchmal dauern.")

    if not auto_update_games.is_running():
        auto_update_games.start()

    logging.info("Blauwals WM Geschenke ist online als %s", bot.user)


@bot.tree.command(name="ping", description="Testet, ob der Bot läuft")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Blauwals WM Geschenke läuft!")


@bot.tree.command(name="update", description="Admin: WM-Spiele sofort aus dem Internet aktualisieren")
async def update(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Dafür brauchst du die Admin-Rolle.", ephemeral=True)
        return

    await interaction.response.defer()

    try:
        count = update_games_from_api()
        await interaction.followup.send(f"✅ {count} WM-Spiele wurden aktualisiert.")
    except Exception as e:
        await interaction.followup.send(f"❌ Fehler beim Update: `{e}`")


@bot.tree.command(name="spiele", description="Zeigt die aktuellen WM-Spiele")
async def spiele(interaction: discord.Interaction):
    cur.execute("""
    SELECT id, heim, auswaerts, startzeit, status, tore_heim, tore_auswaerts
    FROM spiele
    ORDER BY startzeit
    LIMIT 25
    """)

    rows = cur.fetchall()

    if not rows:
        await interaction.response.send_message("Noch keine Spiele geladen. Ein Admin kann `/update` nutzen.")
        return

    text = ""
    for spiel_id, heim, auswaerts, startzeit, status, th, ta in rows:
        ergebnis = "offen" if th is None or ta is None else f"{th}:{ta}"
        text += f"`{spiel_id}` **{heim} vs {auswaerts}** | {ergebnis} | {status}\n"

    if len(text) > 3900:
        text = text[:3900] + "\n..."

    await interaction.response.send_message(embed=make_embed("WM-Spiele", text))


@bot.tree.command(name="tipp", description="Gib deinen Tipp für ein WM-Spiel ab")
async def tipp(interaction: discord.Interaction, spiel_id: str, tore_heim: int, tore_auswaerts: int):
    if tore_heim < 0 or tore_auswaerts < 0:
        await interaction.response.send_message("❌ Tore dürfen nicht negativ sein.", ephemeral=True)
        return

    cur.execute("""
    SELECT heim, auswaerts, status, tore_heim, tore_auswaerts
    FROM spiele
    WHERE id = ?
    """, (spiel_id,))

    spiel = cur.fetchone()

    if not spiel:
        await interaction.response.send_message("❌ Dieses Spiel gibt es nicht. Nutze `/spiele`.", ephemeral=True)
        return

    heim, auswaerts, status, th, ta = spiel

    if not tippen_erlaubt(status, th, ta):
        await interaction.response.send_message("❌ Für dieses Spiel kann man nicht mehr tippen.", ephemeral=True)
        return

    cur.execute("""
    INSERT OR REPLACE INTO tipps
    (user_id, spiel_id, tore_heim, tore_auswaerts)
    VALUES (?, ?, ?, ?)
    """, (interaction.user.id, spiel_id, tore_heim, tore_auswaerts))

    db.commit()

    await interaction.response.send_message(
        f"✅ Dein Tipp für **{heim} vs {auswaerts}** wurde gespeichert: **{tore_heim}:{tore_auswaerts}**",
        ephemeral=True
    )


@bot.tree.command(name="meine_tipps", description="Zeigt deine abgegebenen Tipps")
async def meine_tipps(interaction: discord.Interaction):
    cur.execute("""
    SELECT s.heim, s.auswaerts, t.tore_heim, t.tore_auswaerts
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
    for heim, auswaerts, th, ta in rows:
        text += f"**{heim} vs {auswaerts}** → {th}:{ta}\n"

    if len(text) > 3900:
        text = text[:3900] + "\n..."

    await interaction.response.send_message(embed=make_embed("Deine Tipps", text), ephemeral=True)


@bot.tree.command(name="rangliste", description="Zeigt die aktuelle Rangliste")
async def rangliste(interaction: discord.Interaction):
    cur.execute("""
    SELECT t.user_id, t.tore_heim, t.tore_auswaerts, s.tore_heim, s.tore_auswaerts
    FROM tipps t
    JOIN spiele s ON t.spiel_id = s.id
    """)

    scores = {}

    for user_id, tipp_h, tipp_a, echt_h, echt_a in cur.fetchall():
        scores[user_id] = scores.get(user_id, 0) + punkte(tipp_h, tipp_a, echt_h, echt_a)

    if not scores:
        await interaction.response.send_message("Noch keine Tipps vorhanden.")
        return

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    text = ""
    for platz, (user_id, score) in enumerate(top[:10], start=1):
        user = await bot.fetch_user(user_id)
        preis = f" — Gewinn: **{PREISE[platz]}**" if platz in PREISE else ""
        text += f"**#{platz}** {user.mention} — `{score}` Punkte{preis}\n"

    await interaction.response.send_message(embed=make_embed("Rangliste", text))


@bot.tree.command(name="gewinner", description="Zeigt die finalen Gewinner und Preise")
async def gewinner(interaction: discord.Interaction):
    cur.execute("""
    SELECT t.user_id, t.tore_heim, t.tore_auswaerts, s.tore_heim, s.tore_auswaerts
    FROM tipps t
    JOIN spiele s ON t.spiel_id = s.id
    """)

    scores = {}

    for user_id, tipp_h, tipp_a, echt_h, echt_a in cur.fetchall():
        scores[user_id] = scores.get(user_id, 0) + punkte(tipp_h, tipp_a, echt_h, echt_a)

    if not scores:
        await interaction.response.send_message("Noch keine Gewinner vorhanden.")
        return

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]

    text = ""
    for platz, (user_id, score) in enumerate(top, start=1):
        user = await bot.fetch_user(user_id)
        text += f"**Platz {platz}:** {user.mention} — `{score}` Punkte — **{PREISE[platz]}**\n"

    await interaction.response.send_message(embed=make_embed("Gewinner", text))


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

        teams_response = requests.get(TEAM_API_URL, timeout=20)
        teams_response.raise_for_status()
        teams_data = teams_response.json()

        if isinstance(games_data, dict):
            games = games_data.get("games") or games_data.get("data") or games_data.get("matches") or games_data.get("response") or []
        else:
            games = games_data

        if isinstance(teams_data, dict):
            teams = teams_data.get("teams") or teams_data.get("data") or teams_data.get("response") or []
        else:
            teams = teams_data

        first_game = games[0] if games else {}
        first_team = teams[0] if teams else {}

        import json
        text = "**GAME KEYS:**\n```" + str(list(first_game.keys()))[:900] + "```\n"
        text += "**FIRST GAME:**\n```json\n" + json.dumps(first_game, ensure_ascii=False, indent=2)[:1200] + "\n```\n"
        text += "**TEAM KEYS:**\n```" + str(list(first_team.keys()))[:900] + "```\n"
        text += "**FIRST TEAM:**\n```json\n" + json.dumps(first_team, ensure_ascii=False, indent=2)[:1200] + "\n```"

        if len(text) > 3900:
            text = text[:3900] + "\n... gekürzt"

        await interaction.followup.send(text, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Debug Fehler: `{e}`", ephemeral=True)


bot.run(TOKEN)
