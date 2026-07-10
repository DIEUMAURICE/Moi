# handlers/vehicle.py — Version améliorée (pagination, notifications, robustesse)
import random
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, get_vehicles, increment_field, now, add_notification
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, fmt_time, escape_html, rich_bar
from utils.pagination import paginate_lines
from config import VEHICLES

MAX_VEHICLES = 10

# ==================== COMMANDES ====================
@require_registered
@require_free
async def cmd_vehicules_liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le catalogue des véhicules disponibles."""
    text = "🚗 <b>Showroom de véhicules</b>\n\n"
    for vtype, data in VEHICLES.items():
        text += (
            f"{data['emoji']} <b>{vtype}</b>\n"
            f"  💰 Prix : {fmt(data['price'])}\n"
            f"  🔧 Entretien : {fmt(data['maint'])}/mois\n"
            f"  ✨ Prestige : +{data['status']} pts\n"
            f"  ⚡ Vitesse : {data.get('speed', 0)}/100\n"
            f"  📦 Cargo : {data.get('cargo', 0)}/100\n"
            f"  ✨ Luxe : {data.get('luxury', 0)}/100\n\n"
        )
    text += "<i>/acheterv [véhicule] pour acheter</i>"
    await update.message.reply_text(text, parse_mode="HTML")

@require_registered
@require_free
@cooldown("buy_vehicle", 10, "⏳ Attends un instant avant d'acheter un autre véhicule.")
async def cmd_acheter_vehicule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not context.args:
        await update.message.reply_text("Usage : /acheterv [nom du véhicule]")
        return

    vtype = " ".join(context.args).title()
    matched = None
    for v in VEHICLES:
        if v.lower() == vtype.lower():
            matched = v
            break
    if not matched:
        await update.message.reply_text("❌ Véhicule inconnu. /vehicules pour voir la liste.")
        return

    data = VEHICLES[matched]
    if u["balance"] < data["price"]:
        await update.message.reply_text(
            f"❌ Fonds insuffisants !\n"
            f"💰 Prix : {fmt(data['price'])}\n"
            f"💵 Ton solde : {fmt(u['balance'])}",
            parse_mode="HTML"
        )
        return

    vehs = await get_vehicles(user.id)
    if len(vehs) >= MAX_VEHICLES:
        await update.message.reply_text(f"❌ Tu as déjà {MAX_VEHICLES} véhicules, vends-en un avant d'en acheter un autre.")
        return

    await update_balance(user.id, -data["price"])
    await increment_field(user.id, "prestige", data["status"])

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO vehicles (user_id, veh_type, condition, insured, purchased_at, last_maintenance, speed, cargo, luxury, fuel) VALUES (?,?,100,0,?,?,?,?,?,?)",
            (user.id, matched, now(), now(), data.get("speed", 0), data.get("cargo", 0), data.get("luxury", 0), data.get("fuel_capacity", 0))
        )
        await db.commit()

    await update.message.reply_text(
        f"✅ <b>{matched} acheté !</b>\n\n"
        f"{data['emoji']} Bienvenue dans ta nouvelle monture !\n"
        f"💰 Prix : {fmt(data['price'])}\n"
        f"🔧 Entretien mensuel : {fmt(data['maint'])}\n"
        f"✨ +{data['status']} prestige\n"
        f"⚡ Vitesse : {data.get('speed', 0)}/100\n"
        f"📦 Cargo : {data.get('cargo', 0)}/100\n"
        f"✨ Luxe : {data.get('luxury', 0)}/100\n\n"
        f"👉 /assurerv [numéro] pour l'assurer\n"
        f"👉 /mesvehicules pour voir ta collection",
        parse_mode="HTML"
    )
    await add_notification(user.id, f"🚗 Tu as acheté un {matched} !")

@require_registered
@require_free
async def cmd_mes_vehicules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche la liste des véhicules du joueur avec pagination."""
    user = update.effective_user
    vehs = await get_vehicles(user.id)

    if not vehs:
        await update.message.reply_text(
            "🚗 Tu ne possèdes aucun véhicule.\n"
            "/vehicules pour voir le catalogue."
        )
        return

    lines = []
    for i, v in enumerate(vehs, 1):
        data = VEHICLES.get(v["veh_type"], {})
        cond_bar = "🟢" if v["condition"] >= 70 else "🟡" if v["condition"] >= 40 else "🔴"
        ins_text = "✅ Assuré" if v["insured"] else "❌ Non assuré"
        last_maint = v.get("last_maintenance", 0)
        maint_ago = fmt_time(now() - last_maint) if last_maint else "jamais"
        speed = v.get("speed", 0) or data.get("speed", 0)
        cargo = v.get("cargo", 0) or data.get("cargo", 0)
        luxury = v.get("luxury", 0) or data.get("luxury", 0)
        lines.append(
            f"#{i} {data.get('emoji', '🚗')} <b>{escape_html(v['veh_type'])}</b>\n"
            f"  ⚡ {speed}/100 📦 {cargo}/100 ✨ {luxury}/100\n"
            f"  {cond_bar} État : {v['condition']}%\n"
            f"  🏥 {ins_text}\n"
            f"  🔧 Dernier entretien : il y a {maint_ago}"
        )

    header = f"🚗 <b>Tes véhicules</b> (total: {len(vehs)})\n\n"
    footer = ("\n<i>/reparer [numéro] — réparer\n"
              "/assurerv [numéro] — assurer\n"
              "/vendrevehicule [numéro] — vendre\n"
              "/vehicule_info [numéro] — détails</i>")

    page = 1
    if context.args and context.args[0].isdigit():
        page = int(context.args[0])
    text, markup = await paginate_lines(lines, page, per_page=10, header=header, footer=footer)
    if markup:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode="HTML")

