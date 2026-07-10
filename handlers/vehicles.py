# handlers/vehicles.py
# Système de véhicules 2.0 - Garage, réparation, carburant, sélection active

import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, get_vehicles, now, add_notification
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, escape_html, rich_bar
from utils.aesthetics import alert
from config import VEHICLES

MAX_VEHICLES = 10

# ─── HELPERS ──────────────────────────────────────────────

async def get_active_vehicle(user_id: int):
    """Retourne le véhicule actif (dict) ou None."""
    u = await get_user(user_id)
    active_id = u.get("active_vehicle_id", 0)
    if not active_id:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM vehicles WHERE veh_id = ? AND user_id = ?",
            (active_id, user_id)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def set_active_vehicle(user_id: int, veh_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET active_vehicle_id = ? WHERE user_id = ?",
            (veh_id, user_id)
        )
        await db.commit()

async def get_vehicle_stats(veh_type: str) -> dict:
    """Retourne les stats par défaut d'un type de véhicule."""
    return VEHICLES.get(veh_type, {})

# Dans vehicles.py, dans get_garage_text_and_markup

async def get_garage_text_and_markup(user_id: int):
    """Génère le texte et le clavier du garage."""
    vehicles = await get_vehicles(user_id)
    active = await get_active_vehicle(user_id)

    if not vehicles:
        return "🏎️ **VOTRE GARAGE**\n\nVotre garage est vide. Achetez un véhicule avec /acheterv.", None

    text = "🏎️ **VOTRE GARAGE**\n\n"
    for v in vehicles:
        veh_data = await get_vehicle_stats(v["veh_type"])
        is_active = active and v["veh_id"] == active["veh_id"]
        marker = " ✅ ACTIF" if is_active else ""
        
        # Récupérer les stats avec fallback sur les valeurs du config
        speed = v.get("speed", 0) or veh_data.get("speed", 0)
        cargo = v.get("cargo", 0) or veh_data.get("cargo", 0)
        luxury = v.get("luxury", 0) or veh_data.get("luxury", 0)
        condition = v.get("condition", 100)
        fuel = v.get("fuel", 0) or 0
        capacity = veh_data.get("fuel_capacity", 0)
        
        # Si les stats sont à 0 mais que le config a des valeurs, on met à jour la base
        if v.get("speed", 0) == 0 and veh_data.get("speed", 0) > 0:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE vehicles SET speed = ? WHERE veh_id = ?",
                    (veh_data.get("speed", 0), v["veh_id"])
                )
                await db.commit()
        if v.get("cargo", 0) == 0 and veh_data.get("cargo", 0) > 0:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE vehicles SET cargo = ? WHERE veh_id = ?",
                    (veh_data.get("cargo", 0), v["veh_id"])
                )
                await db.commit()
        if v.get("luxury", 0) == 0 and veh_data.get("luxury", 0) > 0:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE vehicles SET luxury = ? WHERE veh_id = ?",
                    (veh_data.get("luxury", 0), v["veh_id"])
                )
                await db.commit()
        
        condition_bar = rich_bar(condition, 100, 10)
        if capacity > 0:
            fuel_bar = rich_bar(fuel, capacity, 10)
            fuel_display = f"<code>{fuel_bar}</code> {fuel}/{capacity}"
        else:
            fuel_display = "N/A (électrique/manuel)"

        text += (
            f"{veh_data.get('emoji', '🚗')} **{v['veh_type']}**{marker}\n"
            f"⚡ Vitesse: {speed}/100  |  📦 Cargo: {cargo}/100  |  ✨ Luxe: {luxury}/100\n"
            f"🛠 État: <code>{condition_bar}</code> {condition}%\n"
            f"⛽ Essence: {fuel_display}\n\n"
        )

    # ... reste du code

    # Boutons pour changer de véhicule actif
    keyboard = []
    for v in vehicles:
        if active and v["veh_id"] == active["veh_id"]:
            continue
        veh_data = await get_vehicle_stats(v["veh_type"])
        keyboard.append([
            InlineKeyboardButton(
                f"✅ Sélectionner {veh_data.get('emoji', '')} {v['veh_type']}",
                callback_data=f"garage_select_{v['veh_id']}"
            )
        ])

    # Boutons pour réparer / faire le plein du véhicule actif
    if active:
        keyboard.append([
            InlineKeyboardButton("🔧 Réparer", callback_data=f"garage_repair_{active['veh_id']}"),
            InlineKeyboardButton("⛽ Faire le plein", callback_data=f"garage_refuel_{active['veh_id']}"),
        ])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    return text, reply_markup

# ─── COMMANDES ────────────────────────────────────────────

