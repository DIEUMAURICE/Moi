# handlers/missions.py
import random
import time as time_module
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, increment_field, db_connection
from utils.decorators import require_registered, require_free
from utils.helpers import fmt, now
from config import DAILY_MISSIONS, WEEKLY_MISSIONS, TIME_MULTIPLIER
from handlers.competitions import on_xp_gain
from handlers.vehicles import get_active_vehicle  # Import pour le cargo

# ─────────────────────────────────────────────────────────────────────────────
# Helper pour ajouter un item aléatoire (avec pool de connexions)
# ─────────────────────────────────────────────────────────────────────────────
async def add_random_item(user_id: int, min_rarity: str = "common", max_rarity: str = "legendary"):
    """Ajoute un item aléatoire à l'inventaire du joueur. Retourne le nom de l'item ou None."""
    rarity_order = ["common", "rare", "epic", "legendary"]
    min_idx = rarity_order.index(min_rarity)
    max_idx = rarity_order.index(max_rarity)
    possible = [r for r in rarity_order if min_idx <= rarity_order.index(r) <= max_idx]
    chosen_rarity = random.choice(possible)
    
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT item_id, name, emoji, rarity FROM items WHERE rarity = ? ORDER BY RANDOM() LIMIT 1",
            (chosen_rarity,)
        ) as cur:
            item = await cur.fetchone()
        if not item:
            return None
        
        async with db.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item["item_id"])
        ) as cur2:
            existing = await cur2.fetchone()
        if existing:
            await db.execute(
                "UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_id = ?",
                (user_id, item["item_id"])
            )
        else:
            await db.execute("""
                INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, item["item_id"], "mission", item["name"], 1, now()))
        await db.commit()
    return f"{item['emoji']} {item['name']} ({item['rarity']})"

# ─────────────────────────────────────────────────────────────────────────────
# Gestion du temps de jeu (pour les reset)
# ─────────────────────────────────────────────────────────────────────────────
def get_game_day_reset() -> int:
    """Retourne le timestamp (temps de jeu) du début de la journée (00:00:00)."""
    real_ts = int(time_module.time())
    game_seconds = real_ts * TIME_MULTIPLIER
    day_seconds = game_seconds % 86_400
    return game_seconds - day_seconds

def get_game_week_reset() -> int:
    """Retourne le timestamp (temps de jeu) du début de la semaine (lundi 00:00:00)."""
    real_ts = int(time_module.time())
    game_seconds = real_ts * TIME_MULTIPLIER
    return game_seconds - (game_seconds % 604800)

# ─────────────────────────────────────────────────────────────────────────────
# Fonction pour récupérer les missions disponibles selon le cargo
# ─────────────────────────────────────────────────────────────────────────────
async def get_available_missions(user_id: int, missions: list) -> list:
    """
    Filtre les missions selon le cargo du véhicule actif.
    Les missions de type 'delivery' nécessitent un cargo minimum.
    """
    active_vehicle = await get_active_vehicle(user_id)
    vehicle_cargo = active_vehicle.get("cargo", 0) if active_vehicle else 0
    
    available = []
    blocked = []
    
    for mission in missions:
        # Vérifier si c'est une mission de livraison
        if mission.get("type") == "delivery" or "livraison" in mission.get("name", "").lower():
            required_cargo = mission.get("cargo_required", 50)
            if vehicle_cargo < required_cargo:
                blocked.append(mission)
                continue
        available.append(mission)
    
    return available, blocked, vehicle_cargo

# ─────────────────────────────────────────────────────────────────────────────
# Commandes utilisateur
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_missions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if u.get("hospital_until", 0) > now():
        await update.message.reply_text("🏥 Tu es hospitalisé(e) ! Repose-toi avant de consulter tes missions.")
        return

    await ensure_missions(user.id)
    
    # Mise à jour de la mission "balance" avec le solde actuel
    await update_balance_mission(user.id)

    # Récupérer le véhicule actif pour le cargo
    active_vehicle = await get_active_vehicle(user.id)
    vehicle_cargo = active_vehicle.get("cargo", 0) if active_vehicle else 0
    
    # Message d'info sur le cargo
    if active_vehicle:
        cargo_msg = f"🚛 Cargo du véhicule : {vehicle_cargo}/100"
        if vehicle_cargo >= 80:
            cargo_msg += " 🏆 Capacité maximale !"
        elif vehicle_cargo >= 50:
            cargo_msg += " ✅ Capacité suffisante pour les livraisons lourdes"
        else:
            cargo_msg += " ⚠️ Capacité limitée - missions de livraison restreintes"
    else:
        cargo_msg = "🚫 Aucun véhicule actif → missions de livraison indisponibles"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM missions WHERE user_id=? ORDER BY completed ASC, period ASC",
            (user.id,)
        ) as cur:
            all_missions = [dict(m) for m in await cur.fetchall()]

    daily = [m for m in all_missions if m["period"] == "daily"]
    weekly = [m for m in all_missions if m["period"] == "weekly"]

    text = "🎯 **Tes missions**\n\n"
    text += f"_{cargo_msg}_\n\n"
    
    text += "**📅 Missions journalières**\n"
    for m in daily:
        # Vérifier si c'est une mission de livraison et si le cargo est suffisant
        is_delivery = m.get("mission_type") == "delivery" or "livraison" in m.get("mission_name", "").lower()
        cargo_ok = vehicle_cargo >= m.get("cargo_required", 50) if is_delivery else True
        
        if is_delivery and not cargo_ok:
            required = m.get("cargo_required", 50)
            text += (
                f"🔒 **{m['mission_name']}**\n"
                f"  🚛 Cargo requis : {required}\n"
                f"  ❌ Mission bloquée - cargo insuffisant\n\n"
            )
            continue
            
        progress_bar = "█" * int((m["progress"] / max(1, m["target"])) * 10)
        progress_bar = progress_bar[:10].ljust(10, "░")
        status = "✅" if m["completed"] else f"[{progress_bar}]"
        text += (
            f"{status} **{m['mission_name']}**\n"
            f"  Progress : {m['progress']}/{m['target']}\n"
            f"  Récompense : {fmt(m['reward'])} + {m['xp_reward']} XP\n"
        )
        if is_delivery:
            text += f"  🚛 Cargo requis : {m.get('cargo_required', 50)}\n"
        text += "\n"

    text += "\n**📆 Missions hebdomadaires**\n"
    for m in weekly:
        is_delivery = m.get("mission_type") == "delivery" or "livraison" in m.get("mission_name", "").lower()
        cargo_ok = vehicle_cargo >= m.get("cargo_required", 50) if is_delivery else True
        
        if is_delivery and not cargo_ok:
            required = m.get("cargo_required", 50)
            text += (
                f"🔒 **{m['mission_name']}**\n"
                f"  🚛 Cargo requis : {required}\n"
                f"  ❌ Mission bloquée - cargo insuffisant\n\n"
            )
            continue
            
        progress_bar = "█" * int((m["progress"] / max(1, m["target"])) * 10)
        progress_bar = progress_bar[:10].ljust(10, "░")
        status = "✅" if m["completed"] else f"[{progress_bar}]"
        text += (
            f"{status} **{m['mission_name']}**\n"
            f"  Progress : {m['progress']}/{m['target']}\n"
            f"  Récompense : {fmt(m['reward'])} + {m['xp_reward']} XP\n"
        )
        if is_delivery:
            text += f"  🚛 Cargo requis : {m.get('cargo_required', 50)}\n"
        text += "\n"

    completed = len([m for m in all_missions if m["completed"]])
    total = len(all_missions)
    
    # Afficher les missions bloquées pour information
    blocked_deliveries = [m for m in daily + weekly if (m.get("mission_type") == "delivery" or "livraison" in m.get("mission_name", "").lower()) and vehicle_cargo < m.get("cargo_required", 50)]
    if blocked_deliveries:
        text += f"\n🔒 **Missions bloquées :** {len(blocked_deliveries)}\n"
        text += "_Améliore ton cargo avec un véhicule plus grand !_\n"
    
    text += f"\n📊 Complétées : {completed}/{total}"
    await update.message.reply_text(text, parse_mode="Markdown")

@require_registered
@require_free
async def cmd_missions_completed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT mission_name, period, completed FROM missions WHERE user_id=? AND completed=1 ORDER BY completed_at DESC LIMIT 10",
            (user.id,)
        ) as cur:
            completed = await cur.fetchall()
    if not completed:
        await update.message.reply_text("📋 Tu n'as encore terminé aucune mission.")
        return
    text = "🏆 **Missions terminées récemment**\n\n"
    for m in completed:
        text += f"✅ {m['mission_name']} ({m['period']})\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ─────────────────────────────────────────────────────────────────────────────
# Fonction interne pour créer les missions (basées sur le temps de jeu)
# ─────────────────────────────────────────────────────────────────────────────
async def ensure_missions(user_id: int):
    """Crée les missions quotidiennes/hebdomadaires si elles n'existent pas, avec reset basé sur le temps de jeu."""
    async with aiosqlite.connect(DB_PATH) as db:
        today_reset = get_game_day_reset()
        week_reset = get_game_week_reset()

        for m in DAILY_MISSIONS:
            async with db.execute(
                "SELECT 1 FROM missions WHERE user_id=? AND mission_name=? AND period='daily' AND reset_at=?",
                (user_id, m["name"], today_reset)
            ) as cur:
                if await cur.fetchone():
                    continue
            await db.execute(
                "DELETE FROM missions WHERE user_id=? AND mission_name=? AND period='daily'",
                (user_id, m["name"])
            )
            await db.execute(
                "INSERT INTO missions (user_id, mission_name, mission_type, progress, target, reward, xp_reward, completed, period, reset_at, completed_at) VALUES (?,?,?,0,?,?,?,0,'daily',?,0)",
                (user_id, m["name"], m["type"], m["target"], m["reward"], m["xp"], today_reset)
            )

        for m in WEEKLY_MISSIONS:
            async with db.execute(
                "SELECT 1 FROM missions WHERE user_id=? AND mission_name=? AND period='weekly' AND reset_at=?",
                (user_id, m["name"], week_reset)
            ) as cur:
                if await cur.fetchone():
                    continue
            await db.execute(
                "DELETE FROM missions WHERE user_id=? AND mission_name=? AND period='weekly'",
                (user_id, m["name"])
            )
            await db.execute(
                "INSERT INTO missions (user_id, mission_name, mission_type, progress, target, reward, xp_reward, completed, period, reset_at, completed_at) VALUES (?,?,?,0,?,?,?,0,'weekly',?,0)",
                (user_id, m["name"], m["type"], m["target"], m["reward"], m["xp"], week_reset)
            )

        await db.commit()

# ─────────────────────────────────────────────────────────────────────────────
# Mise à jour de la progression (appelée depuis d'autres handlers)
# ─────────────────────────────────────────────────────────────────────────────
async def update_mission_progress(user_id: int, mission_type: str, amount: int = 1):
    """Met à jour la progression d'une mission (appelée depuis d'autres handlers)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM missions WHERE user_id=? AND mission_type=? AND completed=0",
            (user_id, mission_type)
        ) as cur:
            missions = [dict(m) for m in await cur.fetchall()]

        # Récupérer le véhicule actif pour vérifier les missions de livraison
        active_vehicle = await get_active_vehicle(user_id)
        vehicle_cargo = active_vehicle.get("cargo", 0) if active_vehicle else 0

        for m in missions:
            # Vérifier si c'est une mission de livraison et si le cargo est suffisant
            is_delivery = m.get("mission_type") == "delivery" or "livraison" in m.get("mission_name", "").lower()
            if is_delivery:
                required_cargo = m.get("cargo_required", 50)
                if vehicle_cargo < required_cargo:
                    # Ne pas progresser si le cargo est insuffisant
                    continue

            new_progress = m["progress"]
            if m["mission_type"] == "balance":
                # Pour 'balance', on fixe la progression au montant fourni (le solde)
                new_progress = amount
            else:
                new_progress = m["progress"] + amount
            completed = 1 if new_progress >= m["target"] else 0
            completed_at = now() if completed else 0
            await db.execute(
                "UPDATE missions SET progress=?, completed=?, completed_at=? WHERE user_id=? AND mission_name=? AND period=?",
                (min(new_progress, m["target"]), completed, completed_at, user_id, m["mission_name"], m["period"])
            )
            if completed and not m["completed"]:
                # Accorder la récompense monétaire et XP
                await db.execute(
                    "UPDATE users SET balance=balance+?, xp=xp+?, missions_done=missions_done+1 WHERE user_id=?",
                    (m["reward"], m["xp_reward"], user_id)
                )
                # Notifier la compétition du gain d'XP
                await on_xp_gain(user_id, m["xp_reward"])
                # Log optionnel
                await db.execute(
                    "INSERT INTO mission_log (user_id, mission_name, reward, xp, completed_at) VALUES (?,?,?,?,?)",
                    (user_id, m["mission_name"], m["reward"], m["xp_reward"], now())
                )
                
                # ─────────────────────────────────────────────────────────
                # PHASE 1 : Ajout d'un item aléatoire en récompense de mission
                # ─────────────────────────────────────────────────────────
                if random.random() < 0.4:
                    if m["period"] == "weekly":
                        item = await add_random_item(user_id, "rare", "legendary")
                    else:
                        item = await add_random_item(user_id, "common", "epic")
                    if item:
                        from database import add_life_journal
                        await add_life_journal(
                            user_id, "mission",
                            f"Mission '{m['mission_name']}' accomplie ! Récompense bonus : {item}",
                            severity="success"
                        )
                # ─────────────────────────────────────────────────────────

        await db.commit()

# ─────────────────────────────────────────────────────────────────────────────
# Mise à jour de la mission "balance" (solde)
# ─────────────────────────────────────────────────────────────────────────────
async def update_balance_mission(user_id: int):
    """Met à jour la mission de type 'balance' avec le solde actuel."""
    u = await get_user(user_id)
    balance = u.get("balance", 0)
    await update_mission_progress(user_id, "balance", balance)

# ─────────────────────────────────────────────────────────────────────────────
# Réinitialisation automatique (appelée par le scheduler)
# ─────────────────────────────────────────────────────────────────────────────
async def reset_daily_missions():
    """Supprime les missions quotidiennes expirées (basé sur le temps de jeu)."""
    today_reset = get_game_day_reset()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM missions WHERE period='daily' AND reset_at < ?", (today_reset,))
        await db.commit()

async def reset_weekly_missions():
    """Supprime les missions hebdomadaires expirées (basé sur le temps de jeu)."""
    week_reset = get_game_week_reset()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM missions WHERE period='weekly' AND reset_at < ?", (week_reset,))
        await db.commit()

# ─────────────────────────────────────────────────────────────────────────────
# Commande admin pour forcer la réinitialisation
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_resetmissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force la réinitialisation des missions (admin seulement)."""
    user = update.effective_user
    from config import ADMIN_IDS
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Commande réservée aux administrateurs.")
        return
    await reset_daily_missions()
    await reset_weekly_missions()
    await update.message.reply_text("✅ Missions réinitialisées manuellement.")

# ─────────────────────────────────────────────────────────────────────────────
# Fonction pour récupérer les missions disponibles avec filtrage cargo
# ─────────────────────────────────────────────────────────────────────────────
async def get_missions_with_cargo_filter(user_id: int) -> dict:
    """
    Retourne un dictionnaire avec les missions disponibles et bloquées selon le cargo.
    """
    await ensure_missions(user_id)
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM missions WHERE user_id=? ORDER BY completed ASC, period ASC",
            (user_id,)
        ) as cur:
            all_missions = [dict(m) for m in await cur.fetchall()]
    
    active_vehicle = await get_active_vehicle(user_id)
    vehicle_cargo = active_vehicle.get("cargo", 0) if active_vehicle else 0
    
    available = []
    blocked = []
    
    for mission in all_missions:
        is_delivery = mission.get("mission_type") == "delivery" or "livraison" in mission.get("mission_name", "").lower()
        if is_delivery:
            required_cargo = mission.get("cargo_required", 50)
            if vehicle_cargo < required_cargo:
                blocked.append(mission)
                continue
        available.append(mission)
    
    return {
        "available": available,
        "blocked": blocked,
        "vehicle_cargo": vehicle_cargo,
        "has_vehicle": active_vehicle is not None
    }