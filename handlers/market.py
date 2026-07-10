# handlers/market.py — Phase 1 : Marché joueur (commandes renommées)
# CORRECTION COMPLÈTE : Gestion robuste des items

import random
import aiosqlite
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import (
    DB_PATH, get_user, update_balance, add_life_journal,
    update_field, increment_field, log_company_action
)
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, now, parse_amount, fmt_time, escape_html
from utils.aesthetics import card, alert, section
from handlers.vehicles import get_active_vehicle, get_vehicle_stats

# ─────────────────────────────────────────────────────────────────────────────
# Helper : obtenir la quantité d'un item dans l'inventaire
# ─────────────────────────────────────────────────────────────────────────────
async def get_item_quantity(user_id: int, item_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0

# ─────────────────────────────────────────────────────────────────────────────
# Helper : supprimer un item de l'inventaire (quantité)
# ─────────────────────────────────────────────────────────────────────────────
async def remove_item(user_id: int, item_id: int, quantity: int = 1) -> bool:
    qty = await get_item_quantity(user_id, item_id)
    if qty < quantity:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        if qty == quantity:
            await db.execute(
                "DELETE FROM inventory WHERE user_id = ? AND item_id = ?",
                (user_id, item_id)
            )
        else:
            await db.execute(
                "UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND item_id = ?",
                (quantity, user_id, item_id)
            )
        await db.commit()
    return True

# ─────────────────────────────────────────────────────────────────────────────
# Helper : ajouter un item à l'inventaire (robuste)
# ─────────────────────────────────────────────────────────────────────────────
async def add_item(user_id: int, item_id: int, quantity: int, source: str = None):
    """Ajoute un item à l'inventaire d'un joueur. Si l'item n'existe pas, le crée."""
    # 1. S'assurer que l'item existe dans la table items
    await ensure_item_exists(item_id)
    
    # 2. Récupérer le nom de l'item
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name FROM items WHERE item_id = ?", (item_id,)) as cur:
            row = await cur.fetchone()
        item_name = row[0] if row else f"Item #{item_id}"
        
        # 3. Ajouter ou mettre à jour dans l'inventaire
        async with db.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id)
        ) as cur2:
            existing = await cur2.fetchone()
        
        if existing:
            await db.execute(
                "UPDATE inventory SET quantity = quantity + ? WHERE user_id = ? AND item_id = ?",
                (quantity, user_id, item_id)
            )
        else:
            await db.execute(
                "INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at) VALUES (?,?,?,?,?,?)",
                (user_id, item_id, source or "market", item_name, quantity, now())
            )
        await db.commit()