@require_registered
@require_free
@cooldown("repair_vehicle", 10)
async def cmd_reparer_vehicule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    vehs = await get_vehicles(user.id)

    if not context.args:
        await update.message.reply_text("Usage : /reparer [numéro de véhicule]")
        return

    try:
        idx = int(context.args[0]) - 1
        veh = vehs[idx]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Numéro invalide. Utilise /mesvehicules pour voir la liste.")
        return

    if veh["condition"] >= 100:
        await update.message.reply_text("✅ Ton véhicule est déjà en parfait état !")
        return

    veh_data = VEHICLES.get(veh["veh_type"], {})
    repair_cost = int((100 - veh["condition"]) * veh_data.get("price", 10000) / 500)
    if repair_cost < 100:
        repair_cost = 100

    if u["balance"] < repair_cost:
        await update.message.reply_text(f"❌ Réparation coûte {fmt(repair_cost)}\n💵 Ton solde : {fmt(u['balance'])}", parse_mode="HTML")
        return

    await update_balance(user.id, -repair_cost)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE vehicles SET condition=100, last_maintenance=? WHERE veh_id=?", (now(), veh["veh_id"]))
        await db.commit()

    await update.message.reply_text(
        f"🔧 <b>Réparation terminée !</b>\n\n"
        f"{veh_data.get('emoji', '🚗')} {escape_html(veh['veh_type'])}\n"
        f"État : {veh['condition']}% → 100%\n"
        f"💰 Coût : {fmt(repair_cost)}",
        parse_mode="HTML"
    )
    await add_notification(user.id, f"🔧 Tu as réparé ton {veh['veh_type']} pour {fmt(repair_cost)}.")

@require_registered
@require_free
@cooldown("insure_vehicle", 10)
async def cmd_assurer_vehicule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    vehs = await get_vehicles(user.id)

    if not context.args:
        await update.message.reply_text("Usage : /assurerv [numéro de véhicule]")
        return

    try:
        idx = int(context.args[0]) - 1
        veh = vehs[idx]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Numéro invalide. Utilise /mesvehicules pour voir la liste.")
        return

    if veh["insured"]:
        await update.message.reply_text("✅ Ce véhicule est déjà assuré !")
        return

    veh_data = VEHICLES.get(veh["veh_type"], {})
    insurance_cost = int(veh_data.get("price", 10000) * 0.05)
    if insurance_cost < 100:
        insurance_cost = 100

    if u["balance"] < insurance_cost:
        await update.message.reply_text(f"❌ Assurance coûte {fmt(insurance_cost)}/mois", parse_mode="HTML")
        return

    await update_balance(user.id, -insurance_cost)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE vehicles SET insured=1 WHERE veh_id=?", (veh["veh_id"],))
        await db.commit()

    await update.message.reply_text(
        f"✅ <b>Véhicule assuré !</b>\n\n"
        f"{veh_data.get('emoji','🚗')} {escape_html(veh['veh_type'])}\n"
        f"💰 Prime mensuelle : {fmt(insurance_cost)}\n"
        f"_En cas d'accident, tu es couvert !_",
        parse_mode="HTML"
    )
    await add_notification(user.id, f"🏥 Tu as assuré ton {veh['veh_type']} pour {fmt(insurance_cost)}/mois.")

