"""
handlers/specializations.py — Système de Compétences Spécialisées
═══════════════════════════════════════════════════════════════════════
Arbres de talents permettant aux joueurs de se spécialiser dans différents domaines:
- Maître Hacker (cybercriminalité)
- Magnat de l'Immobilier (propriétés)
- Gourou des Médias (réseaux sociaux)
- Roi du Crime (criminalité)
- Tycoon des Affaires (entreprises)
- Scientifique Brillant (recherche)
"""

import aiosqlite
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

# ─── ARBRES DE TALENTS ──────────────────────────────────────────────
SPECIALIZATION_TREES = {
    "hacker": {
        "name": "🖥️ Maître Hacker",
        "description": "Spécialiste en cybercriminalité et piratage informatique",
        "color": "🔴",
        "skills": {
            "level_1": {
                "name": "Initié",
                "cost": 5_000,
                "requirements": {"level": 5},
                "bonuses": {
                    "hack_success_mult": 1.15,
                    "hack_reward_mult": 1.10,
                },
            },
            "level_2": {
                "name": "Expert",
                "cost": 25_000,
                "requirements": {"level": 15, "prev_level": "level_1"},
                "bonuses": {
                    "hack_success_mult": 1.30,
                    "hack_reward_mult": 1.25,
                    "hack_detection_risk": 0.85,
                },
            },
            "level_3": {
                "name": "Maître",
                "cost": 100_000,
                "requirements": {"level": 30, "prev_level": "level_2"},
                "bonuses": {
                    "hack_success_mult": 1.50,
                    "hack_reward_mult": 1.50,
                    "hack_detection_risk": 0.70,
                },
            },
        },
    },
    "realestate": {
        "name": "🏢 Magnat de l'Immobilier",
        "description": "Expert en achat, vente et location de propriétés",
        "color": "🟦",
        "skills": {
            "level_1": {
                "name": "Apprenti",
                "cost": 5_000,
                "requirements": {"level": 5},
                "bonuses": {
                    "property_price_mult": 0.90,
                    "rental_income_mult": 1.15,
                },
            },
            "level_2": {
                "name": "Professionnel",
                "cost": 30_000,
                "requirements": {"level": 20, "prev_level": "level_1"},
                "bonuses": {
                    "property_price_mult": 0.80,
                    "rental_income_mult": 1.30,
                    "maintenance_cost_mult": 0.85,
                },
            },
            "level_3": {
                "name": "Magnat",
                "cost": 150_000,
                "requirements": {"level": 40, "prev_level": "level_2"},
                "bonuses": {
                    "property_price_mult": 0.70,
                    "rental_income_mult": 1.50,
                    "maintenance_cost_mult": 0.70,
                    "property_capacity": 10,
                },
            },
        },
    },
    "media": {
        "name": "📱 Gourou des Médias",
        "description": "Maître des réseaux sociaux et du contenu viral",
        "color": "🟪",
        "skills": {
            "level_1": {
                "name": "Créateur",
                "cost": 5_000,
                "requirements": {"level": 5},
                "bonuses": {
                    "followers_gain_mult": 1.20,
                    "content_virality_mult": 1.15,
                },
            },
            "level_2": {
                "name": "Influenceur",
                "cost": 35_000,
                "requirements": {"level": 25, "prev_level": "level_1"},
                "bonuses": {
                    "followers_gain_mult": 1.40,
                    "content_virality_mult": 1.35,
                    "sponsorship_income_mult": 1.50,
                },
            },
            "level_3": {
                "name": "Gourou",
                "cost": 200_000,
                "requirements": {"level": 45, "prev_level": "level_2"},
                "bonuses": {
                    "followers_gain_mult": 1.60,
                    "content_virality_mult": 1.60,
                    "sponsorship_income_mult": 2.00,
                },
            },
        },
    },
    "crime": {
        "name": "🔪 Roi du Crime",
        "description": "Maître de la criminalité et des opérations illégales",
        "color": "🟥",
        "skills": {
            "level_1": {
                "name": "Débutant",
                "cost": 10_000,
                "requirements": {"level": 10},
                "bonuses": {
                    "crime_success_mult": 1.20,
                    "crime_reward_mult": 1.15,
                },
            },
            "level_2": {
                "name": "Criminel",
                "cost": 50_000,
                "requirements": {"level": 30, "prev_level": "level_1"},
                "bonuses": {
                    "crime_success_mult": 1.40,
                    "crime_reward_mult": 1.35,
                    "jail_time_mult": 0.80,
                },
            },
            "level_3": {
                "name": "Roi",
                "cost": 250_000,
                "requirements": {"level": 50, "prev_level": "level_2"},
                "bonuses": {
                    "crime_success_mult": 1.60,
                    "crime_reward_mult": 1.60,
                    "jail_time_mult": 0.60,
                },
            },
        },
    },
    "business": {
        "name": "💼 Tycoon des Affaires",
        "description": "Entrepreneur et gestionnaire d'entreprises",
        "color": "🟩",
        "skills": {
            "level_1": {
                "name": "Entrepreneur",
                "cost": 8_000,
                "requirements": {"level": 8},
                "bonuses": {
                    "company_revenue_mult": 1.20,
                    "employee_productivity_mult": 1.10,
                },
            },
            "level_2": {
                "name": "Directeur",
                "cost": 40_000,
                "requirements": {"level": 25, "prev_level": "level_1"},
                "bonuses": {
                    "company_revenue_mult": 1.40,
                    "employee_productivity_mult": 1.25,
                    "company_level_gain_mult": 1.30,
                },
            },
            "level_3": {
                "name": "Tycoon",
                "cost": 200_000,
                "requirements": {"level": 45, "prev_level": "level_2"},
                "bonuses": {
                    "company_revenue_mult": 1.60,
                    "employee_productivity_mult": 1.50,
                    "company_level_gain_mult": 1.60,
                },
            },
        },
    },
    "science": {
        "name": "🔬 Scientifique Brillant",
        "description": "Chercheur et innovateur scientifique",
        "color": "🟨",
        "skills": {
            "level_1": {
                "name": "Chercheur",
                "cost": 8_000,
                "requirements": {"level": 8, "diplome": "Master"},
                "bonuses": {
                    "research_speed_mult": 1.20,
                    "xp_gain_mult": 1.15,
                },
            },
            "level_2": {
                "name": "Scientifique",
                "cost": 45_000,
                "requirements": {"level": 30, "prev_level": "level_1", "diplome": "Doctorat"},
                "bonuses": {
                    "research_speed_mult": 1.40,
                    "xp_gain_mult": 1.35,
                    "discovery_bonus": 1.50,
                },
            },
            "level_3": {
                "name": "Génie",
                "cost": 250_000,
                "requirements": {"level": 50, "prev_level": "level_2"},
                "bonuses": {
                    "research_speed_mult": 1.60,
                    "xp_gain_mult": 1.60,
                    "discovery_bonus": 2.00,
                },
            },
        },
    },
}

