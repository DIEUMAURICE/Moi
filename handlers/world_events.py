"""
handlers/world_events.py — Moteur d'Événements Mondiaux Dynamiques
═══════════════════════════════════════════════════════════════════════
Système d'événements globaux affectant tous les joueurs simultanément.
Crises économiques, pandémies, booms technologiques, catastrophes naturelles, etc.
"""

import aiosqlite
import random
import asyncio
import logging
import json
from telegram import Update
from telegram.ext import ContextTypes
from database import db_connection, now
from utils.helpers import fmt_money, clamp
from utils.aesthetics import card, alert
from utils.helpers import fmt_time
from utils.decorators import require_registered as user_required

logger = logging.getLogger(__name__)

# ─── CATALOGUE D'ÉVÉNEMENTS MONDIAUX ────────────────────────────────
WORLD_EVENTS = {
    "economic_boom": {
        "name": "🚀 Boom Économique",
        "description": "L'économie mondiale explose! Les revenus augmentent.",
        "duration": 86400 * 7,  # 7 jours
        "effects": {
            "wealth_mult": 1.30,
            "job_salary_mult": 1.25,
            "market_volatility": 0.05,
        },
        "probability": 0.15,
    },
    "economic_crisis": {
        "name": "📉 Crise Économique",
        "description": "La bourse s'effondre! Les revenus diminuent.",
        "duration": 86400 * 5,
        "effects": {
            "wealth_mult": 0.70,
            "job_salary_mult": 0.80,
            "market_volatility": 0.35,
            "unemployment_risk": 0.10,
        },
        "probability": 0.10,
    },
    "pandemic": {
        "name": "🦠 Pandémie Mondiale",
        "description": "Une maladie se propage. La santé diminue, le travail est limité.",
        "duration": 86400 * 14,
        "effects": {
            "health_loss": 15,
            "work_cooldown_mult": 2.0,
            "hospital_risk": 0.20,
            "stress_mult": 1.40,
        },
        "probability": 0.08,
    },
    "tech_revolution": {
        "name": "💻 Révolution Technologique",
        "description": "Nouvelle technologie révolutionnaire! Les salaires tech augmentent.",
        "duration": 86400 * 10,
        "effects": {
            "tech_salary_mult": 1.50,
            "xp_mult": 1.20,
            "skill_learning_speed": 1.30,
        },
        "probability": 0.12,
    },
    "natural_disaster": {
        "name": "🌪️ Catastrophe Naturelle",
        "description": "Un tremblement de terre dévaste la région!",
        "duration": 86400 * 3,
        "effects": {
            "property_damage": 0.30,
            "health_loss": 20,
            "balance_loss": 0.15,
            "stress_mult": 2.0,
        },
        "probability": 0.05,
    },
    "gold_rush": {
        "name": "🥇 Ruée vers l'Or",
        "description": "Découverte d'une ressource précieuse! Fortunes à faire.",
        "duration": 86400 * 7,
        "effects": {
            "wealth_mult": 1.50,
            "mining_profit_mult": 2.0,
            "competition_intensity": 1.5,
        },
        "probability": 0.08,
    },
    "social_media_boom": {
        "name": "📱 Boom des Réseaux Sociaux",
        "description": "Les créateurs de contenu deviennent riches!",
        "duration": 86400 * 10,
        "effects": {
            "social_followers_mult": 1.40,
            "social_income_mult": 1.80,
            "content_virality": 1.50,
        },
        "probability": 0.10,
    },
    "crime_wave": {
        "name": "🔪 Vague de Criminalité",
        "description": "La criminalité augmente. Soyez prudents!",
        "duration": 86400 * 5,
        "effects": {
            "crime_success_mult": 1.30,
            "crime_reward_mult": 1.40,
            "crime_jail_mult": 1.50,
            "theft_risk": 0.15,
        },
        "probability": 0.09,
    },
    "peace_era": {
        "name": "☮️ Ère de Paix",
        "description": "Une période de paix et de stabilité commence.",
        "duration": 86400 * 14,
        "effects": {
            "happiness_mult": 1.25,
            "stress_reduction": 0.20,
            "crime_success_mult": 0.60,
            "health_recovery": 1.20,
        },
        "probability": 0.07,
    },
    "education_boom": {
        "name": "📚 Boom Éducatif",
        "description": "Les études deviennent plus accessibles et rentables!",
        "duration": 86400 * 10,
        "effects": {
            "study_cost_mult": 0.70,
            "diploma_salary_mult": 1.40,
            "xp_from_study_mult": 1.50,
        },
        "probability": 0.08,
    },
}