@require_registered
@require_free
@cooldown("sell_vehicle", 10)
async def cmd_vendre_vehicule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Vendre un véhicule (prix de reprise = 60% de la valeur neuve, déduction selon état)."""
    user = update.effective_user
    vehs = await get_vehicles(user.id)

    if not context.args:
        await update.message.reply_text("Usage : /vendrevehicule [numéro] (/mesvehicules pour voir la liste)")
        return

    try:
        idx = int(context.args[0]) - 1
        veh = vehs[idx]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Numéro invalide.")
        return

    veh_data = VEHICLES.get(veh["veh_type"], {})
    condition = veh["condition"]
    base_price = veh_data.get("price", 0)
    sell_price = int(base_price * 0.6 * (condition / 100))
    if sell_price <= 0:
        await update.message.reply_text("❌ Ce véhicule ne vaut plus rien, tu peux le détruire (non implémenté).")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM vehicles WHERE veh_id=?", (veh["veh_id"],))
        await db.commit()

    await update_balance(user.id, sell_price)
    await increment_field(user.id, "prestige", -veh_data.get("status", 0))

    await update.message.reply_text(
        f"💰 <b>Vente de véhicule</b>\n\n"
        f"{veh_data.get('emoji', '🚗')} {escape_html(veh['veh_type'])}\n"
        f"État : {condition}%\n"
        f"💰 Prix de reprise : {fmt(sell_price)}\n\n"
        f"_Tu regrettes peut-être..._",
        parse_mode="HTML"
    )
    await add_notification(user.id, f"💰 Tu as vendu ton {veh['veh_type']} pour {fmt(sell_price)}.")

# ==================== NOUVELLE COMMANDE : INFOS DÉTAILLÉES ====================
@require_registered
async def cmd_vehicule_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les détails complets d'un véhicule (par numéro)."""
    user = update.effective_user
    vehs = await get_vehicles(user.id)
    if not context.args:
        await update.message.reply_text(
            "Usage : /vehicule_info [numéro] (voir /mesvehicules pour les numéros)"
        )
        return
    try:
        idx = int(context.args[0]) - 1
        if idx < 0 or idx >= len(vehs):
            await update.message.reply_text("❌ Numéro invalide. Utilise /mesvehicules pour voir la liste.")
            return
        veh = vehs[idx]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Numéro invalide. Utilise /mesvehicules pour voir la liste.")
        return

    data = VEHICLES.get(veh["veh_type"], {})
    cond_bar = rich_bar(veh["condition"], 100, 10)
    last_maint = veh.get("last_maintenance", 0)
    maint_ago = fmt_time(now() - last_maint) if last_maint else "jamais"
    purchased_ago = fmt_time(now() - veh.get("purchased_at", now()))

    speed = veh.get("speed", 0) or data.get("speed", 0)
    cargo = veh.get("cargo", 0) or data.get("cargo", 0)
    luxury = veh.get("luxury", 0) or data.get("luxury", 0)
    fuel = veh.get("fuel", 0) or 0
    capacity = data.get("fuel_capacity", 0)

    text = (
        f"🚗 <b>Informations sur le véhicule</b>\n\n"
        f"{data.get('emoji', '🚗')} <b>{escape_html(veh['veh_type'])}</b>\n"
        f"💰 Valeur neuve : {fmt(data.get('price', 0))}\n"
        f"🔧 Entretien mensuel : {fmt(data.get('maint', 0))}\n"
        f"✨ Prestige : +{data.get('status', 0)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Vitesse : {speed}/100\n"
        f"📦 Cargo : {cargo}/100\n"
        f"✨ Luxe : {luxury}/100\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏚️ État : <code>{cond_bar}</code> {veh['condition']}%\n"
        f"🏥 Assurance : {'✅ Oui' if veh['insured'] else '❌ Non'}\n"
        f"🔧 Dernier entretien : il y a {maint_ago}\n"
        f"📅 Acheté il y a : {purchased_ago}\n"
        f"⛽ Carburant : {fuel}/{capacity}"
    )
    await update.message.reply_text(text, parse_mode="HTML")