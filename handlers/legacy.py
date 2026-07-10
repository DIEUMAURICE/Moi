# handlers/legacy.py
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_field, increment_field, now
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, get_level

# Configuration du système Legacy
LEGACY_TIERS = {
    1: {"min_level": 10, "prestige_cost": 0, "bonus_mult": 1.05, "name": "Réincarnation"},
    2: {"min_level": 25, "prestige_cost": 10, "bonus_mult": 1.10, "name": "Ascension"},
    3: {"min_level": 50, "prestige_cost": 50, "bonus_mult": 1.15, "name": "Transcendance"},
    4: {"min_level": 100, "prestige_cost": 200, "bonus_mult": 1.25, "name": "Déification"},
    5: {"min_level": 200, "prestige_cost": 500, "bonus_mult": 1.50, "name": "Éveil cosmique"},
}

@require_registered
@require_free
async def cmd_legacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le système d'héritage / réincarnation."""
    user = update.effective_user
    u = await get_user(user.id)
    
    current_legacy = u.get("legacy_level", 0)
    current_bonus = 1.0
    for tier in range(1, current_legacy + 1):
        current_bonus *= LEGACY_TIERS.get(tier, {}).get("bonus_mult", 1.0)
    
    text = "🌀 **Système d'Héritage / Réincarnation**\n\n"
    text += "_Renaissez plus fort après chaque vie !_\n\n"
    text += f"📊 **Niveau d'héritage actuel : {current_legacy}**\n"
    text += f"✨ **Bonus permanent : x{current_bonus:.2f} sur tous les gains**\n\n"
    
    next_tier = current_legacy + 1
    if next_tier in LEGACY_TIERS:
        tier_data = LEGACY_TIERS[next_tier]
        text += f"🔓 **Prochain palier : {tier_data['name']}**\n"
        text += f"   • Niveau requis : {tier_data['min_level']}\n"
        text += f"   • Coût prestige : {fmt(tier_data['prestige_cost'])}\n"
        text += f"   • Bonus gagné : x{tier_data['bonus_mult']}\n\n"
        
        if get_level(u["xp"]) >= tier_data["min_level"] and u.get("prestige", 0) >= tier_data["prestige_cost"]:
            text += "✅ **Tu es prêt à te réincarner !**\n"
            text += "👉 `/reincarnate` pour recommencer plus fort."
        else:
            if get_level(u["xp"]) < tier_data["min_level"]:
                text += f"⚠️ Niveau requis : {tier_data['min_level']} (actuel: {get_level(u['xp'])})\n"
            if u.get("prestige", 0) < tier_data["prestige_cost"]:
                text += f"⚠️ Prestige requis : {fmt(tier_data['prestige_cost'])} (actuel: {fmt(u.get('prestige', 0))})\n"
    else:
        text += "🏆 **Tu as atteint le niveau maximum d'héritage !**\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")

@require_registered
@require_free
async def cmd_reincarnate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Effectue une réincarnation."""
    user = update.effective_user
    u = await get_user(user.id)
    
    next_tier = u.get("legacy_level", 0) + 1
    if next_tier not in LEGACY_TIERS:
        await update.message.reply_text("🏆 Tu as déjà atteint le niveau maximum d'héritage !")
        return
    
    tier_data = LEGACY_TIERS[next_tier]
    
    if get_level(u["xp"]) < tier_data["min_level"]:
        await update.message.reply_text(f"❌ Niveau insuffisant. Niveau requis : {tier_data['min_level']}")
        return
    
    if u.get("prestige", 0) < tier_data["prestige_cost"]:
        await update.message.reply_text(f"❌ Prestige insuffisant. Coût : {fmt(tier_data['prestige_cost'])}")
        return
    
    # Confirmation
    if not context.args or context.args[0].lower() != "confirm":
        await update.message.reply_text(
            f"⚠️ **RÉINCARNATION VERS {tier_data['name']}**\n\n"
            f"Ceci va :\n"
            f"• Réinitialiser ton niveau à 1\n"
            f"• Réinitialiser ton XP à 0\n"
            f"• Réinitialiser ton argent à 10 000 coins\n"
            f"• Conserver ton héritage et ton bonus permanent\n\n"
            f"Pour confirmer : `/reincarnate confirm`"
        )
        return
    
    # Enregistrer l'héritage
    new_legacy = next_tier
    await update_field(user.id, "legacy_level", new_legacy)
    
    # Déduire le coût en prestige
    await increment_field(user.id, "prestige", -tier_data["prestige_cost"])
    
    # Réinitialiser (sauf legacy_level)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET 
                xp = 0, level = 1, balance = 10000,
                health = 100, energy = 100, happiness = 100, hunger = 100, stress = 0
            WHERE user_id = ?
        """, (user.id,))
        await db.commit()
    
    await update.message.reply_text(
        f"🌀 **RÉINCARNATION RÉUSSIE VERS {tier_data['name']} !**\n\n"
        f"✨ Nouveau bonus permanent : x{tier_data['bonus_mult']}\n"
        f"🌟 Niveau d'héritage : {new_legacy}\n\n"
        f"_Tu renais de tes cendres, plus fort qu'avant..._",
        parse_mode="Markdown"
    )