@require_registered
@require_free
async def cmd_garage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le garage et permet de sélectionner un véhicule actif."""
    user_id = update.effective_user.id
    text, reply_markup = await get_garage_text_and_markup(user_id)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)


@require_registered
@require_free
async def cmd_garage_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback pour sélectionner un véhicule actif."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    
    data = query.data
    if not data.startswith("garage_select_"):
        return
    veh_id = int(data.split("_")[2])
    user_id = query.from_user.id

    # Vérifier que le véhicule appartient bien à l'utilisateur
    vehicles = await get_vehicles(user_id)
    if not any(v["veh_id"] == veh_id for v in vehicles):
        await query.edit_message_text("❌ Véhicule introuvable.")
        return

    await set_active_vehicle(user_id, veh_id)
    
    # Mettre à jour l'affichage du garage
    text, reply_markup = await get_garage_text_and_markup(user_id)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)


@require_registered
@require_free
async def cmd_garage_repair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback pour réparer le véhicule actif depuis le garage."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    
    data = query.data
    if not data.startswith("garage_repair_"):
        return
    veh_id = int(data.split("_")[2])
    user_id = query.from_user.id

    vehicles = await get_vehicles(user_id)
    veh = next((v for v in vehicles if v["veh_id"] == veh_id), None)
    if not veh:
        await query.edit_message_text("❌ Véhicule introuvable.")
        return

    active = await get_active_vehicle(user_id)
    if not active or active["veh_id"] != veh_id:
        await query.edit_message_text("❌ Ce véhicule n'est pas actif. Sélectionnez-le d'abord.")
        return

    if veh["condition"] >= 100:
        await query.edit_message_text("✅ Ce véhicule est déjà en parfait état.")
        return

    u = await get_user(user_id)
    lux = veh.get("luxury", 0) or (await get_vehicle_stats(veh["veh_type"])).get("luxury", 0)
    repair_cost = int((100 - veh["condition"]) * (lux * 2 + 50) / 100)
    if repair_cost < 100:
        repair_cost = 100

    if u["balance"] < repair_cost:
        await query.edit_message_text(
            f"❌ Réparation coûte {fmt(repair_cost)}.\nSolde : {fmt(u['balance'])}",
            parse_mode="HTML"
        )
        return

    await update_balance(user_id, -repair_cost)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vehicles SET condition = 100, last_maintenance = ? WHERE veh_id = ?",
            (now(), veh_id)
        )
        await db.commit()

    await add_notification(user_id, f"🔧 Véhicule {veh['veh_type']} réparé pour {fmt(repair_cost)}.")
    
    text, reply_markup = await get_garage_text_and_markup(user_id)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)


@require_registered
@require_free
async def cmd_garage_refuel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback pour faire le plein du véhicule actif depuis le garage."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    
    data = query.data
    if not data.startswith("garage_refuel_"):
        return
    veh_id = int(data.split("_")[2])
    user_id = query.from_user.id

    vehicles = await get_vehicles(user_id)
    veh = next((v for v in vehicles if v["veh_id"] == veh_id), None)
    if not veh:
        await query.edit_message_text("❌ Véhicule introuvable.")
        return

    active = await get_active_vehicle(user_id)
    if not active or active["veh_id"] != veh_id:
        await query.edit_message_text("❌ Ce véhicule n'est pas actif. Sélectionnez-le d'abord.")
        return

    veh_data = await get_vehicle_stats(veh["veh_type"])
    capacity = veh_data.get("fuel_capacity", 0)
    if capacity == 0:
        await query.edit_message_text("❌ Ce véhicule n'utilise pas de carburant.")
        return

    current_fuel = veh.get("fuel", 0)
    if current_fuel >= capacity:
        await query.edit_message_text("✅ Le réservoir est déjà plein.")
        return

    needed = capacity - current_fuel
    fuel_price_per_unit = 10
    total_cost = needed * fuel_price_per_unit

    u = await get_user(user_id)
    if u["balance"] < total_cost:
        await query.edit_message_text(
            f"❌ Solde insuffisant. Coût : {fmt(total_cost)}",
            parse_mode="HTML"
        )
        return

    await update_balance(user_id, -total_cost)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vehicles SET fuel = ? WHERE veh_id = ?",
            (capacity, veh_id)
        )
        await db.commit()

    text, reply_markup = await get_garage_text_and_markup(user_id)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)


@require_registered
@require_free
@cooldown("repair_vehicle", 10)
async def cmd_repair_vehicle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Réparer un véhicule (coût basé sur la dégradation et le luxe)."""
    user = update.effective_user
    u = await get_user(user.id)
    vehicles = await get_vehicles(user.id)
    if not vehicles:
        await update.message.reply_text(
            alert("info", "Vous n'avez aucun véhicule."),
            parse_mode="HTML"
        )
        return

    if context.args and context.args[0].isdigit():
        idx = int(context.args[0]) - 1
        if idx < 0 or idx >= len(vehicles):
            await update.message.reply_text("❌ Numéro invalide.")
            return
        veh = vehicles[idx]
    else:
        active = await get_active_vehicle(user.id)
        if not active:
            await update.message.reply_text(
                alert("info", "Aucun véhicule actif. Utilisez /garage pour en sélectionner un."),
                parse_mode="HTML"
            )
            return
        veh = active

    if veh["condition"] >= 100:
        await update.message.reply_text("✅ Ce véhicule est déjà en parfait état.")
        return

    lux = veh.get("luxury", 0) or (await get_vehicle_stats(veh["veh_type"])).get("luxury", 0)
    repair_cost = int((100 - veh["condition"]) * (lux * 2 + 50) / 100)
    if repair_cost < 100:
        repair_cost = 100

    if u["balance"] < repair_cost:
        await update.message.reply_text(
            f"❌ Réparation coûte {fmt(repair_cost)}.\nSolde : {fmt(u['balance'])}",
            parse_mode="HTML"
        )
        return

    await update_balance(user.id, -repair_cost)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vehicles SET condition = 100, last_maintenance = ? WHERE veh_id = ?",
            (now(), veh["veh_id"])
        )
        await db.commit()

    await update.message.reply_text(
        f"🔧 **Réparation terminée !**\n\n"
        f"{VEHICLES.get(veh['veh_type'], {}).get('emoji', '🚗')} {escape_html(veh['veh_type'])}\n"
        f"État : {veh['condition']}% → 100%\n"
        f"💰 Coût : {fmt(repair_cost)}",
        parse_mode="HTML"
    )
    await add_notification(user.id, f"🔧 Véhicule {veh['veh_type']} réparé pour {fmt(repair_cost)}.")


