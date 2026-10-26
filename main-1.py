import os
import random
import sqlite3
import time
from datetime import timedelta, time as datetime_time
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID", "")
DB_FILE = os.getenv("DB_FILE", "fractale.db")

LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "")
REPORT_CHANNEL_ID = os.getenv("REPORT_CHANNEL_ID", "")
EVENING_CHANNEL_ID = os.getenv("EVENING_CHANNEL_ID", "")

MODERATOR_ROLE_ID = int(os.getenv("MODERATOR_ROLE_ID", "0"))
CO_FOUNDER_ROLE_ID = int(os.getenv("CO_FOUNDER_ROLE_ID", "0"))
FOUNDER_ROLE_ID = int(os.getenv("FOUNDER_ROLE_ID", "0"))

COOLDOWN_REP = 24 * 60 * 60
REPORT_COOLDOWN = 10 * 60
# Liste facultative de termes à surveiller, séparés par des virgules dans .env.
# Laisse cette variable vide pour ne pas filtrer automatiquement de mots.
MODERATION_MOTS_INTERDITS = [
    mot.strip().casefold()
    for mot in os.getenv("MODERATION_MOTS_INTERDITS", "").split(",")
    if mot.strip()
]
MAX_REASON_LENGTH = 500
MAX_SUGGESTION_LENGTH = 500

if not TOKEN:
    raise RuntimeError(
        "Configure DISCORD_TOKEN dans le fichier .env."
    )

# ============================================================
# BASE DE DONNEES SQLITE
# ============================================================

db = sqlite3.connect(DB_FILE, timeout=10)
db.row_factory = sqlite3.Row
db.execute("PRAGMA foreign_keys = ON")
db.execute("PRAGMA busy_timeout = 10000")

db.executescript("""
CREATE TABLE IF NOT EXISTS reputation (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    score INTEGER NOT NULL DEFAULT 0 CHECK(score >= 0),
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS rep_cooldowns (
    guild_id INTEGER NOT NULL,
    giver_id INTEGER NOT NULL,
    receiver_id INTEGER NOT NULL,
    given_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, giver_id, receiver_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    reporter_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'approved', 'rejected')),
    moderator_id INTEGER,
    created_at INTEGER NOT NULL,
    reviewed_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_reports_pending
ON reports(guild_id, status, created_at);

CREATE TABLE IF NOT EXISTS negative_reputation (
    report_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(report_id) REFERENCES reports(id)
);

CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    suggestion TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'accepted', 'rejected')),
    moderator_id INTEGER,
    created_at INTEGER NOT NULL,
    reviewed_at INTEGER
);

CREATE TABLE IF NOT EXISTS moderation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    target_id INTEGER,
    reason TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fe_tournaments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    creator_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    event_date TEXT NOT NULL DEFAULT 'À définir',
    max_players INTEGER NOT NULL DEFAULT 16 CHECK(max_players BETWEEN 2 AND 64),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
    message_id INTEGER UNIQUE,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fe_tournament_players (
    tournament_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at INTEGER NOT NULL,
    PRIMARY KEY (tournament_id, user_id),
    FOREIGN KEY(tournament_id) REFERENCES fe_tournaments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fe_tournaments_guild_status
ON fe_tournaments(guild_id, status, created_at);

CREATE TABLE IF NOT EXISTS auto_moderation_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_mod_user_time
ON auto_moderation_incidents(guild_id, user_id, created_at);

CREATE TABLE IF NOT EXISTS teammate_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    game TEXT NOT NULL,
    rank TEXT NOT NULL,
    mode TEXT NOT NULL,
    platform TEXT NOT NULL,
    availability TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_teammate_searches_open
ON teammate_searches(guild_id, status, created_at);

CREATE TABLE IF NOT EXISTS esport_profiles (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    main_game TEXT NOT NULL DEFAULT '',
    rank TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    playstyle TEXT NOT NULL DEFAULT '',
    availability TEXT NOT NULL DEFAULT '',
    bio TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_esport_profiles_game
ON esport_profiles(guild_id, main_game);
""")

db.executescript("""
CREATE TABLE IF NOT EXISTS fe_user_activity (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS fe_user_badges (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    badge_key TEXT NOT NULL,
    awarded_by INTEGER,
    awarded_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id, badge_key)
);
""")
db.commit()


# ============================================================
# RECRUTEMENT AVANCÉ — TABLES SQLITE
# ============================================================

db.executescript("""
CREATE TABLE IF NOT EXISTS fe_recruitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    team_name TEXT NOT NULL,
    game TEXT NOT NULL,
    positions TEXT NOT NULL,
    description TEXT NOT NULL,
    slots INTEGER NOT NULL DEFAULT 1 CHECK(slots BETWEEN 1 AND 50),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
    created_at INTEGER NOT NULL,
    closed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fe_recruitments_open
ON fe_recruitments(guild_id, status, created_at);

CREATE TABLE IF NOT EXISTS fe_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recruitment_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    applicant_id INTEGER NOT NULL,
    in_game_name TEXT NOT NULL,
    experience TEXT NOT NULL,
    motivation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'accepted', 'rejected')),
    created_at INTEGER NOT NULL,
    decision_by INTEGER,
    decision_reason TEXT,
    decided_at INTEGER,
    FOREIGN KEY(recruitment_id) REFERENCES fe_recruitments(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fe_applications_recruitment_status
ON fe_applications(recruitment_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_fe_applications_applicant
ON fe_applications(guild_id, applicant_id, created_at);
""")
db.commit()

# ============================================================
# MODÉRATION COMPÉTITIVE — DOSSIERS DE SIGNALEMENT ROCKET LEAGUE
# ============================================================

db.executescript("""
CREATE TABLE IF NOT EXISTS competitive_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    reporter_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    match_id INTEGER,
    evidence_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'resolved', 'dismissed')),
    decision TEXT NOT NULL DEFAULT '',
    staff_comment TEXT NOT NULL DEFAULT '',
    moderator_id INTEGER,
    created_at INTEGER NOT NULL,
    reviewed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_competitive_reports_pending
ON competitive_reports(guild_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_competitive_reports_target
ON competitive_reports(guild_id, target_id, created_at);
""")
db.commit()

# Sanctions compétitives : indépendantes des sanctions Discord classiques.
db.executescript("""
CREATE TABLE IF NOT EXISTS competitive_sanctions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    sanction_type TEXT NOT NULL CHECK(sanction_type IN ('avertissement', 'suspension_tournoi', 'exclusion_temporaire')),
    reason TEXT NOT NULL,
    issued_by INTEGER NOT NULL,
    issued_at INTEGER NOT NULL,
    expires_at INTEGER,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'revoked', 'expired')),
    revoked_by INTEGER,
    revoked_at INTEGER,
    revoke_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_competitive_sanctions_user
ON competitive_sanctions(guild_id, user_id, status, expires_at);
""")
db.commit()

# Historique permanent des décisions de modération compétitive.
db.executescript("""
CREATE TABLE IF NOT EXISTS competitive_decision_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    sanction_id INTEGER NOT NULL,
    target_user_id INTEGER NOT NULL,
    actor_id INTEGER NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('sanction_creee', 'sanction_levee')),
    sanction_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_competitive_history_guild_time
ON competitive_decision_history(guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_competitive_history_target
ON competitive_decision_history(guild_id, target_user_id, created_at DESC);
""")
db.commit()

# Recours contre les sanctions compétitives. Un recours unique par sanction.
db.executescript("""
CREATE TABLE IF NOT EXISTS competitive_appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    sanction_id INTEGER NOT NULL,
    appellant_id INTEGER NOT NULL,
    appeal_reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'rejected')),
    created_at INTEGER NOT NULL,
    reviewed_by INTEGER,
    reviewed_at INTEGER,
    staff_reason TEXT NOT NULL DEFAULT '',
    UNIQUE(guild_id, sanction_id),
    FOREIGN KEY(sanction_id) REFERENCES competitive_sanctions(id)
);
CREATE INDEX IF NOT EXISTS idx_competitive_appeals_pending
ON competitive_appeals(guild_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_competitive_appeals_player
ON competitive_appeals(guild_id, appellant_id, created_at DESC);
""")
db.commit()

# ============================================================
# OUTILS AVANCÉS DU STAFF — CONFIGURATION ET NOTES INTERNES
# ============================================================

db.executescript("""
CREATE TABLE IF NOT EXISTS staff_role_config (
    guild_id INTEGER PRIMARY KEY,
    moderator_role_id INTEGER NOT NULL,
    co_founder_role_id INTEGER NOT NULL,
    founder_role_id INTEGER NOT NULL,
    configured_by INTEGER NOT NULL,
    configured_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS staff_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_staff_notes_target
ON staff_notes(guild_id, target_id, created_at);
""")
db.commit()

# ============================================================
# SAISONS COMPÉTITIVES — TABLES SQLITE
# ============================================================

db.executescript("""
CREATE TABLE IF NOT EXISTS fe_seasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    creator_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    game TEXT NOT NULL,
    format TEXT NOT NULL CHECK(format IN ('league', 'elimination')),
    participant_type TEXT NOT NULL CHECK(participant_type IN ('players', 'teams')),
    max_entries INTEGER NOT NULL DEFAULT 16 CHECK(max_entries BETWEEN 2 AND 32),
    status TEXT NOT NULL DEFAULT 'registration' CHECK(status IN ('registration', 'active', 'finished', 'cancelled')),
    champion_entry_id INTEGER,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fe_seasons_guild_status
ON fe_seasons(guild_id, status, created_at);

CREATE TABLE IF NOT EXISTS fe_season_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    entry_name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(season_id, owner_id),
    UNIQUE(season_id, entry_name COLLATE NOCASE),
    FOREIGN KEY(season_id) REFERENCES fe_seasons(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fe_season_entries_season
ON fe_season_entries(season_id, created_at);

CREATE TABLE IF NOT EXISTS fe_season_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    round_no INTEGER NOT NULL DEFAULT 1,
    entry_a_id INTEGER NOT NULL,
    entry_b_id INTEGER NOT NULL,
    score_a INTEGER,
    score_b INTEGER,
    winner_entry_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'completed')),
    created_at INTEGER NOT NULL,
    completed_at INTEGER,
    FOREIGN KEY(season_id) REFERENCES fe_seasons(id) ON DELETE CASCADE,
    FOREIGN KEY(entry_a_id) REFERENCES fe_season_entries(id),
    FOREIGN KEY(entry_b_id) REFERENCES fe_season_entries(id),
    FOREIGN KEY(winner_entry_id) REFERENCES fe_season_entries(id)
);
CREATE INDEX IF NOT EXISTS idx_fe_season_matches_season_round
ON fe_season_matches(season_id, round_no, status);
""")
db.commit()





# ============================================================
# DISPONIBILITÉS DES JOUEURS — SQLITE
# ============================================================

db.executescript("""
CREATE TABLE IF NOT EXISTS fe_player_availability (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    game TEXT NOT NULL,
    days TEXT NOT NULL,
    hours TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id, game)
);
CREATE INDEX IF NOT EXISTS idx_fe_player_availability_game
ON fe_player_availability(guild_id, game);
""")
db.commit()

db.executescript("""
CREATE TABLE IF NOT EXISTS fe_scrim_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    game TEXT NOT NULL,
    level TEXT NOT NULL,
    game_format TEXT NOT NULL,
    availability TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'matched', 'closed')),
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fe_scrim_searches_open
ON fe_scrim_searches(guild_id, status, game, created_at);
CREATE TABLE IF NOT EXISTS fe_scrim_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    from_search_id INTEGER NOT NULL,
    to_search_id INTEGER NOT NULL,
    proposer_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'rejected', 'cancelled')),
    created_at INTEGER NOT NULL,
    reviewed_at INTEGER,
    UNIQUE(from_search_id, to_search_id),
    FOREIGN KEY(from_search_id) REFERENCES fe_scrim_searches(id),
    FOREIGN KEY(to_search_id) REFERENCES fe_scrim_searches(id)
);
CREATE INDEX IF NOT EXISTS idx_fe_scrim_proposals_pending
ON fe_scrim_proposals(guild_id, status, created_at);
""")
db.commit()

# ============================================================
# INITIALISATION DU BOT
# ============================================================

intents = discord.Intents.default()
# Nécessaire pour analyser le contenu des messages. Active aussi
# "Message Content Intent" dans le portail développeur Discord.
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

games = {}


# ============================================================
# BADGES ET SUCCÈS (récupérés depuis une autre sauvegarde)
# ============================================================

BADGES = {
    "premier_message": ("👋", "Premier pas", "Tu as envoyé ton premier message."),
    "bavard_100": ("💬", "Bavard", "Tu as envoyé 100 messages."),
    "pilier_500": ("🏛️", "Pilier de la communauté", "Tu as envoyé 500 messages."),
    "entraide": ("🤝", "Esprit d’équipe", "Le staff t’a récompensé pour ton aide à la communauté."),
    "rare": ("👑", "Succès rare", "Un membre du staff t’a accordé un badge rare."),
}

def grant_badge(guild_id: int, user_id: int, badge_key: str, awarded_by: int | None = None) -> bool:
    """Ajoute un badge une seule fois. Retourne True si le badge est nouveau."""
    if badge_key not in BADGES:
        return False
    cursor = db.execute(
        "INSERT OR IGNORE INTO fe_user_badges (guild_id, user_id, badge_key, awarded_by, awarded_at) VALUES (?, ?, ?, ?, ?)",
        (guild_id, user_id, badge_key, awarded_by, int(time.time()))
    )
    db.commit()
    return cursor.rowcount == 1

def badge_announcement(badge_key: str) -> str:
    emoji, name, description = BADGES[badge_key]
    return f"{emoji} **Nouveau badge : {name} !**\n{description}"



# ============================================================
# SYNCHRONISATION DES COMMANDES
# ============================================================

async def sync_commands():
    try:
        if GUILD_ID.isdigit():
            guild = discord.Object(id=int(GUILD_ID))

            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)

            print(
                f"{len(synced)} commandes synchronisées "
                f"sur le serveur {GUILD_ID}."
            )
        else:
            synced = await bot.tree.sync()

            print(
                f"{len(synced)} commandes globales synchronisées."
            )

    except discord.HTTPException as error:
        print(f"Erreur de synchronisation : {error}")


# ============================================================
# CONNEXION DU BOT
# ============================================================

@bot.event
async def on_ready():
    if not getattr(bot, "commands_synced", False):
        await sync_commands()
        bot.commands_synced = True

    # Démarre les événements automatiques du soir.
    if not evenement_du_soir.is_running():
        evenement_du_soir.start()

    print(
        f"Bot connecté : {bot.user} "
        f"(ID : {bot.user.id})"
    )


@bot.event
async def on_guild_remove(guild):
    print(f"Le bot a quitté {guild.name} ({guild.id}).")

# ============================================================
# PARTIE 2/5 — GRADES, OUTILS ET RÉPUTATION
# ============================================================

# ----------------------------
# GRADES DU STAFF
# ----------------------------

def get_staff_level(member: discord.Member) -> int:
    """Retourne le grade staff, avec les rôles configurés en base et .env."""
    if member.id == member.guild.owner_id:
        return 3

    role_ids = {role.id for role in member.roles}
    moderator_id = MODERATOR_ROLE_ID
    co_founder_id = CO_FOUNDER_ROLE_ID
    founder_id = FOUNDER_ROLE_ID

    try:
        configured = db.execute(
            "SELECT moderator_role_id, co_founder_role_id, founder_role_id "
            "FROM staff_role_config WHERE guild_id = ?",
            (member.guild.id,)
        ).fetchone()
        if configured:
            moderator_id = int(configured["moderator_role_id"] or moderator_id)
            co_founder_id = int(configured["co_founder_role_id"] or co_founder_id)
            founder_id = int(configured["founder_role_id"] or founder_id)
    except sqlite3.Error:
        # Les rôles .env restent utilisables si la lecture de configuration échoue.
        pass

    if founder_id and founder_id in role_ids:
        return 3
    if co_founder_id and co_founder_id in role_ids:
        return 2
    if moderator_id and moderator_id in role_ids:
        return 1
    return 0


def require_level(member: discord.Member, level: int) -> bool:
    return get_staff_level(member) >= level


def moderator_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False

        if not require_level(interaction.user, 1):
            raise app_commands.CheckFailure(
                "Cette commande est réservée aux modérateurs et au staff supérieur."
            )

        return True

    return app_commands.check(predicate)


def co_founder_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False

        if not require_level(interaction.user, 2):
            raise app_commands.CheckFailure(
                "Cette commande est réservée aux cofondateurs et aux fondateurs."
            )

        return True

    return app_commands.check(predicate)


def founder_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False

        if not require_level(interaction.user, 3):
            raise app_commands.CheckFailure(
                "Cette commande est réservée aux fondateurs."
            )

        return True

    return app_commands.check(predicate)


# ============================================================
# CÉRÉMONIE DE FIN DE SAISON
# ============================================================

@bot.tree.command(
    name="saison_ceremonie",
    description="Publier la cérémonie officielle d'une saison terminée."
)
@app_commands.describe(
    saison_id="Numéro de la saison terminée",
    salon="Salon où publier la cérémonie",
    message="Message personnalisé facultatif"
)
@moderator_only()
async def saison_ceremonie(
    interaction: discord.Interaction,
    saison_id: app_commands.Range[int, 1, 2147483647],
    salon: discord.TextChannel,
    message: app_commands.Range[str, 0, 1000] = ""
):
    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.",
            ephemeral=True
        )
        return

    season = db.execute(
        "SELECT * FROM fe_seasons WHERE id = ? AND guild_id = ?",
        (saison_id, interaction.guild_id)
    ).fetchone()

    if season is None:
        await interaction.response.send_message(
            "❌ Saison introuvable sur ce serveur.", ephemeral=True
        )
        return

    if season["status"] != "finished":
        await interaction.response.send_message(
            "⏳ La cérémonie ne peut être publiée que lorsque la saison est terminée. "
            "Enregistre les résultats manquants avec `/saison_resultat`.",
            ephemeral=True
        )
        return

    entries = db.execute(
        """SELECT e.id, e.entry_name,
                  SUM(CASE WHEN m.winner_entry_id = e.id THEN 3
                           WHEN m.status = 'completed' AND m.winner_entry_id IS NULL THEN 1
                           ELSE 0 END) AS points,
                  SUM(CASE WHEN m.winner_entry_id = e.id THEN 1 ELSE 0 END) AS wins,
                  SUM(CASE WHEN m.status = 'completed'
                            AND m.winner_entry_id IS NOT NULL
                            AND m.winner_entry_id != e.id THEN 1 ELSE 0 END) AS losses,
                  SUM(CASE WHEN m.status = 'completed' THEN 1 ELSE 0 END) AS played
           FROM fe_season_entries e
           LEFT JOIN fe_season_matches m
             ON (m.entry_a_id = e.id OR m.entry_b_id = e.id)
           WHERE e.season_id = ?
           GROUP BY e.id
           ORDER BY points DESC, wins DESC, e.entry_name COLLATE NOCASE ASC
           LIMIT 10""",
        (saison_id,)
    ).fetchall()

    if not entries:
        await interaction.response.send_message(
            "Cette saison ne contient aucun participant.", ephemeral=True
        )
        return

    champion_name = None
    if season["champion_entry_id"]:
        champion = db.execute(
            "SELECT entry_name FROM fe_season_entries WHERE id = ? AND season_id = ?",
            (season["champion_entry_id"], saison_id)
        ).fetchone()
        if champion:
            champion_name = champion["entry_name"]

    ceremony = discord.Embed(
        title=f"🏆 CÉRÉMONIE DE FIN DE SAISON — {season['name']}",
        description=(
            f"🎮 **Jeu :** {season['game']}\n"
            f"📋 **Format :** {'Championnat' if season['format'] == 'league' else 'Élimination directe'}\n"
            f"👥 **Participants :** {len(db.execute('SELECT id FROM fe_season_entries WHERE season_id = ?', (saison_id,)).fetchall())}\n\n"
            "La saison est officiellement terminée. Félicitations à tous les participants !"
        ),
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow()
    )

    if champion_name:
        ceremony.add_field(
            name="🥇 CHAMPION DE LA SAISON",
            value=f"**{champion_name}**",
            inline=False
        )

    top_lines = []
    for position, row in enumerate(entries, 1):
        wins = int(row["wins"] or 0)
        losses = int(row["losses"] or 0)
        played = int(row["played"] or 0)
        points = int(row["points"] or 0)
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(position, f"**{position}.**")
        top_lines.append(
            f"{medal} **{row['entry_name']}** — {points} pts · "
            f"{wins} V / {losses} D · {played} matchs"
        )
    ceremony.add_field(
        name="📊 Classement final",
        value="\n".join(top_lines)[:1024],
        inline=False
    )

    most_wins = max(entries, key=lambda row: int(row["wins"] or 0))
    if int(most_wins["wins"] or 0) > 0:
        ceremony.add_field(
            name="🔥 Meilleur bilan de victoires",
            value=f"**{most_wins['entry_name']}** — {int(most_wins['wins'] or 0)} victoire(s)",
            inline=False
        )

    if message.strip():
        ceremony.add_field(
            name="💬 Message du staff",
            value=message.strip()[:1024],
            inline=False
        )

    ceremony.set_footer(text=f"Fractale Esport • Saison #{saison_id}")
    try:
        await salon.send(
            embed=ceremony,
            allowed_mentions=discord.AllowedMentions.none()
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Je n'ai pas la permission d'envoyer des messages ou des embeds dans ce salon.",
            ephemeral=True
        )
        return
    except discord.HTTPException:
        await interaction.response.send_message(
            "❌ Discord n'a pas accepté la publication. Réessaie dans quelques instants.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"✅ La cérémonie de **{season['name']}** a été publiée dans {salon.mention}.",
        ephemeral=True
    )




# ============================================================
# COMMANDES DE BADGES
# ============================================================

@bot.tree.command(name="badges", description="Voir les badges que tu as débloqués ou ceux d’un membre.")
@app_commands.describe(membre="Le membre dont tu veux voir les badges (facultatif)")
async def badges(interaction: discord.Interaction, membre: discord.Member | None = None):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    cible = membre or interaction.user
    rows = db.execute(
        "SELECT badge_key, awarded_at FROM fe_user_badges WHERE guild_id = ? AND user_id = ? ORDER BY awarded_at ASC",
        (interaction.guild_id, cible.id)
    ).fetchall()
    embed = discord.Embed(title=f"🏅 Badges de {cible.display_name}", color=discord.Color.gold())
    if rows:
        lines = []
        for row in rows:
            key = row["badge_key"]
            if key in BADGES:
                emoji, name, description = BADGES[key]
                lines.append(f"{emoji} **{name}** — {description}")
        embed.description = "\n".join(lines) if lines else "Aucun badge pour le moment."
    else:
        embed.description = "Aucun badge pour le moment. Envoie des messages pour débloquer les badges d’activité, ou le staff peut t’attribuer un badge spécial."
    embed.set_footer(text=f"{len(rows)} badge(s) débloqué(s)")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="badge_donner", description="Donner un badge d’entraide ou un badge rare à un membre.")
@app_commands.describe(membre="Le membre à récompenser", badge="entraide ou rare")
@moderator_only()
async def badge_donner(interaction: discord.Interaction, membre: discord.Member, badge: str):
    badge = badge.strip().lower()
    if badge not in ("entraide", "rare"):
        await interaction.response.send_message("Choisis `entraide` ou `rare`.", ephemeral=True)
        return
    if membre.bot:
        await interaction.response.send_message("Les badges sont réservés aux membres humains.", ephemeral=True)
        return
    if not grant_badge(interaction.guild_id, membre.id, badge, interaction.user.id):
        await interaction.response.send_message("Ce membre possède déjà ce badge, ou le badge est invalide.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"{badge_announcement(badge)}\nAttribué à {membre.mention} par {interaction.user.mention}.",
        allowed_mentions=discord.AllowedMentions.none()
    )


# ============================================================
# TOURNOIS AUTOMATIQUES — ROCKET LEAGUE 1v1 / 2v2 / 3v3
# ============================================================

TOURNAMENT_SIGNUP_CUSTOM_ID = "fractale:tournament_signup:v1"

# Migration douce : on conserve les tournois déjà enregistrés dans SQLite.
def migrate_tournament_schema():
    columns = {
        row["name"] for row in db.execute("PRAGMA table_info(fe_tournaments)").fetchall()
    }
    if "mode" not in columns:
        db.execute("ALTER TABLE fe_tournaments ADD COLUMN mode TEXT NOT NULL DEFAULT '1v1'")
    if "tournament_state" not in columns:
        db.execute("ALTER TABLE fe_tournaments ADD COLUMN tournament_state TEXT NOT NULL DEFAULT 'signup'")

    db.executescript("""
    CREATE TABLE IF NOT EXISTS fe_tournament_teams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER NOT NULL,
        team_no INTEGER NOT NULL,
        members TEXT NOT NULL,
        FOREIGN KEY(tournament_id) REFERENCES fe_tournaments(id) ON DELETE CASCADE,
        UNIQUE(tournament_id, team_no)
    );
    CREATE TABLE IF NOT EXISTS fe_tournament_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER NOT NULL,
        round_no INTEGER NOT NULL,
        match_no INTEGER NOT NULL,
        team1_id INTEGER,
        team2_id INTEGER,
        winner_team_id INTEGER,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'finished')),
        FOREIGN KEY(tournament_id) REFERENCES fe_tournaments(id) ON DELETE CASCADE,
        FOREIGN KEY(team1_id) REFERENCES fe_tournament_teams(id),
        FOREIGN KEY(team2_id) REFERENCES fe_tournament_teams(id),
        FOREIGN KEY(winner_team_id) REFERENCES fe_tournament_teams(id),
        UNIQUE(tournament_id, round_no, match_no)
    );
    CREATE INDEX IF NOT EXISTS idx_fe_tournament_matches
    ON fe_tournament_matches(tournament_id, round_no, match_no);
    """)
    db.commit()


migrate_tournament_schema()


def tournament_player_count(tournament_id: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM fe_tournament_players WHERE tournament_id = ?",
        (tournament_id,)
    ).fetchone()
    return int(row[0])


def tournament_team_size(mode: str) -> int:
    return {"1v1": 1, "2v2": 2, "3v3": 3}.get(mode, 1)


def tournament_team_label(team_id):
    if team_id is None:
        return "BYE (exempt)"
    row = db.execute(
        "SELECT team_no, members FROM fe_tournament_teams WHERE id = ?", (team_id,)
    ).fetchone()
    if row is None:
        return "Équipe inconnue"
    mentions = [f"<@{user_id}>" for user_id in row["members"].split(",") if user_id.strip()]
    return f"**Équipe #{row['team_no']}** : " + (", ".join(mentions) if mentions else "aucun membre")


def create_tournament_round(tournament_id: int, round_no: int, winner_ids: list[int]):
    """Crée une manche, avec exemption automatique si le nombre d'équipes est impair."""
    match_no = 1
    for index in range(0, len(winner_ids), 2):
        team1 = winner_ids[index]
        team2 = winner_ids[index + 1] if index + 1 < len(winner_ids) else None
        if team2 is None:
            db.execute(
                """INSERT INTO fe_tournament_matches
                   (tournament_id, round_no, match_no, team1_id, team2_id, winner_team_id, status)
                   VALUES (?, ?, ?, ?, NULL, ?, 'finished')""",
                (tournament_id, round_no, match_no, team1, team1)
            )
        else:
            db.execute(
                """INSERT INTO fe_tournament_matches
                   (tournament_id, round_no, match_no, team1_id, team2_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (tournament_id, round_no, match_no, team1, team2)
            )
        match_no += 1
    db.commit()


def advance_tournament_if_ready(tournament_id: int) -> str:
    """Fait avancer les vainqueurs; renvoie une phrase sur l'état du tournoi."""
    while True:
        round_row = db.execute(
            "SELECT MAX(round_no) FROM fe_tournament_matches WHERE tournament_id = ?",
            (tournament_id,)
        ).fetchone()
        round_no = round_row[0]
        if round_no is None:
            return "Aucun match n'a été créé."
        matches = db.execute(
            "SELECT * FROM fe_tournament_matches WHERE tournament_id = ? AND round_no = ? ORDER BY match_no",
            (tournament_id, round_no)
        ).fetchall()
        if not matches or any(match["status"] != "finished" for match in matches):
            return f"Manche {round_no} en cours."
        winners = [int(match["winner_team_id"]) for match in matches if match["winner_team_id"] is not None]
        if len(winners) == 1:
            db.execute(
                "UPDATE fe_tournaments SET tournament_state = 'finished' WHERE id = ?",
                (tournament_id,)
            )
            db.commit()
            return f"🏆 Tournoi terminé ! Vainqueur : {tournament_team_label(winners[0])}"
        if len(winners) < 2:
            db.execute("UPDATE fe_tournaments SET tournament_state = 'finished' WHERE id = ?", (tournament_id,))
            db.commit()
            return "Le tournoi est terminé."
        create_tournament_round(tournament_id, int(round_no) + 1, winners)
        # La nouvelle manche peut ne contenir que des exemptions dans un cas exceptionnel.
        # La boucle vérifie alors automatiquement si elle est déjà terminée.


class TournamentSignupView(discord.ui.View):
    """Bouton d'inscription persistant, fonctionnel après redémarrage."""

    def __init__(self):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="S'inscrire", style=discord.ButtonStyle.success,
            emoji="🎮", custom_id=TOURNAMENT_SIGNUP_CUSTOM_ID
        )
        button.callback = self.signup_callback
        self.add_item(button)

    async def signup_callback(self, interaction: discord.Interaction):
        if interaction.guild_id is None or interaction.message is None:
            await interaction.response.send_message(
                "Cette inscription doit être utilisée sur un serveur.", ephemeral=True
            )
            return
        row = db.execute(
            "SELECT id, guild_id, name, max_players, status, mode FROM fe_tournaments WHERE message_id = ?",
            (interaction.message.id,)
        ).fetchone()
        if row is None or int(row["guild_id"]) != interaction.guild_id:
            await interaction.response.send_message("Je ne retrouve pas ce tournoi. Préviens le staff.", ephemeral=True)
            return
        if row["status"] != "open":
            await interaction.response.send_message("Les inscriptions à ce tournoi sont fermées.", ephemeral=True)
            return
        if interaction.user.bot:
            await interaction.response.send_message("Les bots ne peuvent pas s'inscrire à un tournoi.", ephemeral=True)
            return
        sanction = get_active_competitive_restriction(interaction.guild_id, interaction.user.id)
        if sanction is not None:
            kind = "suspension de tournoi" if sanction["sanction_type"] == "suspension_tournoi" else "exclusion temporaire des compétitions"
            expiry = f" jusqu'au <t:{int(sanction['expires_at'])}:F>" if sanction["expires_at"] else ""
            await interaction.response.send_message(
                f"⛔ Tu ne peux pas t'inscrire à ce tournoi : une sanction active ({kind}) est en cours{expiry}. "
                f"Raison : {sanction['reason']}", ephemeral=True
            )
            return
        tournament_id = int(row["id"])
        try:
            db.execute(
                "INSERT INTO fe_tournament_players (tournament_id, user_id, joined_at) VALUES (?, ?, ?)",
                (tournament_id, interaction.user.id, int(time.time()))
            )
            db.commit()
        except sqlite3.IntegrityError:
            await interaction.response.send_message("Tu es déjà inscrit à ce tournoi.", ephemeral=True)
            return
        count = tournament_player_count(tournament_id)
        if count > int(row["max_players"]):
            db.execute("DELETE FROM fe_tournament_players WHERE tournament_id = ? AND user_id = ?", (tournament_id, interaction.user.id))
            db.commit()
            await interaction.response.send_message("Désolé, ce tournoi a atteint sa limite de joueurs.", ephemeral=True)
            return
        if interaction.message.embeds:
            embed = interaction.message.embeds[0]
            for index, field in enumerate(embed.fields):
                if field.name == "Joueurs inscrits":
                    embed.set_field_at(index, name="Joueurs inscrits", value=f"{count} / {row['max_players']}", inline=True)
                    break
            try:
                await interaction.message.edit(embed=embed, view=self)
            except (discord.Forbidden, discord.HTTPException):
                pass
        await interaction.response.send_message(
            f"✅ Tu es inscrit au tournoi **{row['name']}** ({row['mode']}) !", ephemeral=True
        )


