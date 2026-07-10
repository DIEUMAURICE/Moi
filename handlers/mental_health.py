"""
handlers/mental_health.py — Système de Santé Mentale Avancé
═══════════════════════════════════════════════════════════════════════
Gestion complète de la santé mentale avec dépression, anxiété, burnout,
thérapie, et conséquences narratives concrètes sur le gameplay.
"""

import aiosqlite
import random
import logging
from telegram import Update
from telegram.ext import ContextTypes
from database import db_connection, now
from utils.helpers import fmt_money, clamp
from utils.aesthetics import card, alert
from utils.helpers import fmt_time
from utils.decorators import require_registered as user_required

logger = logging.getLogger(__name__)

# ─── ÉTATS DE SANTÉ MENTALE ────────────────────────────────────────
MENTAL_STATES = {
    "excellent": {
        "name": "Excellent",
        "emoji": "😄",
        "stress_range": (0, 20),
        "effects": {"productivity": 1.20, "happiness_gain": 1.15},
    },
    "good": {
        "name": "Bon",
        "emoji": "😊",
        "stress_range": (20, 40),
        "effects": {"productivity": 1.05, "happiness_gain": 1.05},
    },
    "neutral": {
        "name": "Neutre",
        "emoji": "😐",
        "stress_range": (40, 60),
        "effects": {"productivity": 1.0, "happiness_gain": 1.0},
    },
    "stressed": {
        "name": "Stressé",
        "emoji": "😟",
        "stress_range": (60, 75),
        "effects": {"productivity": 0.85, "happiness_gain": 0.90, "health_loss": 1},
    },
    "anxious": {
        "name": "Anxieux",
        "emoji": "😰",
        "stress_range": (75, 85),
        "effects": {"productivity": 0.70, "happiness_gain": 0.75, "health_loss": 2, "sleep_quality": 0.7},
    },
    "burnout": {
        "name": "Burnout",
        "emoji": "😵",
        "stress_range": (85, 100),
        "effects": {"productivity": 0.50, "happiness_gain": 0.50, "health_loss": 3, "sleep_quality": 0.5, "work_cooldown": 2.0},
    },
    "depression": {
        "name": "Dépression",
        "emoji": "😢",
        "stress_range": (90, 100),
        "effects": {"productivity": 0.30, "happiness_gain": 0.30, "health_loss": 5, "sleep_quality": 0.3, "work_cooldown": 3.0, "hospitalization_risk": 0.10},
    },
}

# ─── CRÉATION DES TABLES DE SANTÉ MENTALE ───────────────────────────
async def init_mental_health_tables():
    """Initialise les tables de santé mentale."""
    async with db_connection() as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mental_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            stress_level INTEGER DEFAULT 0,
            anxiety_level INTEGER DEFAULT 0,
            depression_level INTEGER DEFAULT 0,
            burnout_level INTEGER DEFAULT 0,
            mental_state TEXT DEFAULT 'neutral',
            last_therapy INTEGER DEFAULT 0,
            therapy_sessions INTEGER DEFAULT 0,
            coping_strategies TEXT DEFAULT '[]',
            triggers TEXT DEFAULT '[]',
            last_crisis INTEGER DEFAULT 0
        )""")
        
        # Table pour les événements de santé mentale
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mental_health_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            event_type TEXT,
            description TEXT,
            severity INTEGER,
            occurred_at INTEGER,
            resolved_at INTEGER DEFAULT 0
        )""")
        
        # Table pour les thérapies
        await db.execute("""
        CREATE TABLE IF NOT EXISTS therapy_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            therapist_type TEXT,
            cost INTEGER,
            effectiveness REAL DEFAULT 0.5,
            session_date INTEGER,
            notes TEXT DEFAULT ''
        )""")
        
        await db.commit()


# ─── INITIALISER LA SANTÉ MENTALE D'UN JOUEUR ──────────────────────
async def init_player_mental_health(user_id: int):
    """Initialise le dossier de santé mentale pour un nouveau joueur."""
    async with db_connection() as db:
        async with db.execute(
            "SELECT id FROM mental_health WHERE user_id = ?",
            (user_id,)
        ) as cur:
            if await cur.fetchone():
                return
        
        await db.execute("""
        INSERT INTO mental_health (user_id)
        VALUES (?)
        """, (user_id,))
        
        await db.commit()


# ─── DÉTERMINER L'ÉTAT MENTAL ──────────────────────────────────────
def get_mental_state(stress: int) -> str:
    """Détermine l'état mental basé sur le niveau de stress."""
    for state, data in MENTAL_STATES.items():
        low, high = data["stress_range"]
        if low <= stress < high:
            return state
    return "depression"


