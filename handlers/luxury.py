# handlers/luxury.py
import random
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, increment_field, update_field
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, now
from config import LUXURY_ITEMS, PRESTIGE_RANKS, VIP_LUXE_REQUIRED, VIP_LIEUX
from handlers.missions import update_mission_progress
from handlers.vehicles import get_active_vehicle, get_vehicle_stats


@require_registered
async def cmd_luxe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    # Récupérer le véhicule actif pour le luxe
    active_vehicle = await get_active_vehicle(user.id)
    vehicle_luxe = active_vehicle.get("luxury", 0) if active_vehicle else 0
    
    # Message d'info sur le luxe du véhicule
    luxe_msg = ""
    if active_vehicle:
        luxe_msg = f"\n🚗 **Luxe de votre véhicule** : {vehicle_luxe}/100\n"
        if vehicle_luxe >= VIP_LUXE_REQUIRED:
            luxe_msg += "✅ **Accès VIP débloqué !** Tu as accès à tous les lieux prestigieux.\n"
        else:
            luxe_msg += f"🔒 Besoin de {VIP_LUXE_REQUIRED - vehicle_luxe} points de luxe pour l'accès VIP.\n"
    else:
        luxe_msg = "\n🚫 **Aucun véhicule actif** - Achetez ou sélectionnez un véhicule pour accéder aux lieux VIP.\n"

    text = "👑 **Boutique de Luxe**\n\n"
    text += luxe_msg + "\n"
    text += "━" * 30 + "\n\n"
    
    for item, data in LUXURY_ITEMS.items():
        text += (
            f"{data['emoji']} **{item}**\n"
            f"  💰 Prix : {fmt(data['price'])}\n"
            f"  ✨ Prestige : +{data['prestige']} pts\n"
            f"  {data.get('desc', '')}\n\n"
        )
    text += f"_Ton prestige actuel : {u.get('prestige', 0)} pts_\n"
    text += "_/acheterLuxe [article] pour acheter_\n"
    text += "_/acces_vip pour vérifier ton accès aux lieux prestigieux_"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