@bot.tree.command(name="tournoi_creer", description="Créer un tournoi Rocket League et ouvrir les inscriptions.")
@app_commands.describe(nom="Nom du tournoi", mode="Format Rocket League", places="Nombre maximum de joueurs (2 à 64)", date_tournoi="Date ou horaire", description="Informations du tournoi")
@app_commands.choices(mode=[
    app_commands.Choice(name="1v1 — duel", value="1v1"),
    app_commands.Choice(name="2v2 — équipes aléatoires", value="2v2"),
    app_commands.Choice(name="3v3 — équipes aléatoires", value="3v3"),
])
@moderator_only()
async def tournoi_creer(
    interaction: discord.Interaction,
    nom: app_commands.Range[str, 3, 80],
    mode: app_commands.Choice[str],
    places: app_commands.Range[int, 2, 64] = 16,
    date_tournoi: str = "À définir",
    description: str = "Bienvenue ! Clique sur S'inscrire pour participer."
):
    if interaction.guild is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    nom = nom.strip()
    mode_value = mode.value
    description = description.strip()[:1500] or "Aucune description."
    date_tournoi = date_tournoi.strip()[:100] or "À définir"
    cursor = db.execute(
        """INSERT INTO fe_tournaments (guild_id, creator_id, name, description, event_date, max_players, status, created_at, mode, tournament_state)
           VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, 'signup')""",
        (interaction.guild.id, interaction.user.id, nom, description, date_tournoi, int(places), int(time.time()), mode_value)
    )
    tournament_id = int(cursor.lastrowid)
    db.commit()
    team_size = tournament_team_size(mode_value)
    if team_size == 1:
        format_text = "1v1 — chaque joueur joue individuellement"
    else:
        format_text = f"{mode_value} — le bot formera des équipes aléatoires de {team_size} joueurs"
    embed = discord.Embed(title=f"🏆 {nom}", description=description, color=discord.Color.gold())
    embed.add_field(name="Numéro du tournoi", value=f"#{tournament_id}", inline=True)
    embed.add_field(name="Format", value=format_text, inline=True)
    embed.add_field(name="Date / horaire", value=date_tournoi, inline=True)
    embed.add_field(name="Joueurs inscrits", value=f"0 / {places}", inline=True)
    embed.add_field(name="Statut", value="🟢 Inscriptions ouvertes", inline=False)
    embed.set_footer(text="Fractale Esport • Clique sur le bouton pour t'inscrire")
    await interaction.response.send_message(embed=embed, view=TournamentSignupView())
    message = await interaction.original_response()
    db.execute("UPDATE fe_tournaments SET message_id = ? WHERE id = ?", (message.id, tournament_id))
    db.commit()


