# handlers/competitions.py
import random
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, increment_field, now
from utils.decorators import require_registered
from utils.helpers import fmt, fmt_time

# ─────────────────────────────────────────────────────────────────────────────
# Types de compétitions
# ─────────────────────────────────────────────────────────────────────────────
COMPETITION_TYPES = {
    "wealth": {
        "name": "💰 Course à la richesse",
        "desc": "Celui qui accumule le plus d'argent (cash + banque)",
        "duration": 604800,  # 7 jours
        "rewards": {
            1: {"balance": 5_000_000, "xp": 5000, "prestige": 100, "badge": "🏆 Roi de la richesse"},
            2: {"balance": 2_000_000, "xp": 2500, "prestige": 50},
            3: {"balance": 1_000_000, "xp": 1000, "prestige": 25},
            "participation": {"balance": 100_000, "xp": 200}
        }
    },
    "xp_gain": {
        "name": "⚡ Chasseur d'XP",
        "desc": "Celui qui gagne le plus d'XP pendant la compétition",
        "duration": 604800,
        "rewards": {
            1: {"balance": 3_000_000, "xp": 10000, "prestige": 75, "badge": "🏅 Maître de l'XP"},
            2: {"balance": 1_500_000, "xp": 5000, "prestige": 35},
            3: {"balance": 750_000, "xp": 2500, "prestige": 15},
            "participation": {"balance": 75_000, "xp": 150}
        }
    },
    "arena_wins": {
        "name": "⚔️ Guerrier de l'arène",
        "desc": "Celui qui remporte le plus de combats en arène",
        "duration": 604800,
        "rewards": {
            1: {"balance": 4_000_000, "xp": 4000, "prestige": 80, "badge": "🏅 Seigneur de la guerre"},
            2: {"balance": 2_000_000, "xp": 2000, "prestige": 40},
            3: {"balance": 1_000_000, "xp": 1000, "prestige": 20},
            "participation": {"balance": 100_000, "xp": 250}
        }
    },
    "crime_success": {
        "name": "🔫 Roi du crime",
        "desc": "Celui qui réussit le plus de crimes",
        "duration": 604800,
        "rewards": {
            1: {"balance": 4_000_000, "xp": 4000, "prestige": 80, "badge": "😈 Parrain"},
            2: {"balance": 2_000_000, "xp": 2000, "prestige": 40},
            3: {"balance": 1_000_000, "xp": 1000, "prestige": 20},
            "participation": {"balance": 100_000, "xp": 250}
        }
    },
    "social_followers": {
        "name": "📱 Influenceur ultime",
        "desc": "Celui qui gagne le plus d'abonnés",
        "duration": 604800,
        "rewards": {
            1: {"balance": 3_000_000, "xp": 5000, "prestige": 90, "badge": "📸 Superstar"},
            2: {"balance": 1_500_000, "xp": 2500, "prestige": 45},
            3: {"balance": 750_000, "xp": 1250, "prestige": 20},
            "participation": {"balance": 75_000, "xp": 200}
        }
    },
    "travel_count": {
        "name": "✈️ Explorateur",
        "desc": "Celui qui voyage le plus",
        "duration": 604800,
        "rewards": {
            1: {"balance": 2_500_000, "xp": 3000, "prestige": 60, "badge": "🌍 Globe-trotter"},
            2: {"balance": 1_000_000, "xp": 1500, "prestige": 30},
            3: {"balance": 500_000, "xp": 750, "prestige": 15},
            "participation": {"balance": 50_000, "xp": 150}
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
async def get_active_competition() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM competitions WHERE ended = 0 AND ends_at > ? LIMIT 1",
            (now(),)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None

async def get_competition_scores(comp_id: int, limit: int = 10) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT cs.user_id, u.full_name, cs.score
            FROM competition_scores cs
            JOIN users u ON u.user_id = cs.user_id
            WHERE cs.comp_id = ?
            ORDER BY cs.score DESC
            LIMIT ?
        """, (comp_id, limit)) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def update_competition_score(user_id: int, comp_type: str, increment: int = 1):
    """Met à jour le score d'un joueur dans la compétition active et enregistre sa participation."""
    comp = await get_active_competition()
    if not comp or comp["comp_type"] != comp_type:
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO competition_scores (comp_id, user_id, score)
            VALUES (?, ?, ?)
            ON CONFLICT(comp_id, user_id) DO UPDATE SET score = score + ?
        """, (comp["comp_id"], user_id, increment, increment))
        # Enregistre la participation
        await db.execute("""
            INSERT OR IGNORE INTO competition_participants (comp_id, user_id)
            VALUES (?, ?)
        """, (comp["comp_id"], user_id))
        await db.commit()

async def record_competition_participation(user_id: int):
    """Enregistre la participation d'un joueur (pour les récompenses)."""
    comp = await get_active_competition()
    if not comp:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO competition_participants (comp_id, user_id)
            VALUES (?, ?)
        """, (comp["comp_id"], user_id))
        await db.commit()

async def start_new_competition():
    """Démarre une nouvelle compétition (appelé par le scheduler)."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Terminer l'ancienne
        await db.execute("UPDATE competitions SET ended = 1 WHERE ended = 0 AND ends_at < ?", (now(),))
        await db.commit()
    
    existing = await get_active_competition()
    if existing:
        return
    
    comp_type = random.choice(list(COMPETITION_TYPES.keys()))
    comp_data = COMPETITION_TYPES[comp_type]
    ends_at = now() + comp_data["duration"]
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO competitions (comp_type, starts_at, ends_at) VALUES (?, ?, ?)",
            (comp_type, now(), ends_at)
        )
        await db.commit()
    
    # Nettoyer les anciennes participations
    async with aiosqlite.connect(DB_PATH) as db2:
        await db2.execute("DELETE FROM competition_scores WHERE comp_id NOT IN (SELECT comp_id FROM competitions WHERE ended = 0)")
        await db2.execute("DELETE FROM competition_participants WHERE comp_id NOT IN (SELECT comp_id FROM competitions WHERE ended = 0)")
        await db2.commit()
    
    print(f"🏆 Nouvelle compétition démarrée : {comp_data['name']}")

async def end_competition_and_reward():
    """Termine la compétition et distribue les récompenses."""
    comp = await get_active_competition()
    if not comp or comp["ends_at"] > now():
        return
    
    comp_data = COMPETITION_TYPES.get(comp["comp_type"])
    if not comp_data:
        return
    
    scores = await get_competition_scores(comp["comp_id"], 100)
    
    for idx, score in enumerate(scores[:3], 1):
        rewards = comp_data["rewards"].get(idx, {})
        if rewards.get("balance"):
            await update_balance(score["user_id"], rewards["balance"])
        if rewards.get("xp"):
            await increment_field(score["user_id"], "xp", rewards["xp"])
        if rewards.get("prestige"):
            await increment_field(score["user_id"], "prestige", rewards["prestige"])
        if rewards.get("badge"):
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO user_badges (user_id, badge, earned_at) VALUES (?, ?, ?)",
                    (score["user_id"], rewards["badge"], now())
                )
                await db.commit()
    
    # Récompenses de participation
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM competition_participants WHERE comp_id = ?",
            (comp["comp_id"],)
        ) as cur:
            participants = await cur.fetchall()
    
    participation_reward = comp_data["rewards"].get("participation", {})
    for p in participants:
        if participation_reward.get("balance"):
            await update_balance(p[0], participation_reward["balance"])
        if participation_reward.get("xp"):
            await increment_field(p[0], "xp", participation_reward["xp"])
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE competitions SET ended = 1 WHERE comp_id = ?", (comp["comp_id"],))
        await db.commit()
    
    print(f"🏆 Compétition {comp_data['name']} terminée, récompenses distribuées.")