# ─── CRÉATION DE LA TABLE DES SPÉCIALISATIONS ───────────────────────
async def init_specialization_tables():
    """Initialise les tables de spécialisations."""
    async with db_connection() as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS player_specializations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            specialization_type TEXT,
            current_level TEXT DEFAULT 'level_0',
            xp_in_specialization INTEGER DEFAULT 0,
            unlocked_at INTEGER,
            UNIQUE(user_id, specialization_type)
        )""")
        
        # Table pour tracker les compétences débloquées
        await db.execute("""
        CREATE TABLE IF NOT EXISTS specialization_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            specialization_type TEXT,
            skill_level TEXT,
            unlocked_at INTEGER,
            UNIQUE(user_id, specialization_type, skill_level)
        )""")
        
        await db.commit()


# ─── DÉBLOQUER UNE SPÉCIALISATION ──────────────────────────────────
async def unlock_specialization(user_id: int, spec_type: str) -> bool:
    """Débloque une spécialisation pour un joueur."""
    
    if spec_type not in SPECIALIZATION_TREES:
        return False
    
    async with db_connection() as db:
        # Vérifier si déjà débloquée
        async with db.execute("""
        SELECT id FROM player_specializations 
        WHERE user_id = ? AND specialization_type = ?
        """, (user_id, spec_type)) as cur:
            if await cur.fetchone():
                return False
        
        # Créer la spécialisation
        await db.execute("""
        INSERT INTO player_specializations 
        (user_id, specialization_type, unlocked_at)
        VALUES (?, ?, ?)
        """, (user_id, spec_type, now()))
        
        await db.commit()
        return True


# ─── DÉBLOQUER UN NIVEAU DE COMPÉTENCE ─────────────────────────────
async def unlock_skill_level(user_id: int, spec_type: str, skill_level: str) -> bool:
    """Débloque un niveau de compétence pour une spécialisation."""
    
    if spec_type not in SPECIALIZATION_TREES:
        return False
    if skill_level not in SPECIALIZATION_TREES[spec_type]["skills"]:
        return False
    
    async with db_connection() as db:
        # Vérifier les prérequis
        async with db.execute("""
        SELECT balance, level, diplome FROM users WHERE user_id = ?
        """, (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return False
        
        balance, level, diplome = row
        
        skill_data = SPECIALIZATION_TREES[spec_type]["skills"][skill_level]
        requirements = skill_data.get("requirements", {})
        
        # Vérifier les prérequis
        if "level" in requirements and level < requirements["level"]:
            return False
        if "diplome" in requirements and diplome != requirements["diplome"]:
            return False
        if "prev_level" in requirements:
            async with db.execute("""
            SELECT id FROM specialization_progress 
            WHERE user_id = ? AND specialization_type = ? AND skill_level = ?
            """, (user_id, spec_type, requirements["prev_level"])) as cur:
                if not await cur.fetchone():
                    return False
        
        # Vérifier le coût
        cost = skill_data.get("cost", 0)
        if balance < cost:
            return False
        
        # Débloquer le niveau
        await db.execute("""
        INSERT OR IGNORE INTO specialization_progress 
        (user_id, specialization_type, skill_level, unlocked_at)
        VALUES (?, ?, ?, ?)
        """, (user_id, spec_type, skill_level, now()))
        
        # Déduire le coût
        await db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (cost, user_id)
        )
        
        # Mettre à jour le niveau actuel
        await db.execute("""
        UPDATE player_specializations 
        SET current_level = ?, xp_in_specialization = xp_in_specialization + 100
        WHERE user_id = ? AND specialization_type = ?
        """, (skill_level, user_id, spec_type))
        
        await db.commit()
        return True


# ─── RÉCUPÉRER LES BONUS D'UNE SPÉCIALISATION ──────────────────────
async def get_specialization_bonuses(user_id: int) -> dict:
    """Récupère tous les bonus de spécialisation d'un joueur."""
    
    bonuses = {}
    
    async with db_connection() as db:
        async with db.execute("""
        SELECT specialization_type, current_level 
        FROM player_specializations WHERE user_id = ?
        """, (user_id,)) as cur:
            specs = await cur.fetchall()
    
    for spec_type, current_level in specs:
        if spec_type not in SPECIALIZATION_TREES:
            continue
        
        if current_level == "level_0":
            continue
        
        skill_data = SPECIALIZATION_TREES[spec_type]["skills"].get(current_level, {})
        spec_bonuses = skill_data.get("bonuses", {})
        
        for key, value in spec_bonuses.items():
            if key not in bonuses:
                bonuses[key] = 1.0 if isinstance(value, float) else 0
            
            if isinstance(value, float):
                bonuses[key] *= value
            else:
                bonuses[key] += value
    
    return bonuses


