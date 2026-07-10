"""
handlers/heritage.py — Système d'Héritage et Généalogie Dynamique
═══════════════════════════════════════════════════════════════════════
Permet aux joueurs de créer un héritage persistant où les actions des ancêtres
influencent les bonus des générations futures. Système de généalogie complète
avec arbre familial, legs, et bénédictions/malédictions héréditaires.
"""

import aiosqlite
import logging
from telegram import Update
from telegram.ext import ContextTypes
from database import db_connection, now
from utils.helpers import fmt_money, clamp
from utils.aesthetics import card, alert
from utils.helpers import fmt_time
from utils.decorators import require_registered as user_required

logger = logging.getLogger(__name__)

# ─── CONSTANTES ─────────────────────────────────────────────────────
HERITAGE_BONUSES = {
    "legacy_wealth": 0.15,          # 15% bonus sur les revenus passifs
    "legacy_xp": 0.10,              # 10% bonus XP
    "legacy_health": 5,             # +5 santé de base
    "legacy_luck": 0.05,            # 5% chance bonus
    "legacy_skill_boost": 1.2,      # 20% boost sur les compétences
}

LEGACY_MALUS = {
    "debt": -0.10,                  # -10% revenus si dettes
    "criminal": -0.15,              # -15% revenus si antécédents criminels
    "curse": -0.20,                 # -20% revenus si malédiction
}