# ─────────────────────────────────────────────────────────────────────────────
# Helper : s'assurer qu'un item existe dans la table items (CORRIGÉ)
# ─────────────────────────────────────────────────────────────────────────────
async def ensure_item_exists(item_id: int) -> bool:
    """Vérifie et crée l'item dans la table items si nécessaire."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Vérifier si l'item existe déjà
        async with db.execute("SELECT item_id FROM items WHERE item_id = ?", (item_id,)) as cur:
            if await cur.fetchone():
                return True
        
        # L'item n'existe pas, on le crée
        # Essayer de récupérer le nom depuis l'inventaire
        async with db.execute(
            "SELECT item_name FROM inventory WHERE item_id = ? LIMIT 1",
            (item_id,)
        ) as cur2:
            inv_item = await cur2.fetchone()
        
        name = inv_item[0] if inv_item else f"Item #{item_id}"
        
        # Créer l'item avec un type par défaut
        await db.execute(
            """
            INSERT INTO items (item_id, name, type, rarity, value, emoji, description) 
            VALUES (?, ?, 'unknown', 'common', 0, '📦', ?)
            """,
            (item_id, name, f"Item récupéré automatiquement")
        )
        await db.commit()
        return True

# ─────────────────────────────────────────────────────────────────────────────
# Helper : calculer le bonus cargo pour les ventes
# ─────────────────────────────────────────────────────────────────────────────
async def get_cargo_bonus(user_id: int, base_price: int) -> tuple:
    active_vehicle = await get_active_vehicle(user_id)
    
    if active_vehicle:
        veh_data = await get_vehicle_stats(active_vehicle["veh_type"])
        vehicle_cargo = active_vehicle.get("cargo", 0)
        if vehicle_cargo == 0 and veh_data.get("cargo", 0) > 0:
            vehicle_cargo = veh_data.get("cargo", 0)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE vehicles SET cargo = ? WHERE veh_id = ?",
                    (vehicle_cargo, active_vehicle["veh_id"])
                )
                await db.commit()
    else:
        vehicle_cargo = 0
    
    cargo_bonus_mult = 1 + (vehicle_cargo / 500)
    cargo_bonus_mult = min(1.20, cargo_bonus_mult)
    final_price = int(base_price * cargo_bonus_mult)
    return final_price, cargo_bonus_mult, vehicle_cargo

# ─────────────────────────────────────────────────────────────────────────────
# /market : afficher les annonces
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    page = 1
    if context.args and context.args[0].isdigit():
        page = int(context.args[0])
    
    per_page = 10
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT ml.listing_id, ml.seller_id, ml.item_id, ml.quantity, ml.price,
                   i.name, i.emoji, i.rarity, u.full_name as seller_name
            FROM market_listings ml
            JOIN items i ON i.item_id = ml.item_id
            JOIN users u ON u.user_id = ml.seller_id
            WHERE ml.status = 'active' AND ml.expires_at > ?
            ORDER BY ml.price ASC, ml.created_at ASC
            LIMIT ? OFFSET ?
        """, (now(), per_page, (page - 1) * per_page)) as cur:
            listings = await cur.fetchall()
        
        async with db.execute("SELECT COUNT(*) FROM market_listings WHERE status = 'active' AND expires_at > ?", (now(),)) as cur2:
            total = (await cur2.fetchone())[0]
    
    if not listings:
        await update.message.reply_text(
            card("🏪 Marché joueur", ["Aucune annonce active pour le moment."],
                 icon="🏪", style="thick")
        )
        return
    
    text = f"🏪 **Marché joueur** (page {page}/{max(1, (total + per_page - 1) // per_page)})\n\n"
    for l in listings:
        text += (
            f"{l['emoji']} **{l['name']}** ({l['rarity']})\n"
            f"  Vendeur : {l['seller_name']}\n"
            f"  Quantité : {l['quantity']} | Prix unitaire : {fmt(l['price'])}\n"
            f"  ID annonce : `{l['listing_id']}`\n\n"
        )
    
    keyboard = []
    if page > 1:
        keyboard.append(InlineKeyboardButton("◀️ Page précédente", callback_data=f"market_page_{page-1}"))
    if (page * per_page) < total:
        keyboard.append(InlineKeyboardButton("Page suivante ▶️", callback_data=f"market_page_{page+1}"))
    
    if keyboard:
        reply_markup = InlineKeyboardMarkup([keyboard])
    else:
        reply_markup = None
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def market_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("_")[-1])
    context.args = [str(page)]
    await cmd_market(update, context)
    try:
        await query.message.delete()
    except:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# /sellitem : mettre un item en vente
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("sell_cooldown", 10, "⏳ Attends quelques secondes avant de mettre un autre item en vente.")
async def cmd_sellitem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage : <code>/sellitem [item_id] [prix] [quantité]</code>\n\n"
            "Pour connaître l'ID d'un item, utilise <code>/inventaire</code>.\n"
            "Exemple : <code>/sellitem 3 5000 2</code> (vend 2 exemplaires de l'item #3 à 5000 coins l'unité)\n"
            "La vente expire automatiquement après 7 jours.\n\n"
            "📦 Bonus cargo : ton véhicule augmente le prix de vente (jusqu'à +20%) !",
            parse_mode="HTML"
        )
        return
    
    try:
        item_id = int(context.args[0])
        price = int(context.args[1])
        quantity = int(context.args[2]) if len(context.args) > 2 else 1
        if price <= 0 or quantity <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides. Utilisez des nombres positifs.")
        return
    
    # 1. Vérifier le stock disponible
    current_qty = await get_item_quantity(user.id, item_id)
    if current_qty < quantity:
        await update.message.reply_text(f"❌ Tu ne possèdes que {current_qty} exemplaire(s) de cet item.")
        return

    # 2. Vérifier les quantités déjà en vente (hors inventaire)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT SUM(quantity) FROM market_listings WHERE seller_id = ? AND item_id = ? AND status = 'active'",
            (user.id, item_id)
        ) as cur:
            row = await cur.fetchone()
        already_on_sale = row[0] if row[0] else 0

        if already_on_sale + quantity > current_qty:
            await update.message.reply_text(
                f"❌ Tu as déjà {already_on_sale} exemplaire(s) de cet item en vente.\n"
                f"Tu ne peux pas en mettre plus que ton stock total ({current_qty}).\n"
                f"Essaie de vendre {current_qty - already_on_sale} au maximum."
            )
            return

    # 3. S'assurer que l'item existe dans la table items
    await ensure_item_exists(item_id)
    
    # 4. Récupérer les infos de l'item
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT item_id, name, emoji FROM items WHERE item_id = ?", (item_id,)) as cur:
            item = await cur.fetchone()
    if not item:
        await update.message.reply_text("❌ Item introuvable dans la base.")
        return
    
    # 5. Calcul du bonus cargo sur le prix
    final_price, cargo_bonus_mult, vehicle_cargo = await get_cargo_bonus(user.id, price)
    
    cargo_msg = ""
    if vehicle_cargo > 0 and cargo_bonus_mult > 1.0:
        bonus_pct = int((cargo_bonus_mult - 1) * 100)
        cargo_msg = f"\n📦 Bonus cargo (+{bonus_pct}%) : {fmt(price)} → {fmt(final_price)}"
    
    # 6. Retirer les items de l'inventaire (réservation)
    if not await remove_item(user.id, item_id, quantity):
        await update.message.reply_text("❌ Erreur lors du retrait des items.")
        return
    
    expires_at = now() + 7 * 86400
    
    # 7. Créer l'annonce
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO market_listings (seller_id, item_id, quantity, price, created_at, expires_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
        """, (user.id, item_id, quantity, final_price, now(), expires_at))
        await db.commit()
    
    await add_life_journal(
        user.id, "market",
        f"Mise en vente de {quantity}x {item['emoji']} {item['name']} pour {fmt(final_price)}/u (prix de base {fmt(price)}).",
        severity="info"
    )
    
    text = (
        f"📦 <b>Item mis en vente !</b>\n\n"
        f"{item['emoji']} <b>{escape_html(item['name'])}</b>\n"
        f"Quantité : {quantity}\n"
        f"Prix unitaire : {fmt(final_price)}"
    )
    if cargo_msg:
        text += f"\n{cargo_msg}"
    text += f"\n\nExpiration : {fmt_time(expires_at - now())}\n"
    text += "\n<i>Utilise <code>/cancelitem [listing_id]</code> pour annuler la vente.</i>"
    
    await update.message.reply_text(text, parse_mode="HTML")

# ─────────────────────────────────────────────────────────────────────────────
# /cancelitem : annuler une vente (CORRIGÉ)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_cancelitem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if not context.args:
        await update.message.reply_text("Usage : <code>/cancelitem [listing_id]</code>\n\nL'ID de l'annonce se trouve dans <code>/market</code> ou <code>/myitems</code>.", parse_mode="HTML")
        return
    
    try:
        listing_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT listing_id, seller_id, item_id, quantity, price FROM market_listings WHERE listing_id = ? AND status = 'active'",
            (listing_id,)
        ) as cur:
            listing = await cur.fetchone()
        
        if not listing:
            await update.message.reply_text("❌ Annonce introuvable ou déjà vendue/expirée.")
            return
        if listing["seller_id"] != user.id:
            await update.message.reply_text("❌ Tu ne peux annuler que tes propres annonces.")
            return
        
        # S'assurer que l'item existe dans la table items avant de le remettre
        await ensure_item_exists(listing["item_id"])
        
        # Récupérer le nom de l'item
        async with db.execute("SELECT name FROM items WHERE item_id = ?", (listing["item_id"],)) as cur2:
            item_row = await cur2.fetchone()
        item_name = item_row[0] if item_row else f"Item #{listing['item_id']}"
        
        # Remettre les items dans l'inventaire
        async with db.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?",
            (user.id, listing["item_id"])
        ) as cur3:
            existing = await cur3.fetchone()
        
        if existing:
            await db.execute(
                "UPDATE inventory SET quantity = quantity + ? WHERE user_id = ? AND item_id = ?",
                (listing["quantity"], user.id, listing["item_id"])
            )
        else:
            await db.execute(
                "INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at) VALUES (?,?,?,?,?,?)",
                (user.id, listing["item_id"], "market_return", item_name, listing["quantity"], now())
            )
        
        await db.execute("UPDATE market_listings SET status = 'cancelled' WHERE listing_id = ?", (listing_id,))
        await db.commit()
    
    await update.message.reply_text(f"✅ Annulation réussie. Tu as récupéré {listing['quantity']} exemplaire(s) de l'item.")
    await add_life_journal(user.id, "market", f"Annulation de la vente #{listing_id}", severity="info")

# ─────────────────────────────────────────────────────────────────────────────
# /buyitem : acheter un item
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("buy_cooldown", 5, "⏳ Attends quelques secondes avant de faire un autre achat.")
async def cmd_buyitem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not context.args:
        await update.message.reply_text("Usage : <code>/buyitem [listing_id] [quantité]</code>\n\nL'ID de l'annonce se trouve dans <code>/market</code>.", parse_mode="HTML")
        return

    try:
        listing_id = int(context.args[0])
        quantity = int(context.args[1]) if len(context.args) > 1 else 1
        if quantity <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # 1. Récupérer l'annonce
        async with db.execute("""
            SELECT ml.*, i.name, i.emoji, i.rarity, i.effect_type, i.effect_value,
                   u.full_name as seller_name
            FROM market_listings ml
            JOIN items i ON i.item_id = ml.item_id
            JOIN users u ON u.user_id = ml.seller_id
            WHERE ml.listing_id = ? AND ml.status = 'active' AND ml.expires_at > ?
        """, (listing_id, now())) as cur:
            listing = await cur.fetchone()

        if not listing:
            await update.message.reply_text("❌ Annonce introuvable, expirée ou déjà vendue.")
            return

        if listing["seller_id"] == user.id:
            await update.message.reply_text("❌ Tu ne peux pas acheter tes propres articles.")
            return

        if quantity > listing["quantity"]:
            await update.message.reply_text(f"❌ Le vendeur ne propose que {listing['quantity']} exemplaire(s).")
            return

        total_cost = quantity * listing["price"]
        if u["balance"] < total_cost:
            await update.message.reply_text(f"❌ Fonds insuffisants. Coût total : {fmt(total_cost)}")
            return

        # 2. Transférer l'argent
        await update_balance(user.id, -total_cost)
        await update_balance(listing["seller_id"], total_cost)

        # 3. S'assurer que l'item existe dans la table items
        await ensure_item_exists(listing["item_id"])

        # 4. Ajouter l'item à l'acheteur
        await add_item(user.id, listing["item_id"], quantity, "market")

        # 5. Mettre à jour l'annonce (réduire la quantité ou la marquer comme vendue)
        new_quantity = listing["quantity"] - quantity
        if new_quantity <= 0:
            await db.execute("UPDATE market_listings SET status = 'sold' WHERE listing_id = ?", (listing_id,))
        else:
            await db.execute("UPDATE market_listings SET quantity = ? WHERE listing_id = ?", (new_quantity, listing_id))

        # 6. Récupérer les infos du produit pour le suivi des ventes
        product_info = None
        async with db.execute("""
            SELECT cp.id, cp.sales, cp.revenue, cp.price, cp.company_id
            FROM company_products cp
            JOIN items i ON i.name = cp.name
            WHERE i.item_id = ?
        """, (listing["item_id"],)) as cur2:
            product_row = await cur2.fetchone()
            if product_row:
                product_info = dict(product_row)

        await db.commit()

    # ─── Après le commit : mise à jour des stats et logs ───
    if product_info:
        new_sales = product_info["sales"] + quantity
        new_revenue = product_info["revenue"] + (quantity * product_info["price"])
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE company_products SET sales = ?, revenue = ? WHERE id = ?",
                (new_sales, new_revenue, product_info["id"])
            )
            await log_company_action(
                product_info["company_id"],
                "vente_produit",
                listing["seller_id"],
                f"{quantity}x {listing['name']} pour {fmt(total_cost)} (ID annonce {listing_id})"
            )
            await db.commit()

    await add_life_journal(
        user.id, "market",
        f"Achat de {quantity}x {listing['emoji']} {listing['name']} pour {fmt(total_cost)} à {listing['seller_name']}",
        severity="success"
    )

    await update.message.reply_text(
        f"✅ <b>Achat réussi !</b>\n\n"
        f"{listing['emoji']} <b>{escape_html(listing['name'])}</b> ({listing['rarity']})\n"
        f"Quantité : {quantity}\n"
        f"Prix total : {fmt(total_cost)}\n"
        f"💰 Nouveau solde : {fmt(u['balance'] - total_cost)}\n\n"
        f"<i>Utilise <code>/useitem [item_id]</code> pour utiliser un objet consommable.</i>",
        parse_mode="HTML"
    )

# ─────────────────────────────────────────────────────────────────────────────
# /useitem : utiliser un item (consommable)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("use_cooldown", 2, "⏳ Attends 2 secondes avant d'utiliser un autre item.")
async def cmd_useitem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    
    if not context.args:
        await update.message.reply_text(
            "Usage : <code>/useitem [item_id] [quantité]</code>\n\n"
            "Les items consommables ont des effets :\n"
            "• <code>heal</code> : restaure des points de santé\n"
            "• <code>energy</code> : restaure de l'énergie\n"
            "• <code>xp</code> : donne de l'XP\n"
            "• <code>money</code> : donne de l'argent\n"
            "• <code>buff</code> : boost temporaire (bonheur +, stress -)\n\n"
            "Exemple : <code>/useitem 5</code> (utilise 1 exemplaire de l'item #5)",
            parse_mode="HTML"
        )
        return
    
    try:
        item_id = int(context.args[0])
        quantity = int(context.args[1]) if len(context.args) > 1 else 1
        if quantity <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return
    
    current_qty = await get_item_quantity(user.id, item_id)
    if current_qty < quantity:
        await update.message.reply_text(f"❌ Tu ne possèdes que {current_qty} exemplaire(s) de cet item.")
        return
    
    # S'assurer que l'item existe dans la table items
    await ensure_item_exists(item_id)
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT name, emoji, rarity, type, effect_type, effect_value FROM items WHERE item_id = ?",
            (item_id,)
        ) as cur:
            item = await cur.fetchone()
        if not item:
            await update.message.reply_text("❌ Item introuvable.")
            return
    
    if item["type"] != "consumable":
        await update.message.reply_text(f"❌ L'item {item['emoji']} <b>{escape_html(item['name'])}</b> n'est pas consommable.", parse_mode="HTML")
        return
    
    effect_type = item["effect_type"]
    effect_value = item["effect_value"] * quantity
    
    effect_msg = ""
    if effect_type == "heal":
        new_health = min(100, u["health"] + effect_value)
        await update_field(user.id, "health", new_health)
        effect_msg = f"❤️ Santé : {u['health']} → {new_health}"
    elif effect_type == "energy":
        new_energy = min(100, u["energy"] + effect_value)
        await update_field(user.id, "energy", new_energy)
        effect_msg = f"⚡ Énergie : {u['energy']} → {new_energy}"
    elif effect_type == "xp":
        await increment_field(user.id, "xp", effect_value)
        effect_msg = f"✨ +{effect_value} XP"
    elif effect_type == "money":
        await update_balance(user.id, effect_value)
        effect_msg = f"💰 +{fmt(effect_value)} coins"
    elif effect_type == "buff":
        new_happiness = min(100, u["happiness"] + effect_value)
        new_stress = max(0, u["stress"] - effect_value // 2)
        await update_field(user.id, "happiness", new_happiness)
        await update_field(user.id, "stress", new_stress)
        effect_msg = f"✨ Buff appliqué : Bonheur +{effect_value}, Stress -{effect_value//2}"
    else:
        await update.message.reply_text(f"❌ Effet '{effect_type}' non implémenté pour le moment.")
        return
    
    await remove_item(user.id, item_id, quantity)
    
    await add_life_journal(
        user.id, "item",
        f"Utilisation de {quantity}x {item['emoji']} {item['name']} : {effect_msg}",
        severity="success"
    )
    
    await update.message.reply_text(
        f"✨ <b>Utilisation de {item['emoji']} {escape_html(item['name'])}</b> (x{quantity})\n\n"
        f"{effect_msg}\n\n"
        f"Il te reste {current_qty - quantity} exemplaire(s) de cet item.",
        parse_mode="HTML"
    )

# ─────────────────────────────────────────────────────────────────────────────
# /myitems : voir ses propres annonces
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
async def cmd_myitems(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT ml.listing_id, ml.item_id, ml.quantity, ml.price, ml.created_at, ml.expires_at,
                   i.name, i.emoji, i.rarity
            FROM market_listings ml
            JOIN items i ON i.item_id = ml.item_id
            WHERE ml.seller_id = ? AND ml.status = 'active'
            ORDER BY ml.created_at DESC
        """, (user.id,)) as cur:
            listings = await cur.fetchall()
    
    if not listings:
        await update.message.reply_text("📭 Tu n'as aucune annonce active.")
        return
    
    text = "📦 <b>Tes annonces</b>\n\n"
    for l in listings:
        expires_in = l["expires_at"] - now()
        text += (
            f"ID: <code>{l['listing_id']}</code> — {l['emoji']} <b>{escape_html(l['name'])}</b> ({l['rarity']})\n"
            f"  Quantité : {l['quantity']} | Prix unitaire : {fmt(l['price'])}\n"
            f"  Expire dans : {fmt_time(expires_in)}\n\n"
        )
    text += "Pour annuler : <code>/cancelitem [listing_id]</code>"
    await update.message.reply_text(text, parse_mode="HTML")