# ─────────────────────────────────────────────────────────────────────────────
# Commandes
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
async def cmd_competition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comp = await get_active_competition()
    if not comp:
        await update.message.reply_text(
            "🏆 **Aucune compétition active**\n\n"
            "Une nouvelle compétition commence chaque semaine !\n"
            "Reviens plus tard pour participer et gagner des récompenses exclusives."
        )
        return
    
    comp_data = COMPETITION_TYPES.get(comp["comp_type"], {})
    time_left = comp["ends_at"] - now()
    
    text = f"🏆 **{comp_data.get('name', 'Compétition')}**\n\n"
    text += f"📋 {comp_data.get('desc', '')}\n"
    text += f"⏳ Temps restant : **{fmt_time(time_left)}**\n\n"
    
    scores = await get_competition_scores(comp["comp_id"], 10)
    text += "**🏅 Classement actuel :**\n"
    medals = ["🥇", "🥈", "🥉"] + ["📌"] * 7
    for i, s in enumerate(scores):
        text += f"{medals[i]} **{s['full_name']}** — {fmt(s['score'])}\n"
    if not scores:
        text += "_Aucun participant pour l'instant._\n"
    
    text += f"\n**🎁 Récompenses :**\n"
    for rank, rewards in comp_data.get("rewards", {}).items():
        if rank == "participation":
            text += f"🎖️ Participation : {fmt(rewards.get('balance', 0))} coins, {rewards.get('xp', 0)} XP\n"
        else:
            text += f"{medals[rank-1] if rank <= 3 else f'#{rank}'} : {fmt(rewards.get('balance', 0))} coins, {rewards.get('xp', 0)} XP, +{rewards.get('prestige', 0)} Prestige"
            if rewards.get("badge"):
                text += f", badge **{rewards['badge']}**"
            text += "\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")