# ─── CRÉATION DE LA TABLE D'ÉVÉNEMENTS MONDIAUX ──────────────────────
async def init_world_events_table():
    """Initialise la table des événements mondiaux."""
    async with db_connection() as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS world_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            event_name TEXT NOT NULL,
            description TEXT,
            started_at INTEGER,
            expires_at INTEGER,
            effects TEXT DEFAULT '{}',
            active INTEGER DEFAULT 1,
            affected_users INTEGER DEFAULT 0
        )""")
        
        # Table pour tracker les événements vus par les joueurs
        await db.execute("""
        CREATE TABLE IF NOT EXISTS player_event_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            event_id INTEGER,
            notified_at INTEGER,
            UNIQUE(user_id, event_id)
        )""")
        
        await db.commit()


# ─── LANCER UN ÉVÉNEMENT MONDIAL ────────────────────────────────────
async def trigger_world_event(event_type: str = None):
    """Déclenche un événement mondial aléatoire."""
    
    # Choisir un événement aléatoire
    if event_type is None:
        event_type = random.choices(
            list(WORLD_EVENTS.keys()),
            weights=[e["probability"] for e in WORLD_EVENTS.values()]
        )[0]
    
    if event_type not in WORLD_EVENTS:
        logger.warning(f"⚠️ Type d'événement invalide: {event_type}")
        return None
    
    event_data = WORLD_EVENTS[event_type]
    current_time = now()
    expires_at = current_time + event_data["duration"]
    
    async with db_connection() as db:
        # Vérifier s'il y a déjà un événement actif du même type
        async with db.execute("""
        SELECT event_id FROM world_events 
        WHERE event_type = ? AND active = 1
        """, (event_type,)) as cur:
            if await cur.fetchone():
                logger.info(f"ℹ️ Événement {event_type} déjà actif")
                return None
        
        # Créer l'événement
        await db.execute("""
        INSERT INTO world_events 
        (event_type, event_name, description, started_at, expires_at, effects, active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (
            event_type,
            event_data["name"],
            event_data["description"],
            current_time,
            expires_at,
            json.dumps(event_data["effects"])
        ))
        
        await db.commit()
        
        logger.info(f"🌍 Événement déclenché: {event_data['name']}")
        return event_type


# ─── RÉCUPÉRER LES ÉVÉNEMENTS ACTIFS ────────────────────────────────
async def get_active_events() -> list:
    """Retourne la liste des événements mondiaux actuellement actifs."""
    async with db_connection() as db:
        async with db.execute("""
        SELECT event_id, event_type, event_name, description, started_at, 
               expires_at, effects
        FROM world_events 
        WHERE active = 1 AND expires_at > ?
        ORDER BY started_at DESC
        """, (now(),)) as cur:
            return await cur.fetchall()