# ─── COMMANDE : VOIR LES SPÉCIALISATIONS DISPONIBLES ─────────────────
@user_required
async def cmd_specializations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les spécialisations disponibles."""
    
    user_id = update.effective_user.id
    
    # Récupérer les spécialisations du joueur
    async with db_connection() as db:
        async with db.execute("""
        SELECT specialization_type FROM player_specializations WHERE user_id = ?
        """, (user_id,)) as cur:
            player_specs = [row[0] for row in await cur.fetchall()]
    
    text = "🎯 **Spécialisations Disponibles**\n\n"
    
    for spec_type, spec_data in SPECIALIZATION_TREES.items():
        status = "✅ Débloquée" if spec_type in player_specs else "🔒 Verrouillée"
        text += f"""
{spec_data['color']} **{spec_data['name']}**
{spec_data['description']}
{status}

"""
    
    text += "\nUtilisez `/spec_details <type>` pour voir les détails d'une spécialisation."
    
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── COMMANDE : VOIR LES DÉTAILS D'UNE SPÉCIALISATION ────────────────
@user_required
async def cmd_spec_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les détails d'une spécialisation."""
    
    if not context.args:
        await update.message.reply_text("Usage: /spec_details <type>")
        return
    
    spec_type = context.args[0].lower()
    
    if spec_type not in SPECIALIZATION_TREES:
        await update.message.reply_text("❌ Spécialisation inconnue")
        return
    
    spec_data = SPECIALIZATION_TREES[spec_type]
    
    text = f"""
{spec_data['color']} **{spec_data['name']}**
{spec_data['description']}

**Niveaux de Compétence:**

"""
    
    for level_key, level_data in spec_data["skills"].items():
        cost = level_data.get("cost", 0)
        reqs = level_data.get("requirements", {})
        bonuses = level_data.get("bonuses", {})
        
        text += f"""
**{level_data['name']}** ({level_key})
Coût: {fmt_money(cost)}
Prérequis: Niveau {reqs.get('level', 1)}
Bonus:
"""
        
        for bonus_key, bonus_value in bonuses.items():
            if isinstance(bonus_value, float):
                text += f"  • {bonus_key}: ×{bonus_value:.2f}\n"
            else:
                text += f"  • {bonus_key}: +{bonus_value}\n"
        
        text += "\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── COMMANDE : DÉBLOQUER UNE SPÉCIALISATION ───────────────────────