@require_registered
async def cmd_competition_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    comp = await get_active_competition()
    if not comp:
        await update.message.reply_text("❌ Aucune compétition active.")
        return
    
    await record_competition_participation(user.id)
    await update.message.reply_text(
        "✅ **Tu as rejoint la compétition !**\n\n"
        "Participe aux actions correspondantes pour gagner des points.\n"
        "Utilise `/competition` pour voir ton classement."
    )

@require_registered
async def cmd_competition_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT c.comp_id, c.comp_type, c.starts_at, c.ends_at, cs.score
            FROM competition_scores cs
            JOIN competitions c ON c.comp_id = cs.comp_id
            WHERE cs.user_id = ? AND c.ended = 1
            ORDER BY c.ends_at DESC
            LIMIT 10
        """, (user.id,)) as cur:
            history = await cur.fetchall()
    
    if not history:
        await update.message.reply_text("📜 Tu n'as participé à aucune compétition terminée.")
        return
    
    text = "📜 **Ton historique des compétitions**\n\n"
    for h in history:
        comp_data = COMPETITION_TYPES.get(h["comp_type"], {})
        date = fmt_time(h["starts_at"])
        text += f"🏆 **{comp_data.get('name', h['comp_type'])}**\n"
        text += f"   📊 Score : {fmt(h['score'])}\n"
        text += f"   📅 {date}\n\n"
    
    await update.message.reply_text(text[:4000], parse_mode="Markdown")

# ─────────────────────────────────────────────────────────────────────────────
# Hooks à appeler depuis d'autres modules
# ─────────────────────────────────────────────────────────────────────────────
async def on_xp_gain(user_id: int, amount: int):
    comp = await get_active_competition()
    if comp and comp["comp_type"] == "xp_gain":
        await update_competition_score(user_id, "xp_gain", amount)

async def on_arena_win(user_id: int):
    comp = await get_active_competition()
    if comp and comp["comp_type"] == "arena_wins":
        await update_competition_score(user_id, "arena_wins", 1)

async def on_crime_success(user_id: int):
    comp = await get_active_competition()
    if comp and comp["comp_type"] == "crime_success":
        await update_competition_score(user_id, "crime_success", 1)

async def on_social_gain(user_id: int, amount: int):
    comp = await get_active_competition()
    if comp and comp["comp_type"] == "social_followers":
        await update_competition_score(user_id, "social_followers", amount)

async def on_travel(user_id: int):
    comp = await get_active_competition()
    if comp and comp["comp_type"] == "travel_count":
        await update_competition_score(user_id, "travel_count", 1)
    
async def on_wealth_gain(user_id: int, amount: int):
    """Appelé quand un joueur gagne de l'argent (augmentation de solde)."""
    comp = await get_active_competition()
    if comp and comp["comp_type"] == "wealth":
        await update_competition_score(user_id, "wealth", amount)