# ─────────────────────────────────────────────────────────────────────────────
# /cargobonus : afficher le bonus de cargo actuel
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
async def cmd_cargobonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le bonus de cargo actuel du véhicule."""
    user = update.effective_user
    
    active_vehicle = await get_active_vehicle(user.id)
    
    if not active_vehicle:
        await update.message.reply_text(
            "🚫 <b>Aucun véhicule actif</b>\n\n"
            "Utilise <code>/garage</code> pour sélectionner un véhicule actif.\n"
            "Le bonus cargo s'applique sur les ventes d'items.",
            parse_mode="HTML"
        )
        return
    
    veh_data = await get_vehicle_stats(active_vehicle["veh_type"])
    vehicle_cargo = active_vehicle.get("cargo", 0)
    
    if vehicle_cargo == 0 and veh_data.get("cargo", 0) > 0:
        vehicle_cargo = veh_data.get("cargo", 0)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE vehicles SET cargo = ? WHERE veh_id = ?",
                (vehicle_cargo, active_vehicle["veh_id"])
            )
            await db.commit()
    
    vehicle_name = active_vehicle.get("veh_type", "Véhicule")
    
    cargo_bonus_mult = 1 + (vehicle_cargo / 500)
    cargo_bonus_mult = min(1.20, cargo_bonus_mult)
    bonus_pct = int((cargo_bonus_mult - 1) * 100)
    
    if vehicle_cargo >= 80:
        level = "🏆 Maximale"
        desc = "Vous profitez du bonus maximum sur vos ventes !"
    elif vehicle_cargo >= 50:
        level = "✅ Élevée"
        desc = "Bon bonus sur vos ventes. Les missions de livraison lourdes sont accessibles."
    elif vehicle_cargo >= 30:
        level = "📦 Moyenne"
        desc = "Bonus modéré sur les ventes. Envisagez un véhicule avec plus de cargo."
    elif vehicle_cargo >= 10:
        level = "🌱 Faible"
        desc = "Bonus limité. Un véhicule avec plus de cargo augmenterait vos profits."
    else:
        level = "⚠️ Minimale"
        desc = "Presque pas de bonus. Un véhicule utilitaire serait plus rentable."
    
    text = (
        f"📦 <b>Bonus de Cargo</b>\n\n"
        f"🚗 Véhicule : <b>{escape_html(vehicle_name)}</b>\n"
        f"📦 Cargo : <b>{vehicle_cargo}/100</b>\n"
        f"📊 Niveau : {level}\n"
        f"💰 Bonus sur les ventes : <b>+{bonus_pct}%</b>\n"
        f"📈 Multiplicateur : <b>x{cargo_bonus_mult:.2f}</b>\n\n"
        f"_{desc}_\n\n"
        f"💡 Les véhicules avec un cargo élevé augmentent vos profits lors des ventes sur le marché."
    )
    await update.message.reply_text(text, parse_mode="HTML")

# ─────────────────────────────────────────────────────────────────────────────
# Maintenance : nettoyer les annonces expirées
# ─────────────────────────────────────────────────────────────────────────────
async def clean_expired_listings():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT listing_id, seller_id, item_id, quantity
            FROM market_listings
            WHERE status = 'active' AND expires_at < ?
        """, (now(),)) as cur:
            expired = await cur.fetchall()
        
        for listing in expired:
            # S'assurer que l'item existe avant de le remettre
            await ensure_item_exists(listing["item_id"])
            
            # Récupérer le nom de l'item
            async with db.execute("SELECT name FROM items WHERE item_id = ?", (listing["item_id"],)) as cur2:
                item_row = await cur2.fetchone()
            item_name = item_row[0] if item_row else f"Item #{listing['item_id']}"
            
            # Remettre les items dans l'inventaire
            async with db.execute(
                "SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?",
                (listing["seller_id"], listing["item_id"])
            ) as cur3:
                existing = await cur3.fetchone()
            
            if existing:
                await db.execute(
                    "UPDATE inventory SET quantity = quantity + ? WHERE user_id = ? AND item_id = ?",
                    (listing["quantity"], listing["seller_id"], listing["item_id"])
                )
            else:
                await db.execute(
                    "INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at) VALUES (?,?,?,?,?,?)",
                    (listing["seller_id"], listing["item_id"], "expired_return", item_name, listing["quantity"], now())
                )
            
            await db.execute(
                "UPDATE market_listings SET status = 'expired' WHERE listing_id = ?",
                (listing["listing_id"],)
            )
        await db.commit()