@bot.tree.command(name="tournoi_liste", description="Afficher les tournois ouverts.")
async def tournoi_liste(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    rows = db.execute(
        "SELECT id, name, event_date, max_players, mode FROM fe_tournaments WHERE guild_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 15",
        (interaction.guild_id,)
    ).fetchall()
    if not rows:
        await interaction.response.send_message("Aucun tournoi n'a actuellement ses inscriptions ouvertes.", ephemeral=True)
        return
    embed = discord.Embed(title="🏆 Tournois ouverts — Fractale Esport", color=discord.Color.gold())
    for row in rows:
        count = tournament_player_count(int(row["id"]))
        embed.add_field(name=f"#{row['id']} • {row['name']}", value=f"🎮 {row['mode']} • 📅 {row['event_date']}\n👥 {count} / {row['max_players']} joueurs", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="tournoi_joueurs", description="Afficher les inscrits à un tournoi.")
@app_commands.describe(tournoi_id="Numéro du tournoi")
async def tournoi_joueurs(interaction: discord.Interaction, tournoi_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    tournament = db.execute("SELECT id, name, status, mode FROM fe_tournaments WHERE id = ? AND guild_id = ?", (tournoi_id, interaction.guild_id)).fetchone()
    if tournament is None:
        await interaction.response.send_message("Je ne trouve pas ce tournoi sur ce serveur.", ephemeral=True)
        return
    rows = db.execute("SELECT user_id FROM fe_tournament_players WHERE tournament_id = ? ORDER BY joined_at ASC", (tournoi_id,)).fetchall()
    players = [f"{i}. <@{row['user_id']}>" for i, row in enumerate(rows, 1)]
    embed = discord.Embed(title=f"👥 Inscrits — {tournament['name']} (#{tournoi_id})", description="\n".join(players) if players else "Personne n'est encore inscrit.", color=discord.Color.blurple())
    embed.set_footer(text=f"Format : {tournament['mode']} • Statut : {tournament['status']} • {len(rows)} joueur(s)")
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="tournoi_fermer", description="Fermer les inscriptions d'un tournoi.")
@app_commands.describe(tournoi_id="Numéro du tournoi à fermer")
@moderator_only()
async def tournoi_fermer(interaction: discord.Interaction, tournoi_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    row = db.execute("SELECT name, status, message_id FROM fe_tournaments WHERE id = ? AND guild_id = ?", (tournoi_id, interaction.guild_id)).fetchone()
    if row is None:
        await interaction.response.send_message("Je ne trouve pas ce tournoi sur ce serveur.", ephemeral=True)
        return
    if row["status"] == "closed":
        await interaction.response.send_message("Les inscriptions sont déjà fermées.", ephemeral=True)
        return
    db.execute("UPDATE fe_tournaments SET status = 'closed' WHERE id = ?", (tournoi_id,))
    db.commit()
    if row["message_id"]:
        try:
            message = await interaction.channel.fetch_message(int(row["message_id"]))
            if message.embeds:
                embed = message.embeds[0]
                for index, field in enumerate(embed.fields):
                    if field.name == "Statut":
                        embed.set_field_at(index, name="Statut", value="🔒 Inscriptions fermées", inline=False)
                        break
                await message.edit(embed=embed, view=None)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException, AttributeError):
            pass
    await interaction.response.send_message(f"🔒 Les inscriptions au tournoi **{row['name']}** (#{tournoi_id}) sont fermées.", ephemeral=True)


@bot.tree.command(name="tournoi_lancer", description="Fermer les inscriptions, former les équipes et lancer le tableau.")
@app_commands.describe(tournoi_id="Numéro du tournoi à lancer")
@moderator_only()
async def tournoi_lancer(interaction: discord.Interaction, tournoi_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    tournament = db.execute(
        "SELECT * FROM fe_tournaments WHERE id = ? AND guild_id = ?", (tournoi_id, interaction.guild_id)
    ).fetchone()
    if tournament is None:
        await interaction.response.send_message("Je ne trouve pas ce tournoi sur ce serveur.", ephemeral=True)
        return
    if tournament["tournament_state"] != "signup" or tournament["status"] != "open":
        await interaction.response.send_message("Ce tournoi a déjà été lancé ou n'est plus disponible.", ephemeral=True)
        return
    players = db.execute(
        "SELECT user_id FROM fe_tournament_players WHERE tournament_id = ? ORDER BY joined_at ASC",
        (tournoi_id,)
    ).fetchall()
    mode = tournament["mode"]
    team_size = tournament_team_size(mode)
    player_ids = [int(row["user_id"]) for row in players]
    if len(player_ids) < team_size * 2:
        await interaction.response.send_message(f"Il faut au moins {team_size * 2} joueurs inscrits pour lancer un tournoi {mode}.", ephemeral=True)
        return
    if len(player_ids) % team_size != 0:
        missing = team_size - (len(player_ids) % team_size)
        await interaction.response.send_message(
            f"Il y a {len(player_ids)} joueurs inscrits, ce qui ne permet pas de former toutes les équipes de {team_size}. "
            f"Il faut {missing} joueur(s) supplémentaire(s), ou retirer des inscriptions pour obtenir un multiple de {team_size}.", ephemeral=True
        )
        return
    random.shuffle(player_ids)
    team_ids = []
    for index in range(0, len(player_ids), team_size):
        members = player_ids[index:index + team_size]
        team_no = index // team_size + 1
        cursor = db.execute(
            "INSERT INTO fe_tournament_teams (tournament_id, team_no, members) VALUES (?, ?, ?)",
            (tournoi_id, team_no, ",".join(str(member) for member in members))
        )
        team_ids.append(int(cursor.lastrowid))
    db.execute("UPDATE fe_tournaments SET status = 'closed', tournament_state = 'in_progress' WHERE id = ?", (tournoi_id,))
    create_tournament_round(tournoi_id, 1, team_ids)
    db.commit()
    match_rows = db.execute(
        "SELECT * FROM fe_tournament_matches WHERE tournament_id = ? AND round_no = 1 ORDER BY match_no",
        (tournoi_id,)
    ).fetchall()
    lines = []
    for match in match_rows:
        lines.append(f"**Match #{match['id']}** — {tournament_team_label(match['team1_id'])}  ⚔️  {tournament_team_label(match['team2_id'])}")
    embed = discord.Embed(
        title=f"🏁 Tournoi lancé : {tournament['name']}",
        description=f"Format **{mode}** • {len(player_ids)} joueurs • {len(team_ids)} équipes\nLes équipes ont été tirées au sort.\n\n" + "\n".join(lines),
        color=discord.Color.green()
    )
    embed.set_footer(text="Le staff peut utiliser /tournoi_matchs puis /tournoi_resultat pour faire avancer le tournoi.")
    await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    state_message = advance_tournament_if_ready(tournoi_id)
    if "Tournoi terminé" in state_message:
        await interaction.followup.send(state_message, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="tournoi_matchs", description="Afficher les matchs et les équipes d'un tournoi.")
@app_commands.describe(tournoi_id="Numéro du tournoi")
async def tournoi_matchs(interaction: discord.Interaction, tournoi_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    tournament = db.execute("SELECT name, mode, tournament_state FROM fe_tournaments WHERE id = ? AND guild_id = ?", (tournoi_id, interaction.guild_id)).fetchone()
    if tournament is None:
        await interaction.response.send_message("Je ne trouve pas ce tournoi sur ce serveur.", ephemeral=True)
        return
    matches = db.execute(
        "SELECT * FROM fe_tournament_matches WHERE tournament_id = ? ORDER BY round_no, match_no",
        (tournoi_id,)
    ).fetchall()
    if not matches:
        await interaction.response.send_message("Le tableau n'est pas encore créé. Le staff doit lancer le tournoi avec /tournoi_lancer.", ephemeral=True)
        return
    lines = []
    for match in matches:
        status = "✅ Terminé" if match["status"] == "finished" else "🕒 À jouer"
        line = f"**Manche {match['round_no']} • Match #{match['id']}** ({status})\n{tournament_team_label(match['team1_id'])}  ⚔️  {tournament_team_label(match['team2_id'])}"
        if match["winner_team_id"] is not None:
            line += f"\n🏆 Vainqueur : Équipe #{db.execute('SELECT team_no FROM fe_tournament_teams WHERE id = ?', (match['winner_team_id'],)).fetchone()[0]}"
        lines.append(line)
    description = "\n\n".join(lines)
    if len(description) > 3900:
        description = description[:3850] + "\n… (tableau trop long pour un seul message)"
    embed = discord.Embed(title=f"🏆 Matchs — {tournament['name']} (#{tournoi_id})", description=description, color=discord.Color.blurple())
    embed.set_footer(text=f"Format : {tournament['mode']} • État : {tournament['tournament_state']}")
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="tournoi_resultat", description="Enregistrer le vainqueur d'un match de tournoi.")
@app_commands.describe(match_id="Identifiant du match affiché par /tournoi_matchs", equipe_gagnante="Numéro de l'équipe gagnante affiché dans le tableau")
@moderator_only()
async def tournoi_resultat(
    interaction: discord.Interaction,
    match_id: app_commands.Range[int, 1, 2147483647],
    equipe_gagnante: app_commands.Range[int, 1, 64]
):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    match = db.execute(
        """SELECT m.*, t.name AS tournament_name, t.guild_id, t.tournament_state
           FROM fe_tournament_matches m JOIN fe_tournaments t ON t.id = m.tournament_id
           WHERE m.id = ? AND t.guild_id = ?""",
        (match_id, interaction.guild_id)
    ).fetchone()
    if match is None:
        await interaction.response.send_message("Je ne trouve pas ce match sur ce serveur.", ephemeral=True)
        return
    if match["tournament_state"] != "in_progress":
        await interaction.response.send_message("Ce tournoi n'est pas en cours.", ephemeral=True)
        return
    if match["status"] == "finished":
        await interaction.response.send_message("Ce match est déjà terminé.", ephemeral=True)
        return
    teams = [match["team1_id"], match["team2_id"]]
    winning_team = db.execute(
        "SELECT id, team_no FROM fe_tournament_teams WHERE tournament_id = ? AND team_no = ?",
        (match["tournament_id"], equipe_gagnante)
    ).fetchone()
    if winning_team is None or int(winning_team["id"]) not in teams:
        valid = [db.execute("SELECT team_no FROM fe_tournament_teams WHERE id = ?", (team_id,)).fetchone()[0] for team_id in teams if team_id is not None]
        await interaction.response.send_message(f"Ce match oppose les équipes {', '.join('#' + str(n) for n in valid)}. Choisis l'une d'elles comme gagnante.", ephemeral=True)
        return
    db.execute(
        "UPDATE fe_tournament_matches SET winner_team_id = ?, status = 'finished' WHERE id = ?",
        (int(winning_team["id"]), match_id)
    )
    db.commit()
    progression = advance_tournament_if_ready(int(match["tournament_id"]))
    await interaction.response.send_message(
        f"✅ Résultat enregistré pour le match #{match_id} du tournoi **{match['tournament_name']}**.\n"
        f"🏆 Vainqueur : Équipe #{equipe_gagnante}.\n{progression}",
        allowed_mentions=discord.AllowedMentions.none()
    )


def has_higher_role(
    actor: discord.Member,
    target: discord.Member
) -> bool:
    """Empêche un membre du staff d'agir sur un supérieur."""

    if actor.id == actor.guild.owner_id:
        return actor.id != target.id

    if actor.id == target.id:
        return False

    if get_staff_level(actor) <= get_staff_level(target):
        return False

    return actor.top_role > target.top_role


# ----------------------------
# CONFIGURATION DU JEU DU SERVEUR
# ----------------------------

JEU_SERVEUR = "Rocket League"


def verifier_jeu_serveur(jeu: str, facultatif: bool = False) -> bool:
    """Le serveur Fractale Esport est dédié à Rocket League uniquement."""
    valeur = str(jeu or "").strip().casefold()
    if facultatif and not valeur:
        return True
    return valeur in {"rocket league", "rocketleague", "rl"}


def message_jeu_serveur() -> str:
    return "🚗 Fractale Esport est exclusivement dédiée à **Rocket League**. Indique `Rocket League` (ou `RL`) pour ce paramètre."


# ----------------------------
# FONCTIONS UTILES
# ----------------------------

def get_score(user_id: int, guild_id: int | None = None) -> int:
    """Récupère la réputation positive d'un membre, si possible par serveur."""
    cursor = db.cursor()
    if guild_id is None:
        cursor.execute(
            "SELECT COALESCE(SUM(score), 0) FROM reputation WHERE user_id = ?",
            (user_id,)
        )
    else:
        cursor.execute(
            "SELECT COALESCE(score, 0) FROM reputation WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
    result = cursor.fetchone()
    return int(result[0] or 0) if result else 0


def get_negative_score(user_id: int, guild_id: int | None = None) -> int:
    """Récupère le nombre de signalements validés, si possible par serveur."""
    cursor = db.cursor()
    if guild_id is None:
        cursor.execute(
            "SELECT COUNT(*) FROM negative_reputation WHERE user_id = ?",
            (user_id,)
        )
    else:
        cursor.execute(
            "SELECT COUNT(*) FROM negative_reputation WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
    result = cursor.fetchone()
    return int(result[0] or 0)


def safe_reason(reason: str, limit: int = MAX_REASON_LENGTH) -> str:
    """Nettoie une raison et limite sa longueur."""

    reason = reason.strip()

    if not reason:
        return "Aucune raison précisée."

    return reason[:limit]


async def send_log(
    guild: discord.Guild,
    action: str,
    moderator: discord.abc.User,
    target: discord.abc.User | None = None,
    reason: str = "Aucune raison précisée."
):
    """Enregistre une action dans la base et, si configuré, sur Discord."""

    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO moderation_logs
        (guild_id, moderator_id, target_id, action, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            guild.id,
            moderator.id,
            target.id if target else None,
            action,
            safe_reason(reason),
            int(time.time())
        )
    )
    db.commit()

    if not LOG_CHANNEL_ID.isdigit():
        return

    channel = guild.get_channel(int(LOG_CHANNEL_ID))

    if channel is None:
        try:
            channel = await bot.fetch_channel(int(LOG_CHANNEL_ID))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    if not hasattr(channel, "send"):
        return

    embed = discord.Embed(
        title="📋 Journal de modération",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(
        name="Action",
        value=action[:1024],
        inline=False
    )
    embed.add_field(
        name="Modérateur",
        value=f"{moderator.mention} (`{moderator.id}`)",
        inline=False
    )

    if target:
        embed.add_field(
            name="Membre concerné",
            value=f"{target.mention} (`{target.id}`)",
            inline=False
        )

    embed.add_field(
        name="Raison",
        value=safe_reason(reason)[:1024],
        inline=False
    )

    try:
        await channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none()
        )
    except (discord.Forbidden, discord.HTTPException):
        pass


# ----------------------------
# COMMANDE : RÉPUTATION POSITIVE
# ----------------------------

@bot.tree.command(
    name="rep",
    description="Donner un point de réputation positive à un membre."
)
@app_commands.describe(
    membre="Le membre à féliciter",
    raison="Pourquoi lui donner un point de réputation"
)
async def rep(
    interaction: discord.Interaction,
    membre: discord.Member,
    raison: str = "Aucune raison précisée."
):
    if membre.bot:
        await interaction.response.send_message(
            "Tu ne peux pas donner de réputation à un bot.",
            ephemeral=True
        )
        return

    if membre.id == interaction.user.id:
        await interaction.response.send_message(
            "Tu ne peux pas te donner de réputation à toi-même.",
            ephemeral=True
        )
        return

    giver_id = interaction.user.id
    receiver_id = membre.id
    now = int(time.time())

    cursor = db.cursor()
    cursor.execute(
        """
        SELECT given_at
        FROM rep_cooldowns
        WHERE guild_id = ? AND giver_id = ? AND receiver_id = ?
        """,
        (interaction.guild_id, giver_id, receiver_id)
    )
    row = cursor.fetchone()

    if row and now - row[0] < COOLDOWN_REP:
        remaining = COOLDOWN_REP - (now - row[0])
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60

        await interaction.response.send_message(
            f"Tu as déjà donné un point à ce membre récemment. "
            f"Réessaie dans environ {hours} h {minutes} min.",
            ephemeral=True
        )
        return

    reason_clean = safe_reason(raison)

    cursor.execute(
        """
        INSERT INTO reputation (guild_id, user_id, score)
        VALUES (?, ?, 1)
        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET score = reputation.score + 1
        """,
        (interaction.guild_id, receiver_id)
    )

    cursor.execute(
        """
        INSERT INTO rep_cooldowns (guild_id, giver_id, receiver_id, given_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, giver_id, receiver_id)
        DO UPDATE SET given_at = excluded.given_at
        """,
        (interaction.guild_id, giver_id, receiver_id, now)
    )

    db.commit()

    embed = discord.Embed(
        title="⭐ Réputation positive !",
        description=(
            f"{interaction.user.mention} a donné un point de réputation à "
            f"{membre.mention}.\n\n"
            f"**Raison :** {reason_clean}"
        ),
        color=discord.Color.green()
    )
    embed.add_field(
        name="Réputation positive totale",
        value=str(get_score(receiver_id, interaction.guild_id)),
        inline=True
    )

    await interaction.response.send_message(
        embed=embed,
        allowed_mentions=discord.AllowedMentions.none()
    )


# ----------------------------
# COMMANDE : PROFIL
# ----------------------------

@bot.tree.command(
    name="profil",
    description="Afficher le profil de réputation d'un membre."
)
@app_commands.describe(
    membre="Le membre dont tu veux consulter le profil"
)
async def profil(
    interaction: discord.Interaction,
    membre: discord.Member | None = None
):
    membre = membre or interaction.user

    positive = get_score(membre.id, interaction.guild_id)
    negative = get_negative_score(membre.id, interaction.guild_id)

    embed = discord.Embed(
        title=f"👤 Profil de {membre.display_name}",
        color=discord.Color.blurple()
    )
    embed.set_thumbnail(url=membre.display_avatar.url)
    embed.add_field(
        name="⭐ Réputation positive",
        value=str(positive),
        inline=True
    )
    embed.add_field(
        name="⚠️ Réputation négative validée",
        value=str(negative),
        inline=True
    )
    embed.add_field(
        name="🏷️ Grade du staff",
        value={
            3: "Fondateur",
            2: "Cofondateur",
            1: "Modérateur",
            0: "Membre"
        }[get_staff_level(membre)],
        inline=False
    )

    await interaction.response.send_message(embed=embed)


# ----------------------------
# COMMANDE : MAUVAISE RÉPUTATION
# ----------------------------

@bot.tree.command(
    name="mauvaise_rep",
    description="Consulter la réputation négative validée d'un membre."
)
@app_commands.describe(
    membre="Le membre à consulter"
)
async def mauvaise_rep(
    interaction: discord.Interaction,
    membre: discord.Member
):
    negative = get_negative_score(membre.id, interaction.guild_id)

    embed = discord.Embed(
        title=f"⚠️ Réputation négative de {membre.display_name}",
        description=(
            f"Nombre de signalements validés : **{negative}**\n\n"
            "Seuls les signalements validés par le staff sont comptabilisés."
        ),
        color=discord.Color.orange()
    )
    embed.set_thumbnail(url=membre.display_avatar.url)

    await interaction.response.send_message(embed=embed)

# ============================================================
# PARTIE 3/5 — SIGNALEMENTS ET VALIDATION PAR LE STAFF
# ============================================================

# ----------------------------
# COMMANDE : SIGNALER UN MEMBRE
# ----------------------------

@bot.tree.command(
    name="signaler",
    description="Signaler un membre au staff."
)
@app_commands.describe(
    membre="Le membre que tu souhaites signaler",
    raison="Explique clairement le problème"
)
async def signaler(
    interaction: discord.Interaction,
    membre: discord.Member,
    raison: str
):
    if membre.bot:
        await interaction.response.send_message(
            "Tu ne peux pas signaler un bot avec cette commande.",
            ephemeral=True
        )
        return

    if membre.id == interaction.user.id:
        await interaction.response.send_message(
            "Tu ne peux pas te signaler toi-même.",
            ephemeral=True
        )
        return

    raison = safe_reason(raison)

    if len(raison) < 5:
        await interaction.response.send_message(
            "Donne une raison un peu plus précise, s'il te plaît.",
            ephemeral=True
        )
        return

    now = int(time.time())
    cursor = db.cursor()

    # Limite les signalements trop fréquents du même membre.
    cursor.execute(
        """
        SELECT created_at
        FROM reports
        WHERE guild_id = ? AND reporter_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (interaction.guild_id, interaction.user.id)
    )
    last_report = cursor.fetchone()

    if last_report and now - last_report[0] < REPORT_COOLDOWN:
        remaining = REPORT_COOLDOWN - (now - last_report[0])
        minutes = max(1, remaining // 60)

        await interaction.response.send_message(
            f"Tu as déjà envoyé un signalement récemment. "
            f"Réessaie dans environ {minutes} minute(s).",
            ephemeral=True
        )
        return

    # Empêche plusieurs signalements en attente pour la même personne
    # par le même auteur.
    cursor.execute(
        """
        SELECT id
        FROM reports
        WHERE guild_id = ?
          AND reporter_id = ?
          AND target_id = ?
          AND status = 'pending'
        LIMIT 1
        """,
        (interaction.guild_id, interaction.user.id, membre.id)
    )

    if cursor.fetchone():
        await interaction.response.send_message(
            "Tu as déjà un signalement en attente contre ce membre.",
            ephemeral=True
        )
        return

    cursor.execute(
        """
        INSERT INTO reports
        (guild_id, reporter_id, target_id, reason, status, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (interaction.guild_id, interaction.user.id, membre.id, raison, now)
    )
    report_id = cursor.lastrowid
    db.commit()

    # Envoie le signalement dans le salon du staff.
    if REPORT_CHANNEL_ID.isdigit():
        channel = interaction.guild.get_channel(
            int(REPORT_CHANNEL_ID)
        ) if interaction.guild else None

        if channel is None:
            try:
                channel = await bot.fetch_channel(
                    int(REPORT_CHANNEL_ID)
                )
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException
            ):
                channel = None

        if channel is not None and hasattr(channel, "send"):
            embed = discord.Embed(
                title=f"🚨 Nouveau signalement #{report_id}",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(
                name="Signalé par",
                value=f"{interaction.user} (`{interaction.user.id}`)",
                inline=False
            )
            embed.add_field(
                name="Membre signalé",
                value=f"{membre} (`{membre.id}`)",
                inline=False
            )
            embed.add_field(
                name="Raison",
                value=raison[:1024],
                inline=False
            )
            embed.set_footer(
                text=f"Pour traiter : /valider_signalement ou /refuser_signalement"
            )

            try:
                await channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none()
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

    await interaction.response.send_message(
        f"✅ Ton signalement **#{report_id}** a été enregistré. "
        "Le staff pourra l'examiner.",
        ephemeral=True
    )


# ----------------------------
# COMMANDE : LISTE DES SIGNALEMENTS
# ----------------------------

@bot.tree.command(
    name="signalements",
    description="Afficher les signalements en attente."
)
@moderator_only()
async def signalements(interaction: discord.Interaction):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT id, reporter_id, target_id, reason, created_at
        FROM reports
        WHERE guild_id = ? AND status = 'pending'
        ORDER BY created_at ASC
        LIMIT 10
        """,
        (interaction.guild_id,)
    )
    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            "✅ Aucun signalement en attente.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="📋 Signalements en attente",
        color=discord.Color.orange()
    )

    for row in rows:
        report_id, reporter_id, target_id, reason, created_at = row

        date_text = discord.utils.format_dt(
            discord.utils.utcnow().__class__.fromtimestamp(
                created_at,
                tz=discord.utils.utcnow().tzinfo
            ),
            style="R"
        )

        embed.add_field(
            name=f"Signalement #{report_id}",
            value=(
                f"**Auteur :** <@{reporter_id}>\n"
                f"**Membre :** <@{target_id}>\n"
                f"**Raison :** {reason[:300]}\n"
                f"**Date :** {date_text}"
            ),
            inline=False
        )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none()
    )


# ----------------------------
# COMMANDE : VALIDER UN SIGNALEMENT
# ----------------------------

@bot.tree.command(
    name="valider_signalement",
    description="Valider un signalement et ajouter une réputation négative."
)
@app_commands.describe(
    signalement_id="Le numéro du signalement",
    raison_staff="Commentaire facultatif du staff"
)
@moderator_only()
async def valider_signalement(
    interaction: discord.Interaction,
    signalement_id: app_commands.Range[int, 1, 2147483647],
    raison_staff: str = "Signalement validé par le staff."
):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT reporter_id, target_id, reason, status
        FROM reports
        WHERE id = ? AND guild_id = ?
        """,
        (signalement_id, interaction.guild_id)
    )
    report = cursor.fetchone()

    if report is None:
        await interaction.response.send_message(
            "❌ Ce signalement n'existe pas.",
            ephemeral=True
        )
        return

    reporter_id, target_id, report_reason, status = report

    if status != "pending":
        await interaction.response.send_message(
            "Ce signalement a déjà été traité.",
            ephemeral=True
        )
        return

    cursor.execute(
        """
        UPDATE reports
        SET status = 'approved', moderator_id = ?, reviewed_at = ?
        WHERE id = ? AND guild_id = ? AND status = 'pending'
        """,
        (interaction.user.id, int(time.time()), signalement_id, interaction.guild_id)
    )

    if cursor.rowcount != 1:
        db.commit()
        await interaction.response.send_message(
            "Ce signalement vient d'être traité par quelqu'un d'autre.",
            ephemeral=True
        )
        return

    cursor.execute(
        """
        INSERT INTO negative_reputation
        (report_id, guild_id, user_id, moderator_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            signalement_id,
            interaction.guild_id,
            target_id,
            interaction.user.id,
            int(time.time())
        )
    )
    db.commit()

    await send_log(
        interaction.guild,
        f"Signalement #{signalement_id} validé",
        interaction.user,
        reason=safe_reason(raison_staff)
    )

    await interaction.response.send_message(
        f"✅ Le signalement **#{signalement_id}** est validé. "
        f"La réputation négative de <@{target_id}> a été mise à jour.\n"
        f"**Total actuel :** {get_negative_score(target_id, interaction.guild_id)}",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none()
    )


# ----------------------------
# COMMANDE : REFUSER UN SIGNALEMENT
# ----------------------------

@bot.tree.command(
    name="refuser_signalement",
    description="Refuser un signalement qui n'est pas justifié."
)
@app_commands.describe(
    signalement_id="Le numéro du signalement",
    raison_staff="Pourquoi le signalement est refusé"
)
@moderator_only()
async def refuser_signalement(
    interaction: discord.Interaction,
    signalement_id: app_commands.Range[int, 1, 2147483647],
    raison_staff: str = "Signalement refusé par le staff."
):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT status
        FROM reports
        WHERE id = ? AND guild_id = ?
        """,
        (signalement_id, interaction.guild_id)
    )
    report = cursor.fetchone()

    if report is None:
        await interaction.response.send_message(
            "❌ Ce signalement n'existe pas.",
            ephemeral=True
        )
        return

    if report[0] != "pending":
        await interaction.response.send_message(
            "Ce signalement a déjà été traité.",
            ephemeral=True
        )
        return

    cursor.execute(
        """
        UPDATE reports
        SET status = 'rejected', moderator_id = ?, reviewed_at = ?
        WHERE id = ? AND guild_id = ? AND status = 'pending'
        """,
        (interaction.user.id, int(time.time()), signalement_id, interaction.guild_id)
    )
    if cursor.rowcount != 1:
        db.commit()
        await interaction.response.send_message(
            "Ce signalement vient d'être traité par quelqu'un d'autre.",
            ephemeral=True
        )
        return
    db.commit()

    await send_log(
        interaction.guild,
        f"Signalement #{signalement_id} refusé",
        interaction.user,
        reason=safe_reason(raison_staff)
    )

    await interaction.response.send_message(
        f"❌ Le signalement **#{signalement_id}** a été refusé.",
        ephemeral=True
    )

# ============================================================
# PARTIE 4/5 — MODÉRATION, SUGGESTIONS ET MINI-JEUX
# ============================================================

# ----------------------------
# COMMANDE : EXPULSER UN MEMBRE
# ----------------------------

@bot.tree.command(
    name="kick",
    description="Expulser un membre du serveur."
)
@app_commands.describe(
    membre="Le membre à expulser",
    raison="La raison de l'expulsion"
)
@moderator_only()
async def kick(
    interaction: discord.Interaction,
    membre: discord.Member,
    raison: str = "Aucune raison précisée."
):
    if membre.id == interaction.user.id:
        await interaction.response.send_message(
            "Tu ne peux pas t'expulser toi-même.",
            ephemeral=True
        )
        return

    if not has_higher_role(interaction.user, membre):
        await interaction.response.send_message(
            "Tu ne peux pas agir sur ce membre : son grade est égal ou supérieur au tien.",
            ephemeral=True
        )
        return

    raison = safe_reason(raison)

    try:
        await membre.kick(
            reason=f"{interaction.user} : {raison}"
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Je n'ai pas la permission d'expulser ce membre. Vérifie mes permissions et la hiérarchie des rôles.",
            ephemeral=True
        )
        return
    except discord.HTTPException:
        await interaction.response.send_message(
            "L'expulsion a échoué. Réessaie plus tard.",
            ephemeral=True
        )
        return

    await send_log(
        interaction.guild,
        "Expulsion",
        interaction.user,
        membre,
        raison
    )

    await interaction.response.send_message(
        f"✅ **{membre}** a été expulsé.\nRaison : {raison}",
        ephemeral=True
    )


# ----------------------------
# COMMANDE : BANNIR UN MEMBRE
# ----------------------------

@bot.tree.command(
    name="ban",
    description="Bannir un membre du serveur."
)
@app_commands.describe(
    membre="Le membre à bannir",
    raison="La raison du bannissement"
)
@moderator_only()
async def ban(
    interaction: discord.Interaction,
    membre: discord.Member,
    raison: str = "Aucune raison précisée."
):
    if membre.id == interaction.user.id:
        await interaction.response.send_message(
            "Tu ne peux pas te bannir toi-même.",
            ephemeral=True
        )
        return

    if not has_higher_role(interaction.user, membre):
        await interaction.response.send_message(
            "Tu ne peux pas agir sur ce membre : son grade est égal ou supérieur au tien.",
            ephemeral=True
        )
        return

    raison = safe_reason(raison)

    try:
        await membre.ban(
            reason=f"{interaction.user} : {raison}"
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Je n'ai pas la permission de bannir ce membre. Vérifie mes permissions et la hiérarchie des rôles.",
            ephemeral=True
        )
        return
    except discord.HTTPException:
        await interaction.response.send_message(
            "Le bannissement a échoué. Réessaie plus tard.",
            ephemeral=True
        )
        return

    await send_log(
        interaction.guild,
        "Bannissement",
        interaction.user,
        membre,
        raison
    )

    await interaction.response.send_message(
        f"🔨 **{membre}** a été banni.\nRaison : {raison}",
        ephemeral=True
    )


# ----------------------------
# COMMANDE : SUPPRIMER DES MESSAGES
# ----------------------------

@bot.tree.command(
    name="clear",
    description="Supprimer plusieurs messages d'un salon."
)
@app_commands.describe(
    nombre="Nombre de messages à supprimer (entre 1 et 100)"
)
@moderator_only()
async def clear(
    interaction: discord.Interaction,
    nombre: app_commands.Range[int, 1, 100]
):
    channel = interaction.channel

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message(
            "Cette commande doit être utilisée dans un salon textuel.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        deleted = await channel.purge(limit=nombre)
    except discord.Forbidden:
        await interaction.followup.send(
            "Je n'ai pas la permission de supprimer les messages.",
            ephemeral=True
        )
        return
    except discord.HTTPException:
        await interaction.followup.send(
            "La suppression a échoué.",
            ephemeral=True
        )
        return

    await send_log(
        interaction.guild,
        "Suppression de messages",
        interaction.user,
        reason=f"{len(deleted)} message(s) supprimé(s) dans #{channel.name}."
    )

    await interaction.followup.send(
        f"🧹 {len(deleted)} message(s) supprimé(s).",
        ephemeral=True
    )


# ----------------------------
# COMMANDE : TIMEOUT
# ----------------------------

@bot.tree.command(
    name="timeout",
    description="Empêcher temporairement un membre d'envoyer des messages."
)
@app_commands.describe(
    membre="Le membre concerné",
    minutes="Durée en minutes (entre 1 et 40320)",
    raison="La raison du timeout"
)
@moderator_only()
async def timeout(
    interaction: discord.Interaction,
    membre: discord.Member,
    minutes: app_commands.Range[int, 1, 40320],
    raison: str = "Aucune raison précisée."
):
    if membre.id == interaction.user.id:
        await interaction.response.send_message(
            "Tu ne peux pas te mettre en timeout toi-même.",
            ephemeral=True
        )
        return

    if not has_higher_role(interaction.user, membre):
        await interaction.response.send_message(
            "Tu ne peux pas agir sur ce membre : son grade est égal ou supérieur au tien.",
            ephemeral=True
        )
        return

    raison = safe_reason(raison)

    try:
        await membre.timeout(
            timedelta(minutes=minutes),
            reason=f"{interaction.user} : {raison}"
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Je n'ai pas la permission d'appliquer un timeout à ce membre.",
            ephemeral=True
        )
        return
    except discord.HTTPException:
        await interaction.response.send_message(
            "Le timeout a échoué.",
            ephemeral=True
        )
        return

    await send_log(
        interaction.guild,
        f"Timeout de {minutes} minute(s)",
        interaction.user,
        membre,
        raison
    )

    await interaction.response.send_message(
        f"🔇 {membre.mention} est en timeout pendant **{minutes} minute(s)**.\n"
        f"Raison : {raison}",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none()
    )


# ----------------------------
# COMMANDE : CONSULTER LES LOGS
# ----------------------------

@bot.tree.command(
    name="logs_moderation",
    description="Consulter les dernières actions de modération."
)
@moderator_only()
async def logs_moderation(interaction: discord.Interaction):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT action, moderator_id, target_id, reason, created_at
        FROM moderation_logs
        WHERE guild_id = ?
        ORDER BY created_at DESC
        LIMIT 10
        """,
        (interaction.guild_id,)
    )
    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            "Aucune action de modération enregistrée.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="📋 Dernières actions de modération",
        color=discord.Color.orange()
    )

    for action, moderator_id, target_id, reason, created_at in rows:
        date = discord.utils.format_dt(
            discord.utils.utcnow().__class__.fromtimestamp(
                created_at,
                tz=discord.utils.utcnow().tzinfo
            ),
            style="R"
        )

        target_text = (
            f"<@{target_id}>" if target_id else "Aucun membre"
        )

        embed.add_field(
            name=f"{action[:200]} — {date}",
            value=(
                f"**Staff :** <@{moderator_id}>\n"
                f"**Membre :** {target_text}\n"
                f"**Raison :** {reason[:300]}"
            ),
            inline=False
        )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none()
    )


# ----------------------------
# COMMANDE : PROPOSER UNE SUGGESTION
# ----------------------------

@bot.tree.command(
    name="suggestion",
    description="Proposer une amélioration pour le serveur."
)
@app_commands.describe(
    texte="Décris ton idée"
)
async def suggestion(
    interaction: discord.Interaction,
    texte: str
):
    texte = texte.strip()

    if len(texte) < 5:
        await interaction.response.send_message(
            "Ta suggestion doit contenir au moins 5 caractères.",
            ephemeral=True
        )
        return

    if len(texte) > MAX_SUGGESTION_LENGTH:
        await interaction.response.send_message(
            f"Ta suggestion ne peut pas dépasser {MAX_SUGGESTION_LENGTH} caractères.",
            ephemeral=True
        )
        return

    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO suggestions (guild_id, user_id, suggestion, status, created_at)
        VALUES (?, ?, ?, 'pending', ?)
        """,
        (interaction.guild_id, interaction.user.id, texte, int(time.time()))
    )
    suggestion_id = cursor.lastrowid
    db.commit()

    await interaction.response.send_message(
        f"💡 Ta suggestion **#{suggestion_id}** a été enregistrée. Merci !",
        ephemeral=True
    )


# ----------------------------
# COMMANDE : CONSULTER LES SUGGESTIONS
# ----------------------------

@bot.tree.command(
    name="proposer",
    description="Afficher les suggestions en attente."
)
@moderator_only()
async def proposer(interaction: discord.Interaction):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT id, user_id, suggestion, created_at
        FROM suggestions
        WHERE guild_id = ? AND status = 'pending'
        ORDER BY created_at ASC
        LIMIT 10
        """,
        (interaction.guild_id,)
    )
    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            "Aucune suggestion en attente.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="💡 Suggestions en attente",
        color=discord.Color.blurple()
    )

    for suggestion_id, user_id, content, created_at in rows:
        date = discord.utils.format_dt(
            discord.utils.utcnow().__class__.fromtimestamp(
                created_at,
                tz=discord.utils.utcnow().tzinfo
            ),
            style="R"
        )

        embed.add_field(
            name=f"Suggestion #{suggestion_id}",
            value=(
                f"**Auteur :** <@{user_id}>\n"
                f"**Idée :** {content[:700]}\n"
                f"**Date :** {date}"
            ),
            inline=False
        )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none()
    )


# ----------------------------
# COMMANDE : TRAITER UNE SUGGESTION
# ----------------------------

@bot.tree.command(
    name="traiter_suggestion",
    description="Accepter ou refuser une suggestion."
)
@app_commands.describe(
    suggestion_id="Le numéro de la suggestion",
    decision="Accepter ou refuser la suggestion",
    commentaire="Commentaire facultatif du staff"
)
@app_commands.choices(
    decision=[
        app_commands.Choice(name="Accepter", value="accepted"),
        app_commands.Choice(name="Refuser", value="rejected")
    ]
)
@moderator_only()
async def traiter_suggestion(
    interaction: discord.Interaction,
    suggestion_id: app_commands.Range[int, 1, 2147483647],
    decision: app_commands.Choice[str],
    commentaire: str = "Aucun commentaire."
):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT status
        FROM suggestions
        WHERE id = ? AND guild_id = ?
        """,
        (suggestion_id, interaction.guild_id)
    )
    row = cursor.fetchone()

    if row is None:
        await interaction.response.send_message(
            "Cette suggestion n'existe pas.",
            ephemeral=True
        )
        return

    if row[0] != "pending":
        await interaction.response.send_message(
            "Cette suggestion a déjà été traitée.",
            ephemeral=True
        )
        return

    cursor.execute(
        """
        UPDATE suggestions
        SET status = ?, moderator_id = ?, reviewed_at = ?
        WHERE id = ? AND guild_id = ? AND status = 'pending'
        """,
        (decision.value, interaction.user.id, int(time.time()), suggestion_id, interaction.guild_id)
    )
    if cursor.rowcount != 1:
        db.commit()
        await interaction.response.send_message(
            "Cette suggestion vient d'être traitée par quelqu'un d'autre.",
            ephemeral=True
        )
        return
    db.commit()

    await send_log(
        interaction.guild,
        f"Suggestion #{suggestion_id} traitée : {decision.value}",
        interaction.user,
        reason=safe_reason(commentaire)
    )

    texte_decision = (
        "acceptée ✅"
        if decision.value == "accepted"
        else "refusée ❌"
    )

    await interaction.response.send_message(
        f"La suggestion **#{suggestion_id}** a été {texte_decision}.",
        ephemeral=True
    )


# ----------------------------
# MINI-JEU : PILE OU FACE
# ----------------------------

@bot.tree.command(
    name="pileouface",
    description="Jouer à pile ou face."
)
@app_commands.describe(
    choix="Choisis pile ou face"
)
@app_commands.choices(
    choix=[
        app_commands.Choice(name="Pile", value="Pile"),
        app_commands.Choice(name="Face", value="Face")
    ]
)
async def pileouface(
    interaction: discord.Interaction,
    choix: app_commands.Choice[str]
):
    resultat = random.choice(["Pile", "Face"])
    gagne = choix.value == resultat

    await interaction.response.send_message(
        f"🪙 Résultat : **{resultat}**\n"
        f"{'🎉 Tu as gagné !' if gagne else '😅 Tu as perdu !'}"
    )


# ----------------------------
# MINI-JEU : DÉ
# ----------------------------

@bot.tree.command(
    name="de",
    description="Lancer un dé."
)
async def de(interaction: discord.Interaction):
    resultat = random.randint(1, 6)

    await interaction.response.send_message(
        f"🎲 {interaction.user.mention} lance le dé : **{resultat}** !",
        allowed_mentions=discord.AllowedMentions.none()
    )


# ----------------------------
# MINI-JEU : DEVINETTE
# ----------------------------

@bot.tree.command(
    name="devine",
    description="Trouver le nombre secret entre 1 et 100."
)
@app_commands.describe(
    nombre="Ton nombre entre 1 et 100"
)
async def devine(
    interaction: discord.Interaction,
    nombre: app_commands.Range[int, 1, 100]
):
    user_id = interaction.user.id

    if user_id not in games:
        games[user_id] = {
            "number": random.randint(1, 100),
            "attempts": 0
        }

        await interaction.response.send_message(
            "🎯 Une partie commence ! J'ai choisi un nombre entre 1 et 100.\n"
            "Utilise `/devine nombre` pour proposer une réponse."
        )
        return

    game = games[user_id]
    game["attempts"] += 1
    secret = game["number"]

    if nombre == secret:
        attempts = game["attempts"]
        del games[user_id]

        await interaction.response.send_message(
            f"🎉 Bravo ! Tu as trouvé **{secret}** en {attempts} tentative(s) !"
        )
    elif nombre < secret:
        await interaction.response.send_message(
            "⬆️ C'est plus grand ! Essaie encore avec `/devine`."
        )
    else:
        await interaction.response.send_message(
            "⬇️ C'est plus petit ! Essaie encore avec `/devine`."
        )


# ----------------------------
# COMMANDE : MON GRADE
# ----------------------------

@bot.tree.command(
    name="mon_grade",
    description="Afficher ton grade sur le serveur."
)
async def mon_grade(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur.",
            ephemeral=True
        )
        return

    niveaux = {
        0: "👤 Membre",
        1: "🛡️ Modérateur",
        2: "⭐ Cofondateur",
        3: "👑 Fondateur"
    }

    niveau = get_staff_level(interaction.user)

    await interaction.response.send_message(
        f"Ton grade est : **{niveaux[niveau]}**",
        ephemeral=True
    )

# ============================================================
# PARTIE 5/5 — AIDE, ERREURS ET ÉVÉNEMENTS AUTOMATIQUES
# ============================================================

# ----------------------------
# COMMANDE : AIDE
# ----------------------------

@bot.tree.command(
    name="aide",
    description="Afficher les commandes disponibles du bot."
)
async def aide(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Aide — Fractale Esport",
        description="Voici les principales commandes du bot.",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="⭐ Réputation",
        value=(
            "`/rep` — Donner de la réputation positive\n"
            "`/profil` — Consulter un profil\n"
            "`/mauvaise_rep` — Voir la réputation négative"
        ),
        inline=False
    )

    embed.add_field(
        name="🚨 Signalements",
        value=(
            "`/signaler` — Signaler un membre\n"
            "`/signalements` — Voir les signalements en attente\n"
            "`/valider_signalement` — Valider un signalement\n"
            "`/refuser_signalement` — Refuser un signalement\n"
            "`/signaler_competitif` — Signaler un incident compétitif\n"
            "`/signalements_competitifs` — Voir les dossiers compétitifs (staff)\n"
            "`/traiter_signalement_competitif` — Enregistrer une décision staff"
        ),
        inline=False
    )

    embed.add_field(
        name="🛡️ Modération",
        value=(
            "`/kick` — Expulser un membre\n"
            "`/ban` — Bannir un membre\n"
            "`/clear` — Supprimer des messages\n"
            "`/timeout` — Appliquer un timeout\n"
            "`/logs_moderation` — Consulter les logs"
        ),
        inline=False
    )

    embed.add_field(
        name="💡 Suggestions",
        value=(
            "`/suggestion` — Proposer une idée\n"
            "`/proposer` — Consulter les suggestions\n"
            "`/traiter_suggestion` — Accepter ou refuser une suggestion"
        ),
        inline=False
    )

    embed.add_field(
        name="🏆 Tournois",
        value=(
            "`/tournoi_creer` — Créer un tournoi (staff)\n"
            "`/tournoi_liste` — Voir les tournois ouverts\n"
            "`/tournoi_joueurs` — Voir les inscrits\n"
            "`/tournoi_fermer` — Fermer les inscriptions (staff)\n"
            "`/tournoi_lancer` — Tirer les équipes au sort et lancer le tournoi (staff)\n"
            "`/tournoi_matchs` — Voir le tableau et les matchs\n"
            "`/tournoi_resultat` — Enregistrer le vainqueur d’un match (staff)"
        ),
        inline=False
    )

    embed.add_field(
        name="🚗 Rocket League — Team & compétition",
        value=(
            "`/profil_esport_modifier` — Créer ton profil de joueur RL\n"
            "`/equipe_creer` · `/equipes` · `/equipe_profil` — Gérer les équipes\n"
            "`/equipe_gestion` — Centre de gestion de ton équipe\n"
            "`/coequipier` · `/coequipiers` — Trouver des coéquipiers\n"
            "`/recrutement_ouvrir` · `/recrutements` — Recrutement RL\n"
            "`/disponibilite` · `/disponibilites` — Disponibilités des joueurs\n"
            "`/scrim_rechercher` · `/scrims` — Organiser des scrims\n"
            "`/saison_creer` · `/saison_matchs` · `/saison_resultat` — Saisons compétitives\n"
            "`/clip_proposer` · `/clips` — Clips Rocket League"
        ),
        inline=False
    )

    embed.add_field(
        name="🎮 Mini-jeux",
        value=(
            "`/pileouface` — Jouer à pile ou face\n"
            "`/de` — Lancer un dé\n"
            "`/devine` — Trouver le nombre secret"
        ),
        inline=False
    )

    embed.add_field(
        name="👤 Autres commandes",
        value=(
            "`/mon_grade` — Voir ton grade\n"
            "`/aide` — Afficher cette aide"
        ),
        inline=False
    )

    embed.set_footer(
        text="Fractale Esport • Team Rocket League • Les commandes staff sont protégées."
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# MODÉRATION COMPÉTITIVE — SIGNALEMENTS LIÉS AUX MATCHS
# ============================================================

@bot.tree.command(
    name="signaler_competitif",
    description="Signaler un problème pendant un match ou une compétition Rocket League."
)
@app_commands.describe(
    joueur="Le joueur concerné par le signalement",
    categorie="Le type de problème rencontré",
    description="Décris les faits clairement, sans insultes",
    match_id="Identifiant du match concerné, si connu",
    preuve="Lien vers une capture ou une vidéo (facultatif)"
)
@app_commands.choices(categorie=[
    app_commands.Choice(name="Résultat contesté", value="Résultat contesté"),
    app_commands.Choice(name="Comportement antisportif", value="Comportement antisportif"),
    app_commands.Choice(name="Absence / abandon", value="Absence / abandon"),
    app_commands.Choice(name="Triche ou exploitation suspectée", value="Triche ou exploitation suspectée"),
    app_commands.Choice(name="Autre problème compétitif", value="Autre problème compétitif"),
])
async def signaler_competitif(
    interaction: discord.Interaction,
    joueur: discord.Member,
    categorie: app_commands.Choice[str],
    description: str,
    match_id: app_commands.Range[int, 1, 2147483647] | None = None,
    preuve: str | None = None,
):
    if interaction.guild is None or interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur le serveur Fractale Esport.", ephemeral=True
        )
        return
    if joueur.bot or joueur.id == interaction.user.id:
        await interaction.response.send_message(
            "Tu ne peux pas signaler un bot ni te signaler toi-même.", ephemeral=True
        )
        return

    description = safe_reason(description, 1000)
    if len(description) < 10:
        await interaction.response.send_message(
            "Ajoute un peu plus de détails (au moins 10 caractères) pour que le staff puisse examiner les faits.",
            ephemeral=True,
        )
        return

    evidence_url = (preuve or "").strip()
    if evidence_url and (not evidence_url.startswith("https://") or len(evidence_url) > 500):
        await interaction.response.send_message(
            "Le lien de preuve doit commencer par `https://` et ne pas dépasser 500 caractères.",
            ephemeral=True,
        )
        return

    now = int(time.time())
    recent = db.execute(
        "SELECT created_at FROM competitive_reports WHERE guild_id = ? AND reporter_id = ? ORDER BY created_at DESC LIMIT 1",
        (interaction.guild_id, interaction.user.id),
    ).fetchone()
    if recent and now - int(recent["created_at"]) < REPORT_COOLDOWN:
        remaining = REPORT_COOLDOWN - (now - int(recent["created_at"]))
        await interaction.response.send_message(
            f"Tu as envoyé un signalement compétitif récemment. Réessaie dans environ {max(1, remaining // 60)} minute(s).",
            ephemeral=True,
        )
        return

    pending = db.execute(
        "SELECT id FROM competitive_reports WHERE guild_id = ? AND reporter_id = ? AND target_id = ? AND status = 'pending' LIMIT 1",
        (interaction.guild_id, interaction.user.id, joueur.id),
    ).fetchone()
    if pending:
        await interaction.response.send_message(
            f"Tu as déjà un signalement compétitif en attente contre ce joueur (#{pending['id']}).",
            ephemeral=True,
        )
        return

    cursor = db.execute(
        """INSERT INTO competitive_reports
        (guild_id, reporter_id, target_id, category, description, match_id, evidence_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (interaction.guild_id, interaction.user.id, joueur.id, categorie.value,
         description, match_id, evidence_url, now),
    )
    report_id = int(cursor.lastrowid)
    db.commit()

    # Le signalement est privé pour l'auteur ; le staff peut le consulter via la commande dédiée.
    await interaction.response.send_message(
        f"✅ Signalement compétitif **#{report_id}** enregistré. Il est en attente d'examen par le staff.\n"
        "Un signalement ne constitue pas une preuve à lui seul et n'entraîne aucune sanction automatique.",
        ephemeral=True,
    )


@bot.tree.command(
    name="signalements_competitifs",
    description="Afficher les signalements compétitifs en attente (staff)."
)
@moderator_only()
async def signalements_competitifs(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("Utilise cette commande sur le serveur.", ephemeral=True)
        return
    rows = db.execute(
        """SELECT id, reporter_id, target_id, category, description, match_id, evidence_url, created_at
        FROM competitive_reports WHERE guild_id = ? AND status = 'pending'
        ORDER BY created_at ASC LIMIT 10""",
        (interaction.guild_id,),
    ).fetchall()
    if not rows:
        await interaction.response.send_message("✅ Aucun signalement compétitif en attente.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🛡️ Signalements compétitifs en attente",
        description="Examine les faits et les preuves avant de prendre une décision.",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    for row in rows:
        details = (
            f"**Auteur :** <@{row['reporter_id']}>\n"
            f"**Joueur concerné :** <@{row['target_id']}>\n"
            f"**Catégorie :** {row['category']}\n"
            f"**Description :** {row['description'][:500]}\n"
            f"**Match :** #{row['match_id']}\n" if row['match_id'] else
            f"**Auteur :** <@{row['reporter_id']}>\n"
            f"**Joueur concerné :** <@{row['target_id']}>\n"
            f"**Catégorie :** {row['category']}\n"
            f"**Description :** {row['description'][:500]}\n"
        )
        if row['evidence_url']:
            details += f"**Preuve :** {row['evidence_url']}\n"
        details += f"**Envoyé :** <t:{int(row['created_at'])}:R>\nTraiter avec `/traiter_signalement_competitif` (ID **{row['id']}**)."
        embed.add_field(name=f"Dossier #{row['id']}", value=details[:1024], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(
    name="traiter_signalement_competitif",
    description="Enregistrer la décision du staff pour un signalement compétitif."
)
@app_commands.describe(
    signalement_id="Numéro du dossier",
    decision="Décision prise après examen",
    commentaire="Explique brièvement la décision"
)
@app_commands.choices(decision=[
    app_commands.Choice(name="Avertissement", value="Avertissement"),
    app_commands.Choice(name="Sanction décidée par le staff", value="Sanction décidée par le staff"),
    app_commands.Choice(name="Résultat à réexaminer", value="Résultat à réexaminer"),
    app_commands.Choice(name="Classé sans suite", value="Classé sans suite"),
])
@moderator_only()
async def traiter_signalement_competitif(
    interaction: discord.Interaction,
    signalement_id: app_commands.Range[int, 1, 2147483647],
    decision: app_commands.Choice[str],
    commentaire: str,
):
    if interaction.guild is None or interaction.guild_id is None:
        await interaction.response.send_message("Utilise cette commande sur le serveur.", ephemeral=True)
        return
    commentaire = safe_reason(commentaire, 1000)
    row = db.execute(
        "SELECT * FROM competitive_reports WHERE id = ? AND guild_id = ?",
        (signalement_id, interaction.guild_id),
    ).fetchone()
    if row is None:
        await interaction.response.send_message("❌ Ce dossier n'existe pas sur ce serveur.", ephemeral=True)
        return
    if row["status"] != "pending":
        await interaction.response.send_message("Ce dossier a déjà été traité.", ephemeral=True)
        return

    status = "dismissed" if decision.value == "Classé sans suite" else "resolved"
    now = int(time.time())
    update = db.execute(
        """UPDATE competitive_reports SET status = ?, decision = ?, staff_comment = ?,
        moderator_id = ?, reviewed_at = ? WHERE id = ? AND guild_id = ? AND status = 'pending'""",
        (status, decision.value, commentaire, interaction.user.id, now, signalement_id, interaction.guild_id),
    )
    if update.rowcount != 1:
        db.rollback()
        await interaction.response.send_message("Ce dossier vient d'être traité par un autre membre du staff.", ephemeral=True)
        return
    db.commit()

    target = interaction.guild.get_member(int(row["target_id"]))
    await send_log(
        interaction.guild,
        f"Dossier compétitif #{signalement_id} : {decision.value}",
        interaction.user,
        target,
        f"Catégorie : {row['category']}. Commentaire staff : {commentaire}",
    )
    await interaction.response.send_message(
        f"✅ Le dossier compétitif **#{signalement_id}** a été traité.\n"
        f"**Décision :** {decision.value}\n**Commentaire :** {commentaire}\n"
        "La décision est enregistrée dans l'historique de modération. Aucune sanction Discord n'est appliquée automatiquement.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


# ============================================================
# MODÉRATION INTELLIGENTE (règles simples et configurables)
# ============================================================

# Les messages identiques répétés rapidement sont traités comme du spam.
_recent_messages: dict[tuple[int, int], list[tuple[float, str]]] = {}

async def _log_auto_moderation(guild: discord.Guild, member: discord.Member, reason: str):
    now = int(time.time())
    db.execute(
        "INSERT INTO auto_moderation_incidents (guild_id, user_id, reason, created_at) VALUES (?, ?, ?, ?)",
        (guild.id, member.id, reason[:MAX_REASON_LENGTH], now)
    )
    db.commit()

    # On ne compte que les incidents de la dernière heure pour l'escalade.
    db.execute(
        "DELETE FROM auto_moderation_incidents WHERE created_at < ?",
        (now - 7 * 24 * 60 * 60,)
    )
    db.commit()
    count = db.execute(
        "SELECT COUNT(*) FROM auto_moderation_incidents WHERE guild_id = ? AND user_id = ? AND created_at >= ?",
        (guild.id, member.id, now - 60 * 60)
    ).fetchone()[0]
    return count


@bot.event
async def on_message(message: discord.Message):
    # Compte les messages des membres humains et attribue les badges d'activité.
    # Le contenu des messages n'est pas enregistré dans la base de données.
    if message.guild is not None and not message.author.bot:
        guild_id = message.guild.id
        user_id = message.author.id
        db.execute(
            "INSERT INTO fe_user_activity (guild_id, user_id, message_count) VALUES (?, ?, 1) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET message_count = message_count + 1",
            (guild_id, user_id)
        )
        db.commit()
        count_row = db.execute(
            "SELECT message_count FROM fe_user_activity WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        ).fetchone()
        count = int(count_row[0]) if count_row else 0
        earned = []
        if count >= 1 and grant_badge(guild_id, user_id, "premier_message"):
            earned.append("premier_message")
        if count >= 100 and grant_badge(guild_id, user_id, "bavard_100"):
            earned.append("bavard_100")
        if count >= 500 and grant_badge(guild_id, user_id, "pilier_500"):
            earned.append("pilier_500")
        if earned:
            try:
                await message.channel.send(
                    "\n".join(badge_announcement(key) for key in earned),
                    allowed_mentions=discord.AllowedMentions.none()
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

    # La suite applique la modération automatique existante.
    if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
        await bot.process_commands(message)
        return

    member = message.author
    # Les membres ayant une permission de modération ne sont pas filtrés.
    if member.guild_permissions.manage_messages or member.guild_permissions.administrator:
        await bot.process_commands(message)
        return

    content = message.content or ""
    normalized = " ".join(content.casefold().split())
    reason = None

    # Détection de spam : 5 messages identiques en 8 secondes.
    key = (message.guild.id, member.id)
    now = time.time()
    recent = [(stamp, text_value) for stamp, text_value in _recent_messages.get(key, []) if now - stamp <= 8]
    if normalized:
        recent.append((now, normalized))
    _recent_messages[key] = recent[-8:]
    if normalized and len([item for item in recent if item[1] == normalized]) >= 5:
        reason = "Spam : message répété plusieurs fois en quelques secondes."

    # Les invitations Discord sont retirées pour limiter la publicité non sollicitée.
    if reason is None and ("discord.gg/" in normalized or "discord.com/invite/" in normalized):
        reason = "Lien d'invitation Discord non autorisé dans les messages."

    # Filtre facultatif configurable par le propriétaire dans le fichier .env.
    if reason is None and MODERATION_MOTS_INTERDITS:
        padded = f" {normalized} "
        if any(mot in padded for mot in MODERATION_MOTS_INTERDITS):
            reason = "Terme interdit détecté par le filtre configuré du serveur."

    if reason:
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

        incidents = await _log_auto_moderation(message.guild, member, reason)
        try:
            await message.channel.send(
                f"⚠️ {member.mention}, ton message a été signalé par la modération automatique. "
                "Merci de respecter les règles du serveur.",
                delete_after=8,
                allowed_mentions=discord.AllowedMentions(users=[member])
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

        await send_log(message.guild, "Modération automatique", bot.user or member, member,
                       f"{reason} (incident n°{incidents} sur la dernière heure)")

        # Après 3 incidents en une heure, timeout court. Jamais de bannissement automatique.
        if incidents >= 3 and member.guild_permissions.moderate_members is False:
            try:
                await member.timeout(timedelta(minutes=5), reason="3 incidents de modération automatique en moins d'une heure")
                await send_log(message.guild, "Timeout automatique (5 min)", bot.user or member, member,
                               "3 incidents de modération automatique en moins d'une heure.")
            except (discord.Forbidden, discord.HTTPException):
                pass

    await bot.process_commands(message)


# ============================================================
# PROFILS ESPORT
# ============================================================

@bot.tree.command(
    name="profil_esport",
    description="Afficher ton profil esport ou celui d'un membre."
)
@app_commands.describe(membre="Membre dont tu veux voir le profil (facultatif)")
async def profil_esport(
    interaction: discord.Interaction,
    membre: discord.Member | None = None
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur.", ephemeral=True
        )
        return

    membre = membre or interaction.user
    row = db.execute(
        "SELECT main_game, rank, platform, playstyle, availability, bio, updated_at "
        "FROM esport_profiles WHERE guild_id = ? AND user_id = ?",
        (interaction.guild.id, membre.id)
    ).fetchone()

    embed = discord.Embed(
        title=f"🎮 Profil esport de {membre.display_name}",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=membre.display_avatar.url)

    if row is None or not any(row[key] for key in ("main_game", "rank", "platform", "playstyle", "availability", "bio")):
        embed.description = (
            "Ce membre n'a pas encore configuré son profil esport.\n"
            "Utilise `/profil_esport_modifier` pour créer le tien."
        )
    else:
        embed.add_field(name="Jeu principal", value=row["main_game"] or "Non renseigné", inline=True)
        embed.add_field(name="Rang / niveau", value=row["rank"] or "Non renseigné", inline=True)
        embed.add_field(name="Plateforme", value=row["platform"] or "Non renseignée", inline=True)
        embed.add_field(name="Style de jeu", value=row["playstyle"] or "Non renseigné", inline=False)
        embed.add_field(name="Disponibilités", value=row["availability"] or "Non renseignées", inline=False)
        if row["bio"]:
            embed.add_field(name="Présentation", value=row["bio"], inline=False)
        embed.set_footer(text="Profil esport • Modifiable avec /profil_esport_modifier")

    await interaction.response.send_message(
        embed=embed, allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(
    name="profil_esport_modifier",
    description="Créer ou mettre à jour ton profil de joueur Rocket League."
)
@app_commands.describe(
    jeu="Jeu du serveur : Rocket League uniquement",
    rang="Ton rang ou niveau dans ce jeu",
    plateforme="Ta plateforme (ex. Xbox, PC, PlayStation)",
    style="Ton style de jeu ou ton rôle",
    disponibilites="Tes horaires habituels de jeu",
    presentation="Courte présentation (facultative)"
)
@app_commands.checks.cooldown(3, 60.0)
async def profil_esport_modifier(
    interaction: discord.Interaction,
    jeu: str,
    rang: str,
    plateforme: str,
    style: str,
    disponibilites: str,
    presentation: str = ""
):
    if not verifier_jeu_serveur(jeu, facultatif=False):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    jeu = JEU_SERVEUR
    if interaction.guild is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur.", ephemeral=True
        )
        return

    values = {
        "jeu": jeu.strip(),
        "rang": rang.strip(),
        "plateforme": plateforme.strip(),
        "style": style.strip(),
        "disponibilités": disponibilites.strip(),
        "présentation": presentation.strip(),
    }
    limits = {
        "jeu": 60,
        "rang": 60,
        "plateforme": 40,
        "style": 100,
        "disponibilités": 100,
        "présentation": 250,
    }
    for label, value in values.items():
        if label != "présentation" and not value:
            await interaction.response.send_message(
                f"Le champ **{label}** ne peut pas être vide.", ephemeral=True
            )
            return
        if len(value) > limits[label]:
            await interaction.response.send_message(
                f"Le champ **{label}** est trop long (maximum {limits[label]} caractères).",
                ephemeral=True
            )
            return

    now = int(time.time())
    db.execute(
        """INSERT INTO esport_profiles
        (guild_id, user_id, main_game, rank, platform, playstyle, availability, bio, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
            main_game = excluded.main_game,
            rank = excluded.rank,
            platform = excluded.platform,
            playstyle = excluded.playstyle,
            availability = excluded.availability,
            bio = excluded.bio,
            updated_at = excluded.updated_at
        """,
        (interaction.guild.id, interaction.user.id, jeu.strip(), rang.strip(),
         plateforme.strip(), style.strip(), disponibilites.strip(), presentation.strip(), now)
    )
    db.commit()
    await interaction.response.send_message(
        "✅ Ton profil esport a été enregistré ! Tu peux le consulter avec `/profil_esport`.",
        ephemeral=True
    )


# ============================================================
# PROFILS D'ÉQUIPES
# ============================================================

# Ces tables sont ajoutées sans modifier les tables existantes.
db.executescript("""
CREATE TABLE IF NOT EXISTS fe_teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    game TEXT NOT NULL,
    platform TEXT NOT NULL,
    rank TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    captain_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(guild_id, name)
);

CREATE TABLE IF NOT EXISTS fe_team_members (
    team_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at INTEGER NOT NULL,
    PRIMARY KEY(team_id, user_id),
    UNIQUE(guild_id, user_id),
    FOREIGN KEY(team_id) REFERENCES fe_teams(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fe_team_join_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'accepted', 'rejected', 'cancelled')),
    created_at INTEGER NOT NULL,
    decided_at INTEGER,
    UNIQUE(team_id, user_id, status),
    FOREIGN KEY(team_id) REFERENCES fe_teams(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fe_team_requests_pending
ON fe_team_join_requests(guild_id, team_id, status, created_at);
""")
db.executescript("""
CREATE TABLE IF NOT EXISTS fe_team_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    from_team_id INTEGER NOT NULL,
    to_team_id INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected','cancelled')),
    created_at INTEGER NOT NULL,
    decided_at INTEGER,
    decided_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fe_team_transfers_pending
ON fe_team_transfers(guild_id, to_team_id, status, created_at);
CREATE TABLE IF NOT EXISTS fe_team_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    team_id INTEGER,
    team_name TEXT NOT NULL,
    actor_id INTEGER NOT NULL,
    target_user_id INTEGER,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fe_team_history
ON fe_team_history(guild_id, team_id, created_at DESC);
""")
db.commit()


def _team_history(guild_id: int, team_id: int | None, team_name: str, actor_id: int, action: str, target_user_id: int | None = None, details: str = ""):
    db.execute(
        "INSERT INTO fe_team_history (guild_id, team_id, team_name, actor_id, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (guild_id, team_id, team_name, actor_id, target_user_id, action, details[:500], int(time.time()))
    )


def _team_staff(member: discord.Member) -> bool:
    return require_level(member, 1)


def _team_name(value: str) -> str:
    return value.strip()


@bot.tree.command(name="equipe_creer", description="Créer le profil de ton équipe Rocket League.")
@app_commands.describe(
    nom="Nom de l'équipe (unique sur ce serveur)",
    jeu="Jeu du serveur : Rocket League uniquement",
    plateforme="Plateforme principale (PC, Xbox, PlayStation, etc.)",
    rang="Niveau ou rang moyen de l'équipe",
    presentation="Présentation courte de l'équipe (facultative)"
)
@app_commands.checks.cooldown(2, 60.0)
async def equipe_creer(
    interaction: discord.Interaction,
    nom: app_commands.Range[str, 2, 40],
    jeu: app_commands.Range[str, 2, 60],
    plateforme: app_commands.Range[str, 2, 40],
    rang: app_commands.Range[str, 1, 60],
    presentation: app_commands.Range[str, 0, 250] = ""
):
    if not verifier_jeu_serveur(jeu, facultatif=False):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    jeu = JEU_SERVEUR
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return

    nom, jeu, plateforme, rang = _team_name(nom), jeu.strip(), plateforme.strip(), rang.strip()
    presentation = presentation.strip()
    if not all((nom, jeu, plateforme, rang)):
        await interaction.response.send_message("Le nom, le jeu, la plateforme et le rang sont obligatoires.", ephemeral=True)
        return
    already = db.execute(
        "SELECT team_id FROM fe_team_members WHERE guild_id = ? AND user_id = ?",
        (interaction.guild_id, interaction.user.id)
    ).fetchone()
    if already:
        await interaction.response.send_message("Tu es déjà membre d'une équipe. Quitte-la avant d'en créer une autre.", ephemeral=True)
        return
    try:
        now = int(time.time())
        cur = db.execute(
            """INSERT INTO fe_teams
            (guild_id, name, game, platform, rank, description, captain_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (interaction.guild_id, nom, jeu, plateforme, rang, presentation, interaction.user.id, now, now)
        )
        team_id = cur.lastrowid
        db.execute(
            "INSERT INTO fe_team_members (team_id, guild_id, user_id, joined_at) VALUES (?, ?, ?, ?)",
            (team_id, interaction.guild_id, interaction.user.id, now)
        )
        _team_history(interaction.guild_id, team_id, nom, interaction.user.id, "creation", interaction.user.id, "Création de l'équipe")
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        await interaction.response.send_message("Une équipe porte déjà ce nom, ou tu es déjà dans une équipe.", ephemeral=True)
        return

    embed = discord.Embed(title=f"🛡️ Équipe créée : {nom}", color=discord.Color.blurple())
    embed.add_field(name="Jeu", value=jeu, inline=True)
    embed.add_field(name="Plateforme", value=plateforme, inline=True)
    embed.add_field(name="Rang", value=rang, inline=True)
    embed.add_field(name="Capitaine", value=interaction.user.mention, inline=True)
    if presentation:
        embed.add_field(name="Présentation", value=presentation, inline=False)
    embed.set_footer(text=f"Équipe #{team_id} • Utilise /equipe_profil pour consulter la fiche")
    await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="equipes", description="Lister les équipes Rocket League du serveur.")
@app_commands.describe(jeu="Filtrer sur Rocket League (facultatif)")
async def equipes(interaction: discord.Interaction, jeu: str = ""):
    if not verifier_jeu_serveur(jeu, facultatif=True):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    if str(jeu or "").strip():
        jeu = JEU_SERVEUR
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    filtre = jeu.strip()
    if filtre:
        rows = db.execute(
            """SELECT t.id, t.name, t.game, t.platform, t.rank, t.captain_id, COUNT(m.user_id) AS member_count
            FROM fe_teams t LEFT JOIN fe_team_members m ON m.team_id = t.id
            WHERE t.guild_id = ? AND t.game LIKE ? COLLATE NOCASE
            GROUP BY t.id ORDER BY t.name COLLATE NOCASE LIMIT 25""",
            (interaction.guild_id, f"%{filtre}%")
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT t.id, t.name, t.game, t.platform, t.rank, t.captain_id, COUNT(m.user_id) AS member_count
            FROM fe_teams t LEFT JOIN fe_team_members m ON m.team_id = t.id
            WHERE t.guild_id = ? GROUP BY t.id ORDER BY t.name COLLATE NOCASE LIMIT 25""",
            (interaction.guild_id,)
        ).fetchall()
    if not rows:
        await interaction.response.send_message("Aucune équipe trouvée pour ce filtre.", ephemeral=True)
        return
    embed = discord.Embed(title="🛡️ Équipes de Fractale Esport", color=discord.Color.blurple())
    for row in rows:
        embed.add_field(
            name=f"#{row['id']} • {row['name']}",
            value=f"**Jeu :** {row['game']}\n**Rang :** {row['rank']} • **Plateforme :** {row['platform']}\n**Joueurs :** {row['member_count']} • **Capitaine :** <@{row['captain_id']}>\nVoir : `/equipe_profil nom:{row['name']}`",
            inline=False
        )
    if len(rows) == 25:
        embed.set_footer(text="Affichage limité à 25 équipes. Utilise le filtre par jeu si nécessaire.")
    await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="equipe_profil", description="Afficher la fiche détaillée d'une équipe.")
@app_commands.describe(nom="Nom exact de l'équipe")
async def equipe_profil(interaction: discord.Interaction, nom: str):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    team = db.execute(
        "SELECT * FROM fe_teams WHERE guild_id = ? AND name = ? COLLATE NOCASE",
        (interaction.guild_id, nom.strip())
    ).fetchone()
    if team is None:
        await interaction.response.send_message("Équipe introuvable. Utilise `/equipes` pour voir les équipes disponibles.", ephemeral=True)
        return
    members = db.execute(
        "SELECT user_id, joined_at FROM fe_team_members WHERE team_id = ? ORDER BY joined_at ASC",
        (team['id'],)
    ).fetchall()
    embed = discord.Embed(title=f"🛡️ {team['name']}", color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
    embed.add_field(name="Jeu principal", value=team['game'], inline=True)
    embed.add_field(name="Rang / niveau", value=team['rank'], inline=True)
    embed.add_field(name="Plateforme", value=team['platform'], inline=True)
    embed.add_field(name="Capitaine", value=f"<@{team['captain_id']}>", inline=True)
    embed.add_field(name=f"Membres ({len(members)})", value="\n".join(f"• <@{m['user_id']}>" for m in members[:25]) or "Aucun membre", inline=False)
    if team['description']:
        embed.add_field(name="Présentation", value=team['description'], inline=False)
    embed.set_footer(text=f"Équipe #{team['id']} • Fractale Esport")
    await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="equipe_modifier", description="Modifier la fiche de ton équipe (capitaine ou staff).")
@app_commands.describe(nom="Nom actuel de l'équipe", jeu="Jeu du serveur : Rocket League uniquement", plateforme="Nouvelle plateforme", rang="Nouveau rang", presentation="Nouvelle présentation")
async def equipe_modifier(
    interaction: discord.Interaction,
    nom: str,
    jeu: app_commands.Range[str, 2, 60],
    plateforme: app_commands.Range[str, 2, 40],
    rang: app_commands.Range[str, 1, 60],
    presentation: app_commands.Range[str, 0, 250] = ""
):
    if not verifier_jeu_serveur(jeu, facultatif=False):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    jeu = JEU_SERVEUR
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    team = db.execute("SELECT * FROM fe_teams WHERE guild_id = ? AND name = ? COLLATE NOCASE", (interaction.guild_id, nom.strip())).fetchone()
    if team is None:
        await interaction.response.send_message("Équipe introuvable.", ephemeral=True)
        return
    staff = isinstance(interaction.user, discord.Member) and _team_staff(interaction.user)
    if team['captain_id'] != interaction.user.id and not staff:
        await interaction.response.send_message("Seul le capitaine ou le staff peut modifier cette fiche.", ephemeral=True)
        return
    try:
        db.execute(
            "UPDATE fe_teams SET game = ?, platform = ?, rank = ?, description = ?, updated_at = ? WHERE id = ? AND guild_id = ?",
            (jeu.strip(), plateforme.strip(), rang.strip(), presentation.strip(), int(time.time()), team['id'], interaction.guild_id)
        )
        db.commit()
    except sqlite3.Error:
        db.rollback()
        await interaction.response.send_message("Impossible de mettre à jour cette équipe pour le moment.", ephemeral=True)
        return
    await interaction.response.send_message(f"✅ La fiche de **{team['name']}** a été mise à jour.", ephemeral=True)


@bot.tree.command(name="equipe_rejoindre", description="Demander à rejoindre une équipe esport.")
@app_commands.describe(nom="Nom de l'équipe", message="Petit message au capitaine (facultatif)")
async def equipe_rejoindre(interaction: discord.Interaction, nom: str, message: app_commands.Range[str, 0, 250] = ""):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    team = db.execute("SELECT * FROM fe_teams WHERE guild_id = ? AND name = ? COLLATE NOCASE", (interaction.guild_id, nom.strip())).fetchone()
    if team is None:
        await interaction.response.send_message("Équipe introuvable. Utilise `/equipes`.", ephemeral=True)
        return
    if team['captain_id'] == interaction.user.id:
        await interaction.response.send_message("Tu es déjà capitaine de cette équipe.", ephemeral=True)
        return
    current = db.execute("SELECT team_id FROM fe_team_members WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, interaction.user.id)).fetchone()
    if current:
        await interaction.response.send_message("Tu es déjà membre d'une équipe. Quitte-la avant de demander à en rejoindre une autre.", ephemeral=True)
        return
    pending = db.execute("SELECT id FROM fe_team_join_requests WHERE team_id = ? AND user_id = ? AND status = 'pending'", (team['id'], interaction.user.id)).fetchone()
    if pending:
        await interaction.response.send_message(f"Tu as déjà une demande en attente (#{pending['id']}).", ephemeral=True)
        return
    db.execute(
        "INSERT INTO fe_team_join_requests (team_id, guild_id, user_id, message, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
        (team['id'], interaction.guild_id, interaction.user.id, message.strip(), int(time.time()))
    )
    db.commit()
    await interaction.response.send_message(f"✅ Ta demande pour rejoindre **{team['name']}** a été envoyée au capitaine <@{team['captain_id']}>.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="equipe_demandes", description="Voir les demandes pour ton équipe (capitaine).")
async def equipe_demandes(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    teams = db.execute("SELECT id, name FROM fe_teams WHERE guild_id = ? AND captain_id = ?", (interaction.guild_id, interaction.user.id)).fetchall()
    if not teams:
        await interaction.response.send_message("Tu n'es capitaine d'aucune équipe.", ephemeral=True)
        return
    team_ids = [int(t['id']) for t in teams]
    placeholders = ",".join("?" for _ in team_ids)
    rows = db.execute(
        f"SELECT r.id, r.user_id, r.message, r.created_at, t.name FROM fe_team_join_requests r JOIN fe_teams t ON t.id = r.team_id WHERE r.guild_id = ? AND r.team_id IN ({placeholders}) AND r.status = 'pending' ORDER BY r.created_at ASC LIMIT 25",
        (interaction.guild_id, *team_ids)
    ).fetchall()
    if not rows:
        await interaction.response.send_message("Aucune demande en attente pour tes équipes.", ephemeral=True)
        return
    embed = discord.Embed(title="📨 Demandes pour rejoindre ton équipe", color=discord.Color.blurple())
    for row in rows:
        desc = f"Équipe : **{row['name']}**\nCandidat : <@{row['user_id']}>\nDemande : `/equipe_decider demande_id:{row['id']} decision:accepter`"
        if row['message']:
            desc += f"\nMessage : {row['message']}"
        embed.add_field(name=f"Demande #{row['id']}", value=desc, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="equipe_decider", description="Accepter ou refuser une demande de rejoindre ton équipe.")
@app_commands.describe(demande_id="Identifiant affiché par /equipe_demandes", decision="Accepter ou refuser la demande")
@app_commands.choices(decision=[
    app_commands.Choice(name="Accepter", value="accepter"),
    app_commands.Choice(name="Refuser", value="refuser")
])
async def equipe_decider(interaction: discord.Interaction, demande_id: app_commands.Range[int, 1, 2147483647], decision: app_commands.Choice[str]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    req = db.execute(
        "SELECT r.*, t.name AS team_name, t.captain_id FROM fe_team_join_requests r JOIN fe_teams t ON t.id = r.team_id WHERE r.id = ? AND r.guild_id = ? AND r.status = 'pending'",
        (demande_id, interaction.guild_id)
    ).fetchone()
    if req is None:
        await interaction.response.send_message("Demande introuvable ou déjà traitée.", ephemeral=True)
        return
    staff = isinstance(interaction.user, discord.Member) and _team_staff(interaction.user)
    if req['captain_id'] != interaction.user.id and not staff:
        await interaction.response.send_message("Seul le capitaine de cette équipe ou le staff peut traiter cette demande.", ephemeral=True)
        return
    now = int(time.time())
    if decision.value == "accepter":
        current = db.execute("SELECT team_id FROM fe_team_members WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, req['user_id'])).fetchone()
        if current:
            await interaction.response.send_message("Ce membre appartient déjà à une équipe. La demande reste en attente jusqu'à sa clôture.", ephemeral=True)
            return
        try:
            db.execute("INSERT INTO fe_team_members (team_id, guild_id, user_id, joined_at) VALUES (?, ?, ?, ?)", (req['team_id'], interaction.guild_id, req['user_id'], now))
            db.execute("UPDATE fe_team_join_requests SET status = 'accepted', decided_at = ? WHERE id = ?", (now, demande_id))
            db.execute("UPDATE fe_team_join_requests SET status = 'rejected', decided_at = ? WHERE team_id = ? AND user_id = ? AND status = 'pending' AND id != ?", (now, req['team_id'], req['user_id'], demande_id))
            _team_history(interaction.guild_id, int(req['team_id']), str(req['team_name']), interaction.user.id, "adhesion_acceptee", int(req['user_id']), f"Demande #{demande_id} acceptée")
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            await interaction.response.send_message("Impossible d'ajouter ce membre : il a peut-être rejoint une autre équipe entre-temps.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ <@{req['user_id']}> rejoint désormais **{req['team_name']}**.", allowed_mentions=discord.AllowedMentions.none())
    else:
        db.execute("UPDATE fe_team_join_requests SET status = 'rejected', decided_at = ? WHERE id = ?", (now, demande_id))
        db.commit()
        await interaction.response.send_message(f"Demande **#{demande_id}** refusée.", ephemeral=True)


@bot.tree.command(name="equipe_quitter", description="Quitter l'équipe dont tu es membre.")
async def equipe_quitter(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    member = db.execute(
        "SELECT m.team_id, t.name, t.captain_id FROM fe_team_members m JOIN fe_teams t ON t.id = m.team_id WHERE m.guild_id = ? AND m.user_id = ?",
        (interaction.guild_id, interaction.user.id)
    ).fetchone()
    if member is None:
        await interaction.response.send_message("Tu n'es membre d'aucune équipe.", ephemeral=True)
        return
    if member['captain_id'] == interaction.user.id:
        await interaction.response.send_message("Tu es capitaine. Transfère d'abord le rôle avec `/equipe_capitaine`, ou supprime l'équipe avec `/equipe_supprimer`.", ephemeral=True)
        return
    db.execute("DELETE FROM fe_team_members WHERE team_id = ? AND user_id = ?", (member['team_id'], interaction.user.id))
    db.execute("UPDATE fe_team_join_requests SET status = 'cancelled', decided_at = ? WHERE guild_id = ? AND user_id = ? AND status = 'pending'", (int(time.time()), interaction.guild_id, interaction.user.id))
    _team_history(interaction.guild_id, int(member['team_id']), str(member['name']), interaction.user.id, "depart", interaction.user.id, "Le joueur a quitté l'équipe")
    db.commit()
    await interaction.response.send_message(f"Tu as quitté l'équipe **{member['name']}**.", ephemeral=True)


@bot.tree.command(name="equipe_exclure", description="Retirer un membre de ton équipe (capitaine ou staff).")
@app_commands.describe(membre="Membre à retirer de l'équipe")
async def equipe_exclure(interaction: discord.Interaction, membre: discord.Member):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    staff = _team_staff(interaction.user) if isinstance(interaction.user, discord.Member) else False
    if staff:
        team = db.execute(
            "SELECT t.* FROM fe_teams t JOIN fe_team_members m ON m.team_id = t.id WHERE t.guild_id = ? AND m.user_id = ?",
            (interaction.guild_id, membre.id)
        ).fetchone()
    else:
        team = db.execute(
            "SELECT * FROM fe_teams WHERE guild_id = ? AND captain_id = ?",
            (interaction.guild_id, interaction.user.id)
        ).fetchone()
    if team is None:
        await interaction.response.send_message("Tu dois être capitaine d'une équipe, ou le membre visé doit appartenir à une équipe si tu es staff.", ephemeral=True)
        return
    if membre.id == team['captain_id']:
        await interaction.response.send_message("Tu ne peux pas exclure le capitaine avec cette commande. Transfère d'abord le rôle ou supprime l'équipe.", ephemeral=True)
        return
    if team['captain_id'] != interaction.user.id and not staff:
        await interaction.response.send_message("Seul le capitaine de l'équipe ou le staff peut exclure un membre.", ephemeral=True)
        return
    deleted = db.execute("DELETE FROM fe_team_members WHERE team_id = ? AND user_id = ? AND user_id != ?", (team['id'], membre.id, team['captain_id'])).rowcount
    if not deleted:
        await interaction.response.send_message("Ce membre n'appartient pas à cette équipe, ou il s'agit du capitaine.", ephemeral=True)
        return
    db.execute("UPDATE fe_team_join_requests SET status = 'cancelled', decided_at = ? WHERE team_id = ? AND user_id = ? AND status = 'pending'", (int(time.time()), team['id'], membre.id))
    _team_history(interaction.guild_id, int(team['id']), str(team['name']), interaction.user.id, "exclusion", membre.id, "Membre retiré de l'équipe")
    db.commit()
    await interaction.response.send_message(f"Le membre {membre.mention} a été retiré de **{team['name']}**.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="equipe_capitaine", description="Transférer le rôle de capitaine à un membre de ton équipe.")
@app_commands.describe(nouveau_capitaine="Membre actuel de ton équipe")
async def equipe_capitaine(interaction: discord.Interaction, nouveau_capitaine: discord.Member):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    team = db.execute("SELECT * FROM fe_teams WHERE guild_id = ? AND captain_id = ?", (interaction.guild_id, interaction.user.id)).fetchone()
    if team is None:
        await interaction.response.send_message("Seul le capitaine actuel peut transférer ce rôle.", ephemeral=True)
        return
    member = db.execute("SELECT 1 FROM fe_team_members WHERE team_id = ? AND user_id = ?", (team['id'], nouveau_capitaine.id)).fetchone()
    if not member or nouveau_capitaine.id == interaction.user.id:
        await interaction.response.send_message("Le nouveau capitaine doit être un autre membre de ton équipe.", ephemeral=True)
        return
    db.execute("UPDATE fe_teams SET captain_id = ?, updated_at = ? WHERE id = ?", (nouveau_capitaine.id, int(time.time()), team['id']))
    _team_history(interaction.guild_id, int(team['id']), str(team['name']), interaction.user.id, "changement_capitaine", nouveau_capitaine.id, f"Ancien capitaine : {interaction.user.id}")
    db.commit()
    await interaction.response.send_message(f"👑 {nouveau_capitaine.mention} est désormais capitaine de **{team['name']}**.", allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="equipe_supprimer", description="Supprimer ton équipe (capitaine) ou une équipe en tant que staff.")
@app_commands.describe(nom="Nom exact de l'équipe à supprimer")
async def equipe_supprimer(interaction: discord.Interaction, nom: str):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    team = db.execute("SELECT * FROM fe_teams WHERE guild_id = ? AND name = ? COLLATE NOCASE", (interaction.guild_id, nom.strip())).fetchone()
    if team is None:
        await interaction.response.send_message("Équipe introuvable.", ephemeral=True)
        return
    staff = isinstance(interaction.user, discord.Member) and _team_staff(interaction.user)
    if team['captain_id'] != interaction.user.id and not staff:
        await interaction.response.send_message("Seul le capitaine ou le staff peut supprimer cette équipe.", ephemeral=True)
        return
    _team_history(interaction.guild_id, int(team['id']), str(team['name']), interaction.user.id, "suppression", None, "Équipe supprimée")
    db.execute("DELETE FROM fe_teams WHERE id = ? AND guild_id = ?", (team['id'], interaction.guild_id))
    db.commit()
    await interaction.response.send_message(f"🗑️ L'équipe **{team['name']}** a été supprimée.", ephemeral=True)


# ============================================================
# TRANSFERTS ET HISTORIQUE DES ÉQUIPES
# ============================================================

@bot.tree.command(name="equipe_transfert", description="Demander un transfert vers une autre équipe Rocket League.")
@app_commands.describe(equipe_cible="Nom exact de l'équipe qui pourrait t'accueillir", message="Message facultatif pour le capitaine")
async def equipe_transfert(interaction: discord.Interaction, equipe_cible: str, message: app_commands.Range[str, 0, 250] = ""):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    current = db.execute(
        "SELECT t.id, t.name FROM fe_team_members m JOIN fe_teams t ON t.id = m.team_id WHERE m.guild_id = ? AND m.user_id = ?",
        (interaction.guild_id, interaction.user.id)
    ).fetchone()
    if current is None:
        await interaction.response.send_message("Tu dois déjà appartenir à une équipe pour demander un transfert.", ephemeral=True)
        return
    target = db.execute(
        "SELECT id, name, captain_id FROM fe_teams WHERE guild_id = ? AND name = ? COLLATE NOCASE",
        (interaction.guild_id, equipe_cible.strip())
    ).fetchone()
    if target is None:
        await interaction.response.send_message("Équipe cible introuvable. Consulte `/equipes`.", ephemeral=True)
        return
    if int(target["id"]) == int(current["id"]):
        await interaction.response.send_message("Tu es déjà dans cette équipe.", ephemeral=True)
        return
    pending = db.execute(
        "SELECT id FROM fe_team_transfers WHERE guild_id = ? AND user_id = ? AND status = 'pending'",
        (interaction.guild_id, interaction.user.id)
    ).fetchone()
    if pending:
        await interaction.response.send_message(f"Tu as déjà un transfert en attente (#{pending['id']}).", ephemeral=True)
        return
    now = int(time.time())
    cur = db.execute(
        "INSERT INTO fe_team_transfers (guild_id, user_id, from_team_id, to_team_id, message, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (interaction.guild_id, interaction.user.id, current["id"], target["id"], message.strip(), now)
    )
    transfer_id = int(cur.lastrowid)
    _team_history(interaction.guild_id, int(current["id"]), str(current["name"]), interaction.user.id, "transfert_demande", interaction.user.id, f"Demande #{transfer_id} vers {target['name']}")
    _team_history(interaction.guild_id, int(target["id"]), str(target["name"]), interaction.user.id, "transfert_recu", interaction.user.id, f"Demande #{transfer_id} depuis {current['name']}")
    db.commit()
    await interaction.response.send_message(
        f"📨 Demande de transfert **#{transfer_id}** envoyée au capitaine de **{target['name']}**. Tu restes dans **{current['name']}** tant que le transfert n'est pas accepté.",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(name="transferts_equipe", description="Consulter les demandes de transfert reçues par ton équipe.")
async def transferts_equipe(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    staff = isinstance(interaction.user, discord.Member) and _team_staff(interaction.user)
    if staff:
        rows = db.execute(
            "SELECT x.id, x.user_id, x.message, x.created_at, a.name AS from_name, b.name AS to_name, b.captain_id FROM fe_team_transfers x JOIN fe_teams a ON a.id = x.from_team_id JOIN fe_teams b ON b.id = x.to_team_id WHERE x.guild_id = ? AND x.status = 'pending' ORDER BY x.created_at ASC LIMIT 25",
            (interaction.guild_id,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT x.id, x.user_id, x.message, x.created_at, a.name AS from_name, b.name AS to_name, b.captain_id FROM fe_team_transfers x JOIN fe_teams a ON a.id = x.from_team_id JOIN fe_teams b ON b.id = x.to_team_id WHERE x.guild_id = ? AND b.captain_id = ? AND x.status = 'pending' ORDER BY x.created_at ASC LIMIT 25",
            (interaction.guild_id, interaction.user.id)
        ).fetchall()
    if not rows:
        await interaction.response.send_message("Aucun transfert en attente pour tes équipes.", ephemeral=True)
        return
    embed = discord.Embed(title="🔁 Demandes de transfert", color=discord.Color.blurple())
    for row in rows:
        value = f"Joueur : <@{row['user_id']}>\nDe : **{row['from_name']}**\nVers : **{row['to_name']}**\nDécider : `/transfert_accepter transfert_id:{row['id']}` ou `/transfert_refuser transfert_id:{row['id']}`"
        if row["message"]:
            value += f"\nMessage : {row['message']}"
        embed.add_field(name=f"Transfert #{row['id']}", value=value[:1024], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def _decider_transfert(interaction: discord.Interaction, transfert_id: int, accepter: bool):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    row = db.execute(
        "SELECT x.*, a.name AS from_name, a.captain_id AS from_captain_id, b.name AS to_name, b.captain_id FROM fe_team_transfers x JOIN fe_teams a ON a.id = x.from_team_id JOIN fe_teams b ON b.id = x.to_team_id WHERE x.id = ? AND x.guild_id = ? AND x.status = 'pending'",
        (transfert_id, interaction.guild_id)
    ).fetchone()
    if row is None:
        await interaction.response.send_message("Transfert introuvable, déjà traité, ou une équipe a été supprimée.", ephemeral=True)
        return
    staff = isinstance(interaction.user, discord.Member) and _team_staff(interaction.user)
    if int(row["captain_id"]) != interaction.user.id and not staff:
        await interaction.response.send_message("Seul le capitaine de l'équipe cible ou le staff peut traiter ce transfert.", ephemeral=True)
        return
    now = int(time.time())
    if accepter:
        current = db.execute("SELECT team_id FROM fe_team_members WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, row["user_id"])).fetchone()
        if current is None or int(current["team_id"]) != int(row["from_team_id"]):
            db.execute("UPDATE fe_team_transfers SET status = 'cancelled', decided_at = ?, decided_by = ? WHERE id = ?", (now, interaction.user.id, transfert_id))
            db.commit()
            await interaction.response.send_message("Le transfert a été annulé : le joueur n'est plus dans l'équipe de départ.", ephemeral=True)
            return
        if int(row["user_id"]) == int(row["from_captain_id"]):
            await interaction.response.send_message("Le capitaine doit d'abord transférer son rôle avec `/equipe_capitaine` avant un transfert.", ephemeral=True)
            return
        try:
            db.execute("DELETE FROM fe_team_members WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, row["user_id"]))
            db.execute("INSERT INTO fe_team_members (team_id, guild_id, user_id, joined_at) VALUES (?, ?, ?, ?)", (row["to_team_id"], interaction.guild_id, row["user_id"], now))
            db.execute("UPDATE fe_team_transfers SET status = 'accepted', decided_at = ?, decided_by = ? WHERE id = ?", (now, interaction.user.id, transfert_id))
            db.execute("UPDATE fe_team_transfers SET status = 'cancelled', decided_at = ?, decided_by = ? WHERE guild_id = ? AND user_id = ? AND status = 'pending' AND id != ?", (now, interaction.user.id, interaction.guild_id, row["user_id"], transfert_id))
            db.execute("UPDATE fe_team_join_requests SET status = 'cancelled', decided_at = ? WHERE guild_id = ? AND user_id = ? AND status = 'pending'", (now, interaction.guild_id, row["user_id"]))
            _team_history(interaction.guild_id, int(row["from_team_id"]), str(row["from_name"]), interaction.user.id, "transfert_sortant", int(row["user_id"]), f"Transfert #{transfert_id} vers {row['to_name']}")
            _team_history(interaction.guild_id, int(row["to_team_id"]), str(row["to_name"]), interaction.user.id, "transfert_entrant", int(row["user_id"]), f"Transfert #{transfert_id} depuis {row['from_name']}")
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            await interaction.response.send_message("Le transfert n'a pas pu être effectué. Vérifie que le joueur n'appartient pas déjà à une autre équipe.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Transfert accepté : <@{row['user_id']}> rejoint **{row['to_name']}** depuis **{row['from_name']}**.", allowed_mentions=discord.AllowedMentions.none())
    else:
        db.execute("UPDATE fe_team_transfers SET status = 'rejected', decided_at = ?, decided_by = ? WHERE id = ?", (now, interaction.user.id, transfert_id))
        _team_history(interaction.guild_id, int(row["to_team_id"]), str(row["to_name"]), interaction.user.id, "transfert_refuse", int(row["user_id"]), f"Demande #{transfert_id} depuis {row['from_name']}")
        db.commit()
        await interaction.response.send_message(f"Demande de transfert **#{transfert_id}** refusée. Le joueur reste dans **{row['from_name']}**.", ephemeral=True)


@bot.tree.command(name="transfert_accepter", description="Accepter un transfert vers ton équipe.")
@app_commands.describe(transfert_id="Identifiant affiché par /transferts_equipe")
async def transfert_accepter(interaction: discord.Interaction, transfert_id: app_commands.Range[int, 1, 2147483647]):
    await _decider_transfert(interaction, transfert_id, True)


@bot.tree.command(name="transfert_refuser", description="Refuser un transfert vers ton équipe.")
@app_commands.describe(transfert_id="Identifiant affiché par /transferts_equipe")
async def transfert_refuser(interaction: discord.Interaction, transfert_id: app_commands.Range[int, 1, 2147483647]):
    await _decider_transfert(interaction, transfert_id, False)


@bot.tree.command(name="historique_equipe", description="Consulter l'historique des mouvements d'une équipe Rocket League.")
@app_commands.describe(nom="Nom exact de l'équipe", nombre="Nombre d'événements à afficher (1 à 15)")
async def historique_equipe(interaction: discord.Interaction, nom: str, nombre: app_commands.Range[int, 1, 15] = 10):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    team = db.execute("SELECT id, name, captain_id FROM fe_teams WHERE guild_id = ? AND name = ? COLLATE NOCASE", (interaction.guild_id, nom.strip())).fetchone()
    if team is None:
        await interaction.response.send_message("Équipe introuvable.", ephemeral=True)
        return
    staff = isinstance(interaction.user, discord.Member) and _team_staff(interaction.user)
    is_member = db.execute("SELECT 1 FROM fe_team_members WHERE guild_id = ? AND team_id = ? AND user_id = ?", (interaction.guild_id, team["id"], interaction.user.id)).fetchone() is not None
    if not staff and not is_member:
        await interaction.response.send_message("Seuls les membres de l'équipe et le staff peuvent consulter cet historique.", ephemeral=True)
        return
    rows = db.execute("SELECT * FROM fe_team_history WHERE guild_id = ? AND team_id = ? ORDER BY created_at DESC, id DESC LIMIT ?", (interaction.guild_id, team["id"], nombre)).fetchall()
    if not rows:
        await interaction.response.send_message(f"Aucun événement enregistré pour **{team['name']}** pour le moment.", ephemeral=True)
        return
    labels = {"creation": "Équipe créée", "adhesion_acceptee": "Nouveau membre", "depart": "Départ volontaire", "exclusion": "Membre exclu", "changement_capitaine": "Changement de capitaine", "suppression": "Équipe supprimée", "transfert_demande": "Transfert demandé", "transfert_recu": "Transfert reçu", "transfert_sortant": "Transfert sortant", "transfert_entrant": "Transfert entrant", "transfert_refuse": "Transfert refusé"}
    embed = discord.Embed(title=f"📜 Historique — {team['name']}", color=discord.Color.blurple())
    for event in rows:
        timestamp = discord.utils.format_dt(discord.utils.snowflake_time(0), style="R") if False else f"<t:{int(event['created_at'])}:R>"
        detail = f"\n{event['details']}" if event["details"] else ""
        target = f" • Joueur : <@{event['target_user_id']}>" if event["target_user_id"] else ""
        value = f"{timestamp}\nPar : <@{event['actor_id']}>{target}{detail}"
        embed.add_field(name=labels.get(event["action"], str(event["action"])), value=value[:1024], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


# ============================================================
# CENTRE DE GESTION DES ÉQUIPES
# ============================================================

@bot.tree.command(
    name="equipe_gestion",
    description="Afficher le tableau de bord de ton équipe et ses activités."
)
@app_commands.describe(nom="Nom de l'équipe (facultatif pour les membres ; utile au staff)")
async def equipe_gestion(interaction: discord.Interaction, nom: str = ""):
    """Tableau de bord en lecture seule : centralise les informations déjà enregistrées."""
    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return

    is_staff = isinstance(interaction.user, discord.Member) and _team_staff(interaction.user)
    team = None
    nom = nom.strip()

    if nom:
        team = db.execute(
            "SELECT * FROM fe_teams WHERE guild_id = ? AND name = ? COLLATE NOCASE",
            (interaction.guild_id, nom)
        ).fetchone()
        if team is None:
            await interaction.response.send_message(
                "Équipe introuvable. Utilise `/equipes` pour consulter les équipes disponibles.",
                ephemeral=True
            )
            return
        is_member = db.execute(
            "SELECT 1 FROM fe_team_members WHERE guild_id = ? AND team_id = ? AND user_id = ?",
            (interaction.guild_id, team["id"], interaction.user.id)
        ).fetchone() is not None
        if not is_staff and not is_member:
            await interaction.response.send_message(
                "Tu dois être membre de cette équipe ou faire partie du staff pour ouvrir son tableau de bord.",
                ephemeral=True
            )
            return
    else:
        team = db.execute(
            """SELECT t.* FROM fe_teams t
               JOIN fe_team_members m ON m.team_id = t.id AND m.guild_id = t.guild_id
               WHERE t.guild_id = ? AND m.user_id = ? LIMIT 1""",
            (interaction.guild_id, interaction.user.id)
        ).fetchone()
        if team is None:
            if is_staff:
                await interaction.response.send_message(
                    "Indique le nom de l'équipe à consulter, par exemple `/equipe_gestion nom:Zenith`.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Tu ne fais partie d'aucune équipe. Utilise `/equipes` pour en consulter ou `/equipe_creer` pour créer la tienne.",
                    ephemeral=True
                )
            return

    team_id = int(team["id"])
    captain_id = int(team["captain_id"])
    is_captain = interaction.user.id == captain_id
    can_manage = is_staff or is_captain

    members = db.execute(
        """SELECT user_id, joined_at FROM fe_team_members
           WHERE team_id = ? AND guild_id = ? ORDER BY joined_at ASC""",
        (team_id, interaction.guild_id)
    ).fetchall()
    member_lines = []
    for row in members[:15]:
        label = " 👑" if int(row["user_id"]) == captain_id else ""
        member_lines.append(f"• <@{int(row['user_id'])}>{label}")
    if len(members) > 15:
        member_lines.append(f"… et {len(members) - 15} autre(s) membre(s)")

    embed = discord.Embed(
        title=f"🧭 Centre de gestion — {team['name']}",
        description=(team["description"] or "Aucune présentation enregistrée.")[:400],
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="🎮 Jeu", value=str(team["game"])[:100], inline=True)
    embed.add_field(name="🖥️ Plateforme", value=str(team["platform"])[:100], inline=True)
    embed.add_field(name="🏅 Rang", value=str(team["rank"])[:100], inline=True)
    embed.add_field(name="👑 Capitaine", value=f"<@{captain_id}>", inline=True)
    embed.add_field(
        name=f"👥 Effectif ({len(members)})",
        value="\n".join(member_lines) or "Aucun membre enregistré.",
        inline=False
    )

    if can_manage:
        pending_requests = db.execute(
            """SELECT id, user_id, message, created_at FROM fe_team_join_requests
               WHERE guild_id = ? AND team_id = ? AND status = 'pending'
               ORDER BY created_at ASC LIMIT 6""",
            (interaction.guild_id, team_id)
        ).fetchall()
        all_pending_requests = db.execute(
            """SELECT COUNT(*) FROM fe_team_join_requests
               WHERE guild_id = ? AND team_id = ? AND status = 'pending'""",
            (interaction.guild_id, team_id)
        ).fetchone()[0]
        if pending_requests:
            request_lines = [f"• **#{r['id']}** — <@{int(r['user_id'])}>" for r in pending_requests]
            if all_pending_requests > len(pending_requests):
                request_lines.append(f"… et {all_pending_requests - len(pending_requests)} autre(s)")
            request_text = "\n".join(request_lines) + "\nConsulter : `/equipe_demandes`"
        else:
            request_text = "Aucune demande en attente."
        embed.add_field(
            name=f"📨 Demandes d'adhésion ({all_pending_requests})",
            value=request_text[:1024], inline=False
        )

        recruitments = db.execute(
            """SELECT id, game, positions, slots, created_at FROM fe_recruitments
               WHERE guild_id = ? AND team_name = ? COLLATE NOCASE AND status = 'open'
               ORDER BY created_at DESC LIMIT 5""",
            (interaction.guild_id, team["name"])
        ).fetchall()
        open_recruitment_count = db.execute(
            """SELECT COUNT(*) FROM fe_recruitments
               WHERE guild_id = ? AND team_name = ? COLLATE NOCASE AND status = 'open'""",
            (interaction.guild_id, team["name"])
        ).fetchone()[0]
        recruitment_lines = []
        for rec in recruitments:
            pending_apps = db.execute(
                "SELECT COUNT(*) FROM fe_applications WHERE recruitment_id = ? AND status = 'pending'",
                (rec["id"],)
            ).fetchone()[0]
            recruitment_lines.append(
                f"• **Annonce #{rec['id']}** — {rec['game']} · {rec['positions']} · "
                f"places : {rec['slots']} · candidatures en attente : {pending_apps}"
            )
        if not recruitment_lines:
            recruitment_lines.append("Aucune annonce ouverte associée à ce nom d'équipe.")
        recruitment_lines.append("Commandes : `/recrutements`, `/candidatures_equipe`, `/recrutement_fermer`.")
        embed.add_field(
            name=f"📣 Recrutement ({open_recruitment_count} annonce(s) ouverte(s))",
            value="\n".join(recruitment_lines)[:1024], inline=False
        )

        scrims = db.execute(
            """SELECT id, game, level, game_format, availability, status, created_at
               FROM fe_scrim_searches
               WHERE guild_id = ? AND team_name = ? COLLATE NOCASE
                 AND status IN ('open', 'matched')
               ORDER BY created_at DESC LIMIT 5""",
            (interaction.guild_id, team["name"])
        ).fetchall()
        open_scrims = db.execute(
            """SELECT COUNT(*) FROM fe_scrim_searches
               WHERE guild_id = ? AND team_name = ? COLLATE NOCASE AND status = 'open'""",
            (interaction.guild_id, team["name"])
        ).fetchone()[0]
        scrim_lines = []
        for scrim in scrims:
            status_label = "ouverte" if scrim["status"] == "open" else "match trouvé"
            scrim_lines.append(
                f"• **#{scrim['id']}** {scrim['game']} · {scrim['game_format']} · "
                f"{scrim['level']} · {status_label} · {scrim['availability']}"
            )
        if not scrim_lines:
            scrim_lines.append("Aucune recherche de scrim active ou récemment associée à cette équipe.")
        scrim_lines.append("Commandes : `/scrims`, `/scrim_rechercher`, `/scrim_annuler`.")
        embed.add_field(
            name=f"⚔️ Scrims ({open_scrims} recherche(s) ouverte(s))",
            value="\n".join(scrim_lines)[:1024], inline=False
        )

        availability_rows = db.execute(
            """SELECT a.user_id, a.days, a.hours, a.note
               FROM fe_player_availability a
               JOIN fe_team_members m ON m.guild_id = a.guild_id AND m.user_id = a.user_id
               WHERE m.team_id = ? AND a.guild_id = ? AND a.game = ? COLLATE NOCASE
               ORDER BY a.user_id LIMIT 10""",
            (team_id, interaction.guild_id, team["game"])
        ).fetchall()
        availability_lines = [
            f"• <@{int(a['user_id'])}> — {a['days']}, {a['hours']}" +
            (f" ({a['note']})" if a["note"] else "")
            for a in availability_rows
        ]
        if not availability_lines:
            availability_lines = [
                f"Aucune disponibilité enregistrée pour **{team['game']}**. "
                "Les membres peuvent utiliser `/disponibilite`."
            ]
        embed.add_field(
            name=f"📅 Disponibilités des membres ({len(availability_rows)} affichée(s))",
            value="\n".join(availability_lines)[:1024], inline=False
        )
        embed.add_field(
            name="🧰 Raccourcis de gestion",
            value=(
                "`/equipe_modifier` · `/equipe_demandes` · `/equipe_decider`\n"
                "`/equipe_exclure` · `/equipe_capitaine` · `/equipe_profil`\n"
                "`/disponibilites` · `/recrutements` · `/scrims`"
            ),
            inline=False
        )
    else:
        embed.add_field(
            name="🧰 Actions disponibles",
            value=(
                "`/equipe_profil` pour la fiche complète · `/equipe_quitter` pour quitter l'équipe.\n"
                "Le tableau de bord de gestion (demandes, recrutement et scrims) est réservé au capitaine et au staff."
            ),
            inline=False
        )

    embed.set_footer(text=f"Équipe #{team_id} • Tableau de bord privé • Fractale Esport")
    await interaction.response.send_message(
        embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
    )


# ============================================================
# RECHERCHE DE COÉQUIPIERS
# ============================================================

@bot.tree.command(name="coequipier", description="Publier une recherche de coéquipiers Rocket League.")
@app_commands.describe(
    jeu="Rocket League uniquement",
    rang="Ton rang ou ton niveau",
    mode="Mode de jeu recherché",
    plateforme="Ta plateforme",
    disponibilite="Quand tu es disponible",
    note="Micro, style de jeu ou autres précisions (facultatif)"
)
@app_commands.checks.cooldown(1, 60.0)
async def coequipier(
    interaction: discord.Interaction,
    jeu: str,
    rang: str,
    mode: str,
    plateforme: str,
    disponibilite: str,
    note: str = ""
):
    if not verifier_jeu_serveur(jeu, facultatif=False):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    jeu = JEU_SERVEUR
    if interaction.guild is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    fields = {"jeu": jeu, "rang": rang, "mode": mode, "plateforme": plateforme, "disponibilités": disponibilite}
    for label, value in fields.items():
        if not value.strip() or len(value.strip()) > 80:
            await interaction.response.send_message(f"Le champ **{label}** doit contenir entre 1 et 80 caractères.", ephemeral=True)
            return
    if len(note) > 180:
        await interaction.response.send_message("La note ne doit pas dépasser 180 caractères.", ephemeral=True)
        return
    now = int(time.time())
    db.execute(
        "INSERT INTO teammate_searches (guild_id, user_id, game, rank, mode, platform, availability, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (interaction.guild.id, interaction.user.id, jeu.strip(), rang.strip(), mode.strip(), plateforme.strip(), disponibilite.strip(), note.strip(), now)
    )
    search_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    embed = discord.Embed(title="🎮 Recherche de coéquipiers", color=discord.Color.blurple())
    embed.description = f"**{interaction.user.display_name}** cherche des joueurs !\n\n" \
        f"**Jeu :** {jeu}\n**Rang/niveau :** {rang}\n**Mode :** {mode}\n" \
        f"**Plateforme :** {plateforme}\n**Disponibilités :** {disponibilite}"
    if note.strip():
        embed.add_field(name="Précisions", value=note.strip(), inline=False)
    embed.set_footer(text=f"Annonce #{search_id} • Ferme-la avec /coequipier_fermer {search_id}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="coequipiers", description="Afficher les recherches de coéquipiers ouvertes.")
async def coequipiers(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    rows = db.execute(
        "SELECT id, user_id, game, rank, mode, platform, availability, note, created_at FROM teammate_searches WHERE guild_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 10",
        (interaction.guild.id,)
    ).fetchall()
    if not rows:
        await interaction.response.send_message("Aucune recherche ouverte pour le moment. Utilise `/coequipier` pour en publier une !", ephemeral=True)
        return
    embed = discord.Embed(title="🔎 Recherches de coéquipiers ouvertes", color=discord.Color.blurple())
    for row in rows:
        note = f"\n_{row['note']}_" if row['note'] else ""
        embed.add_field(
            name=f"#{row['id']} • {row['game']} — {row['rank']}",
            value=f"👤 <@{row['user_id']}>\n🎯 {row['mode']} • {row['platform']}\n🕒 {row['availability']}{note}",
            inline=False
        )
    embed.set_footer(text="Pour retirer ta propre annonce : /coequipier_fermer numéro")
    await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="coequipier_fermer", description="Fermer ta recherche de coéquipiers.")
@app_commands.describe(annonce_id="Numéro de l'annonce affiché dans /coequipiers")
async def coequipier_fermer(interaction: discord.Interaction, annonce_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    row = db.execute(
        "SELECT user_id, status FROM teammate_searches WHERE id = ? AND guild_id = ?",
        (annonce_id, interaction.guild.id)
    ).fetchone()
    if row is None:
        await interaction.response.send_message("Cette annonce n'existe pas.", ephemeral=True)
        return
    if row["user_id"] != interaction.user.id and not (isinstance(interaction.user, discord.Member) and require_level(interaction.user, 1)):
        await interaction.response.send_message("Tu peux uniquement fermer ta propre annonce (ou être modérateur).", ephemeral=True)
        return
    if row["status"] == "closed":
        await interaction.response.send_message("Cette annonce est déjà fermée.", ephemeral=True)
        return
    db.execute("UPDATE teammate_searches SET status = 'closed' WHERE id = ?", (annonce_id,))
    db.commit()
    await interaction.response.send_message(f"✅ L'annonce **#{annonce_id}** est maintenant fermée.", ephemeral=True)


# ----------------------------
# GESTION DES ERREURS
# ----------------------------

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.CheckFailure):
        message = (
            str(error)
            or "Tu n'as pas la permission d'utiliser cette commande."
        )
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = "Cette commande est temporairement en attente. Réessaie plus tard."
    else:
        print(
            f"Erreur dans une commande : {error!r}"
        )
        message = (
            "Une erreur est survenue pendant l'exécution de la commande. "
            "Le problème a été enregistré dans la console du bot."
        )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True
            )
    except (discord.HTTPException, discord.NotFound):
        pass


# ----------------------------
# ÉVÉNEMENTS AUTOMATIQUES DU SOIR
# ----------------------------

EVENEMENTS_SOIR = {
    0: {
        "titre": "🎯 Défi du lundi",
        "description": (
            "C'est le début de la semaine !\n\n"
            "💬 Présente un objectif que tu aimerais atteindre "
            "cette semaine sur ton jeu préféré.\n\n"
            "🏆 Le but est de partager tes objectifs avec la communauté."
        ),
        "couleur": discord.Color.blue()
    },

    1: {
        "titre": "🧠 Quiz du mardi",
        "description": (
            "Petit quiz gaming !\n\n"
            "**Question :** Quel studio a créé Minecraft ?\n\n"
            "💬 Écrivez vos réponses dans le salon !\n"
            "📢 La réponse sera révélée demain."
        ),
        "couleur": discord.Color.green()
    },

    2: {
        "titre": "❓ Énigme du mercredi",
        "description": (
            "À toi de réfléchir !\n\n"
            "**Énigme :** J'ai des touches, mais je n'ouvre "
            "aucune porte. Qui suis-je ?\n\n"
            "💬 Propose ta réponse dans le salon !"
        ),
        "couleur": discord.Color.purple()
    },

    3: {
        "titre": "⚔️ Défi communautaire du jeudi",
        "description": (
            "Ce soir, place à la communauté !\n\n"
            "🎮 Propose un jeu auquel plusieurs membres "
            "pourraient jouer ensemble.\n\n"
            "💬 Discutez et organisez vos parties dans le salon !"
        ),
        "couleur": discord.Color.orange()
    },

    4: {
        "titre": "🎭 Vendredi mystère",
        "description": (
            "🔥 **Le défi mystère de Fractale Esport !**\n\n"
            "Une énigme pour terminer la semaine :\n\n"
            "**Je peux être gagnée ou perdue. "
            "Je peux se jouer seul ou à plusieurs, "
            "et je peux rendre heureux ou frustré. "
            "Qui suis-je ?**\n\n"
            "💬 Écrivez vos réponses dans le salon !\n"
            "🏆 Le but est de trouver la bonne réponse avant les autres.\n\n"
            "🤫 La réponse sera révélée le lendemain."
        ),
        "couleur": discord.Color.gold()
    },

    5: {
        "titre": "🎮 Samedi communautaire",
        "description": (
            "C'est le week-end !\n\n"
            "💬 Dis-nous à quel jeu tu joues en ce moment "
            "et invite les autres membres à te rejoindre.\n\n"
            "L'objectif : passer un bon moment ensemble !"
        ),
        "couleur": discord.Color.teal()
    },

    6: {
        "titre": "🌟 Bilan du dimanche",
        "description": (
            "La semaine se termine !\n\n"
            "💬 Quel a été ton meilleur moment gaming cette semaine ?\n"
            "🎯 Quel est ton objectif pour la semaine prochaine ?\n\n"
            "Merci de faire vivre la communauté Fractale Esport !"
        ),
        "couleur": discord.Color.blurple()
    }
}


# ----------------------------
# ENVOI AUTOMATIQUE À 20 H
# ----------------------------

@tasks.loop(
    time=datetime_time(
        hour=20,
        minute=0,
        tzinfo=ZoneInfo("Europe/Paris")
    )
)
async def evenement_du_soir():
    if not EVENING_CHANNEL_ID.isdigit():
        print(
            "Événement du soir ignoré : "
            "EVENING_CHANNEL_ID n'est pas configuré."
        )
        return

    channel = bot.get_channel(int(EVENING_CHANNEL_ID))

    if channel is None:
        try:
            channel = await bot.fetch_channel(
                int(EVENING_CHANNEL_ID)
            )
        except discord.NotFound:
            print("Erreur : le salon des événements est introuvable.")
            return
        except discord.Forbidden:
            print("Erreur : le bot ne peut pas accéder au salon des événements.")
            return
        except discord.HTTPException as error:
            print(f"Erreur Discord : {error}")
            return

    if not hasattr(channel, "send"):
        print("Erreur : le salon configuré ne permet pas d'envoyer de messages.")
        return

    maintenant = discord.utils.utcnow().astimezone(
        ZoneInfo("Europe/Paris")
    )

    evenement = EVENEMENTS_SOIR[maintenant.weekday()]

    embed = discord.Embed(
        title=evenement["titre"],
        description=evenement["description"],
        color=evenement["couleur"],
        timestamp=maintenant
    )

    embed.set_footer(
        text="Fractale Esport • Événement automatique du soir"
    )

    try:
        await channel.send(
            content="🌙 **L'événement du soir commence !**",
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none()
        )
        print(
            f"Événement envoyé : {evenement['titre']}"
        )
    except discord.Forbidden:
        print("Erreur : le bot ne peut pas envoyer de messages dans ce salon.")
    except discord.HTTPException as error:
        print(f"Impossible d'envoyer l'événement : {error}")


@evenement_du_soir.before_loop
async def avant_evenement_du_soir():
    await bot.wait_until_ready()



# ============================================================
# RECRUTEMENT AVANCÉ — ANNONCES, FORMULAIRES ET CANDIDATURES
# ============================================================

def recruitment_is_staff(member: discord.Member) -> bool:
    """Vérifie les permissions du staff et les rôles configurés dans .env."""
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    configured_ids = {MODERATOR_ROLE_ID, CO_FOUNDER_ROLE_ID, FOUNDER_ROLE_ID} - {0}
    return any(role.id in configured_ids for role in member.roles)


def recruitment_row(recruitment_id: int, guild_id: int):
    return db.execute(
        "SELECT * FROM fe_recruitments WHERE id = ? AND guild_id = ?",
        (recruitment_id, guild_id)
    ).fetchone()


class RecruitmentApplicationModal(discord.ui.Modal):
    def __init__(self, recruitment_id: int):
        super().__init__(title="Candidature d'équipe", timeout=300)
        self.recruitment_id = recruitment_id
        self.game_name = discord.ui.TextInput(
            label="Ton pseudo en jeu",
            placeholder="Ex. : Player123",
            max_length=100
        )
        self.experience = discord.ui.TextInput(
            label="Ton rang et ton expérience",
            placeholder="Ex. : rang, ancienneté, rôle préféré…",
            style=discord.TextStyle.paragraph,
            max_length=500
        )
        self.motivation = discord.ui.TextInput(
            label="Pourquoi rejoindre cette équipe ?",
            placeholder="Présente ta motivation en quelques phrases.",
            style=discord.TextStyle.paragraph,
            max_length=1000
        )
        self.add_item(self.game_name)
        self.add_item(self.experience)
        self.add_item(self.motivation)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette candidature doit être envoyée depuis le serveur.",
                ephemeral=True
            )
            return

        recruitment = recruitment_row(self.recruitment_id, interaction.guild.id)
        if recruitment is None or recruitment["status"] != "open":
            await interaction.response.send_message(
                "Cette annonce n'est plus ouverte.", ephemeral=True
            )
            return

        existing = db.execute(
            "SELECT id, status FROM fe_applications WHERE recruitment_id = ? AND applicant_id = ?",
            (self.recruitment_id, interaction.user.id)
        ).fetchone()
        if existing and existing["status"] == "pending":
            await interaction.response.send_message(
                f"Tu as déjà une candidature en attente (n° {existing['id']}).",
                ephemeral=True
            )
            return

        cur = db.execute(
            """INSERT INTO fe_applications
            (recruitment_id, guild_id, applicant_id, in_game_name, experience,
             motivation, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                self.recruitment_id, interaction.guild.id, interaction.user.id,
                str(self.game_name.value).strip(), str(self.experience.value).strip(),
                str(self.motivation.value).strip(), int(time.time())
            )
        )
        db.commit()
        application_id = cur.lastrowid

        await interaction.response.send_message(
            f"✅ Ta candidature n° **{application_id}** a été envoyée. "
            "Le responsable de l'équipe pourra l'examiner.",
            ephemeral=True
        )

        # Prévenir le responsable en message privé sans exposer la candidature publiquement.
        try:
            owner = interaction.client.get_user(recruitment["owner_id"])
            if owner is None:
                owner = await interaction.client.fetch_user(recruitment["owner_id"])
            embed = discord.Embed(
                title=f"📨 Nouvelle candidature n° {application_id}",
                description=(
                    f"**Équipe :** {recruitment['team_name']}\n"
                    f"**Jeu :** {recruitment['game']}\n"
                    f"**Candidat :** {interaction.user.mention} (`{interaction.user.id}`)\n"
                    f"**Pseudo en jeu :** {str(self.game_name.value).strip()}\n\n"
                    f"**Rang / expérience :**\n{str(self.experience.value).strip()}\n\n"
                    f"**Motivation :**\n{str(self.motivation.value).strip()}\n\n"
                    f"Utilise `/candidatures_equipe` avec l'identifiant d'annonce **{self.recruitment_id}** "
                    "pour consulter les candidatures, puis `/candidature_traiter`."
                ),
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow()
            )
            await owner.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass


class RecruitmentView(discord.ui.View):
    def __init__(self, recruitment_id: int):
        super().__init__(timeout=None)
        self.recruitment_id = recruitment_id
        button = discord.ui.Button(
            label="Postuler",
            emoji="📨",
            style=discord.ButtonStyle.success,
            custom_id=f"fractale_recruit_apply:{recruitment_id}"
        )
        button.callback = self.apply_callback
        self.add_item(button)

    async def apply_callback(self, interaction: discord.Interaction):
        recruitment = None
        if interaction.guild is not None:
            recruitment = recruitment_row(self.recruitment_id, interaction.guild.id)
        if recruitment is None or recruitment["status"] != "open":
            await interaction.response.send_message(
                "Cette annonce de recrutement est fermée.", ephemeral=True
            )
            return
        if interaction.user.id == recruitment["owner_id"]:
            await interaction.response.send_message(
                "Tu ne peux pas candidater à ta propre annonce.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            RecruitmentApplicationModal(self.recruitment_id)
        )


@bot.listen("on_ready")
async def register_recruitment_views():
    if getattr(bot, "recruitment_views_registered", False):
        return
    rows = db.execute(
        "SELECT id, message_id FROM fe_recruitments WHERE status = 'open' AND message_id IS NOT NULL"
    ).fetchall()
    for row in rows:
        try:
            bot.add_view(RecruitmentView(row["id"]), message_id=row["message_id"])
        except (ValueError, discord.HTTPException):
            continue
    bot.recruitment_views_registered = True


@bot.tree.command(
    name="recrutement_ouvrir",
    description="Publier une annonce de recrutement pour ton équipe."
)
@app_commands.describe(
    equipe="Nom de l'équipe ou du groupe",
    jeu="Rocket League uniquement",
    postes="Rôles ou postes recherchés",
    description="Présentation et conditions de l'équipe",
    places="Nombre de joueurs recherchés (1 à 50)"
)
@app_commands.checks.cooldown(2, 60.0)
async def recrutement_ouvrir(
    interaction: discord.Interaction,
    equipe: app_commands.Range[str, 2, 80],
    jeu: app_commands.Range[str, 2, 60],
    postes: app_commands.Range[str, 2, 200],
    description: app_commands.Range[str, 5, 1500],
    places: app_commands.Range[int, 1, 50] = 1
):
    if not verifier_jeu_serveur(jeu, facultatif=False):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    jeu = JEU_SERVEUR
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur.", ephemeral=True
        )
        return
    if not interaction.channel or not hasattr(interaction.channel, "send"):
        await interaction.response.send_message(
            "Je ne peux pas publier dans ce salon.", ephemeral=True
        )
        return

    cur = db.execute(
        """INSERT INTO fe_recruitments
        (guild_id, owner_id, channel_id, team_name, game, positions,
         description, slots, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (
            interaction.guild.id, interaction.user.id, interaction.channel.id,
            str(equipe).strip(), str(jeu).strip(), str(postes).strip(),
            str(description).strip(), int(places), int(time.time())
        )
    )
    db.commit()
    recruitment_id = cur.lastrowid

    embed = discord.Embed(
        title=f"🎮 Recrutement — {str(equipe).strip()}",
        description=str(description).strip(),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Jeu", value=str(jeu).strip(), inline=True)
    embed.add_field(name="Places recherchées", value=str(places), inline=True)
    embed.add_field(name="Postes recherchés", value=str(postes).strip(), inline=False)
    embed.add_field(name="Responsable", value=interaction.user.mention, inline=True)
    embed.add_field(name="Identifiant de l'annonce", value=str(recruitment_id), inline=True)
    embed.set_footer(text="Fractale Esport • Clique sur Postuler pour envoyer ta candidature")

    try:
        message = await interaction.channel.send(
            embed=embed,
            view=RecruitmentView(recruitment_id),
            allowed_mentions=discord.AllowedMentions.none()
        )
    except discord.HTTPException as error:
        db.execute("DELETE FROM fe_recruitments WHERE id = ?", (recruitment_id,))
        db.commit()
        await interaction.response.send_message(
            f"Impossible de publier l'annonce : {error}", ephemeral=True
        )
        return

    db.execute(
        "UPDATE fe_recruitments SET message_id = ? WHERE id = ?",
        (message.id, recruitment_id)
    )
    db.commit()
    await interaction.response.send_message(
        f"✅ Annonce publiée ! Identifiant : **{recruitment_id}**.",
        ephemeral=True
    )


@bot.tree.command(
    name="recrutements",
    description="Afficher les annonces de recrutement ouvertes du serveur."
)
async def recrutements(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Commande disponible sur un serveur.", ephemeral=True)
        return
    rows = db.execute(
        """SELECT id, owner_id, team_name, game, positions, slots, created_at
        FROM fe_recruitments WHERE guild_id = ? AND status = 'open'
        ORDER BY created_at DESC LIMIT 15""",
        (interaction.guild.id,)
    ).fetchall()
    if not rows:
        await interaction.response.send_message(
            "Aucune annonce de recrutement n'est ouverte pour le moment.", ephemeral=True
        )
        return
    embed = discord.Embed(
        title="🎮 Recrutements ouverts — Fractale Esport",
        color=discord.Color.blurple()
    )
    for row in rows:
        embed.add_field(
            name=f"#{row['id']} • {row['team_name']}",
            value=(
                f"**Jeu :** {row['game']}\n"
                f"**Postes :** {row['positions']}\n"
                f"**Places :** {row['slots']}\n"
                f"**Responsable :** <@{row['owner_id']}>"
            ),
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="mes_candidatures",
    description="Consulter l'état de tes candidatures d'équipe."
)
async def mes_candidatures(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Commande disponible sur un serveur.", ephemeral=True)
        return
    rows = db.execute(
        """SELECT a.id, a.status, a.created_at, r.team_name, r.game
        FROM fe_applications a JOIN fe_recruitments r ON r.id = a.recruitment_id
        WHERE a.guild_id = ? AND a.applicant_id = ?
        ORDER BY a.created_at DESC LIMIT 15""",
        (interaction.guild.id, interaction.user.id)
    ).fetchall()
    if not rows:
        await interaction.response.send_message(
            "Tu n'as pas encore envoyé de candidature.", ephemeral=True
        )
        return
    labels = {"pending": "⏳ En attente", "accepted": "✅ Acceptée", "rejected": "❌ Refusée"}
    embed = discord.Embed(title="📨 Mes candidatures", color=discord.Color.blurple())
    for row in rows:
        embed.add_field(
            name=f"Candidature n° {row['id']} — {row['team_name']}",
            value=f"**Jeu :** {row['game']}\n**État :** {labels.get(row['status'], row['status'])}",
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="candidatures_equipe",
    description="Consulter les candidatures reçues pour une annonce dont tu es responsable."
)
@app_commands.describe(annonce_id="Identifiant affiché dans l'annonce de recrutement")
async def candidatures_equipe(interaction: discord.Interaction, annonce_id: int):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Commande disponible sur un serveur.", ephemeral=True)
        return
    recruitment = recruitment_row(annonce_id, interaction.guild.id)
    if recruitment is None:
        await interaction.response.send_message("Annonce introuvable.", ephemeral=True)
        return
    if interaction.user.id != recruitment["owner_id"] and not recruitment_is_staff(interaction.user):
        await interaction.response.send_message(
            "Seul le responsable de l'annonce ou le staff autorisé peut consulter ces candidatures.",
            ephemeral=True
        )
        return
    rows = db.execute(
        """SELECT id, applicant_id, in_game_name, experience, motivation, status, created_at
        FROM fe_applications WHERE recruitment_id = ? ORDER BY created_at DESC LIMIT 15""",
        (annonce_id,)
    ).fetchall()
    if not rows:
        await interaction.response.send_message("Aucune candidature reçue pour cette annonce.", ephemeral=True)
        return
    labels = {"pending": "⏳ En attente", "accepted": "✅ Acceptée", "rejected": "❌ Refusée"}
    embed = discord.Embed(
        title=f"📨 Candidatures — {recruitment['team_name']} (annonce #{annonce_id})",
        color=discord.Color.blurple()
    )
    for row in rows:
        embed.add_field(
            name=f"N° {row['id']} • {row['in_game_name']} • {labels.get(row['status'], row['status'])}",
            value=(
                f"**Membre :** <@{row['applicant_id']}>\n"
                f"**Expérience :** {row['experience'][:400]}\n"
                f"**Motivation :** {row['motivation'][:600]}"
            ),
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="candidature_traiter",
    description="Accepter ou refuser une candidature reçue."
)
@app_commands.describe(
    candidature_id="Identifiant de la candidature",
    decision="Décision à appliquer",
    raison="Raison facultative communiquée au candidat"
)
@app_commands.choices(decision=[
    app_commands.Choice(name="Accepter", value="accepted"),
    app_commands.Choice(name="Refuser", value="rejected")
])
async def candidature_traiter(
    interaction: discord.Interaction,
    candidature_id: int,
    decision: app_commands.Choice[str],
    raison: app_commands.Range[str, 1, 300] = ""
):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Commande disponible sur un serveur.", ephemeral=True)
        return
    row = db.execute(
        """SELECT a.*, r.owner_id, r.team_name, r.status AS recruitment_status
        FROM fe_applications a JOIN fe_recruitments r ON r.id = a.recruitment_id
        WHERE a.id = ? AND a.guild_id = ?""",
        (candidature_id, interaction.guild.id)
    ).fetchone()
    if row is None:
        await interaction.response.send_message("Candidature introuvable.", ephemeral=True)
        return
    if interaction.user.id != row["owner_id"] and not recruitment_is_staff(interaction.user):
        await interaction.response.send_message(
            "Seul le responsable de l'annonce ou le staff autorisé peut traiter cette candidature.",
            ephemeral=True
        )
        return
    if row["status"] != "pending":
        await interaction.response.send_message("Cette candidature a déjà été traitée.", ephemeral=True)
        return

    db.execute(
        """UPDATE fe_applications SET status = ?, decision_by = ?,
        decision_reason = ?, decided_at = ? WHERE id = ?""",
        (decision.value, interaction.user.id, str(raison).strip(), int(time.time()), candidature_id)
    )
    db.commit()

    state_label = "acceptée" if decision.value == "accepted" else "refusée"
    await interaction.response.send_message(
        f"La candidature n° **{candidature_id}** a été {state_label}.", ephemeral=True
    )
    try:
        applicant = interaction.client.get_user(row["applicant_id"])
        if applicant is None:
            applicant = await interaction.client.fetch_user(row["applicant_id"])
        message = (
            f"Ta candidature pour **{row['team_name']}** a été {state_label}."
        )
        if str(raison).strip():
            message += f"\n**Message du responsable :** {str(raison).strip()}"
        await applicant.send(message)
    except (discord.Forbidden, discord.HTTPException):
        pass


@bot.tree.command(
    name="recrutement_fermer",
    description="Fermer une annonce de recrutement."
)
@app_commands.describe(annonce_id="Identifiant de l'annonce à fermer")
async def recrutement_fermer(interaction: discord.Interaction, annonce_id: int):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Commande disponible sur un serveur.", ephemeral=True)
        return
    recruitment = recruitment_row(annonce_id, interaction.guild.id)
    if recruitment is None:
        await interaction.response.send_message("Annonce introuvable.", ephemeral=True)
        return
    if interaction.user.id != recruitment["owner_id"] and not recruitment_is_staff(interaction.user):
        await interaction.response.send_message(
            "Seul le responsable de l'annonce ou le staff autorisé peut la fermer.",
            ephemeral=True
        )
        return
    if recruitment["status"] == "closed":
        await interaction.response.send_message("Cette annonce est déjà fermée.", ephemeral=True)
        return
    db.execute(
        "UPDATE fe_recruitments SET status = 'closed', closed_at = ? WHERE id = ?",
        (int(time.time()), annonce_id)
    )
    db.commit()

    try:
        channel = interaction.client.get_channel(recruitment["channel_id"])
        if channel is None:
            channel = await interaction.client.fetch_channel(recruitment["channel_id"])
        message = await channel.fetch_message(recruitment["message_id"])
        embed = message.embeds[0] if message.embeds else discord.Embed(title="Recrutement fermé")
        embed.color = discord.Color.dark_grey()
        embed.add_field(name="Statut", value="🔒 Recrutement fermé", inline=False)
        await message.edit(embed=embed, view=None)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
        pass

    await interaction.response.send_message(
        f"🔒 L'annonce n° **{annonce_id}** est maintenant fermée.", ephemeral=True
    )


@recrutement_ouvrir.error
async def recrutement_ouvrir_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        message = f"Réessaie dans {int(error.retry_after)} seconde(s)."
    else:
        message = "Une erreur est survenue lors de la création du recrutement."
        print(f"Erreur /recrutement_ouvrir : {error!r}")
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

# ============================================================
# OUTILS DU STAFF — CONFIGURATION, DOSSIERS ET HISTORIQUE
# ============================================================


def staff_config_for(guild_id: int):
    return db.execute(
        "SELECT * FROM staff_role_config WHERE guild_id = ?",
        (guild_id,)
    ).fetchone()


def staff_role_id_for(guild_id: int, level: int) -> int:
    config = staff_config_for(guild_id)
    if config:
        column = {1: "moderator_role_id", 2: "co_founder_role_id", 3: "founder_role_id"}.get(level)
        if column:
            return int(config[column])
    return {
        1: MODERATOR_ROLE_ID,
        2: CO_FOUNDER_ROLE_ID,
        3: FOUNDER_ROLE_ID,
    }.get(level, 0)


def staff_level_name(level: int) -> str:
    return {
        0: "👤 Membre",
        1: "🛡️ Modérateur",
        2: "⭐ Cofondateur",
        3: "👑 Fondateur",
    }.get(level, "Inconnu")


def staff_actor_can_manage(actor: discord.Member, target: discord.Member) -> bool:
    """Vérifie le grade applicatif et la hiérarchie Discord."""
    if actor.id == actor.guild.owner_id:
        return actor.id != target.id
    if actor.id == target.id:
        return False
    if get_staff_level(actor) <= get_staff_level(target):
        return False
    return actor.top_role > target.top_role


@bot.tree.command(
    name="staff_configurer_roles",
    description="Configurer les rôles modérateur, cofondateur et fondateur."
)
@app_commands.describe(
    role_moderateur="Rôle des modérateurs",
    role_cofondateur="Rôle des cofondateurs",
    role_fondateur="Rôle des fondateurs"
)
@founder_only()
async def staff_configurer_roles(
    interaction: discord.Interaction,
    role_moderateur: discord.Role,
    role_cofondateur: discord.Role,
    role_fondateur: discord.Role,
):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Commande disponible uniquement sur un serveur.", ephemeral=True)
        return

    role_ids = {role_moderateur.id, role_cofondateur.id, role_fondateur.id}
    if len(role_ids) != 3:
        await interaction.response.send_message(
            "Choisis trois rôles différents pour les trois grades.", ephemeral=True
        )
        return

    if interaction.user.id != interaction.guild.owner_id:
        if interaction.user.top_role <= role_fondateur or interaction.user.top_role <= role_cofondateur:
            await interaction.response.send_message(
                "Pour des raisons de sécurité, tes rôles doivent être au-dessus des rôles de cofondateur et de fondateur que tu configures.",
                ephemeral=True
            )
            return

    db.execute(
        """INSERT INTO staff_role_config
        (guild_id, moderator_role_id, co_founder_role_id, founder_role_id, configured_by, configured_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
        moderator_role_id=excluded.moderator_role_id,
        co_founder_role_id=excluded.co_founder_role_id,
        founder_role_id=excluded.founder_role_id,
        configured_by=excluded.configured_by,
        configured_at=excluded.configured_at""",
        (interaction.guild.id, role_moderateur.id, role_cofondateur.id,
         role_fondateur.id, interaction.user.id, int(time.time()))
    )
    db.commit()

    await send_log(
        interaction.guild, "Configuration des rôles du staff", interaction.user,
        reason=(f"Modérateur={role_moderateur.id}; Cofondateur={role_cofondateur.id}; "
                f"Fondateur={role_fondateur.id}")
    )
    await interaction.response.send_message(
        "✅ Rôles du staff enregistrés. Les commandes utilisant les grades prendront désormais cette configuration en compte.",
        ephemeral=True
    )


@bot.tree.command(
    name="staff_permissions",
    description="Afficher les rôles et les niveaux d'accès du staff."
)
@moderator_only()
async def staff_permissions(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Commande disponible uniquement sur un serveur.", ephemeral=True)
        return
    config = staff_config_for(interaction.guild.id)
    if config:
        mod_id = int(config["moderator_role_id"])
        co_id = int(config["co_founder_role_id"])
        founder_id = int(config["founder_role_id"])
        configured_by = int(config["configured_by"])
        configured_at = int(config["configured_at"])
        footer = f"Configuré par <@{configured_by}> • <t:{configured_at}:R>"
    else:
        mod_id, co_id, founder_id = MODERATOR_ROLE_ID, CO_FOUNDER_ROLE_ID, FOUNDER_ROLE_ID
        footer = "Configuration de secours lue depuis le fichier .env."

    def role_label(role_id: int) -> str:
        role = interaction.guild.get_role(role_id) if role_id else None
        return role.mention if role else (f"Rôle ID `{role_id}` (introuvable)" if role_id else "Non configuré")

    embed = discord.Embed(
        title="🛡️ Configuration du staff",
        description=(
            f"**Modérateur — niveau 1 :** {role_label(mod_id)}\n"
            f"**Cofondateur — niveau 2 :** {role_label(co_id)}\n"
            f"**Fondateur — niveau 3 :** {role_label(founder_id)}\n\n"
            "Un membre ne peut normalement agir que sur un membre de grade inférieur, "
            "et la hiérarchie des rôles Discord reste appliquée."
        ),
        color=discord.Color.blurple()
    )
    embed.set_footer(text=footer[:2048])
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="staff_dossier",
    description="Consulter un résumé staff et les dernières actions concernant un membre."
)
@app_commands.describe(membre="Membre dont tu veux consulter le dossier")
@moderator_only()
async def staff_dossier(interaction: discord.Interaction, membre: discord.Member):
    if interaction.guild is None:
        await interaction.response.send_message("Commande disponible uniquement sur un serveur.", ephemeral=True)
        return

    level = get_staff_level(membre)
    joined = discord.utils.format_dt(membre.joined_at, style="R") if membre.joined_at else "Inconnue"
    created = discord.utils.format_dt(membre.created_at, style="D")
    recent = db.execute(
        """SELECT action, moderator_id, reason, created_at FROM moderation_logs
        WHERE guild_id = ? AND target_id = ? ORDER BY created_at DESC LIMIT 5""",
        (interaction.guild.id, membre.id)
    ).fetchall()
    notes_count = db.execute(
        "SELECT COUNT(*) FROM staff_notes WHERE guild_id = ? AND target_id = ?",
        (interaction.guild.id, membre.id)
    ).fetchone()[0]

    embed = discord.Embed(title=f"📁 Dossier — {membre}", color=discord.Color.blurple())
    embed.set_thumbnail(url=membre.display_avatar.url)
    embed.add_field(name="Grade staff", value=staff_level_name(level), inline=True)
    embed.add_field(name="A rejoint le serveur", value=joined, inline=True)
    embed.add_field(name="Compte créé", value=created, inline=True)
    embed.add_field(name="Notes internes", value=str(notes_count), inline=True)
    if recent:
        lines = []
        for row in recent:
            when = f"<t:{int(row['created_at'])}:R>"
            reason = (row["reason"] or "Aucune raison")[:180]
            lines.append(f"• **{row['action']}** — {when}\n  {reason}")
        embed.add_field(name="Dernières actions enregistrées", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="Dernières actions enregistrées", value="Aucune action enregistrée.", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="staff_promouvoir",
    description="Attribuer un rôle de staff configuré à un membre de grade inférieur."
)
@app_commands.describe(
    membre="Membre à promouvoir",
    grade="Grade à attribuer",
    raison="Motif de la promotion"
)
@app_commands.choices(grade=[
    app_commands.Choice(name="Modérateur", value="moderator"),
    app_commands.Choice(name="Cofondateur", value="cofounder"),
    app_commands.Choice(name="Fondateur", value="founder"),
])
@co_founder_only()
async def staff_promouvoir(
    interaction: discord.Interaction,
    membre: discord.Member,
    grade: app_commands.Choice[str],
    raison: str = "Aucune raison précisée."
):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Commande disponible uniquement sur un serveur.", ephemeral=True)
        return
    if not staff_actor_can_manage(interaction.user, membre):
        await interaction.response.send_message(
            "Tu ne peux pas promouvoir ce membre : son grade ou sa hiérarchie Discord est égal ou supérieur au tien.",
            ephemeral=True
        )
        return
    level = {"moderator": 1, "cofounder": 2, "founder": 3}[grade.value]
    if get_staff_level(interaction.user) <= level:
        await interaction.response.send_message(
            "Tu ne peux attribuer qu'un grade strictement inférieur au tien.", ephemeral=True
        )
        return
    role_id = staff_role_id_for(interaction.guild.id, level)
    role = interaction.guild.get_role(role_id) if role_id else None
    if role is None:
        await interaction.response.send_message(
            "Le rôle correspondant à ce grade n'est pas configuré. Utilise d'abord `/staff_configurer_roles` en tant que fondateur.",
            ephemeral=True
        )
        return
    bot_member = interaction.guild.me
    if bot_member is None or bot_member.top_role <= role:
        await interaction.response.send_message(
            "Le rôle du bot doit être placé au-dessus du rôle de staff à attribuer dans les paramètres Discord.",
            ephemeral=True
        )
        return
    try:
        await membre.add_roles(role, reason=f"Promotion par {interaction.user}: {safe_reason(raison)}")
    except discord.Forbidden:
        await interaction.response.send_message("Je n'ai pas la permission d'attribuer ce rôle.", ephemeral=True)
        return
    except discord.HTTPException:
        await interaction.response.send_message("Discord n'a pas pu attribuer le rôle. Réessaie plus tard.", ephemeral=True)
        return

    await send_log(interaction.guild, f"Promotion staff — {grade.name}", interaction.user, membre, safe_reason(raison))
    await interaction.response.send_message(
        f"✅ {membre.mention} a reçu le rôle {role.mention}. Motif : {safe_reason(raison)}",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(
    name="staff_retrograder",
    description="Retirer un rôle de staff à un membre de grade inférieur."
)
@app_commands.describe(membre="Membre concerné", raison="Motif du retrait")
@co_founder_only()
async def staff_retrograder(
    interaction: discord.Interaction,
    membre: discord.Member,
    raison: str = "Aucune raison précisée."
):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Commande disponible uniquement sur un serveur.", ephemeral=True)
        return
    if not staff_actor_can_manage(interaction.user, membre):
        await interaction.response.send_message(
            "Tu ne peux pas modifier le grade de ce membre : son grade ou sa hiérarchie est égal ou supérieur au tien.",
            ephemeral=True
        )
        return

    role_ids = {staff_role_id_for(interaction.guild.id, level) for level in (1, 2, 3)}
    role_ids.discard(0)
    roles = [role for role in membre.roles if role.id in role_ids]
    if not roles:
        await interaction.response.send_message("Ce membre ne possède aucun rôle de staff configuré.", ephemeral=True)
        return
    bot_member = interaction.guild.me
    if bot_member is None or any(bot_member.top_role <= role for role in roles):
        await interaction.response.send_message(
            "Le rôle du bot doit être au-dessus de tous les rôles de staff à retirer.", ephemeral=True
        )
        return
    try:
        await membre.remove_roles(*roles, reason=f"Retrait de grade par {interaction.user}: {safe_reason(raison)}")
    except discord.Forbidden:
        await interaction.response.send_message("Je n'ai pas la permission de retirer ces rôles.", ephemeral=True)
        return
    except discord.HTTPException:
        await interaction.response.send_message("Discord n'a pas pu retirer les rôles. Réessaie plus tard.", ephemeral=True)
        return

    await send_log(interaction.guild, "Retrait des rôles staff", interaction.user, membre, safe_reason(raison))
    await interaction.response.send_message(
        f"✅ Les rôles staff configurés ont été retirés à {membre.mention}.",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(
    name="staff_note_ajouter",
    description="Ajouter une note interne au dossier d'un membre."
)
@app_commands.describe(membre="Membre concerné", note="Note interne, visible uniquement aux grades autorisés")
@moderator_only()
async def staff_note_ajouter(
    interaction: discord.Interaction,
    membre: discord.Member,
    note: app_commands.Range[str, 1, 500]
):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Commande disponible uniquement sur un serveur.", ephemeral=True)
        return
    if not staff_actor_can_manage(interaction.user, membre):
        await interaction.response.send_message(
            "Tu ne peux pas ajouter une note au dossier d'un membre de grade égal ou supérieur au tien.",
            ephemeral=True
        )
        return
    clean_note = safe_reason(str(note), 500)
    db.execute(
        "INSERT INTO staff_notes (guild_id, target_id, author_id, note, created_at) VALUES (?, ?, ?, ?, ?)",
        (interaction.guild.id, membre.id, interaction.user.id, clean_note, int(time.time()))
    )
    db.commit()
    await send_log(interaction.guild, "Note interne ajoutée", interaction.user, membre, clean_note[:250])
    await interaction.response.send_message(
        f"📝 Note interne ajoutée au dossier de {membre.mention}. Elle n'est pas envoyée au membre.",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(
    name="staff_notes",
    description="Consulter les notes internes d'un membre (cofondateurs et fondateurs)."
)
@app_commands.describe(membre="Membre dont tu veux consulter les notes")
@co_founder_only()
async def staff_notes(interaction: discord.Interaction, membre: discord.Member):
    if interaction.guild is None:
        await interaction.response.send_message("Commande disponible uniquement sur un serveur.", ephemeral=True)
        return
    rows = db.execute(
        """SELECT id, author_id, note, created_at FROM staff_notes
        WHERE guild_id = ? AND target_id = ? ORDER BY created_at DESC LIMIT 10""",
        (interaction.guild.id, membre.id)
    ).fetchall()
    if not rows:
        await interaction.response.send_message("Aucune note interne pour ce membre.", ephemeral=True)
        return
    embed = discord.Embed(title=f"🔒 Notes internes — {membre}", color=discord.Color.dark_grey())
    for row in rows:
        value = f"{row['note']}\n— <@{row['author_id']}> • <t:{int(row['created_at'])}:R>"
        embed.add_field(name=f"Note #{row['id']}", value=value[:1024], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="staff_audit",
    description="Afficher l'historique récent des actions de modération et du staff."
)
@app_commands.describe(nombre="Nombre d'actions à afficher (1 à 20)")
@moderator_only()
async def staff_audit(
    interaction: discord.Interaction,
    nombre: app_commands.Range[int, 1, 20] = 10
):
    if interaction.guild is None:
        await interaction.response.send_message("Commande disponible uniquement sur un serveur.", ephemeral=True)
        return
    rows = db.execute(
        """SELECT action, moderator_id, target_id, reason, created_at
        FROM moderation_logs WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?""",
        (interaction.guild.id, int(nombre))
    ).fetchall()
    if not rows:
        await interaction.response.send_message("Aucune action enregistrée pour ce serveur.", ephemeral=True)
        return
    embed = discord.Embed(title="🧾 Audit du staff", color=discord.Color.orange())
    for row in rows:
        target = f"<@{row['target_id']}>" if row['target_id'] else "Aucune cible"
        value = (
            f"**Auteur :** <@{row['moderator_id']}>\n"
            f"**Cible :** {target}\n"
            f"**Motif :** {(row['reason'] or 'Aucun')[:220]}\n"
            f"**Date :** <t:{int(row['created_at'])}:R>"
        )
        embed.add_field(name=str(row['action'])[:256], value=value[:1024], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ============================================================
# SAISONS COMPÉTITIVES FRACTALE ESPORT
# ============================================================

def _season_name(guild_id: int, entry_id: int) -> str:
    row = db.execute(
        "SELECT entry_name FROM fe_season_entries WHERE guild_id = ? AND id = ?",
        (guild_id, entry_id)
    ).fetchone()
    return str(row["entry_name"]) if row else f"Inscription #{entry_id}"

def _season_entry_count(season_id: int) -> int:
    row = db.execute("SELECT COUNT(*) AS n FROM fe_season_entries WHERE season_id = ?", (season_id,)).fetchone()
    return int(row["n"]) if row else 0

def _create_elimination_round(season_id: int, guild_id: int, entry_ids: list[int], round_no: int) -> int | None:
    """Crée un tour à élimination directe. Une entrée sans adversaire reçoit un bye."""
    if len(entry_ids) == 1:
        db.execute(
            "UPDATE fe_seasons SET status = 'finished', champion_entry_id = ?, finished_at = ? WHERE id = ? AND guild_id = ?",
            (entry_ids[0], int(time.time()), season_id, guild_id)
        )
        return entry_ids[0]
    for i in range(0, len(entry_ids) - 1, 2):
        db.execute(
            "INSERT INTO fe_season_matches (season_id, guild_id, round_no, entry_a_id, entry_b_id, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (season_id, guild_id, round_no, entry_ids[i], entry_ids[i + 1], int(time.time()))
        )
    if len(entry_ids) % 2:
        # Le dernier participant avance automatiquement au tour suivant.
        db.execute(
            "INSERT INTO fe_season_matches (season_id, guild_id, round_no, entry_a_id, entry_b_id, score_a, score_b, winner_entry_id, status, created_at, completed_at) VALUES (?, ?, ?, ?, ?, 1, 0, ?, 'completed', ?, ?)",
            (season_id, guild_id, round_no, entry_ids[-1], entry_ids[-1], entry_ids[-1], int(time.time()), int(time.time()))
        )
    return None

@bot.tree.command(name="saison_creer", description="Créer une saison compétitive Rocket League pour joueurs ou équipes.")
@app_commands.describe(nom="Nom de la saison", jeu="Rocket League uniquement", format="Championnat ou élimination directe", participants="Joueurs individuels ou équipes", places="Nombre maximum de participants")
@app_commands.choices(format=[app_commands.Choice(name="Championnat", value="league"), app_commands.Choice(name="Élimination directe", value="elimination")], participants=[app_commands.Choice(name="Joueurs individuels", value="players"), app_commands.Choice(name="Équipes", value="teams")])
@moderator_only()
async def saison_creer(interaction: discord.Interaction, nom: app_commands.Range[str, 3, 80], jeu: app_commands.Range[str, 2, 50], format: app_commands.Choice[str], participants: app_commands.Choice[str], places: app_commands.Range[int, 2, 32] = 16):
    if not verifier_jeu_serveur(jeu, facultatif=False):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    jeu = JEU_SERVEUR
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    name = nom.strip()
    game = jeu.strip()
    cur = db.execute("INSERT INTO fe_seasons (guild_id, creator_id, name, game, format, participant_type, max_entries, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'registration', ?)", (interaction.guild_id, interaction.user.id, name, game, format.value, participants.value, int(places), int(time.time())))
    season_id = int(cur.lastrowid)
    db.commit()
    fmt = "Championnat (tous contre tous)" if format.value == "league" else "Élimination directe"
    part = "joueurs individuels" if participants.value == "players" else "équipes"
    embed = discord.Embed(title=f"🏆 Saison #{season_id} — {name}", description=f"**Jeu :** {game}\n**Format :** {fmt}\n**Participants :** {part}\n**Places :** 0 / {places}\n**État :** Inscriptions ouvertes", color=discord.Color.gold())
    embed.set_footer(text="Fractale Esport • Utilise /saison_rejoindre pour participer")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="saison_liste", description="Afficher les saisons compétitives du serveur.")
async def saison_liste(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    rows = db.execute("SELECT id, name, game, format, participant_type, max_entries, status FROM fe_seasons WHERE guild_id = ? AND status != 'cancelled' ORDER BY id DESC LIMIT 15", (interaction.guild_id,)).fetchall()
    if not rows:
        await interaction.response.send_message("Aucune saison n'a encore été créée.", ephemeral=True)
        return
    embed = discord.Embed(title="🏆 Saisons — Fractale Esport", color=discord.Color.gold())
    for row in rows:
        count = _season_entry_count(int(row["id"]))
        fmt = "Championnat" if row["format"] == "league" else "Élimination directe"
        part = "joueurs" if row["participant_type"] == "players" else "équipes"
        status = {"registration": "🟢 Inscriptions", "active": "🔴 En cours", "finished": "🏆 Terminée"}.get(row["status"], row["status"])
        embed.add_field(name=f"#{row['id']} • {row['name']}", value=f"🎮 {row['game']} • {fmt}\n👥 {count}/{row['max_entries']} {part} • {status}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="saison_rejoindre", description="S'inscrire à une saison compétitive ouverte.")
@app_commands.describe(saison_id="Numéro de la saison", nom_equipe="Nom de ton équipe (obligatoire pour une saison par équipes)")
async def saison_rejoindre(interaction: discord.Interaction, saison_id: app_commands.Range[int, 1, 2147483647], nom_equipe: app_commands.Range[str, 2, 50] = ""):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    season = db.execute("SELECT * FROM fe_seasons WHERE id = ? AND guild_id = ?", (saison_id, interaction.guild_id)).fetchone()
    if season is None:
        await interaction.response.send_message("Saison introuvable sur ce serveur.", ephemeral=True)
        return
    if season["status"] != "registration":
        await interaction.response.send_message("Les inscriptions à cette saison sont fermées.", ephemeral=True)
        return
    sanction = get_active_competitive_restriction(interaction.guild_id, interaction.user.id)
    if sanction is not None:
        kind = "suspension de tournoi" if sanction["sanction_type"] == "suspension_tournoi" else "exclusion temporaire des compétitions"
        expiry = f" jusqu'au <t:{int(sanction['expires_at'])}:F>" if sanction["expires_at"] else ""
        await interaction.response.send_message(
            f"⛔ Tu ne peux pas rejoindre cette saison : une sanction active ({kind}) est en cours{expiry}. "
            f"Raison : {sanction['reason']}", ephemeral=True
        )
        return
    if _season_entry_count(saison_id) >= int(season["max_entries"]):
        await interaction.response.send_message("Cette saison a atteint sa limite de participants.", ephemeral=True)
        return
    if season["participant_type"] == "teams":
        entry_name = nom_equipe.strip()
        if not entry_name:
            await interaction.response.send_message("Pour cette saison, indique le nom de ton équipe dans le champ `nom_equipe`.", ephemeral=True)
            return
    else:
        entry_name = interaction.user.display_name[:50]
    try:
        db.execute("INSERT INTO fe_season_entries (season_id, guild_id, owner_id, entry_name, created_at) VALUES (?, ?, ?, ?, ?)", (saison_id, interaction.guild_id, interaction.user.id, entry_name, int(time.time())))
        db.commit()
    except sqlite3.IntegrityError:
        await interaction.response.send_message("Tu es déjà inscrit à cette saison, ou ce nom d'équipe est déjà utilisé.", ephemeral=True)
        return
    await interaction.response.send_message(f"✅ **{entry_name}** est inscrit à la saison **{season['name']}** (#{saison_id}) !", ephemeral=True)

@bot.tree.command(name="saison_inscrits", description="Afficher les participants d'une saison.")
async def saison_inscrits(interaction: discord.Interaction, saison_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    season = db.execute("SELECT name, participant_type FROM fe_seasons WHERE id = ? AND guild_id = ?", (saison_id, interaction.guild_id)).fetchone()
    if season is None:
        await interaction.response.send_message("Saison introuvable.", ephemeral=True)
        return
    rows = db.execute("SELECT entry_name, owner_id FROM fe_season_entries WHERE season_id = ? ORDER BY created_at, id", (saison_id,)).fetchall()
    if not rows:
        await interaction.response.send_message("Personne n'est inscrit pour le moment.", ephemeral=True)
        return
    lines = [f"{i}. **{r['entry_name']}** — responsable : <@{r['owner_id']}>" for i, r in enumerate(rows, 1)]
    embed = discord.Embed(title=f"👥 Participants — {season['name']}", description="\n".join(lines)[:4000], color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

@bot.tree.command(name="saison_demarrer", description="Fermer les inscriptions et générer les matchs de la saison.")
@moderator_only()
async def saison_demarrer(interaction: discord.Interaction, saison_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    season = db.execute("SELECT * FROM fe_seasons WHERE id = ? AND guild_id = ?", (saison_id, interaction.guild_id)).fetchone()
    if season is None:
        await interaction.response.send_message("Saison introuvable.", ephemeral=True)
        return
    if season["status"] != "registration":
        await interaction.response.send_message("Cette saison a déjà démarré ou est terminée.", ephemeral=True)
        return
    entries = db.execute("SELECT id FROM fe_season_entries WHERE season_id = ? ORDER BY created_at, id", (saison_id,)).fetchall()
    ids = [int(r["id"]) for r in entries]
    if len(ids) < 2:
        await interaction.response.send_message("Il faut au moins deux participants pour démarrer une saison.", ephemeral=True)
        return
    now = int(time.time())
    db.execute("UPDATE fe_seasons SET status = 'active', started_at = ? WHERE id = ?", (now, saison_id))
    if season["format"] == "league":
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                db.execute("INSERT INTO fe_season_matches (season_id, guild_id, round_no, entry_a_id, entry_b_id, status, created_at) VALUES (?, ?, 1, ?, ?, 'pending', ?)", (saison_id, interaction.guild_id, ids[i], ids[j], now))
    else:
        _create_elimination_round(saison_id, interaction.guild_id, ids, 1)
    db.commit()
    match_count = db.execute("SELECT COUNT(*) AS n FROM fe_season_matches WHERE season_id = ?", (saison_id,)).fetchone()["n"]
    await interaction.response.send_message(f"🚀 La saison **{season['name']}** démarre avec {len(ids)} participants et {match_count} match(s) généré(s). Utilise `/saison_matchs` pour consulter le calendrier.", ephemeral=True)

@bot.tree.command(name="saison_matchs", description="Afficher les matchs d'une saison.")
async def saison_matchs(interaction: discord.Interaction, saison_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    season = db.execute("SELECT name FROM fe_seasons WHERE id = ? AND guild_id = ?", (saison_id, interaction.guild_id)).fetchone()
    if season is None:
        await interaction.response.send_message("Saison introuvable.", ephemeral=True)
        return
    rows = db.execute("SELECT * FROM fe_season_matches WHERE season_id = ? ORDER BY round_no, id LIMIT 40", (saison_id,)).fetchall()
    if not rows:
        await interaction.response.send_message("Aucun match généré pour cette saison.", ephemeral=True)
        return
    lines = []
    for r in rows:
        a, b = _season_name(interaction.guild_id, int(r["entry_a_id"])), _season_name(interaction.guild_id, int(r["entry_b_id"]))
        if r["status"] == "completed":
            score = f"{r['score_a']}-{r['score_b']}"
            status = f"Terminé ({score})"
        else:
            status = f"En attente • ID match {r['id']}"
        lines.append(f"**Tour {r['round_no']} — Match #{r['id']}** : {a} vs {b} — {status}")
    embed = discord.Embed(title=f"📅 Matchs — {season['name']}", description="\n".join(lines)[:4000], color=discord.Color.blurple())
    embed.set_footer(text="Pour saisir un résultat, le staff utilise /saison_resultat")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="saison_resultat", description="Enregistrer le score d'un match de saison.")
@app_commands.describe(match_id="Numéro du match affiché par /saison_matchs", score_a="Score du premier participant", score_b="Score du second participant")
@moderator_only()
async def saison_resultat(interaction: discord.Interaction, match_id: app_commands.Range[int, 1, 2147483647], score_a: app_commands.Range[int, 0, 99], score_b: app_commands.Range[int, 0, 99]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    match = db.execute("SELECT m.*, s.format, s.status AS season_status, s.name AS season_name FROM fe_season_matches m JOIN fe_seasons s ON s.id = m.season_id WHERE m.id = ? AND m.guild_id = ?", (match_id, interaction.guild_id)).fetchone()
    if match is None:
        await interaction.response.send_message("Match introuvable.", ephemeral=True)
        return
    if match["season_status"] != "active" or match["status"] != "pending":
        await interaction.response.send_message("Ce match n'est pas en attente dans une saison active.", ephemeral=True)
        return
    if match["format"] == "elimination" and score_a == score_b:
        await interaction.response.send_message("En élimination directe, il faut un vainqueur : le score ne peut pas être égal.", ephemeral=True)
        return
    winner = int(match["entry_a_id"]) if score_a > score_b else int(match["entry_b_id"]) if score_b > score_a else None
    db.execute("UPDATE fe_season_matches SET score_a = ?, score_b = ?, winner_entry_id = ?, status = 'completed', completed_at = ? WHERE id = ?", (score_a, score_b, winner, int(time.time()), match_id))
    if match["format"] == "elimination":
        round_no = int(match["round_no"])
        remaining = db.execute("SELECT COUNT(*) AS n FROM fe_season_matches WHERE season_id = ? AND round_no = ? AND status = 'pending'", (match["season_id"], round_no)).fetchone()["n"]
        if int(remaining) == 0:
            winners = [int(r["winner_entry_id"]) for r in db.execute("SELECT winner_entry_id FROM fe_season_matches WHERE season_id = ? AND round_no = ? AND winner_entry_id IS NOT NULL ORDER BY id", (match["season_id"], round_no)).fetchall()]
            if len(winners) == 1:
                db.execute("UPDATE fe_seasons SET status = 'finished', champion_entry_id = ?, finished_at = ? WHERE id = ?", (winners[0], int(time.time()), match["season_id"]))
            elif winners:
                _create_elimination_round(int(match["season_id"]), interaction.guild_id, winners, round_no + 1)
    else:
        pending = db.execute("SELECT COUNT(*) AS n FROM fe_season_matches WHERE season_id = ? AND status = 'pending'", (match["season_id"],)).fetchone()["n"]
        if int(pending) == 0:
            standings = db.execute("SELECT e.id, SUM(CASE WHEN m.winner_entry_id = e.id THEN 3 WHEN m.status = 'completed' AND m.winner_entry_id IS NULL THEN 1 ELSE 0 END) AS points, SUM(CASE WHEN m.winner_entry_id = e.id THEN 1 ELSE 0 END) AS wins, SUM(CASE WHEN m.winner_entry_id IS NOT NULL AND m.winner_entry_id != e.id THEN 1 ELSE 0 END) AS losses FROM fe_season_entries e JOIN fe_season_matches m ON (m.entry_a_id = e.id OR m.entry_b_id = e.id) WHERE e.season_id = ? GROUP BY e.id ORDER BY points DESC, wins DESC, e.entry_name COLLATE NOCASE ASC", (match["season_id"],)).fetchall()
            if standings:
                db.execute("UPDATE fe_seasons SET status = 'finished', champion_entry_id = ?, finished_at = ? WHERE id = ?", (int(standings[0]["id"]), int(time.time()), match["season_id"]))
    db.commit()
    a, b = _season_name(interaction.guild_id, int(match["entry_a_id"])), _season_name(interaction.guild_id, int(match["entry_b_id"]))
    extra = " La saison est terminée ! Consulte `/saison_classement`." if db.execute("SELECT status FROM fe_seasons WHERE id = ?", (match["season_id"],)).fetchone()["status"] == "finished" else ""
    await interaction.response.send_message(f"✅ Résultat enregistré : **{a} {score_a} – {score_b} {b}**.{extra}", ephemeral=True)

@bot.tree.command(name="saison_classement", description="Afficher le classement ou le vainqueur d'une saison.")
async def saison_classement(interaction: discord.Interaction, saison_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    season = db.execute("SELECT * FROM fe_seasons WHERE id = ? AND guild_id = ?", (saison_id, interaction.guild_id)).fetchone()
    if season is None:
        await interaction.response.send_message("Saison introuvable.", ephemeral=True)
        return
    entries = db.execute("SELECT id, entry_name FROM fe_season_entries WHERE season_id = ?", (saison_id,)).fetchall()
    if season["format"] == "league":
        rows = db.execute("SELECT e.id, e.entry_name, SUM(CASE WHEN m.winner_entry_id = e.id THEN 3 WHEN m.status = 'completed' AND m.winner_entry_id IS NULL THEN 1 ELSE 0 END) AS points, SUM(CASE WHEN m.winner_entry_id = e.id THEN 1 ELSE 0 END) AS wins, SUM(CASE WHEN m.status = 'completed' AND m.winner_entry_id IS NOT NULL AND m.winner_entry_id != e.id THEN 1 ELSE 0 END) AS losses, SUM(CASE WHEN m.status = 'completed' THEN 1 ELSE 0 END) AS played FROM fe_season_entries e LEFT JOIN fe_season_matches m ON (m.entry_a_id = e.id OR m.entry_b_id = e.id) WHERE e.season_id = ? GROUP BY e.id ORDER BY points DESC, wins DESC, e.entry_name COLLATE NOCASE ASC", (saison_id,)).fetchall()
        lines = [f"**{i}. {r['entry_name']}** — {r['points'] or 0} pts • {r['wins'] or 0} V / {r['losses'] or 0} D • {r['played'] or 0} matchs" for i, r in enumerate(rows, 1)]
    else:
        lines = [f"• {r['entry_name']}" for r in entries]
        if not season["champion_entry_id"]:
            lines.insert(0, "Format élimination directe — consulte `/saison_matchs` pour l'avancement du tableau.")
    if season["status"] == "finished" and season["champion_entry_id"]:
        lines.insert(0, f"🏆 **Champion officiel : {_season_name(interaction.guild_id, int(season['champion_entry_id']))}**")
    embed = discord.Embed(title=f"📊 Classement — {season['name']}", description="\n".join(lines)[:4000] or "Aucun participant.", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="stats_performance",
    description="Afficher les statistiques détaillées d'un joueur ou d'une équipe dans une saison."
)
@app_commands.describe(
    saison_id="Numéro de la saison",
    participant="Nom exact du joueur ou de l'équipe inscrit à la saison"
)
async def stats_performance(
    interaction: discord.Interaction,
    saison_id: app_commands.Range[int, 1, 2147483647],
    participant: app_commands.Range[str, 1, 100]
):
    """Affiche les statistiques calculées à partir des résultats enregistrés."""
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.",
            ephemeral=True
        )
        return

    season = db.execute(
        "SELECT id, name, game, status FROM fe_seasons WHERE id = ? AND guild_id = ?",
        (saison_id, interaction.guild_id)
    ).fetchone()
    if season is None:
        await interaction.response.send_message(
            "❌ Saison introuvable sur ce serveur.",
            ephemeral=True
        )
        return

    entry = db.execute(
        """SELECT id, entry_name
           FROM fe_season_entries
           WHERE season_id = ? AND guild_id = ? AND entry_name = ? COLLATE NOCASE""",
        (saison_id, interaction.guild_id, participant.strip())
    ).fetchone()
    if entry is None:
        await interaction.response.send_message(
            "❌ Participant introuvable. Vérifie son nom avec `/saison_participants`.",
            ephemeral=True
        )
        return

    matches = db.execute(
        """SELECT id, entry_a_id, entry_b_id, score_a, score_b, winner_entry_id,
                  completed_at, round_no
           FROM fe_season_matches
           WHERE season_id = ?
             AND status = 'completed'
             AND (entry_a_id = ? OR entry_b_id = ?)
           ORDER BY COALESCE(completed_at, 0), id""",
        (saison_id, int(entry["id"]), int(entry["id"]))
    ).fetchall()

    played = len(matches)
    wins = losses = draws = scored = conceded = 0
    results = []

    for match in matches:
        is_a = int(match["entry_a_id"]) == int(entry["id"])
        # Les scores peuvent être à 0 ou NULL ; conversion explicite.
        own_score = int((match["score_a"] if is_a else match["score_b"]) or 0)
        other_score = int((match["score_b"] if is_a else match["score_a"]) or 0)
        scored += own_score
        conceded += other_score

        winner_id = match["winner_entry_id"]
        if winner_id is None:
            draws += 1
            results.append("N")
        elif int(winner_id) == int(entry["id"]):
            wins += 1
            results.append("V")
        else:
            losses += 1
            results.append("D")

    win_rate = (wins / played * 100) if played else 0.0
    average_scored = (scored / played) if played else 0.0
    average_conceded = (conceded / played) if played else 0.0
    difference = scored - conceded

    # Série actuelle : part du match terminé le plus récent.
    current_streak = ""
    if results:
        latest = results[-1]
        streak_count = 0
        for result in reversed(results):
            if result != latest:
                break
            streak_count += 1
        streak_label = {"V": "victoire(s)", "D": "défaite(s)", "N": "match nul"}
        current_streak = f"{streak_count} {streak_label[latest]}"
    else:
        current_streak = "Aucun match terminé"

    embed = discord.Embed(
        title=f"📈 Statistiques de performance — {entry['entry_name']}",
        description=(
            f"**Saison :** {season['name']} (#{saison_id})\n"
            f"**Jeu :** {season['game']}\n"
            f"**État de la saison :** {season['status']}"
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="🎮 Matchs joués", value=str(played), inline=True)
    embed.add_field(name="🏆 Victoires", value=str(wins), inline=True)
    embed.add_field(name="📉 Défaites", value=str(losses), inline=True)
    embed.add_field(name="🤝 Matchs nuls", value=str(draws), inline=True)
    embed.add_field(name="🎯 Taux de victoire", value=f"{win_rate:.1f} %", inline=True)
    embed.add_field(name="🔥 Série actuelle", value=current_streak, inline=True)
    embed.add_field(
        name="⚽ Scores",
        value=(
            f"**Pour :** {scored}\n"
            f"**Contre :** {conceded}\n"
            f"**Différence :** {difference:+d}"
        ),
        inline=True
    )
    embed.add_field(
        name="📊 Moyenne par match",
        value=(
            f"**Marqués :** {average_scored:.2f}\n"
            f"**Encaissés :** {average_conceded:.2f}"
        ),
        inline=True
    )
    if played == 0:
        embed.set_footer(text="Les statistiques apparaîtront quand au moins un résultat sera enregistré.")
    else:
        embed.set_footer(text="Statistiques calculées à partir des résultats saisis avec /saison_resultat.")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="saison_annuler", description="Annuler une saison (staff).")
@moderator_only()
async def saison_annuler(interaction: discord.Interaction, saison_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    row = db.execute("SELECT name, status FROM fe_seasons WHERE id = ? AND guild_id = ?", (saison_id, interaction.guild_id)).fetchone()
    if row is None:
        await interaction.response.send_message("Saison introuvable.", ephemeral=True)
        return
    if row["status"] == "finished":
        await interaction.response.send_message("Cette saison est déjà terminée et ne peut plus être annulée.", ephemeral=True)
        return
    db.execute("UPDATE fe_seasons SET status = 'cancelled' WHERE id = ?", (saison_id,))
    db.commit()
    await interaction.response.send_message(f"🛑 La saison **{row['name']}** a été annulée.", ephemeral=True)



# ============================================================
# DISPONIBILITÉS DES JOUEURS
# ============================================================

@bot.tree.command(
    name="disponibilite",
    description="Enregistrer tes disponibilités pour Rocket League."
)
@app_commands.describe(
    jeu="Rocket League uniquement",
    jours="Jours où tu peux jouer (ex. lundi, mercredi, samedi)",
    horaires="Plages horaires et fuseau (ex. 18h-21h, heure française)",
    note="Précision facultative (ex. une semaine sur deux)"
)
async def disponibilite(
    interaction: discord.Interaction,
    jeu: app_commands.Range[str, 2, 50],
    jours: app_commands.Range[str, 2, 120],
    horaires: app_commands.Range[str, 2, 120],
    note: app_commands.Range[str, 0, 250] = ""
):
    if not verifier_jeu_serveur(jeu, facultatif=False):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    jeu = JEU_SERVEUR
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return

    jeu = jeu.strip()
    jours = jours.strip()
    horaires = horaires.strip()
    note = note.strip()
    if not jeu or not jours or not horaires:
        await interaction.response.send_message(
            "Le jeu, les jours et les horaires ne peuvent pas être vides.", ephemeral=True
        )
        return

    db.execute(
        """INSERT INTO fe_player_availability
           (guild_id, user_id, game, days, hours, note, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(guild_id, user_id, game) DO UPDATE SET
             days = excluded.days, hours = excluded.hours,
             note = excluded.note, updated_at = excluded.updated_at""",
        (interaction.guild_id, interaction.user.id, jeu, jours, horaires, note, int(time.time()))
    )
    db.commit()

    embed = discord.Embed(
        title="📅 Disponibilités enregistrées",
        description=f"Tes disponibilités pour **{jeu}** ont été sauvegardées.",
        color=discord.Color.green()
    )
    embed.add_field(name="Jours", value=jours, inline=False)
    embed.add_field(name="Horaires", value=horaires, inline=False)
    if note:
        embed.add_field(name="Précision", value=note, inline=False)
    embed.set_footer(text="Utilise /disponibilites pour consulter les disponibilités du serveur.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="disponibilites",
    description="Consulter les disponibilités des joueurs du serveur."
)
@app_commands.describe(jeu="Filtrer sur Rocket League (facultatif)")
async def disponibilites(
    interaction: discord.Interaction,
    jeu: app_commands.Range[str, 2, 50] = ""
):
    if not verifier_jeu_serveur(jeu, facultatif=True):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    if str(jeu or "").strip():
        jeu = JEU_SERVEUR
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return

    jeu = jeu.strip()
    if jeu:
        rows = db.execute(
            "SELECT user_id, game, days, hours, note, updated_at FROM fe_player_availability "
            "WHERE guild_id = ? AND game = ? COLLATE NOCASE ORDER BY game COLLATE NOCASE, user_id LIMIT 25",
            (interaction.guild_id, jeu)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT user_id, game, days, hours, note, updated_at FROM fe_player_availability "
            "WHERE guild_id = ? ORDER BY game COLLATE NOCASE, user_id LIMIT 25",
            (interaction.guild_id,)
        ).fetchall()

    if not rows:
        message = (f"Personne n'a encore enregistré ses disponibilités pour **{jeu}**."
                   if jeu else "Personne n'a encore enregistré ses disponibilités. Utilise `/disponibilite` pour ajouter les tiennes.")
        await interaction.response.send_message(message, ephemeral=True)
        return

    embed = discord.Embed(
        title="🗓️ Disponibilités des joueurs" + (f" — {jeu}" if jeu else ""),
        description="Les horaires sont ceux indiqués par les membres. Pensez à préciser le fuseau horaire.",
        color=discord.Color.blurple()
    )
    for row in rows:
        member = interaction.guild.get_member(int(row["user_id"])) if interaction.guild else None
        member_name = member.display_name if member else f"Membre {row['user_id']}"
        value = f"**Jeu :** {row['game']}\
**Jours :** {row['days']}\
**Horaires :** {row['hours']}"
        if row["note"]:
            value += f"\
**Précision :** {row['note']}"
        embed.add_field(name=member_name[:256], value=value[:1024], inline=False)
    if len(rows) == 25:
        embed.set_footer(text="Affichage limité aux 25 premières disponibilités. Filtre avec le paramètre jeu pour affiner.")
    await interaction.response.send_message(
        embed=embed, allowed_mentions=discord.AllowedMentions.none(), ephemeral=True
    )


@bot.tree.command(
    name="disponibilite_effacer",
    description="Supprimer tes disponibilités enregistrées pour un jeu."
)
@app_commands.describe(jeu="Rocket League uniquement")
async def disponibilite_effacer(
    interaction: discord.Interaction,
    jeu: app_commands.Range[str, 2, 50]
):
    if not verifier_jeu_serveur(jeu, facultatif=False):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    jeu = JEU_SERVEUR
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return

    cursor = db.execute(
        "DELETE FROM fe_player_availability WHERE guild_id = ? AND user_id = ? AND game = ? COLLATE NOCASE",
        (interaction.guild_id, interaction.user.id, jeu.strip())
    )
    db.commit()
    if cursor.rowcount:
        await interaction.response.send_message(
            f"🗑️ Tes disponibilités pour **{jeu.strip()}** ont été supprimées.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"Tu n'avais pas de disponibilités enregistrées pour **{jeu.strip()}**.", ephemeral=True
        )


# ============================================================
# RECHERCHE AUTOMATIQUE DE SCRIMS
# ============================================================

@bot.tree.command(
    name="scrim_rechercher",
    description="Publier une recherche de match d'entraînement et trouver des équipes compatibles."
)
@app_commands.describe(
    equipe="Nom de ton équipe",
    jeu="Rocket League uniquement",
    niveau="Niveau ou rang recherché",
    format="Format du match, par exemple 2v2 ou 5v5",
    disponibilites="Jours et horaires possibles",
    note="Règles ou précisions facultatives"
)
@app_commands.checks.cooldown(1, 30.0)
async def scrim_rechercher(
    interaction: discord.Interaction,
    equipe: app_commands.Range[str, 2, 50],
    jeu: app_commands.Range[str, 2, 50],
    niveau: app_commands.Range[str, 2, 50],
    format: app_commands.Range[str, 2, 30],
    disponibilites: app_commands.Range[str, 2, 100],
    note: app_commands.Range[str, 0, 250] = ""
):
    if not verifier_jeu_serveur(jeu, facultatif=False):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    jeu = JEU_SERVEUR
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return

    equipe, jeu, niveau, format, disponibilites, note = (
        equipe.strip(), jeu.strip(), niveau.strip(), format.strip(),
        disponibilites.strip(), note.strip()
    )
    if not all((equipe, jeu, niveau, format, disponibilites)):
        await interaction.response.send_message(
            "Tous les champs sauf la note doivent être renseignés.", ephemeral=True
        )
        return

    now = int(time.time())
    db.execute(
        """INSERT INTO fe_scrim_searches
           (guild_id, owner_id, team_name, game, level, game_format, availability, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (interaction.guild_id, interaction.user.id, equipe, jeu, niveau, format,
         disponibilites, note, now)
    )
    search_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()

    # Recherche automatique d'annonces ouvertes du même jeu et du même format.
    compatible = db.execute(
        """SELECT id, owner_id, team_name, level, availability
           FROM fe_scrim_searches
           WHERE guild_id = ? AND status = 'open' AND id != ?
             AND game = ? COLLATE NOCASE AND game_format = ? COLLATE NOCASE
           ORDER BY created_at DESC LIMIT 5""",
        (interaction.guild_id, search_id, jeu, format)
    ).fetchall()

    embed = discord.Embed(
        title=f"🔎 Recherche de scrim #{search_id}",
        description=f"L'équipe **{equipe}** recherche un match d'entraînement.",
        color=discord.Color.blurple()
    )
    embed.add_field(name="Jeu", value=jeu, inline=True)
    embed.add_field(name="Format", value=format, inline=True)
    embed.add_field(name="Niveau", value=niveau, inline=True)
    embed.add_field(name="Disponibilités", value=disponibilites, inline=False)
    if note:
        embed.add_field(name="Précisions", value=note, inline=False)

    if compatible:
        lines = []
        for row in compatible:
            lines.append(
                f"**#{row['id']} — {row['team_name']}** · Niveau : {row['level']}\n"
                f"Disponibilités : {row['availability']}\n"
                f"Pour proposer un match : `/scrim_proposer annonce_id:{row['id']}`"
            )
        embed.add_field(
            name="🎯 Équipes compatibles trouvées",
            value="\n\n".join(lines)[:1024], inline=False
        )
    else:
        embed.add_field(
            name="Recherche automatique",
            value="Aucune annonce ouverte du même jeu et du même format pour le moment. "
                  "Ta recherche reste publiée ; les prochaines équipes pourront te trouver avec `/scrims`.",
            inline=False
        )
    embed.set_footer(text="Ferme ta recherche avec /scrim_annuler numéro")
    await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(
    name="scrims",
    description="Afficher les recherches de scrims ouvertes et les équipes compatibles."
)
@app_commands.describe(jeu="Filtrer sur Rocket League (facultatif)")
async def scrims(interaction: discord.Interaction, jeu: app_commands.Range[str, 2, 50] = ""):
    if not verifier_jeu_serveur(jeu, facultatif=True):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    if str(jeu or "").strip():
        jeu = JEU_SERVEUR
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return
    jeu = jeu.strip()
    if jeu:
        rows = db.execute(
            """SELECT id, owner_id, team_name, game, level, game_format, availability, note
               FROM fe_scrim_searches WHERE guild_id = ? AND status = 'open'
                 AND game = ? COLLATE NOCASE ORDER BY created_at DESC LIMIT 15""",
            (interaction.guild_id, jeu)
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT id, owner_id, team_name, game, level, game_format, availability, note
               FROM fe_scrim_searches WHERE guild_id = ? AND status = 'open'
               ORDER BY created_at DESC LIMIT 15""",
            (interaction.guild_id,)
        ).fetchall()

    if not rows:
        message = (f"Aucune recherche de scrim ouverte pour **{jeu}**." if jeu
                   else "Aucune recherche de scrim ouverte pour le moment.")
        await interaction.response.send_message(message + " Utilise `/scrim_rechercher` pour en publier une.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎮 Recherches de scrims ouvertes",
        description="Pour proposer un match, utilise `/scrim_proposer` avec le numéro de l'annonce.\n"
                    "Les annonces sont considérées compatibles si le jeu et le format correspondent.",
        color=discord.Color.blurple()
    )
    for row in rows:
        value = (
            f"👥 Équipe : **{row['team_name']}** (<@{row['owner_id']}>)\n"
            f"🎯 Niveau : {row['level']} · Format : {row['game_format']}\n"
            f"🕒 Disponibilités : {row['availability']}"
        )
        if row['note']:
            value += f"\n📝 {row['note']}"
        embed.add_field(name=f"#{row['id']} · {row['game']}", value=value[:1024], inline=False)
    embed.set_footer(text="Affichage limité à 15 annonces. Filtre par jeu pour affiner.")
    await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(
    name="scrim_proposer",
    description="Proposer un scrim à une équipe qui recherche un adversaire."
)
@app_commands.describe(annonce_id="Numéro de l'annonce affiché dans /scrims")
@app_commands.checks.cooldown(1, 20.0)
async def scrim_proposer(
    interaction: discord.Interaction,
    annonce_id: app_commands.Range[int, 1, 2147483647]
):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return

    target = db.execute(
        """SELECT id, owner_id, team_name, game, game_format, status
           FROM fe_scrim_searches WHERE id = ? AND guild_id = ?""",
        (annonce_id, interaction.guild_id)
    ).fetchone()
    if target is None or target['status'] != 'open':
        await interaction.response.send_message(
            "Cette annonce n'existe pas ou n'est plus ouverte.", ephemeral=True
        )
        return
    if int(target['owner_id']) == interaction.user.id:
        await interaction.response.send_message("Tu ne peux pas proposer un scrim à ta propre équipe.", ephemeral=True)
        return

    source = db.execute(
        """SELECT id, team_name, game, game_format FROM fe_scrim_searches
           WHERE guild_id = ? AND owner_id = ? AND status = 'open'
             AND game = ? COLLATE NOCASE AND game_format = ? COLLATE NOCASE
           ORDER BY created_at DESC LIMIT 1""",
        (interaction.guild_id, interaction.user.id, target['game'], target['game_format'])
    ).fetchone()
    if source is None:
        await interaction.response.send_message(
            f"Pour proposer ce match, publie d'abord une recherche compatible avec `/scrim_rechercher` "
            f"(jeu : **{target['game']}**, format : **{target['game_format']}**).",
            ephemeral=True
        )
        return

    existing = db.execute(
        """SELECT id, status FROM fe_scrim_proposals
           WHERE from_search_id = ? AND to_search_id = ?""",
        (source['id'], target['id'])
    ).fetchone()
    if existing and existing['status'] == 'pending':
        await interaction.response.send_message("Tu as déjà une proposition en attente pour cette équipe.", ephemeral=True)
        return
    if existing and existing['status'] == 'accepted':
        await interaction.response.send_message("Cette proposition a déjà été acceptée.", ephemeral=True)
        return

    now = int(time.time())
    if existing:
        db.execute(
            "UPDATE fe_scrim_proposals SET status = 'pending', created_at = ?, reviewed_at = NULL WHERE id = ?",
            (now, existing['id'])
        )
        proposal_id = int(existing['id'])
    else:
        db.execute(
            """INSERT INTO fe_scrim_proposals
               (guild_id, from_search_id, to_search_id, proposer_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (interaction.guild_id, source['id'], target['id'], interaction.user.id, now)
        )
        proposal_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()

    owner = interaction.guild.get_member(int(target['owner_id'])) if interaction.guild else None
    dm_sent = False
    if owner is not None:
        try:
            await owner.send(
                f"🏆 L'équipe **{source['team_name']}** te propose un scrim pour **{target['game']}** "
                f"au format **{target['game_format']}** sur **{interaction.guild.name}**.\n"
                f"Proposition n° **{proposal_id}**. Réponds avec `/scrim_repondre proposition_id:{proposal_id} decision:accepter` "
                f"ou `decision:refuser` dans le serveur."
            )
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False

    await interaction.response.send_message(
        f"✅ Proposition de scrim **#{proposal_id}** envoyée à l'équipe **{target['team_name']}**. "
        + ("Le responsable a reçu un message privé." if dm_sent else
           "Je n'ai pas pu envoyer de message privé au responsable ; préviens-le sur le serveur."),
        ephemeral=True
    )


@bot.tree.command(
    name="scrim_repondre",
    description="Accepter ou refuser une proposition de scrim reçue."
)
@app_commands.describe(
    proposition_id="Numéro de la proposition reçue",
    decision="Accepter ou refuser la proposition"
)
@app_commands.choices(decision=[
    app_commands.Choice(name="Accepter", value="accepter"),
    app_commands.Choice(name="Refuser", value="refuser")
])
async def scrim_repondre(
    interaction: discord.Interaction,
    proposition_id: app_commands.Range[int, 1, 2147483647],
    decision: app_commands.Choice[str]
):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return
    proposal = db.execute(
        """SELECT p.id, p.status, p.to_search_id, p.from_search_id,
                  target.owner_id AS target_owner, source.team_name AS source_team,
                  target.team_name AS target_team
           FROM fe_scrim_proposals p
           JOIN fe_scrim_searches target ON target.id = p.to_search_id
           JOIN fe_scrim_searches source ON source.id = p.from_search_id
           WHERE p.id = ? AND p.guild_id = ?""",
        (proposition_id, interaction.guild_id)
    ).fetchone()
    if proposal is None:
        await interaction.response.send_message("Proposition introuvable.", ephemeral=True)
        return
    if int(proposal['target_owner']) != interaction.user.id:
        await interaction.response.send_message(
            "Seul le responsable de l'équipe destinataire peut répondre à cette proposition.", ephemeral=True
        )
        return
    if proposal['status'] != 'pending':
        await interaction.response.send_message("Cette proposition a déjà été traitée.", ephemeral=True)
        return

    now = int(time.time())
    if decision.value == 'accepter':
        # Ne pas valider si l'une des annonces a déjà été fermée ou associée à un autre match.
        statuses = db.execute(
            "SELECT id, status FROM fe_scrim_searches WHERE id IN (?, ?)",
            (proposal['from_search_id'], proposal['to_search_id'])
        ).fetchall()
        if len(statuses) != 2 or any(row['status'] != 'open' for row in statuses):
            db.execute(
                "UPDATE fe_scrim_proposals SET status = 'cancelled', reviewed_at = ? WHERE id = ?",
                (now, proposition_id)
            )
            db.commit()
            await interaction.response.send_message(
                "Impossible d'accepter : l'une des recherches n'est plus ouverte.", ephemeral=True
            )
            return
        db.execute(
            "UPDATE fe_scrim_proposals SET status = 'accepted', reviewed_at = ? WHERE id = ?",
            (now, proposition_id)
        )
        db.execute(
            "UPDATE fe_scrim_searches SET status = 'matched' WHERE id IN (?, ?)",
            (proposal['from_search_id'], proposal['to_search_id'])
        )
        db.execute(
            """UPDATE fe_scrim_proposals SET status = 'cancelled', reviewed_at = ?
               WHERE status = 'pending' AND id != ? AND (from_search_id IN (?, ?) OR to_search_id IN (?, ?))""",
            (now, proposition_id, proposal['from_search_id'], proposal['to_search_id'],
             proposal['from_search_id'], proposal['to_search_id'])
        )
        db.commit()
        await interaction.response.send_message(
            f"🤝 Scrim confirmé entre **{proposal['target_team']}** et **{proposal['source_team']}** ! "
            "Les deux recherches sont maintenant fermées. Organisez les détails du match entre vous.",
            allowed_mentions=discord.AllowedMentions.none()
        )
        guild = interaction.guild
        proposer_member = guild.get_member(int(db.execute(
            "SELECT proposer_id FROM fe_scrim_proposals WHERE id = ?", (proposition_id,)
        ).fetchone()[0])) if guild else None
        if proposer_member:
            try:
                await proposer_member.send(
                    f"✅ Ta proposition de scrim **#{proposition_id}** avec l'équipe **{proposal['target_team']}** a été acceptée sur **{guild.name}**."
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
    else:
        db.execute(
            "UPDATE fe_scrim_proposals SET status = 'rejected', reviewed_at = ? WHERE id = ?",
            (now, proposition_id)
        )
        db.commit()
        await interaction.response.send_message(
            f"La proposition de scrim **#{proposition_id}** de l'équipe **{proposal['source_team']}** a été refusée.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


@bot.tree.command(
    name="scrim_annuler",
    description="Fermer ta recherche de scrim ou annuler une annonce."
)
@app_commands.describe(annonce_id="Numéro de l'annonce affiché dans /scrims")
async def scrim_annuler(
    interaction: discord.Interaction,
    annonce_id: app_commands.Range[int, 1, 2147483647]
):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return
    row = db.execute(
        "SELECT owner_id, status FROM fe_scrim_searches WHERE id = ? AND guild_id = ?",
        (annonce_id, interaction.guild_id)
    ).fetchone()
    if row is None:
        await interaction.response.send_message("Cette annonce n'existe pas.", ephemeral=True)
        return
    if int(row['owner_id']) != interaction.user.id and not (
        isinstance(interaction.user, discord.Member) and require_level(interaction.user, 1)
    ):
        await interaction.response.send_message(
            "Tu peux uniquement fermer ta propre annonce (ou être modérateur).", ephemeral=True
        )
        return
    if row['status'] != 'open':
        await interaction.response.send_message("Cette annonce n'est plus ouverte.", ephemeral=True)
        return
    db.execute(
        "UPDATE fe_scrim_searches SET status = 'closed' WHERE id = ?",
        (annonce_id,)
    )
    db.execute(
        """UPDATE fe_scrim_proposals SET status = 'cancelled', reviewed_at = ?
           WHERE status = 'pending' AND (from_search_id = ? OR to_search_id = ?)""",
        (int(time.time()), annonce_id, annonce_id)
    )
    db.commit()
    await interaction.response.send_message(
        f"✅ La recherche de scrim **#{annonce_id}** a été fermée.", ephemeral=True
    )


# ============================================================
# CLIPS DE LA SEMAINE
# ============================================================

db.executescript("""
CREATE TABLE IF NOT EXISTS fe_clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    game TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open', 'winner', 'removed')),
    submitted_at INTEGER NOT NULL,
    winner_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fe_clips_week
ON fe_clips(guild_id, status, submitted_at);
CREATE TABLE IF NOT EXISTS fe_clip_votes (
    clip_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    voted_at INTEGER NOT NULL,
    PRIMARY KEY(clip_id, user_id),
    FOREIGN KEY(clip_id) REFERENCES fe_clips(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fe_clip_votes_clip
ON fe_clip_votes(clip_id);
""")
db.commit()


def _clip_week_start() -> int:
    """Début de la période de vote : les 7 derniers jours glissants."""
    return int(time.time()) - 7 * 24 * 60 * 60


def _clip_is_url(value: str) -> bool:
    value = value.strip().lower()
    return value.startswith("https://") and ("." in value[8:])


@bot.tree.command(
    name="clip_proposer",
    description="Proposer un clip de jeu pour le classement des clips de la semaine."
)
@app_commands.describe(
    jeu="Rocket League uniquement",
    titre="Titre court du clip",
    lien="Lien HTTPS vers le clip (YouTube, Twitch, Medal, etc.)"
)
@app_commands.checks.cooldown(1, 20.0)
async def clip_proposer(
    interaction: discord.Interaction,
    jeu: app_commands.Range[str, 2, 50],
    titre: app_commands.Range[str, 3, 80],
    lien: app_commands.Range[str, 8, 500]
):
    if not verifier_jeu_serveur(jeu, facultatif=False):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    jeu = JEU_SERVEUR
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return

    jeu, titre, lien = jeu.strip(), titre.strip(), lien.strip()
    if not jeu or not titre or not _clip_is_url(lien):
        await interaction.response.send_message(
            "Indique un jeu, un titre et un lien HTTPS valide vers ton clip.", ephemeral=True
        )
        return

    # Un membre ne peut pas soumettre exactement le même lien plusieurs fois.
    duplicate = db.execute(
        "SELECT id FROM fe_clips WHERE guild_id = ? AND user_id = ? AND url = ? AND status != 'removed'",
        (interaction.guild_id, interaction.user.id, lien)
    ).fetchone()
    if duplicate:
        await interaction.response.send_message(
            f"Tu as déjà proposé ce lien (clip **#{duplicate['id']}**).", ephemeral=True
        )
        return

    now = int(time.time())
    db.execute(
        "INSERT INTO fe_clips (guild_id, user_id, game, title, url, submitted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (interaction.guild_id, interaction.user.id, jeu, titre, lien, now)
    )
    clip_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
    db.commit()

    embed = discord.Embed(
        title=f"🎬 Clip proposé : #{clip_id} — {titre}",
        description=f"**Jeu :** {jeu}\n**Proposé par :** {interaction.user.mention}\n\n[▶️ Voir le clip]({lien})",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Vote avec /clip_vote • Les clips sont éligibles pendant 7 jours.")
    await interaction.response.send_message(
        content=f"✅ Ton clip a été ajouté sous le numéro **#{clip_id}**.",
        embed=embed,
        allowed_mentions=discord.AllowedMentions.none(),
        ephemeral=True
    )


@bot.tree.command(
    name="clips",
    description="Afficher les clips proposés au cours des 7 derniers jours."
)
@app_commands.describe(jeu="Filtrer sur Rocket League (facultatif)")
async def clips(interaction: discord.Interaction, jeu: app_commands.Range[str, 2, 50] = ""):
    if not verifier_jeu_serveur(jeu, facultatif=True):
        await interaction.response.send_message(message_jeu_serveur(), ephemeral=True)
        return
    if str(jeu or "").strip():
        jeu = JEU_SERVEUR
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return

    since = _clip_week_start()
    jeu = jeu.strip()
    if jeu:
        rows = db.execute(
            """SELECT c.id, c.user_id, c.game, c.title, c.url, c.submitted_at,
                      (SELECT COUNT(*) FROM fe_clip_votes v WHERE v.clip_id = c.id) AS votes
               FROM fe_clips c WHERE c.guild_id = ? AND c.status = 'open'
                 AND c.submitted_at >= ? AND c.game = ? COLLATE NOCASE
               ORDER BY votes DESC, c.submitted_at DESC LIMIT 15""",
            (interaction.guild_id, since, jeu)
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT c.id, c.user_id, c.game, c.title, c.url, c.submitted_at,
                      (SELECT COUNT(*) FROM fe_clip_votes v WHERE v.clip_id = c.id) AS votes
               FROM fe_clips c WHERE c.guild_id = ? AND c.status = 'open'
                 AND c.submitted_at >= ?
               ORDER BY votes DESC, c.submitted_at DESC LIMIT 15""",
            (interaction.guild_id, since)
        ).fetchall()

    if not rows:
        await interaction.response.send_message(
            "Aucun clip proposé au cours des 7 derniers jours. Utilise `/clip_proposer` pour en ajouter un.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🎬 Clips de la semaine" + (f" — {jeu}" if jeu else ""),
        description="Vote avec `/clip_vote clip_id:<numéro>`. Un seul vote par personne et par clip.",
        color=discord.Color.blurple()
    )
    for row in rows:
        member = interaction.guild.get_member(int(row['user_id'])) if interaction.guild else None
        author = member.mention if member else f"Membre {row['user_id']}"
        value = f"**Jeu :** {row['game']} · **Votes :** {row['votes']}\n**Proposé par :** {author}\n[▶️ Voir le clip]({row['url']})"
        embed.add_field(name=f"#{row['id']} — {row['title']}"[:256], value=value[:1024], inline=False)
    if len(rows) == 15:
        embed.set_footer(text="Affichage limité aux 15 clips les plus votés. Filtre par jeu pour affiner.")
    await interaction.response.send_message(
        embed=embed, allowed_mentions=discord.AllowedMentions.none(), ephemeral=True
    )


@bot.tree.command(
    name="clip_vote",
    description="Voter pour un clip proposé au cours des 7 derniers jours."
)
@app_commands.describe(clip_id="Numéro du clip affiché dans /clips")
async def clip_vote(interaction: discord.Interaction, clip_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return

    clip = db.execute(
        "SELECT id, user_id, title, status, submitted_at FROM fe_clips WHERE id = ? AND guild_id = ?",
        (clip_id, interaction.guild_id)
    ).fetchone()
    if clip is None or clip['status'] != 'open' or int(clip['submitted_at']) < _clip_week_start():
        await interaction.response.send_message(
            "Ce clip n'existe pas, n'est plus ouvert ou a plus de 7 jours.", ephemeral=True
        )
        return
    if int(clip['user_id']) == interaction.user.id:
        await interaction.response.send_message("Tu ne peux pas voter pour ton propre clip.", ephemeral=True)
        return

    try:
        db.execute(
            "INSERT INTO fe_clip_votes (clip_id, guild_id, user_id, voted_at) VALUES (?, ?, ?, ?)",
            (clip_id, interaction.guild_id, interaction.user.id, int(time.time()))
        )
        db.commit()
    except sqlite3.IntegrityError:
        await interaction.response.send_message(
            "Tu as déjà voté pour ce clip. Chaque personne dispose d'un vote par clip.", ephemeral=True
        )
        return

    count = db.execute(
        "SELECT COUNT(*) FROM fe_clip_votes WHERE clip_id = ?", (clip_id,)
    ).fetchone()[0]
    await interaction.response.send_message(
        f"🗳️ Ton vote pour **#{clip_id} — {clip['title']}** a été enregistré. Total : **{count}** vote(s).",
        ephemeral=True
    )


@bot.tree.command(
    name="clip_gagnant",
    description="Annoncer le clip le plus voté des 7 derniers jours. (Staff)"
)
@app_commands.describe(salon="Salon où publier le résultat (par défaut : salon actuel)")
@moderator_only()
async def clip_gagnant(
    interaction: discord.Interaction,
    salon: discord.TextChannel | None = None
):
    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return

    since = _clip_week_start()
    winner = db.execute(
        """SELECT c.id, c.user_id, c.game, c.title, c.url,
                  COUNT(v.user_id) AS votes
           FROM fe_clips c LEFT JOIN fe_clip_votes v ON v.clip_id = c.id
           WHERE c.guild_id = ? AND c.status = 'open' AND c.submitted_at >= ?
           GROUP BY c.id
           ORDER BY votes DESC, c.submitted_at ASC LIMIT 1""",
        (interaction.guild_id, since)
    ).fetchone()
    if winner is None:
        await interaction.response.send_message(
            "Il n'y a aucun clip éligible cette semaine.", ephemeral=True
        )
        return

    channel = salon or interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Choisis un salon textuel pour publier le gagnant.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🏆 Clip de la semaine !",
        description=f"**{winner['title']}**\nJeu : **{winner['game']}**\nProposé par : <@{winner['user_id']}>\nVotes : **{winner['votes']}**\n\n[▶️ Voir le clip]({winner['url']})",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"Clip #{winner['id']} • Fractale Esport")
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException):
        await interaction.response.send_message(
            "Je n'ai pas pu publier dans ce salon. Vérifie mes permissions d'envoi de messages et d'intégration de liens.",
            ephemeral=True
        )
        return

    db.execute(
        "UPDATE fe_clips SET status = 'winner', winner_at = ? WHERE id = ?",
        (int(time.time()), winner['id'])
    )
    db.commit()
    await interaction.response.send_message(
        f"✅ Le clip **#{winner['id']}** a été annoncé dans {channel.mention}.", ephemeral=True
    )


@bot.tree.command(
    name="clip_supprimer",
    description="Retirer un clip que tu as proposé (ou modération par le staff)."
)
@app_commands.describe(clip_id="Numéro du clip à retirer")
async def clip_supprimer(interaction: discord.Interaction, clip_id: app_commands.Range[int, 1, 2147483647]):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée sur un serveur Discord.", ephemeral=True
        )
        return

    clip = db.execute(
        "SELECT user_id, status FROM fe_clips WHERE id = ? AND guild_id = ?",
        (clip_id, interaction.guild_id)
    ).fetchone()
    if clip is None or clip['status'] == 'removed':
        await interaction.response.send_message("Clip introuvable ou déjà retiré.", ephemeral=True)
        return
    is_staff = isinstance(interaction.user, discord.Member) and require_level(interaction.user, 1)
    if int(clip['user_id']) != interaction.user.id and not is_staff:
        await interaction.response.send_message(
            "Tu peux uniquement retirer ton propre clip (ou être modérateur).", ephemeral=True
        )
        return

    db.execute("UPDATE fe_clips SET status = 'removed' WHERE id = ?", (clip_id,))
    db.commit()
    await interaction.response.send_message(f"🗑️ Le clip **#{clip_id}** a été retiré.", ephemeral=True)


# ============================================================
# SANCTIONS COMPÉTITIVES — AVERTISSEMENTS ET SUSPENSIONS
# ============================================================

def get_active_competitive_restriction(guild_id: int, user_id: int):
    """Renvoie la restriction compétitive active la plus récente, en actualisant les expirations."""
    now = int(time.time())
    db.execute(
        "UPDATE competitive_sanctions SET status = 'expired' "
        "WHERE guild_id = ? AND user_id = ? AND status = 'active' "
        "AND expires_at IS NOT NULL AND expires_at <= ?",
        (guild_id, user_id, now)
    )
    db.commit()
    return db.execute(
        "SELECT * FROM competitive_sanctions "
        "WHERE guild_id = ? AND user_id = ? AND status = 'active' "
        "AND sanction_type IN ('suspension_tournoi', 'exclusion_temporaire') "
        "ORDER BY issued_at DESC LIMIT 1",
        (guild_id, user_id)
    ).fetchone()


@bot.tree.command(name="sanction_competitive", description="Attribuer un avertissement ou une suspension compétitive.")
@app_commands.describe(
    joueur="Joueur sanctionné",
    type_sanction="Type de sanction compétitive",
    raison="Motif précis et factuel",
    duree_jours="Durée en jours, obligatoire pour une suspension ou exclusion"
)
@app_commands.choices(type_sanction=[
    app_commands.Choice(name="Avertissement officiel", value="avertissement"),
    app_commands.Choice(name="Suspension des tournois", value="suspension_tournoi"),
    app_commands.Choice(name="Exclusion temporaire des compétitions", value="exclusion_temporaire"),
])
@moderator_only()
async def sanction_competitive(
    interaction: discord.Interaction,
    joueur: discord.Member,
    type_sanction: app_commands.Choice[str],
    raison: app_commands.Range[str, 5, 500],
    duree_jours: app_commands.Range[int, 1, 365] | None = None,
):
    if interaction.guild is None or interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur le serveur.", ephemeral=True)
        return
    if joueur.bot or joueur.id == interaction.user.id:
        await interaction.response.send_message("Tu ne peux pas sanctionner un bot ni te sanctionner toi-même.", ephemeral=True)
        return
    if isinstance(interaction.user, discord.Member) and not has_higher_role(interaction.user, joueur):
        await interaction.response.send_message("Tu ne peux pas sanctionner un membre de ton niveau ou supérieur.", ephemeral=True)
        return

    kind = type_sanction.value
    if kind == "avertissement" and duree_jours is not None:
        await interaction.response.send_message("Un avertissement officiel n'a pas besoin de durée. Laisse `duree_jours` vide.", ephemeral=True)
        return
    if kind != "avertissement" and duree_jours is None:
        await interaction.response.send_message("Indique `duree_jours` pour une suspension ou une exclusion temporaire.", ephemeral=True)
        return

    now = int(time.time())
    expires_at = now + int(duree_jours) * 86400 if duree_jours is not None else None
    db.execute(
        "INSERT INTO competitive_sanctions "
        "(guild_id, user_id, sanction_type, reason, issued_by, issued_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (interaction.guild_id, joueur.id, kind, safe_reason(raison, 500), interaction.user.id, now, expires_at)
    )
    sanction_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
    db.execute(
        "INSERT INTO competitive_decision_history "
        "(guild_id, sanction_id, target_user_id, actor_id, action, sanction_type, reason, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, 'sanction_creee', ?, ?, ?, ?)",
        (interaction.guild_id, sanction_id, joueur.id, interaction.user.id, kind, safe_reason(raison, 500), now, expires_at)
    )
    db.commit()
    labels = {
        "avertissement": "Avertissement officiel",
        "suspension_tournoi": "Suspension des tournois",
        "exclusion_temporaire": "Exclusion temporaire des compétitions",
    }
    duration_text = f"\n**Durée :** {duree_jours} jour(s) — fin <t:{expires_at}:F>" if expires_at else "\n**Durée :** sans expiration automatique"
    await send_log(
        interaction.guild,
        f"Sanction compétitive : {labels[kind]}",
        interaction.user,
        joueur,
        f"Raison : {safe_reason(raison, 500)}" + (f" | Durée : {duree_jours} jour(s)" if expires_at else "")
    )
    await interaction.response.send_message(
        f"✅ **{labels[kind]}** enregistré pour {joueur.mention}.\n**Raison :** {safe_reason(raison, 500)}{duration_text}\n"
        "Les suspensions et exclusions actives bloquent les inscriptions aux tournois et aux saisons compétitives.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(name="sanctions_joueur", description="Consulter les sanctions compétitives d'un joueur.")
@app_commands.describe(joueur="Joueur à consulter")
@moderator_only()
async def sanctions_joueur(interaction: discord.Interaction, joueur: discord.Member):
    if interaction.guild_id is None:
        await interaction.response.send_message("Utilise cette commande sur le serveur.", ephemeral=True)
        return
    now = int(time.time())
    db.execute(
        "UPDATE competitive_sanctions SET status = 'expired' WHERE guild_id = ? AND user_id = ? "
        "AND status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
        (interaction.guild_id, joueur.id, now)
    )
    db.commit()
    rows = db.execute(
        "SELECT * FROM competitive_sanctions WHERE guild_id = ? AND user_id = ? ORDER BY issued_at DESC LIMIT 10",
        (interaction.guild_id, joueur.id)
    ).fetchall()
    if not rows:
        await interaction.response.send_message(f"Aucune sanction compétitive enregistrée pour {joueur.mention}.", ephemeral=True)
        return
    labels = {"avertissement": "⚠️ Avertissement officiel", "suspension_tournoi": "🏆 Suspension des tournois", "exclusion_temporaire": "⛔ Exclusion temporaire des compétitions"}
    lines = []
    for row in rows:
        status = {"active": "🟠 Active", "revoked": "♻️ Levée", "expired": "⌛ Expirée"}.get(row["status"], row["status"])
        end = f" • fin <t:{int(row['expires_at'])}:R>" if row["expires_at"] else ""
        lines.append(f"**#{row['id']} — {labels.get(row['sanction_type'], row['sanction_type'])}** ({status}){end}\nRaison : {row['reason']}\nPar <@{row['issued_by']}> • <t:{int(row['issued_at'])}:d>")
    embed = discord.Embed(title=f"🛡️ Sanctions compétitives — {joueur.display_name}", description="\n\n".join(lines)[:4000], color=discord.Color.orange())
    embed.set_footer(text="Affichage des 10 sanctions les plus récentes")
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="sanction_lever", description="Lever une sanction compétitive active (cofondateur ou fondateur).")
@app_commands.describe(sanction_id="Identifiant affiché dans /sanctions_joueur", raison="Motif de la levée")
@co_founder_only()
async def sanction_lever(
    interaction: discord.Interaction,
    sanction_id: app_commands.Range[int, 1, 2147483647],
    raison: app_commands.Range[str, 5, 500],
):
    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message("Utilise cette commande sur le serveur.", ephemeral=True)
        return
    row = db.execute(
        "SELECT * FROM competitive_sanctions WHERE id = ? AND guild_id = ? AND status = 'active'",
        (sanction_id, interaction.guild_id)
    ).fetchone()
    if row is None:
        await interaction.response.send_message("Cette sanction active est introuvable. Consulte `/sanctions_joueur`.", ephemeral=True)
        return
    now = int(time.time())
    db.execute(
        "UPDATE competitive_sanctions SET status = 'revoked', revoked_by = ?, revoked_at = ?, revoke_reason = ? "
        "WHERE id = ? AND guild_id = ? AND status = 'active'",
        (interaction.user.id, now, safe_reason(raison, 500), sanction_id, interaction.guild_id)
    )
    db.execute(
        "INSERT INTO competitive_decision_history "
        "(guild_id, sanction_id, target_user_id, actor_id, action, sanction_type, reason, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, 'sanction_levee', ?, ?, ?, ?)",
        (interaction.guild_id, sanction_id, int(row["user_id"]), interaction.user.id, row["sanction_type"], safe_reason(raison, 500), now, row["expires_at"])
    )
    db.commit()
    target = interaction.guild.get_member(int(row["user_id"]))
    await send_log(
        interaction.guild,
        f"Sanction compétitive #{sanction_id} levée",
        interaction.user,
        target,
        f"Motif de levée : {safe_reason(raison, 500)}"
    )
    await interaction.response.send_message(
        f"♻️ La sanction **#{sanction_id}** a été levée.\n**Motif :** {safe_reason(raison, 500)}",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(name="historique_competitif", description="Consulter l'historique des décisions de modération compétitive.")
@app_commands.describe(joueur="Filtrer sur un joueur (facultatif)", nombre="Nombre de décisions à afficher (1 à 15)")
@moderator_only()
async def historique_competitif(
    interaction: discord.Interaction,
    joueur: discord.Member | None = None,
    nombre: app_commands.Range[int, 1, 15] = 10,
):
    if interaction.guild_id is None:
        await interaction.response.send_message("Utilise cette commande sur le serveur.", ephemeral=True)
        return

    if joueur is None:
        rows = db.execute(
            "SELECT * FROM competitive_decision_history WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?",
            (interaction.guild_id, int(nombre))
        ).fetchall()
        title = "📜 Historique des décisions compétitives"
    else:
        rows = db.execute(
            "SELECT * FROM competitive_decision_history WHERE guild_id = ? AND target_user_id = ? ORDER BY created_at DESC LIMIT ?",
            (interaction.guild_id, joueur.id, int(nombre))
        ).fetchall()
        title = f"📜 Historique compétitif — {joueur.display_name}"

    if not rows:
        message = "Aucune décision de modération compétitive n'est enregistrée pour le moment."
        await interaction.response.send_message(message, ephemeral=True)
        return

    labels = {
        "avertissement": "Avertissement officiel",
        "suspension_tournoi": "Suspension des tournois",
        "exclusion_temporaire": "Exclusion temporaire",
    }
    lines = []
    for row in rows:
        if row["action"] == "sanction_creee":
            action_text = "🟠 **Sanction attribuée**"
            reason_label = "Motif"
        else:
            action_text = "♻️ **Sanction levée**"
            reason_label = "Motif de levée"
        expiry = f" • expiration prévue <t:{int(row['expires_at'])}:R>" if row["expires_at"] else ""
        lines.append(
            f"**Décision #{row['id']} — Sanction #{row['sanction_id']}**\n"
            f"{action_text} : {labels.get(row['sanction_type'], row['sanction_type'])}\n"
            f"Joueur : <@{row['target_user_id']}> • Responsable : <@{row['actor_id']}>\n"
            f"{reason_label} : {row['reason']}\n"
            f"Date : <t:{int(row['created_at'])}:F>{expiry}"
        )

    description = "\n\n".join(lines)
    if len(description) > 3900:
        description = description[:3850] + "\n… (historique tronqué)"
    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    embed.set_footer(text=f"{len(rows)} décision(s) • Historique conservé dans SQLite")
    await interaction.response.send_message(
        embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
    )


# ============================================================
# RECOURS CONTRE LES SANCTIONS COMPÉTITIVES
# ============================================================

@bot.tree.command(name="recours_competitif", description="Contester une sanction compétitive qui t'a été attribuée.")
@app_commands.describe(sanction_id="Identifiant affiché dans /sanctions_joueur", raison="Explique clairement pourquoi tu contestes la sanction")
async def recours_competitif(
    interaction: discord.Interaction,
    sanction_id: app_commands.Range[int, 1, 2147483647],
    raison: app_commands.Range[str, 10, 500],
):
    if interaction.guild_id is None:
        await interaction.response.send_message("Cette commande doit être utilisée sur le serveur.", ephemeral=True)
        return
    if interaction.user.bot:
        await interaction.response.send_message("Les bots ne peuvent pas déposer de recours.", ephemeral=True)
        return

    sanction = db.execute(
        "SELECT id, user_id, sanction_type, status, reason FROM competitive_sanctions WHERE id = ? AND guild_id = ?",
        (sanction_id, interaction.guild_id)
    ).fetchone()
    if sanction is None:
        await interaction.response.send_message("Sanction introuvable. Vérifie son identifiant avec le staff.", ephemeral=True)
        return
    if int(sanction["user_id"]) != interaction.user.id:
        await interaction.response.send_message("Tu peux uniquement contester une sanction qui t'a été attribuée.", ephemeral=True)
        return
    if sanction["status"] == "revoked":
        await interaction.response.send_message("Cette sanction a déjà été levée : aucun recours n'est nécessaire.", ephemeral=True)
        return

    now = int(time.time())
    try:
        cursor = db.execute(
            "INSERT INTO competitive_appeals (guild_id, sanction_id, appellant_id, appeal_reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (interaction.guild_id, sanction_id, interaction.user.id, safe_reason(raison, 500), now)
        )
        appeal_id = int(cursor.lastrowid)
        db.commit()
    except sqlite3.IntegrityError:
        await interaction.response.send_message(
            "Un recours a déjà été déposé pour cette sanction. Le staff doit traiter le recours existant.",
            ephemeral=True
        )
        return

    guild = interaction.guild
    try:
        await send_log(
            guild,
            f"Nouveau recours compétitif #{appeal_id}",
            interaction.user,
            interaction.user if isinstance(interaction.user, discord.Member) else None,
            f"Sanction contestée : #{sanction_id}\nMotif du recours : {safe_reason(raison, 500)}"
        )
    except (discord.HTTPException, discord.Forbidden):
        pass

    await interaction.response.send_message(
        f"✅ Ton recours **#{appeal_id}** contre la sanction **#{sanction_id}** a été transmis au staff.\n"
        "Le recours sera examiné par un membre du staff. Déposer un recours ne suspend pas automatiquement la sanction.",
        ephemeral=True
    )


@bot.tree.command(name="recours_liste", description="Afficher les recours compétitifs en attente.")
@app_commands.describe(nombre="Nombre de recours à afficher (1 à 15)")
@moderator_only()
async def recours_liste(
    interaction: discord.Interaction,
    nombre: app_commands.Range[int, 1, 15] = 10,
):
    if interaction.guild_id is None:
        await interaction.response.send_message("Utilise cette commande sur le serveur.", ephemeral=True)
        return
    rows = db.execute(
        "SELECT a.*, s.sanction_type, s.status AS sanction_status FROM competitive_appeals a "
        "JOIN competitive_sanctions s ON s.id = a.sanction_id "
        "WHERE a.guild_id = ? AND a.status = 'pending' ORDER BY a.created_at ASC LIMIT ?",
        (interaction.guild_id, int(nombre))
    ).fetchall()
    if not rows:
        await interaction.response.send_message("Aucun recours compétitif n'est en attente.", ephemeral=True)
        return

    labels = {
        "avertissement": "Avertissement officiel",
        "suspension_tournoi": "Suspension des tournois",
        "exclusion_temporaire": "Exclusion temporaire",
    }
    lines = []
    for row in rows:
        lines.append(
            f"**Recours #{row['id']} — Sanction #{row['sanction_id']}**\n"
            f"Joueur : <@{row['appellant_id']}> • Sanction : {labels.get(row['sanction_type'], row['sanction_type'])}\n"
            f"État de la sanction : {row['sanction_status']} • Déposé <t:{int(row['created_at'])}:R>\n"
            f"Motif : {row['appeal_reason']}"
        )
    description = "\n\n".join(lines)
    if len(description) > 3900:
        description = description[:3850] + "\n… (liste tronquée)"
    embed = discord.Embed(title="⚖️ Recours compétitifs en attente", description=description, color=discord.Color.gold())
    embed.set_footer(text="Traite un recours avec /recours_accepter ou /recours_refuser")
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def traiter_recours(interaction: discord.Interaction, recours_id: int, decision: str, motif: str):
    if interaction.guild is None or interaction.guild_id is None:
        await interaction.response.send_message("Utilise cette commande sur le serveur.", ephemeral=True)
        return
    row = db.execute(
        "SELECT a.*, s.user_id AS target_user_id, s.sanction_type, s.status AS sanction_status, s.expires_at "
        "FROM competitive_appeals a JOIN competitive_sanctions s ON s.id = a.sanction_id "
        "WHERE a.id = ? AND a.guild_id = ?",
        (recours_id, interaction.guild_id)
    ).fetchone()
    if row is None:
        await interaction.response.send_message("Recours introuvable sur ce serveur.", ephemeral=True)
        return
    if row["status"] != "pending":
        await interaction.response.send_message("Ce recours a déjà été traité.", ephemeral=True)
        return
    now = int(time.time())
    db.execute(
        "UPDATE competitive_appeals SET status = ?, reviewed_by = ?, reviewed_at = ?, staff_reason = ? "
        "WHERE id = ? AND guild_id = ? AND status = 'pending'",
        (decision, interaction.user.id, now, safe_reason(motif, 500), recours_id, interaction.guild_id)
    )

    # Un recours accepté lève automatiquement la sanction si elle est toujours active.
    sanction_lifted = False
    if decision == "accepted" and row["sanction_status"] == "active":
        db.execute(
            "UPDATE competitive_sanctions SET status = 'revoked', revoked_by = ?, revoked_at = ?, revoke_reason = ? "
            "WHERE id = ? AND guild_id = ? AND status = 'active'",
            (interaction.user.id, now, f"Recours #{recours_id} accepté : {safe_reason(motif, 500)}", row["sanction_id"], interaction.guild_id)
        )
        sanction_lifted = db.execute("SELECT changes()").fetchone()[0] == 1
        if sanction_lifted:
            db.execute(
                "INSERT INTO competitive_decision_history "
                "(guild_id, sanction_id, target_user_id, actor_id, action, sanction_type, reason, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, 'sanction_levee', ?, ?, ?, ?)",
                (interaction.guild_id, row["sanction_id"], row["target_user_id"], interaction.user.id, row["sanction_type"],
                 f"Recours #{recours_id} accepté : {safe_reason(motif, 500)}", now, row["expires_at"])
            )
    db.commit()

    decision_text = "accepté" if decision == "accepted" else "refusé"
    try:
        target = interaction.guild.get_member(int(row["target_user_id"]))
        await send_log(
            interaction.guild, f"Recours compétitif #{recours_id} {decision_text}", interaction.user, target,
            f"Sanction : #{row['sanction_id']}\nMotif du staff : {safe_reason(motif, 500)}"
        )
    except (discord.HTTPException, discord.Forbidden):
        pass

    # Notification privée facultative : l'absence de DM ne doit pas annuler la décision.
    try:
        user = bot.get_user(int(row["appellant_id"])) or await bot.fetch_user(int(row["appellant_id"]))
        dm_text = (
            f"Ton recours compétitif **#{recours_id}** sur Fractale Esport a été {decision_text}.\n"
            f"Sanction concernée : **#{row['sanction_id']}**.\nMotif du staff : {safe_reason(motif, 500)}"
        )
        if decision == "accepted":
            dm_text += "\nLa sanction active a été levée." if sanction_lifted else "\nLa sanction n'était plus active, elle n'a donc pas été modifiée."
        await user.send(dm_text)
    except (discord.Forbidden, discord.HTTPException):
        pass

    extra = " La sanction active a été levée et l'historique mis à jour." if sanction_lifted else (
        " La sanction n'était plus active, donc aucune levée supplémentaire n'a été faite." if decision == "accepted" else ""
    )
    await interaction.response.send_message(
        f"✅ Recours **#{recours_id}** {decision_text}.\n**Motif :** {safe_reason(motif, 500)}{extra}",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(name="recours_accepter", description="Accepter un recours et lever la sanction si elle est encore active.")
@app_commands.describe(recours_id="Numéro affiché dans /recours_liste", motif="Justification de la décision")
@moderator_only()
async def recours_accepter(
    interaction: discord.Interaction,
    recours_id: app_commands.Range[int, 1, 2147483647],
    motif: app_commands.Range[str, 5, 500],
):
    await traiter_recours(interaction, int(recours_id), "accepted", motif)


@bot.tree.command(name="recours_refuser", description="Refuser un recours compétitif.")
@app_commands.describe(recours_id="Numéro affiché dans /recours_liste", motif="Justification de la décision")
@moderator_only()
async def recours_refuser(
    interaction: discord.Interaction,
    recours_id: app_commands.Range[int, 1, 2147483647],
    motif: app_commands.Range[str, 5, 500],
):
    await traiter_recours(interaction, int(recours_id), "rejected", motif)


# ============================================================
# DÉMARRAGE DU BOT — À LA FIN DU FICHIER
# ============================================================

if __name__ == "__main__":
    # Le bouton reste utilisable après le redémarrage du bot.
    bot.add_view(TournamentSignupView())
    try:
        bot.run(TOKEN)
    finally:
        db.close()