@require_free
@cooldown("luxury_buy", 5, "⏳ Attends quelques secondes avant de faire un autre achat.")
async def cmd_acheter_luxe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not context.args:
        await update.message.reply_text("Usage : /acheterLuxe [nom article]\n/luxe pour voir la liste.")
        return

    item_name = " ".join(context.args).title()
    matched = None
    for item in LUXURY_ITEMS:
        if item.lower() == item_name.lower():
            matched = item
            break
    if not matched:
        for item in LUXURY_ITEMS:
            if item_name.lower() in item.lower():
                matched = item
                break
    if not matched:
        await update.message.reply_text("❌ Article inconnu. /luxe pour voir la liste.")
        return

    data = LUXURY_ITEMS[matched]

    if u["balance"] < data["price"]:
        await update.message.reply_text(
            f"❌ Fonds insuffisants !\n"
            f"💰 Prix : {fmt(data['price'])}\n"
            f"💵 Ton solde : {fmt(u['balance'])}"
        )
        return

    await update_balance(user.id, -data["price"])
    new_prestige = u.get("prestige", 0) + data["prestige"]
    await update_field(user.id, "prestige", new_prestige)

    # ─────────────────────────────────────────────────────────────
    # 1. Créer ou récupérer l'item dans la table `items`
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Chercher si l'item existe déjà
        async with db.execute("SELECT item_id FROM items WHERE name = ?", (matched,)) as cur:
            row = await cur.fetchone()
        if row:
            item_id = row["item_id"]
        else:
            # Créer un nouvel item (type "luxury", rareté "epic" par défaut)
            await db.execute("""
                INSERT INTO items (name, type, rarity, value, effect_type, effect_value, emoji, description)
                VALUES (?, 'luxury', 'epic', ?, NULL, 0, ?, ?)
            """, (matched, data["price"], data["emoji"], f"Article de luxe : {matched}"))
            async with db.execute("SELECT last_insert_rowid()") as cur2:
                item_id = (await cur2.fetchone())[0]

        # 2. Ajouter l'item à l'inventaire du joueur (avec item_id)
        async with db.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?",
            (user.id, item_id)
        ) as cur3:
            existing = await cur3.fetchone()
        if existing:
            await db.execute(
                "UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_id = ?",
                (user.id, item_id)
            )
        else:
            # On conserve l'ancienne colonne item_type='Luxe' pour compatibilité avec les achievements
            await db.execute("""
                INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at)
                VALUES (?, ?, 'Luxe', ?, 1, ?)
            """, (user.id, item_id, matched, now()))
        await db.commit()

    # ─────────────────────────────────────────────────────────────
    await increment_field(user.id, "xp", 50)
    await increment_field(user.id, "karma", data.get("karma", 0))
    # Mise à jour de la mission "Dépenser 150K en luxe"
    await update_mission_progress(user.id, "luxury", data["price"])

    # Message bonus si c'est le 10e, 50e, 100e article (via comptage sur item_type='Luxe')
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT SUM(quantity) FROM inventory WHERE user_id=? AND item_type='Luxe'",
            (user.id,)
        ) as cur:
            total_items = (await cur.fetchone())[0] or 0
    achievement = ""
    if total_items == 10:
        achievement = "\n🏆 **10 articles de luxe** → +100 Prestige !"
        await increment_field(user.id, "prestige", 100)
    elif total_items == 50:
        achievement = "\n🏆 **50 articles de luxe** → +500 Prestige !"
        await increment_field(user.id, "prestige", 500)
    elif total_items == 100:
        achievement = "\n🏆 **100 articles de luxe** → +1000 Prestige !"
        await increment_field(user.id, "prestige", 1000)

    final_prestige = u.get("prestige", 0) + data["prestige"] + (100 if total_items == 10 else 500 if total_items == 50 else 1000 if total_items == 100 else 0)

    await update.message.reply_text(
        f"✨ **Article de luxe acquis !**\n\n"
        f"{data['emoji']} **{matched}**\n"
        f"💰 Prix : {fmt(data['price'])}\n"
        f"✨ Prestige : +{data['prestige']}\n"
        f"🌟 Karma : +{data.get('karma', 0)}\n"
        f"💎 Prestige total : {final_prestige}{achievement}\n\n"
        f"_Un vrai connaisseur !_",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_prestige(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    prestige = u.get("prestige", 0)

    # Détermination du rang
    rank = "🥚 Inconnu"
    for r in PRESTIGE_RANKS:
        if prestige >= r["min"]:
            rank = r["name"]
        else:
            break

    # Récupérer les articles de luxe
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT item_name, quantity FROM inventory WHERE user_id=? AND item_type='Luxe' ORDER BY acquired_at DESC",
            (user.id,)
        ) as cur:
            luxury = await cur.fetchall()

    lux_text = "\n".join(f"  • {l[0]} x{l[1]}" for l in luxury[:10]) if luxury else "  _Aucun article de luxe_"
    if len(luxury) > 10:
        lux_text += f"\n  _... et {len(luxury)-10} autres_"

    # Classement général des plus prestigieux (top 5)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT full_name, prestige FROM users WHERE prestige > 0 ORDER BY prestige DESC LIMIT 5"
        ) as cur:
            top = await cur.fetchall()
    top_text = ""
    if top:
        top_text = "\n🏆 **Top Prestige :**\n"
        for i, t in enumerate(top, 1):
            top_text += f"{i}. {t[0]} — {t[1]} pts\n"

    await update.message.reply_text(
        f"✨ **Prestige de {user.full_name}**\n\n"
        f"🎖️ Points : **{prestige}**\n"
        f"🏅 Rang : **{rank}**\n\n"
        f"👑 **Collection luxe :**\n{lux_text}\n\n"
        f"_Comment augmenter son prestige :_\n"
        f"• 🛒 Acheter des articles de luxe\n"
        f"• 🏠 Acheter des propriétés\n"
        f"• 🚗 Acheter des véhicules haut de gamme\n"
        f"• 🎓 Obtenir des diplômes\n"
        f"• 🏛️ Exercer un poste politique\n"
        f"{top_text}",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_classementprestige(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le classement des joueurs par prestige."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT full_name, prestige FROM users WHERE prestige > 0 ORDER BY prestige DESC LIMIT 20"
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        await update.message.reply_text("📊 Aucun joueur n'a encore de prestige.")
        return
    medals = ["🥇", "🥈", "🥉"] + ["✨"] * 17
    text = "👑 **Classement du Prestige**\n\n"
    for i, row in enumerate(rows[:20]):
        rank_disp = medals[i] if i < len(medals) else f"{i+1}."
        text += f"{rank_disp} **{row[0]}** — {row[1]} pts\n"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
async def cmd_prestigelog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche l'historique des achats de luxe (les 10 derniers)."""
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT item_name, acquired_at FROM inventory WHERE user_id=? AND item_type='Luxe' ORDER BY acquired_at DESC LIMIT 10",
            (user.id,)
        ) as cur:
            logs = await cur.fetchall()
    if not logs:
        await update.message.reply_text("📜 Aucun achat de luxe enregistré.")
        return
    from datetime import datetime
    text = "📜 **Derniers achats de luxe**\n\n"
    for l in logs:
        date = datetime.fromtimestamp(l[1]).strftime("%d/%m/%Y")
        text += f"• {l[0]} — le {date}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── ACCÈS VIP ─────────────────────────────────────────────

@require_registered
@require_free
async def cmd_acces_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Vérifie l'accès aux lieux VIP selon le luxe du véhicule."""
    user = update.effective_user
    
    # Récupérer le véhicule actif
    active_vehicle = await get_active_vehicle(user.id)
    
    if not active_vehicle:
        await update.message.reply_text(
            "🚫 **Accès VIP refusé**\n\n"
            "Vous devez posséder un véhicule pour accéder aux lieux prestigieux.\n"
            "Achetez un véhicule avec /acheterv ou sélectionnez-en un avec /garage.\n\n"
            "💡 _Un véhicule de luxe vous ouvre toutes les portes._"
        )
        return
    
    # Récupérer les stats du véhicule avec fallback sur config
    veh_data = await get_vehicle_stats(active_vehicle["veh_type"])
    vehicle_luxe = active_vehicle.get("luxury", 0)
    
    # Si le luxe est à 0 mais que le config a une valeur, on la prend
    if vehicle_luxe == 0 and veh_data.get("luxury", 0) > 0:
        vehicle_luxe = veh_data.get("luxury", 0)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE vehicles SET luxury = ? WHERE veh_id = ?",
                (vehicle_luxe, active_vehicle["veh_id"])
            )
            await db.commit()
    
    # Vérification du niveau de luxe
    if vehicle_luxe < VIP_LUXE_REQUIRED:
        missing = VIP_LUXE_REQUIRED - vehicle_luxe
        await update.message.reply_text(
            f"🚫 **Accès refusé**\n\n"
            f"Votre véhicule n'est pas assez luxueux pour entrer dans les lieux VIP.\n"
            f"🔒 Luxe requis : **{VIP_LUXE_REQUIRED}**\n"
            f"🚗 Luxe actuel : **{vehicle_luxe}/100**\n\n"
            f"💡 Il vous manque **{missing}** points de luxe.\n\n"
            f"🏎️ Pour améliorer votre luxe :\n"
            f"  • Achetez un véhicule plus luxueux avec /acheterv\n"
            f"  • Certains véhicules comme le Jet privé ou le Yacht ont un luxe élevé\n"
            f"  • Consultez /vehicules pour voir les options disponibles",
            parse_mode="HTML"
        )
        return
    
    # Accès VIP accordé
    lieu = random.choice(VIP_LIEUX)
    
    # Bonus pour le prestige si luxe élevé
    bonus_prestige = 0
    if vehicle_luxe >= 90:
        bonus_prestige = 5
        await increment_field(user.id, "prestige", bonus_prestige)
    
    # Bonus social (lié au luxe) pour les réseaux sociaux
    social_bonus_msg = ""
    if vehicle_luxe > 80:
        social_bonus = 1 + (vehicle_luxe - 80) / 100
        social_bonus_msg = f"\n📱 Bonus social actif : vos posts gagnent **+{int((social_bonus - 1) * 100)}%** d'abonnés."
    
    await update.message.reply_text(
        f"✅ **Accès VIP accordé**\n\n"
        f"🚗 Votre véhicule (luxe {vehicle_luxe}/100) vous ouvre toutes les portes.\n\n"
        f"🎯 Vous avez accès au : **{lieu}**\n\n"
        f"🥂 Profitez des salons privés et des événements exclusifs !\n"
        f"{'✨ +' + str(bonus_prestige) + ' Prestige (luxe exceptionnel) !' if bonus_prestige else ''}"
        f"{social_bonus_msg}\n\n"
        f"💎 _Le luxe attire le luxe..._",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_lieux_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche la liste des lieux VIP accessibles."""
    user = update.effective_user
    
    active_vehicle = await get_active_vehicle(user.id)
    
    if not active_vehicle:
        text = "🏛️ **Lieux VIP accessibles**\n\n"
        text += "🚫 Aucun véhicule actif. Sélectionnez-en un avec /garage.\n"
        text += "\n💡 Les lieux VIP nécessitent un véhicule de luxe."
        await update.message.reply_text(text)
        return
    
    # Récupérer les stats du véhicule avec fallback sur config
    veh_data = await get_vehicle_stats(active_vehicle["veh_type"])
    vehicle_luxe = active_vehicle.get("luxury", 0)
    
    # Si le luxe est à 0 mais que le config a une valeur, on la prend
    if vehicle_luxe == 0 and veh_data.get("luxury", 0) > 0:
        vehicle_luxe = veh_data.get("luxury", 0)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE vehicles SET luxury = ? WHERE veh_id = ?",
                (vehicle_luxe, active_vehicle["veh_id"])
            )
            await db.commit()
    
    text = "🏛️ **Lieux VIP accessibles**\n\n"
    text += f"🚗 Luxe de votre véhicule : **{vehicle_luxe}/100**\n"
    text += f"🔒 Seuil requis : **{VIP_LUXE_REQUIRED}**\n\n"
    
    if vehicle_luxe >= VIP_LUXE_REQUIRED:
        text += "✅ **Accès complet**\n\n"
        for lieu in VIP_LIEUX:
            text += f"  • {lieu}\n"
        text += "\n🥂 _Profitez de vos privilèges !_"
    else:
        missing = VIP_LUXE_REQUIRED - vehicle_luxe
        text += f"🔒 **Accès restreint**\n\n"
        text += f"Il vous manque **{missing}** points de luxe.\n\n"
        text += "Voici ce qui vous attend :\n"
        for lieu in VIP_LIEUX[:4]:
            text += f"  • {lieu}\n"
        if len(VIP_LIEUX) > 4:
            text += f"  _... et {len(VIP_LIEUX) - 4} autres lieux exclusifs_"
        text += "\n\n🏎️ Améliorez votre véhicule avec /acheterv"
    
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── MAINTENANCE (appelée par le scheduler) ─────────────────────────

async def process_prestige_decay():
    """Réduit le prestige de 1% par mois (arrondi à l'inférieur)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET prestige = MAX(0, prestige - (prestige / 100)) WHERE prestige > 0"
        )
        await db.commit()