@user_required
async def cmd_unlock_spec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Débloque une spécialisation."""
    
    if not context.args:
        await update.message.reply_text("Usage: /unlock_spec <type>")
        return
    
    user_id = update.effective_user.id
    spec_type = context.args[0].lower()
    
    if spec_type not in SPECIALIZATION_TREES:
        await update.message.reply_text("❌ Spécialisation inconnue")
        return
    
    if await unlock_specialization(user_id, spec_type):
        spec_data = SPECIALIZATION_TREES[spec_type]
        await update.message.reply_text(
            f"✅ **{spec_data['name']}** débloquée!\n\n"
            f"Vous pouvez maintenant débloquer les niveaux de compétence."
        )
    else:
        await update.message.reply_text("❌ Impossible de débloquer cette spécialisation")


# ─── COMMANDE : DÉBLOQUER UN NIVEAU DE COMPÉTENCE ────────────────────
@user_required
async def cmd_unlock_skill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Débloque un niveau de compétence."""
    
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /unlock_skill <spec_type> <level>")
        return
    
    user_id = update.effective_user.id
    spec_type = context.args[0].lower()
    skill_level = context.args[1].lower()
    
    if await unlock_skill_level(user_id, spec_type, skill_level):
        await update.message.reply_text(f"✅ Niveau {skill_level} débloqué!")
    else:
        await update.message.reply_text("❌ Impossible de débloquer ce niveau")


# ─── FONCTION D'INITIALISATION ──────────────────────────────────────
async def init_specializations():
    """Initialise le système de spécialisations."""
    await init_specialization_tables()
    logger.info("✅ Système de spécialisations initialisé")