# ─── CRÉATION DE LA TABLE D'HÉRITAGE ─────────────────────────────────
async def init_heritage_tables():
    """Initialise les tables d'héritage si elles n'existent pas."""
    async with db_connection() as db:
        # Table des générations
        await db.execute("""
        CREATE TABLE IF NOT EXISTS heritage_lineage (
            lineage_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            ancestor_id INTEGER DEFAULT 0,
            generation INTEGER DEFAULT 1,
            created_at INTEGER,
            legacy_score INTEGER DEFAULT 0,
            legacy_wealth_accumulated INTEGER DEFAULT 0,
            legacy_items TEXT DEFAULT '{}',
            blessings TEXT DEFAULT '[]',
            curses TEXT DEFAULT '[]'
        )""")
        
        # Table des legs (héritage)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS heritage_bequests (
            bequest_id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER,
            to_user_id INTEGER DEFAULT 0,
            amount INTEGER,
            items TEXT DEFAULT '{}',
            message TEXT DEFAULT '',
            created_at INTEGER,
            claimed_at INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending'
        )""")
        
        # Table des bénédictions/malédictions
        await db.execute("""
        CREATE TABLE IF NOT EXISTS heritage_effects (
            effect_id INTEGER PRIMARY KEY AUTOINCREMENT,
            lineage_id INTEGER,
            effect_type TEXT,
            name TEXT,
            description TEXT,
            multiplier REAL DEFAULT 1.0,
            duration INTEGER DEFAULT 0,
            created_at INTEGER,
            expires_at INTEGER DEFAULT 0
        )""")
        
        # Table de l'arbre généalogique
        await db.execute("""
        CREATE TABLE IF NOT EXISTS heritage_tree (
            tree_id INTEGER PRIMARY KEY AUTOINCREMENT,
            lineage_id INTEGER,
            parent_lineage_id INTEGER DEFAULT 0,
            generation_name TEXT DEFAULT '',
            achievements TEXT DEFAULT '[]',
            total_wealth_generated INTEGER DEFAULT 0,
            total_xp_generated INTEGER DEFAULT 0,
            notable_events TEXT DEFAULT '[]'
        )""")
        
        await db.commit()


# ─── INITIALISATION DE L'HÉRITAGE POUR UN NOUVEAU JOUEUR ────────────
async def init_player_heritage(user_id: int, ancestor_id: int = 0):
    """Initialise le dossier d'héritage pour un nouveau joueur."""
    async with db_connection() as db:
        # Vérifier si l'héritage existe déjà
        async with db.execute(
            "SELECT lineage_id FROM heritage_lineage WHERE user_id = ?",
            (user_id,)
        ) as cur:
            if await cur.fetchone():
                return
        
        generation = 1
        if ancestor_id > 0:
            # Récupérer la génération de l'ancêtre
            async with db.execute(
                "SELECT generation FROM heritage_lineage WHERE user_id = ?",
                (ancestor_id,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    generation = row[0] + 1
        
        await db.execute("""
        INSERT INTO heritage_lineage 
        (user_id, ancestor_id, generation, created_at)
        VALUES (?, ?, ?, ?)
        """, (user_id, ancestor_id, generation, now()))
        
        # Créer une entrée dans l'arbre généalogique
        async with db.execute(
            "SELECT lineage_id FROM heritage_lineage WHERE user_id = ?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                lineage_id = row[0]
                parent_lineage_id = 0
                if ancestor_id > 0:
                    async with db.execute(
                        "SELECT lineage_id FROM heritage_lineage WHERE user_id = ?",
                        (ancestor_id,)
                    ) as cur2:
                        parent_row = await cur2.fetchone()
                        if parent_row:
                            parent_lineage_id = parent_row[0]
                
                await db.execute("""
                INSERT INTO heritage_tree 
                (lineage_id, parent_lineage_id, generation_name)
                VALUES (?, ?, ?)
                """, (lineage_id, parent_lineage_id, f"Génération {generation}"))
        
        await db.commit()


# ─── CALCUL DES BONUS D'HÉRITAGE ────────────────────────────────────
async def calculate_heritage_bonuses(user_id: int) -> dict:
    """Calcule les bonus d'héritage applicables au joueur."""
    async with db_connection() as db:
        async with db.execute("""
        SELECT legacy_score, legacy_wealth_accumulated, blessings, curses
        FROM heritage_lineage WHERE user_id = ?
        """, (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return {"wealth_mult": 1.0, "xp_mult": 1.0, "health_bonus": 0}
        
        legacy_score, wealth_acc, blessings_str, curses_str = row
        
        bonuses = {
            "wealth_mult": 1.0,
            "xp_mult": 1.0,
            "health_bonus": 0,
            "luck_mult": 1.0,
            "skill_mult": 1.0,
        }
        
        # Bonus basé sur le score d'héritage
        if legacy_score > 0:
            bonuses["wealth_mult"] += min(0.50, legacy_score * 0.01)
            bonuses["xp_mult"] += min(0.30, legacy_score * 0.005)
            bonuses["health_bonus"] += min(20, legacy_score // 10)
        
        # Bonus de richesse accumulée
        if wealth_acc > 1_000_000:
            bonuses["wealth_mult"] += 0.10
        if wealth_acc > 10_000_000:
            bonuses["wealth_mult"] += 0.15
        
        # Appliquer les bénédictions/malédictions
        import json
        try:
            blessings = json.loads(blessings_str or "[]")
            curses = json.loads(curses_str or "[]")
            
            for blessing in blessings:
                if blessing == "prosperity":
                    bonuses["wealth_mult"] *= 1.25
                elif blessing == "wisdom":
                    bonuses["xp_mult"] *= 1.20
                elif blessing == "vitality":
                    bonuses["health_bonus"] += 15
                elif blessing == "fortune":
                    bonuses["luck_mult"] *= 1.30
            
            for curse in curses:
                if curse == "poverty":
                    bonuses["wealth_mult"] *= 0.75
                elif curse == "ignorance":
                    bonuses["xp_mult"] *= 0.80
                elif curse == "sickness":
                    bonuses["health_bonus"] -= 10
                elif curse == "misfortune":
                    bonuses["luck_mult"] *= 0.70
        except:
            pass
        
        return bonuses


# ─── COMMANDE : VOIR L'ARBRE GÉNÉALOGIQUE ───────────────────────────
@user_required
async def cmd_heritage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche l'arbre généalogique et l'héritage du joueur."""
    user_id = update.effective_user.id
    
    async with db_connection() as db:
        # Récupérer les infos d'héritage
        async with db.execute("""
        SELECT lineage_id, ancestor_id, generation, legacy_score, 
               legacy_wealth_accumulated, blessings, curses
        FROM heritage_lineage WHERE user_id = ?
        """, (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await init_player_heritage(user_id)
                await update.message.reply_text(
                    "🌳 **Héritage Initialisé**\n\n"
                    "Votre lignée commence aujourd'hui. Les actions que vous accomplissez "
                    "influenceront les générations futures.",
                    parse_mode="Markdown"
                )
                return
        
        lineage_id, ancestor_id, generation, legacy_score, wealth_acc, blessings_str, curses_str = row
        
        # Récupérer l'arbre généalogique
        async with db.execute("""
        SELECT parent_lineage_id, generation_name, achievements, 
               total_wealth_generated, total_xp_generated, notable_events
        FROM heritage_tree WHERE lineage_id = ?
        """, (lineage_id,)) as cur:
            tree_row = await cur.fetchone()
        
        # Construire le message
        import json
        blessings = json.loads(blessings_str or "[]")
        curses = json.loads(curses_str or "[]")
        
        text = f"""
🌳 **Arbre Généalogique - Génération {generation}**

**Informations de Lignée:**
• Score d'Héritage: {legacy_score} pts
• Richesse Accumulée: {fmt_money(wealth_acc)}
• Génération: {generation}

**Bénédictions:** {', '.join(blessings) if blessings else 'Aucune'}
**Malédictions:** {', '.join(curses) if curses else 'Aucune'}

**Bonus Actuels:**
"""
        
        bonuses = await calculate_heritage_bonuses(user_id)
        text += f"""
• Multiplicateur Richesse: ×{bonuses['wealth_mult']:.2f}
• Multiplicateur XP: ×{bonuses['xp_mult']:.2f}
• Bonus Santé: +{bonuses['health_bonus']}
• Multiplicateur Chance: ×{bonuses['luck_mult']:.2f}

**Commandes Disponibles:**
/leguer - Créer un legs pour la génération suivante
/bequests - Voir les legs en attente
/arbre - Voir l'arbre généalogique complet
"""
        
        await update.message.reply_text(text, parse_mode="Markdown")


# ─── COMMANDE : CRÉER UN LEGS ───────────────────────────────────────
@user_required
async def cmd_leguer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permet de créer un legs pour la génération suivante."""
    user_id = update.effective_user.id
    args = context.args
    
    if len(args) < 1:
        await update.message.reply_text(
            "Usage: /leguer <montant> [message]\n\n"
            "Exemple: /leguer 100000 Bonne chance pour ta vie!"
        )
        return
    
    try:
        amount = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide")
        return
    
    message = " ".join(args[1:]) if len(args) > 1 else ""
    
    async with db_connection() as db:
        # Vérifier le solde
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row or row[0] < amount:
                await update.message.reply_text("❌ Solde insuffisant")
                return
        
        # Créer le legs
        await db.execute("""
        INSERT INTO heritage_bequests 
        (from_user_id, amount, message, created_at, status)
        VALUES (?, ?, ?, ?, 'pending')
        """, (user_id, amount, message, now()))
        
        # Déduire le montant
        await db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (amount, user_id)
        )
        
        await db.commit()
        
        await update.message.reply_text(
            f"✅ **Legs Créé**\n\n"
            f"Montant: {fmt_money(amount)}\n"
            f"Message: {message or '(aucun)'}\n\n"
            f"Ce legs sera transmis à votre descendant lors de sa création."
        )


# ─── COMMANDE : VOIR LES LEGS EN ATTENTE ────────────────────────────
@user_required
async def cmd_bequests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les legs en attente pour le joueur."""
    user_id = update.effective_user.id
    
    async with db_connection() as db:
        async with db.execute("""
        SELECT bequest_id, from_user_id, amount, message, created_at
        FROM heritage_bequests 
        WHERE to_user_id = ? AND status = 'pending'
        ORDER BY created_at DESC
        """, (user_id,)) as cur:
            rows = await cur.fetchall()
        
        if not rows:
            await update.message.reply_text("📭 Aucun legs en attente")
            return
        
        text = "📜 **Legs en Attente**\n\n"
        for bequest_id, from_user_id, amount, message, created_at in rows:
            text += f"""
**Legs #{bequest_id}**
De: `{from_user_id}`
Montant: {fmt_money(amount)}
Message: {message or '(aucun)'}
Date: {fmt_time(created_at)}

"""
        
        await update.message.reply_text(text, parse_mode="Markdown")


# ─── COMMANDE : VOIR L'ARBRE GÉNÉALOGIQUE COMPLET ────────────────────
@user_required
async def cmd_arbre_genealogique(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche l'arbre généalogique complet."""
    user_id = update.effective_user.id
    
    async with db_connection() as db:
        async with db.execute("""
        SELECT lineage_id FROM heritage_lineage WHERE user_id = ?
        """, (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await update.message.reply_text("❌ Héritage non initialisé")
                return
        
        lineage_id = row[0]
        
        # Récupérer l'arbre complet
        async with db.execute("""
        SELECT parent_lineage_id, generation_name, achievements, 
               total_wealth_generated, total_xp_generated
        FROM heritage_tree WHERE lineage_id = ?
        """, (lineage_id,)) as cur:
            tree_row = await cur.fetchone()
        
        if not tree_row:
            await update.message.reply_text("❌ Arbre généalogique non trouvé")
            return
        
        parent_lineage_id, gen_name, achievements_str, wealth_gen, xp_gen = tree_row
        
        import json
        achievements = json.loads(achievements_str or "[]")
        
        text = f"""
🌳 **Arbre Généalogique**

**Génération Actuelle:** {gen_name}
**Richesse Générée:** {fmt_money(wealth_gen or 0)}
**XP Généré:** {xp_gen or 0}

**Accomplissements:**
"""
        
        if achievements:
            for ach in achievements:
                text += f"• {ach}\n"
        else:
            text += "• Aucun accomplissement enregistré\n"
        
        await update.message.reply_text(text, parse_mode="Markdown")


# ─── FONCTION D'AJOUT DE BÉNÉDICTION ────────────────────────────────
async def add_blessing(user_id: int, blessing_name: str, description: str = ""):
    """Ajoute une bénédiction à la lignée du joueur."""
    async with db_connection() as db:
        async with db.execute(
            "SELECT lineage_id FROM heritage_lineage WHERE user_id = ?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return False
        
        lineage_id = row[0]
        
        import json
        async with db.execute(
            "SELECT blessings FROM heritage_lineage WHERE lineage_id = ?",
            (lineage_id,)
        ) as cur:
            row = await cur.fetchone()
            blessings = json.loads(row[0] or "[]")
        
        if blessing_name not in blessings:
            blessings.append(blessing_name)
            await db.execute(
                "UPDATE heritage_lineage SET blessings = ? WHERE lineage_id = ?",
                (json.dumps(blessings), lineage_id)
            )
            await db.commit()
            return True
        return False


# ─── FONCTION D'AJOUT DE MALÉDICTION ────────────────────────────────
async def add_curse(user_id: int, curse_name: str, description: str = ""):
    """Ajoute une malédiction à la lignée du joueur."""
    async with db_connection() as db:
        async with db.execute(
            "SELECT lineage_id FROM heritage_lineage WHERE user_id = ?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return False
        
        lineage_id = row[0]
        
        import json
        async with db.execute(
            "SELECT curses FROM heritage_lineage WHERE lineage_id = ?",
            (lineage_id,)
        ) as cur:
            row = await cur.fetchone()
            curses = json.loads(row[0] or "[]")
        
        if curse_name not in curses:
            curses.append(curse_name)
            await db.execute(
                "UPDATE heritage_lineage SET curses = ? WHERE lineage_id = ?",
                (json.dumps(curses), lineage_id)
            )
            await db.commit()
            return True
        return False