# ─── APPLIQUER LES EFFETS D'UN ÉVÉNEMENT À UN JOUEUR ──────────────────
async def apply_event_effects(user_id: int, event_id: int, effects: dict):
    """Applique les effets d'un événement à un joueur."""
    async with db_connection() as db:
        async with db.execute("""
        SELECT balance, health, stress, happiness, energy, hunger
        FROM users WHERE user_id = ?
        """, (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return
        
        balance, health, stress, happiness, energy, hunger = row
        
        # Appliquer les multiplicateurs
        if "wealth_mult" in effects:
            balance = int(balance * effects["wealth_mult"])
        if "health_loss" in effects:
            health = clamp(health - effects["health_loss"], 0, 100)
        if "stress_mult" in effects:
            stress = clamp(stress * effects["stress_mult"], 0, 100)
        if "happiness_mult" in effects:
            happiness = clamp(happiness * effects["happiness_mult"], 0, 100)
        if "stress_reduction" in effects:
            stress = clamp(stress * (1 - effects["stress_reduction"]), 0, 100)
        
        # Mettre à jour le joueur
        await db.execute("""
        UPDATE users 
        SET balance = ?, health = ?, stress = ?, happiness = ?
        WHERE user_id = ?
        """, (balance, health, stress, happiness, user_id))
        
        # Enregistrer la notification
        await db.execute("""
        INSERT OR IGNORE INTO player_event_notifications 
        (user_id, event_id, notified_at)
        VALUES (?, ?, ?)
        """, (user_id, event_id, now()))
        
        await db.commit()


# ─── MAINTENANCE DES ÉVÉNEMENTS MONDIAUX ────────────────────────────
async def process_world_events_maintenance():
    """Maintenance périodique des événements mondiaux."""
    current_time = now()
    
    async with db_connection() as db:
        # Désactiver les événements expirés
        await db.execute("""
        UPDATE world_events 
        SET active = 0 
        WHERE expires_at <= ? AND active = 1
        """, (current_time,))
        
        # Vérifier si on doit déclencher un nouvel événement
        async with db.execute("""
        SELECT COUNT(*) FROM world_events WHERE active = 1
        """) as cur:
            count = (await cur.fetchone())[0]
        
        # Probabilité de déclencher un nouvel événement
        if count == 0 and random.random() < 0.30:
            await trigger_world_event()
        
        await db.commit()


# ─── COMMANDE : VOIR LES ÉVÉNEMENTS MONDIAUX ────────────────────────
@user_required
async def cmd_world_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les événements mondiaux actuellement actifs."""
    
    events = await get_active_events()
    
    if not events:
        await update.message.reply_text("🌍 **Événements Mondiaux**\n\nAucun événement actif pour le moment.")
        return
    
    text = "🌍 **Événements Mondiaux Actuels**\n\n"
    
    for event_id, event_type, event_name, description, started_at, expires_at, effects_str in events:
        remaining = expires_at - now()
        hours = remaining // 3600
        
        effects = json.loads(effects_str or "{}")
        
        text += f"""
**{event_name}**
{description}

"""
        
        # Afficher les effets principaux
        if "wealth_mult" in effects:
            text += f"💰 Revenus: ×{effects['wealth_mult']:.2f}\n"
        if "health_loss" in effects:
            text += f"❤️ Santé: -{effects['health_loss']}\n"
        if "stress_mult" in effects:
            text += f"😰 Stress: ×{effects['stress_mult']:.2f}\n"
        if "happiness_mult" in effects:
            text += f"😊 Bonheur: ×{effects['happiness_mult']:.2f}\n"
        
        text += f"\n⏱️ Durée restante: {hours}h\n\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── COMMANDE : HISTORIQUE DES ÉVÉNEMENTS ──────────────────────────
@user_required
async def cmd_events_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche l'historique des événements mondiaux."""
    
    async with db_connection() as db:
        async with db.execute("""
        SELECT event_id, event_name, description, started_at, expires_at
        FROM world_events 
        ORDER BY started_at DESC
        LIMIT 20
        """) as cur:
            events = await cur.fetchall()
    
    if not events:
        await update.message.reply_text("📜 Aucun événement enregistré.")
        return
    
    text = "📜 **Historique des Événements Mondiaux**\n\n"
    
    for event_id, event_name, description, started_at, expires_at in events:
        duration_hours = (expires_at - started_at) // 3600
        text += f"""
**{event_name}**
Durée: {duration_hours}h
Date: {fmt_time(started_at)}

"""
    
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── NOTIFICATION POUR LES JOUEURS ──────────────────────────────────
async def notify_players_of_event(bot, event_type: str):
    """Notifie tous les joueurs d'un nouvel événement."""
    if event_type not in WORLD_EVENTS:
        return
    
    event_data = WORLD_EVENTS[event_type]
    
    async with db_connection() as db:
        async with db.execute("SELECT user_id FROM users WHERE registered = 1") as cur:
            users = await cur.fetchall()
    
    message = f"""
🌍 **Événement Mondial: {event_data['name']}**

{event_data['description']}

Utilisez /world_events pour voir les détails et l'impact sur votre économie.
"""
    
    for (user_id,) in users:
        try:
            await bot.send_message(user_id, message, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"⚠️ Impossible de notifier {user_id}: {e}")
        await asyncio.sleep(0.1)  # Rate limiting


# ─── FONCTION D'INITIALISATION ──────────────────────────────────────
async def init_world_events():
    """Initialise le système d'événements mondiaux."""
    await init_world_events_table()
    logger.info("✅ Système d'événements mondiaux initialisé")
