# handlers/achievements.py
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, increment_field, update_balance, now
from utils.decorators import require_registered
from utils.helpers import fmt

# ─────────────────────────────────────────────────────────────────────────────
# Liste des succès (ID, nom, condition, récompense)
# ─────────────────────────────────────────────────────────────────────────────
ACHIEVEMENTS = {
    # Succès de richesse
    "millionnaire": {
        "name": "💰 Millionnaire",
        "desc": "Atteindre 1 000 000 coins",
        "condition": lambda u: u.get("balance", 0) >= 1_000_000,
        "reward_balance": 100_000,
        "reward_xp": 500,
        "reward_prestige": 10
    },
    "milliardaire": {
        "name": "💎 Milliardaire",
        "desc": "Atteindre 1 000 000 000 coins",
        "condition": lambda u: u.get("balance", 0) >= 1_000_000_000,
        "reward_balance": 10_000_000,
        "reward_xp": 5000,
        "reward_prestige": 100
    },
    "trillionaire": {
        "name": "⭐ Trillionaire",
        "desc": "Atteindre 1 000 000 000 000 coins",
        "condition": lambda u: u.get("balance", 0) >= 1_000_000_000_000,
        "reward_balance": 100_000_000,
        "reward_xp": 50000,
        "reward_prestige": 1000
    },
    
    # Succès de niveau
    "level_10": {
        "name": "📈 Apprenti",
        "desc": "Atteindre le niveau 10",
        "condition": lambda u: u.get("level", 1) >= 10,
        "reward_balance": 50_000,
        "reward_xp": 200,
        "reward_prestige": 5
    },
    "level_25": {
        "name": "⚡ Expert",
        "desc": "Atteindre le niveau 25",
        "condition": lambda u: u.get("level", 1) >= 25,
        "reward_balance": 200_000,
        "reward_xp": 500,
        "reward_prestige": 15
    },
    "level_50": {
        "name": "🔥 Légende",
        "desc": "Atteindre le niveau 50",
        "condition": lambda u: u.get("level", 1) >= 50,
        "reward_balance": 1_000_000,
        "reward_xp": 2000,
        "reward_prestige": 50
    },
    "level_100": {
        "name": "👑 Dieu vivant",
        "desc": "Atteindre le niveau 100",
        "condition": lambda u: u.get("level", 1) >= 100,
        "reward_balance": 10_000_000,
        "reward_xp": 10000,
        "reward_prestige": 200
    },
    
    # Succès de combat
    "arena_10": {
        "name": "⚔️ Combattant",
        "desc": "Gagner 10 combats en arène",
        "condition": lambda u: u.get("arena_wins", 0) >= 10,
        "reward_balance": 50_000,
        "reward_xp": 300,
        "reward_prestige": 5
    },
    "arena_50": {
        "name": "🏆 Champion",
        "desc": "Gagner 50 combats en arène",
        "condition": lambda u: u.get("arena_wins", 0) >= 50,
        "reward_balance": 500_000,
        "reward_xp": 1000,
        "reward_prestige": 25
    },
    "arena_100": {
        "name": "🥊 Légende de l'arène",
        "desc": "Gagner 100 combats en arène",
        "condition": lambda u: u.get("arena_wins", 0) >= 100,
        "reward_balance": 2_000_000,
        "reward_xp": 5000,
        "reward_prestige": 100
    },
    
    # Succès de crime
    "crime_10": {
        "name": "🔫 Petit criminel",
        "desc": "Commettez 10 crimes",
        "condition": lambda u: u.get("crimes_done", 0) >= 10,
        "reward_balance": 25_000,
        "reward_xp": 200,
        "reward_prestige": 3
    },
    "crime_50": {
        "name": "😈 Seigneur du crime",
        "desc": "Commettez 50 crimes",
        "condition": lambda u: u.get("crimes_done", 0) >= 50,
        "reward_balance": 250_000,
        "reward_xp": 1000,
        "reward_prestige": 25
    },
    "crime_100": {
        "name": "💀 Parrain",
        "desc": "Commettez 100 crimes",
        "condition": lambda u: u.get("crimes_done", 0) >= 100,
        "reward_balance": 1_000_000,
        "reward_xp": 5000,
        "reward_prestige": 100
    },
    
    # Succès de karma
    "karma_100": {
        "name": "😇 Vertueux",
        "desc": "Atteindre 100 points de karma",
        "condition": lambda u: u.get("karma", 0) >= 100,
        "reward_balance": 100_000,
        "reward_xp": 500,
        "reward_prestige": 10
    },
    "karma_500": {
        "name": "🌟 Saint",
        "desc": "Atteindre 500 points de karma",
        "condition": lambda u: u.get("karma", 0) >= 500,
        "reward_balance": 500_000,
        "reward_xp": 2000,
        "reward_prestige": 50
    },
    "karma_minus_100": {
        "name": "😈 Démon",
        "desc": "Atteindre -100 points de karma",
        "condition": lambda u: u.get("karma", 0) <= -100,
        "reward_balance": 100_000,
        "reward_xp": 500,
        "reward_prestige": 10
    },
    "karma_minus_500": {
        "name": "💀 Incarné",
        "desc": "Atteindre -500 points de karma",
        "condition": lambda u: u.get("karma", 0) <= -500,
        "reward_balance": 500_000,
        "reward_xp": 2000,
        "reward_prestige": 50
    },
    
    # Succès de voyage
    "travel_5": {
        "name": "✈️ Voyageur",
        "desc": "Effectuer 5 voyages",
        "condition": lambda u: u.get("travel_count", 0) >= 5,
        "reward_balance": 50_000,
        "reward_xp": 300,
        "reward_prestige": 5
    },
    "travel_20": {
        "name": "🌍 Globe-trotter",
        "desc": "Effectuer 20 voyages",
        "condition": lambda u: u.get("travel_count", 0) >= 20,
        "reward_balance": 500_000,
        "reward_xp": 1500,
        "reward_prestige": 30
    },
    
    # Succès de diplôme
    "diploma_master": {
        "name": "🎓 Master",
        "desc": "Obtenir un Master",
        "condition": lambda u: u.get("diplome", "") == "Master",
        "reward_balance": 500_000,
        "reward_xp": 2000,
        "reward_prestige": 25
    },
    "diploma_doctorat": {
        "name": "🔬 Docteur",
        "desc": "Obtenir un Doctorat",
        "condition": lambda u: u.get("diplome", "") == "Doctorat",
        "reward_balance": 2_000_000,
        "reward_xp": 5000,
        "reward_prestige": 100
    },
    
    # Succès d'investissement (calcul dynamique)
    "invest_1m": {
        "name": "📈 Investisseur",
        "desc": "Avoir 1 000 000 coins investis en bourse",
        "condition": None,  # sera évalué via une fonction async
        "reward_balance": 100_000,
        "reward_xp": 500,
        "reward_prestige": 10
    },
    "invest_10m": {
        "name": "💹 Baron de la bourse",
        "desc": "Avoir 10 000 000 coins investis en bourse",
        "condition": None,
        "reward_balance": 1_000_000,
        "reward_xp": 2000,
        "reward_prestige": 50
    },
    
    # Succès de jardin
    "garden_10": {
        "name": "🌱 Jardinier",
        "desc": "Planter 10 plantes",
        "condition": lambda u: u.get("plants_grown", 0) >= 10,
        "reward_balance": 25_000,
        "reward_xp": 200,
        "reward_prestige": 3
    },
    "garden_100": {
        "name": "🌻 Maître jardinier",
        "desc": "Planter 100 plantes",
        "condition": lambda u: u.get("plants_grown", 0) >= 100,
        "reward_balance": 250_000,
        "reward_xp": 1000,
        "reward_prestige": 25
    },
    
    # Succès de prestige
    "prestige_100": {
        "name": "✨ Distingué",
        "desc": "Atteindre 100 points de prestige",
        "condition": lambda u: u.get("prestige", 0) >= 100,
        "reward_balance": 500_000,
        "reward_xp": 1000,
        "reward_prestige": 20
    },
    "prestige_500": {
        "name": "🏅 Élite",
        "desc": "Atteindre 500 points de prestige",
        "condition": lambda u: u.get("prestige", 0) >= 500,
        "reward_balance": 2_000_000,
        "reward_xp": 5000,
        "reward_prestige": 100
    },
    "prestige_1000": {
        "name": "👑 Légende vivante",
        "desc": "Atteindre 1000 points de prestige",
        "condition": lambda u: u.get("prestige", 0) >= 1000,
        "reward_balance": 10_000_000,
        "reward_xp": 20000,
        "reward_prestige": 500
    },
    
    # Succès de guilde (calcul dynamique)
    "guild_join": {
        "name": "🏰 Fidèle",
        "desc": "Rejoindre une guilde",
        "condition": None,
        "reward_balance": 100_000,
        "reward_xp": 500,
        "reward_prestige": 10
    },
    "guild_create": {
        "name": "👑 Fondateur",
        "desc": "Créer sa propre guilde",
        "condition": None,
        "reward_balance": 500_000,
        "reward_xp": 2000,
        "reward_prestige": 50
    },
    
    # Succès sociaux
    "social_10k": {
        "name": "📱 Influenceur",
        "desc": "Atteindre 10 000 followers sur les réseaux",
        "condition": lambda u: u.get("social_followers", 0) >= 10_000,
        "reward_balance": 100_000,
        "reward_xp": 1000,
        "reward_prestige": 15
    },
    "social_100k": {
        "name": "⭐ Célébrité",
        "desc": "Atteindre 100 000 followers sur les réseaux",
        "condition": lambda u: u.get("social_followers", 0) >= 100_000,
        "reward_balance": 1_000_000,
        "reward_xp": 5000,
        "reward_prestige": 100
    },
    
    # Succès de mission
    "missions_10": {
        "name": "🎯 Accompli",
        "desc": "Compléter 10 missions",
        "condition": lambda u: u.get("missions_done", 0) >= 10,
        "reward_balance": 100_000,
        "reward_xp": 500,
        "reward_prestige": 10
    },
    "missions_50": {
        "name": "🏆 Héroïque",
        "desc": "Compléter 50 missions",
        "condition": lambda u: u.get("missions_done", 0) >= 50,
        "reward_balance": 500_000,
        "reward_xp": 2000,
        "reward_prestige": 50
    },
    "missions_100": {
        "name": "💪 Légendaire",
        "desc": "Compléter 100 missions",
        "condition": lambda u: u.get("missions_done", 0) >= 100,
        "reward_balance": 2_000_000,
        "reward_xp": 10000,
        "reward_prestige": 200
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers asynchrones pour les conditions dynamiques
# ─────────────────────────────────────────────────────────────────────────────
async def check_invested_amount(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT SUM(quantity * avg_price) FROM investments WHERE user_id = ?",
            (user_id,)
        ) as cur:
            invested = (await cur.fetchone())[0] or 0
    return invested

async def check_guild_info(user_id: int):
    """Retourne (guild_id, is_owner)"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT g.guild_id, g.owner_id
            FROM guild_members gm
            JOIN guilds g ON g.guild_id = gm.guild_id
            WHERE gm.user_id = ?
        """, (user_id,)) as cur:
            row = await cur.fetchone()
    if row:
        return row[0], row[1] == user_id
    return None, False

# ─────────────────────────────────────────────────────────────────────────────
async def get_user_achievements(user_id: int) -> list:
    """Récupère la liste des succès déjà débloqués par l'utilisateur."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT achievement_id FROM user_achievements WHERE user_id = ?",
            (user_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]

async def unlock_achievement(user_id: int, achievement_id: str, u: dict) -> bool:
    """Débloque un succès, distribue les récompenses si non déjà débloqué."""
    if achievement_id in await get_user_achievements(user_id):
        return False
    
    ach = ACHIEVEMENTS.get(achievement_id)
    if not ach:
        return False
    
    # Évaluation dynamique des conditions spéciales
    condition_met = False
    if ach["condition"] is not None:
        condition_met = ach["condition"](u)
    else:
        # Conditions spéciales
        if achievement_id in ("invest_1m", "invest_10m"):
            invested = await check_invested_amount(user_id)
            target = 1_000_000 if achievement_id == "invest_1m" else 10_000_000
            condition_met = invested >= target
        elif achievement_id in ("guild_join", "guild_create"):
            _, is_owner = await check_guild_info(user_id)
            if achievement_id == "guild_join":
                condition_met = (await check_guild_info(user_id))[0] is not None
            else:  # guild_create
                condition_met = is_owner
    
    if not condition_met:
        return False
    
    # Distribuer les récompenses
    if ach.get("reward_balance"):
        await update_balance(user_id, ach["reward_balance"])
    if ach.get("reward_xp"):
        await increment_field(user_id, "xp", ach["reward_xp"])
    if ach.get("reward_prestige"):
        await increment_field(user_id, "prestige", ach["reward_prestige"])
    
    # Enregistrer dans la base
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_achievements (user_id, achievement_id, unlocked_at) VALUES (?,?,?)",
            (user_id, achievement_id, now())
        )
        await db.commit()
    
    return True

async def check_all_achievements(user_id: int, u: dict) -> list:
    """Vérifie tous les succès et retourne ceux qui viennent d'être débloqués."""
    unlocked = []
    for aid in ACHIEVEMENTS:
        if await unlock_achievement(user_id, aid, u):
            unlocked.append(ACHIEVEMENTS[aid]["name"])
    return unlocked

# ─────────────────────────────────────────────────────────────────────────────
# Commandes
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
async def cmd_achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche la liste des succès et la progression."""
    user = update.effective_user
    u = await get_user(user.id)
    unlocked = await get_user_achievements(user.id)
    
    text = "🏆 **Tes Succès**\n\n"
    text += f"_Succès débloqués : {len(unlocked)}/{len(ACHIEVEMENTS)}_\n\n"
    
    # Pour les succès d'investissement, on calcule le montant investi
    invested = await check_invested_amount(user.id)
    guild_id, is_owner = await check_guild_info(user.id)
    
    for aid, ach in ACHIEVEMENTS.items():
        # Vérifier si la condition est remplie (en tenant compte des spéciaux)
        condition_met = False
        if aid in unlocked:
            condition_met = True
        elif ach["condition"] is not None:
            condition_met = ach["condition"](u)
        else:
            if aid == "invest_1m":
                condition_met = invested >= 1_000_000
            elif aid == "invest_10m":
                condition_met = invested >= 10_000_000
            elif aid == "guild_join":
                condition_met = guild_id is not None
            elif aid == "guild_create":
                condition_met = is_owner
        
        if aid in unlocked:
            status = "✅"
        elif condition_met:
            status = "🔓"
        else:
            status = "🔒"
        
        text += f"{status} **{ach['name']}**\n"
        text += f"   _{ach['desc']}_\n"
        if condition_met and aid not in unlocked:
            text += f"   ⚡ **PRÊT À DÉBLOQUER !**\n"
        elif aid not in unlocked:
            text += f"   🎁 Récompense : {fmt(ach.get('reward_balance', 0))} coins | {ach.get('reward_xp', 0)} XP | {ach.get('reward_prestige', 0)} Prestige\n"
        text += "\n"
    
    await update.message.reply_text(text[:4000], parse_mode="Markdown")

@require_registered
async def cmd_achievements_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force la vérification des succès (utile après une action)."""
    user = update.effective_user
    u = await get_user(user.id)
    new_unlocked = await check_all_achievements(user.id, u)
    
    if new_unlocked:
        text = "🎉 **Nouveaux succès débloqués !**\n\n"
        for name in new_unlocked:
            text += f"✅ {name}\n"
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text("🔍 Aucun nouveau succès à débloquer pour le moment.")

# ─────────────────────────────────────────────────────────────────────────────
# Middleware / Hook à appeler après les actions importantes
# ─────────────────────────────────────────────────────────────────────────────
async def on_user_action(user_id: int, action_type: str, value=None):
    """À appeler après certaines actions pour déclencher les vérifications de succès."""
    u = await get_user(user_id)
    await check_all_achievements(user_id, u)