# ─── APPLIQUER LES EFFETS DE SANTÉ MENTALE ─────────────────────────
async def apply_mental_health_effects(user_id: int) -> dict:
    """Applique les effets de santé mentale à un joueur."""
    
    async with db_connection() as db:
        async with db.execute("""
        SELECT stress_level, anxiety_level, depression_level, burnout_level, mental_state
        FROM mental_health WHERE user_id = ?
        """, (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await init_player_mental_health(user_id)
                return {}
        
        stress, anxiety, depression, burnout, mental_state = row
        
        # Recalculer l'état mental
        new_state = get_mental_state(stress)
        if new_state != mental_state:
            await db.execute(
                "UPDATE mental_health SET mental_state = ? WHERE user_id = ?",
                (new_state, user_id)
            )
            await db.commit()
        
        # Récupérer les effets
        state_data = MENTAL_STATES.get(new_state, {})
        effects = state_data.get("effects", {})
        
        return effects


# ─── AUGMENTER LE STRESS ────────────────────────────────────────────
async def increase_stress(user_id: int, amount: int, reason: str = ""):
    """Augmente le stress d'un joueur."""
    async with db_connection() as db:
        async with db.execute(
            "SELECT stress_level FROM mental_health WHERE user_id = ?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                await init_player_mental_health(user_id)
                return
        
        new_stress = clamp(row[0] + amount, 0, 100)
        
        await db.execute(
            "UPDATE mental_health SET stress_level = ? WHERE user_id = ?",
            (new_stress, user_id)
        )
        
        # Enregistrer l'événement
        if amount > 10:
            await db.execute("""
            INSERT INTO mental_health_events 
            (user_id, event_type, description, severity, occurred_at)
            VALUES (?, 'stress_increase', ?, ?, ?)
            """, (user_id, reason or "Augmentation du stress", min(5, amount // 10), now()))
        
        await db.commit()


# ─── DIMINUER LE STRESS ─────────────────────────────────────────────
async def decrease_stress(user_id: int, amount: int):
    """Diminue le stress d'un joueur."""
    async with db_connection() as db:
        async with db.execute(
            "SELECT stress_level FROM mental_health WHERE user_id = ?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                await init_player_mental_health(user_id)
                return
        
        new_stress = clamp(row[0] - amount, 0, 100)
        
        await db.execute(
            "UPDATE mental_health SET stress_level = ? WHERE user_id = ?",
            (new_stress, user_id)
        )
        
        await db.commit()


# ─── COMMANDE : VOIR L'ÉTAT DE SANTÉ MENTALE ────────────────────────
@user_required
async def cmd_mental_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche l'état de santé mentale du joueur."""
    
    user_id = update.effective_user.id
    
    async with db_connection() as db:
        async with db.execute("""
        SELECT stress_level, anxiety_level, depression_level, burnout_level, 
               mental_state, therapy_sessions, last_therapy
        FROM mental_health WHERE user_id = ?
        """, (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await init_player_mental_health(user_id)
                await update.message.reply_text("🧠 Santé mentale initialisée")
                return
        
        stress, anxiety, depression, burnout, mental_state, therapy_sessions, last_therapy = row
        
        state_data = MENTAL_STATES.get(mental_state, {})
        emoji = state_data.get("emoji", "😐")
        state_name = state_data.get("name", "Inconnu")
        
        # Créer une barre de stress visuelle
        stress_bar = "█" * (stress // 10) + "░" * (10 - stress // 10)
        
        text = f"""
🧠 **État de Santé Mentale**

**État Actuel:** {emoji} {state_name}

**Niveaux:**
Stress: {stress_bar} {stress}%
Anxiété: {anxiety}%
Dépression: {depression}%
Burnout: {burnout}%

**Thérapie:**
Sessions complétées: {therapy_sessions}
Dernière session: {fmt_time(last_therapy) if last_therapy else 'Aucune'}

**Conseils:**
"""
        
        if stress > 70:
            text += "⚠️ Votre stress est très élevé. Consultez un thérapeute!\n"
        if anxiety > 60:
            text += "⚠️ Votre anxiété est importante. Essayez la méditation.\n"
        if depression > 50:
            text += "⚠️ Vous montrez des signes de dépression. Cherchez de l'aide.\n"
        
        text += "\n**Commandes:**\n/therapy - Consulter un thérapeute\n/meditation - Méditer\n/activities - Activités relaxantes"
        
        await update.message.reply_text(text, parse_mode="Markdown")


# ─── COMMANDE : CONSULTER UN THÉRAPEUTE ────────────────────────────
@user_required
async def cmd_therapy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permet au joueur de consulter un thérapeute."""
    
    user_id = update.effective_user.id
    
    therapists = {
        "psychologue": {"cost": 5_000, "effectiveness": 0.8},
        "psychiatre": {"cost": 15_000, "effectiveness": 0.9},
        "coach_vie": {"cost": 3_000, "effectiveness": 0.6},
    }
    
    if not context.args:
        text = "🧑‍⚕️ **Thérapeutes Disponibles**\n\n"
        for name, data in therapists.items():
            text += f"• {name}: {fmt_money(data['cost'])}\n"
        text += "\nUsage: /therapy <type>"
        await update.message.reply_text(text)
        return
    
    therapist_type = context.args[0].lower()
    
    if therapist_type not in therapists:
        await update.message.reply_text("❌ Thérapeute inconnu")
        return
    
    therapist_data = therapists[therapist_type]
    cost = therapist_data["cost"]
    effectiveness = therapist_data["effectiveness"]
    
    async with db_connection() as db:
        # Vérifier le solde
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row or row[0] < cost:
                await update.message.reply_text("❌ Solde insuffisant")
                return
        
        # Déduire le coût
        await db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (cost, user_id)
        )
        
        # Enregistrer la session
        await db.execute("""
        INSERT INTO therapy_sessions 
        (user_id, therapist_type, cost, effectiveness, session_date)
        VALUES (?, ?, ?, ?, ?)
        """, (user_id, therapist_type, cost, effectiveness, now()))
        
        # Réduire le stress
        stress_reduction = int(20 * effectiveness)
        await db.execute("""
        UPDATE mental_health 
        SET stress_level = MAX(0, stress_level - ?),
            therapy_sessions = therapy_sessions + 1,
            last_therapy = ?
        WHERE user_id = ?
        """, (stress_reduction, now(), user_id))
        
        await db.commit()
        
        await update.message.reply_text(
            f"✅ **Thérapie Complétée**\n\n"
            f"Thérapeute: {therapist_type}\n"
            f"Coût: {fmt_money(cost)}\n"
            f"Stress réduit de: {stress_reduction}%\n\n"
            f"Vous vous sentez mieux!"
        )


# ─── COMMANDE : MÉDITER ────────────────────────────────────────────
@user_required
async def cmd_meditation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permet au joueur de méditer pour réduire le stress."""
    
    user_id = update.effective_user.id
    
    async with db_connection() as db:
        async with db.execute(
            "SELECT stress_level FROM mental_health WHERE user_id = ?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                await init_player_mental_health(user_id)
                return
        
        stress_reduction = random.randint(5, 15)
        new_stress = clamp(row[0] - stress_reduction, 0, 100)
        
        await db.execute(
            "UPDATE mental_health SET stress_level = ? WHERE user_id = ?",
            (new_stress, user_id)
        )
        
        # Augmenter le bonheur
        await db.execute(
            "UPDATE users SET happiness = MIN(100, happiness + 5) WHERE user_id = ?",
            (user_id,)
        )
        
        await db.commit()
        
        await update.message.reply_text(
            f"🧘 **Méditation Complétée**\n\n"
            f"Stress réduit de: {stress_reduction}%\n"
            f"Bonheur augmenté de: 5%\n\n"
            f"Vous vous sentez plus calme et centré."
        )


# ─── COMMANDE : ACTIVITÉS RELAXANTES ───────────────────────────────
@user_required
async def cmd_relaxing_activities(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Propose des activités relaxantes."""
    
    user_id = update.effective_user.id
    
    activities = [
        {"name": "Yoga", "cost": 1_000, "stress_reduction": 10, "happiness_gain": 5},
        {"name": "Massage", "cost": 3_000, "stress_reduction": 15, "happiness_gain": 10},
        {"name": "Spa", "cost": 5_000, "stress_reduction": 20, "happiness_gain": 15},
        {"name": "Promenade", "cost": 0, "stress_reduction": 5, "happiness_gain": 3},
        {"name": "Jeu vidéo", "cost": 500, "stress_reduction": 8, "happiness_gain": 8},
    ]
    
    if not context.args:
        text = "🧘 **Activités Relaxantes**\n\n"
        for i, activity in enumerate(activities, 1):
            text += f"{i}. {activity['name']}: {fmt_money(activity['cost'])}\n"
        text += "\nUsage: /activities <numéro>"
        await update.message.reply_text(text)
        return
    
    try:
        activity_idx = int(context.args[0]) - 1
        if activity_idx < 0 or activity_idx >= len(activities):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Activité invalide")
        return
    
    activity = activities[activity_idx]
    cost = activity["cost"]
    
    async with db_connection() as db:
        # Vérifier le solde
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row or row[0] < cost:
                await update.message.reply_text("❌ Solde insuffisant")
                return
        
        # Déduire le coût et appliquer les effets
        await db.execute(
            "UPDATE users SET balance = balance - ?, happiness = MIN(100, happiness + ?) WHERE user_id = ?",
            (cost, activity["happiness_gain"], user_id)
        )
        
        await db.execute(
            "UPDATE mental_health SET stress_level = MAX(0, stress_level - ?) WHERE user_id = ?",
            (activity["stress_reduction"], user_id)
        )
        
        await db.commit()
        
        await update.message.reply_text(
            f"✅ **{activity['name']} Complété**\n\n"
            f"Coût: {fmt_money(cost)}\n"
            f"Stress réduit de: {activity['stress_reduction']}%\n"
            f"Bonheur augmenté de: {activity['happiness_gain']}%"
        )


# ─── FONCTION D'INITIALISATION ──────────────────────────────────────
async def init_mental_health():
    """Initialise le système de santé mentale."""
    await init_mental_health_tables()
    logger.info("✅ Système de santé mentale initialisé")