@require_registered
@require_free
@cooldown("refuel", 60)
async def cmd_refuel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Faire le plein du véhicule actif."""
    user = update.effective_user
    u = await get_user(user.id)
    active = await get_active_vehicle(user.id)
    if not active:
        await update.message.reply_text(
            alert("info", "Aucun véhicule actif. Utilisez /garage pour en sélectionner un."),
            parse_mode="HTML"
        )
        return

    veh_data = await get_vehicle_stats(active["veh_type"])
    capacity = veh_data.get("fuel_capacity", 0)
    if capacity == 0:
        await update.message.reply_text("❌ Ce véhicule n'utilise pas de carburant.")
        return

    current_fuel = active.get("fuel", 0)
    if current_fuel >= capacity:
        await update.message.reply_text("✅ Le réservoir est déjà plein.")
        return

    needed = capacity - current_fuel
    fuel_price_per_unit = 10
    total_cost = needed * fuel_price_per_unit

    if u["balance"] < total_cost:
        await update.message.reply_text(
            f"❌ Solde insuffisant. Coût : {fmt(total_cost)}",
            parse_mode="HTML"
        )
        return

    await update_balance(user.id, -total_cost)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vehicles SET fuel = ? WHERE veh_id = ?",
            (capacity, active["veh_id"])
        )
        await db.commit()

    await update.message.reply_text(
        f"⛽ **Plein effectué !**\n\n"
        f"{veh_data.get('emoji', '🚗')} {escape_html(active['veh_type'])}\n"
        f"⛽ Essence : {current_fuel} → {capacity}\n"
        f"💰 Coût : {fmt(total_cost)}",
        parse_mode="HTML"
    )


# ─── MAINTENANCE QUOTIDIENNE ─────────────────────────────────

async def process_vehicles_maintenance():
    """Appelée par le scheduler : dégradation, consommation de fuel, coût d'entretien."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        await db.execute("UPDATE vehicles SET condition = MAX(0, condition - 2)")

        async with db.execute("""
            SELECT v.* FROM vehicles v
            JOIN users u ON u.active_vehicle_id = v.veh_id
            WHERE v.user_id = u.user_id
        """) as cur:
            active_vehicles = await cur.fetchall()

        for v in active_vehicles:
            veh_data = await get_vehicle_stats(v["veh_type"])
            capacity = veh_data.get("fuel_capacity", 0)
            if capacity > 0:
                consumption = max(1, int(capacity * 0.05))
                new_fuel = max(0, v["fuel"] - consumption)
                await db.execute(
                    "UPDATE vehicles SET fuel = ? WHERE veh_id = ?",
                    (new_fuel, v["veh_id"])
                )
                if new_fuel == 0 and v["fuel"] > 0:
                    await add_notification(
                        v["user_id"],
                        f"⛽ Le réservoir de votre {v['veh_type']} est vide !"
                    )

        async with db.execute("SELECT veh_id, user_id, luxury FROM vehicles") as cur:
            all_vehicles = await cur.fetchall()

        for v in all_vehicles:
            lux = v["luxury"] or (await get_vehicle_stats(v["veh_type"])).get("luxury", 0)
            cost = lux * 2
            if cost > 0:
                u = await get_user(v["user_id"])
                if u and u["balance"] >= cost:
                    await update_balance(v["user_id"], -cost)
                else:
                    await db.execute(
                        "UPDATE vehicles SET condition = MAX(0, condition - 5) WHERE veh_id = ?",
                        (v["veh_id"],)
                    )
                    await add_notification(
                        v["user_id"],
                        f"⚠️ Entretien impayé pour votre {v['veh_type']} (état -5%)."
                    )

        await db.commit()