# handlers/phone.py — SmartPhone Hub v3.0 (Amélioré)

import aiosqlite
import time
import random
import re
import calendar
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import (
    DB_PATH, get_user, update_balance, update_field, now, add_notification,
    get_inventory, get_bank_account, get_all_bank_accounts, get_user_company,
    get_vehicles, get_properties, get_marriage
)
from utils.decorators import require_registered
from utils.helpers import fmt, escape_html, fmt_time, parse_amount, rich_bar, get_level
from utils.aesthetics import card, alert
from config import TIME_MULTIPLIER, SEASONS, SEASON_EFFECTS, VEHICLES, VIP_LUXE_REQUIRED
from handlers.vehicles import get_active_vehicle, get_vehicle_stats


# ============================================================
# INFOS DE JEU (heure, date, saison, météo)
# ============================================================

def get_game_datetime():
    real_ts = int(time.time())
    game_seconds = real_ts * TIME_MULTIPLIER
    ref_ts = 1704067200
    game_ts = ref_ts + game_seconds
    gm = time.gmtime(game_ts)
    year, month, day = gm.tm_year, gm.tm_mon, gm.tm_mday
    hour, minute = gm.tm_hour, gm.tm_min

    if month in (3, 4, 5):
        season = "Printemps"
    elif month in (6, 7, 8):
        season = "Été"
    elif month in (9, 10, 11):
        season = "Automne"
    else:
        season = "Hiver"

    weather_options = {
        "Printemps": ["☀️ Ensoleillé", "🌦️ Averses", "🌈 Arc-en-ciel", "☁️ Nuageux", "🌸 Fleuri"],
        "Été": ["☀️ Torride", "⛅ Nuageux", "🌩️ Orage", "🌤️ Doux", "🏖️ Canicule"],
        "Automne": ["🍂 Brumeux", "🌧️ Pluvieux", "☁️ Couvert", "🌫️ Brouillard", "🍁 Venteux"],
        "Hiver": ["❄️ Neigeux", "🌨️ Tempête", "☀️ Froid sec", "🌬️ Vent glacial", "☁️ Givré"]
    }
    weather = random.choice(weather_options.get(season, ["🌤️ Variable"]))

    return {
        "hour": hour, "minute": minute, "day": day, "month": month, "year": year,
        "season": season, "weather": weather, "game_ts": game_ts, "real_ts": real_ts
    }


# ============================================================
# ÉCRAN D'ACCUEIL
# ============================================================

async def get_smartphone_home_text(user_id: int) -> str:
    u = await get_user(user_id)
    dt = get_game_datetime()
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND sent = 0",
            (user_id,)
        ) as cur:
            notif_count = (await cur.fetchone())[0] or 0
    
    active_vehicle = await get_active_vehicle(user_id)
    vehicle_name = active_vehicle.get("veh_type", "Aucun") if active_vehicle else "Aucun"
    
    # Niveau de batterie
    battery = random.randint(65, 100)
    battery_bar = "█" * (battery // 10) + "░" * (10 - battery // 10)
    
    text = (
        f"📱 <b>SMARTPHONE OS v3.0</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {dt['hour']:02d}:{dt['minute']:02d}  📅 {dt['day']:02d}/{dt['month']:02d}/{dt['year']}\n"
        f"🌤️ {dt['weather']}  🍃 {dt['season']}\n"
        f"🔋 Batterie: <code>{battery_bar}</code> {battery}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {escape_html(u.get('full_name', 'Joueur'))}\n"
        f"💰 {fmt(u.get('balance', 0))}  ⭐ Niv.{u.get('level', 1)}\n"
        f"🚗 {escape_html(vehicle_name)}  📩 {notif_count} notif(s)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Sélectionnez une application</i>"
    )
    return text


async def get_smartphone_home_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🏦 Wallet", callback_data="phone_app_wallet"),
         InlineKeyboardButton("🏎️ Garage", callback_data="phone_app_garage")],
        [InlineKeyboardButton("🌐 VoidGram", callback_data="phone_app_social"),
         InlineKeyboardButton("📊 Classements", callback_data="phone_app_rankings")],
        [InlineKeyboardButton("📦 Inventaire", callback_data="phone_app_inventory"),
         InlineKeyboardButton("🏪 Marché", callback_data="phone_app_market")],
        [InlineKeyboardButton("🏠 Immobilier", callback_data="phone_app_realestate"),
         InlineKeyboardButton("❤️ Relations", callback_data="phone_app_relations")],
        [InlineKeyboardButton("👤 Profil", callback_data="phone_app_profile"),
         InlineKeyboardButton("⚙️ Paramètres", callback_data="phone_app_settings")],
        [InlineKeyboardButton("❌ Éteindre", callback_data="phone_power_off")]
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# COMMANDE PRINCIPALE
# ============================================================

@require_registered
async def cmd_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = await get_smartphone_home_text(user_id)
    reply_markup = await get_smartphone_home_keyboard(user_id)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)


# ============================================================
# CALLBACK PRINCIPAL
# ============================================================

async def phone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    
    data = query.data
    user_id = query.from_user.id
    
    if data == "phone_power_off":
        await query.edit_message_text("📱 SmartPhone éteint. Tapez /phone pour le rallumer.")
        return
    
    if data == "phone_app_wallet":
        await app_wallet(query, user_id)
    elif data == "phone_app_garage":
        await app_garage(query, user_id)
    elif data == "phone_app_social":
        await app_social(query, user_id)
    elif data == "phone_app_rankings":
        await app_rankings(query, user_id)
    elif data == "phone_app_inventory":
        await app_inventory(query, user_id)
    elif data == "phone_app_market":
        await app_market(query, user_id)
    elif data == "phone_app_realestate":
        await app_realestate(query, user_id)
    elif data == "phone_app_relations":
        await app_relations(query, user_id)
    elif data == "phone_app_profile":
        await app_profile(query, user_id)
    elif data == "phone_app_settings":
        await app_settings(query, user_id)
    else:
        # Sous-menus
        if data.startswith("phone_wallet_"):
            await app_wallet_sub(query, user_id, data)
        elif data.startswith("phone_garage_"):
            await app_garage_sub(query, user_id, data)
        elif data.startswith("phone_social_"):
            await app_social_sub(query, user_id, data)
        elif data.startswith("phone_profile_"):
            await app_profile_sub(query, user_id, data)
        elif data.startswith("phone_settings_"):
            await app_settings_sub(query, user_id, data)
        elif data == "phone_home":
            await show_home(query, user_id)


# ============================================================
# APPLICATION A : WALLET (Banque & Finance)
# ============================================================

async def app_wallet(query, user_id: int):
    u = await get_user(user_id)
    accounts = await get_all_bank_accounts(user_id)
    
    total_bank = sum(a["balance"] for a in accounts)
    total_loan = sum(a["loan"] for a in accounts)
    
    text = (
        f"🏦 <b>WALLET</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Espèces : <b>{fmt(u.get('balance', 0))}</b>\n"
        f"🏦 Banque : <b>{fmt(total_bank)}</b>\n"
        f"💳 Prêts : <b>-{fmt(total_loan)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 Total net : <b>{fmt(u.get('balance', 0) + total_bank - total_loan)}</b>\n"
        f"📊 Comptes : <b>{len(accounts)}</b>\n"
        f"📈 Plus-value totale : <b>{fmt(u.get('total_earned', 0) - u.get('total_spent', 0))}</b>"
    )
    
    keyboard = [
        [InlineKeyboardButton("💰 Dépôt", callback_data="phone_wallet_deposit"),
         InlineKeyboardButton("💳 Retrait", callback_data="phone_wallet_withdraw")],
        [InlineKeyboardButton("📊 Mes comptes", callback_data="phone_wallet_accounts"),
         InlineKeyboardButton("💸 Vendre un item", callback_data="phone_wallet_sell")],
        [InlineKeyboardButton("📈 Historique", callback_data="phone_wallet_history"),
         InlineKeyboardButton("⬅️ Retour", callback_data="phone_home")]
    ]
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def app_wallet_sub(query, user_id: int, data: str):
    action = data.replace("phone_wallet_", "")
    
    if action == "home":
        await show_home(query, user_id)
        return
    
    if action == "deposit":
        await query.edit_message_text(
            "💰 <b>Dépôt</b>\n\n"
            "Envoie le montant à déposer :\n"
            "<code>/depot [montant] [banque]</code>\n\n"
            "Ex: <code>/depot 50000 Banque Populaire</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_wallet_home")]])
        )
        return
    
    if action == "withdraw":
        await query.edit_message_text(
            "💳 <b>Retrait</b>\n\n"
            "Envoie le montant à retirer :\n"
            "<code>/retrait [montant] [banque]</code>\n\n"
            "Ex: <code>/retrait 30000 Banque Populaire</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_wallet_home")]])
        )
        return
    
    if action == "accounts":
        accounts = await get_all_bank_accounts(user_id)
        if not accounts:
            text = "📭 Aucun compte bancaire. Ouvrez-en un avec <code>/ouvrir</code>"
        else:
            text = "🏦 <b>Vos comptes</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
            for acc in accounts:
                bank = next((b for b in BANKS if b["name"] == acc["bank_name"]), {})
                interest = int(acc["balance"] * bank.get("interest", 0))
                text += f"🏛️ <b>{escape_html(acc['bank_name'])}</b>\n"
                text += f"   💰 {fmt(acc['balance'])}\n"
                text += f"   📈 +{fmt(interest)}/jour\n"
                if acc['loan'] > 0:
                    text += f"   💳 Prêt : {fmt(acc['loan'])}\n"
                text += "\n"
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_wallet_home")]])
        )
        return
    
    if action == "sell":
        await query.edit_message_text(
            "💸 <b>Vendre un item</b>\n\n"
            "Utilisez : <code>/sellitem [item_id] [prix] [quantité]</code>\n\n"
            "📦 Bonus cargo : jusqu'à +20% !",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_wallet_home")]])
        )
        return
    
    if action == "history":
        # Simuler un historique simple
        u = await get_user(user_id)
        text = (
            f"📈 <b>Historique financier</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Total gagné : {fmt(u.get('total_earned', 0))}\n"
            f"💸 Total dépensé : {fmt(u.get('total_spent', 0))}\n"
            f"📊 Plus-value : {fmt(u.get('total_earned', 0) - u.get('total_spent', 0))}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 <i>Utilisez /graphstats pour les graphiques</i>"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_wallet_home")]])
        )
        return


# ============================================================
# APPLICATION B : GARAGE & LUXE
# ============================================================

async def app_garage(query, user_id: int):
    active = await get_active_vehicle(user_id)
    vehicles = await get_vehicles(user_id)
    
    if active:
        veh_data = await get_vehicle_stats(active["veh_type"])
        vehicle_luxe = active.get("luxury", 0) or veh_data.get("luxury", 0)
        vehicle_name = active.get("veh_type", "Inconnu")
        vehicle_speed = active.get("speed", 0) or veh_data.get("speed", 0)
        vehicle_cargo = active.get("cargo", 0) or veh_data.get("cargo", 0)
        condition = active.get("condition", 100)
        
        progress = min(100, int((vehicle_luxe / VIP_LUXE_REQUIRED) * 100))
        bar = rich_bar(progress, 100, 10)
        cond_bar = rich_bar(condition, 100, 10)
        
        text = (
            f"🏎️ <b>GARAGE & LUXE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚗 <b>{escape_html(vehicle_name)}</b> ✅ ACTIF\n"
            f"⚡ Vitesse: {vehicle_speed}/100\n"
            f"📦 Cargo: {vehicle_cargo}/100\n"
            f"✨ Luxe: {vehicle_luxe}/100\n"
            f"🛠️ État: <code>{cond_bar}</code> {condition}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Progression VIP</b>\n"
            f"Seuil: {VIP_LUXE_REQUIRED}\n"
            f"<code>{bar}</code> {progress}%\n"
            f"{'✅ ACCÈS VIP DÉBLOQUÉ' if vehicle_luxe >= VIP_LUXE_REQUIRED else '🔒 ' + str(VIP_LUXE_REQUIRED - vehicle_luxe) + ' pts manquants'}"
        )
    else:
        text = (
            f"🏎️ <b>GARAGE & LUXE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚫 Aucun véhicule actif\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 Utilisez <code>/garage</code> pour en sélectionner un"
        )
    
    keyboard = [
        [InlineKeyboardButton("📋 Voir le garage", callback_data="phone_garage_view"),
         InlineKeyboardButton("🏎️ Acheter", callback_data="phone_garage_buy")],
        [InlineKeyboardButton("🔧 Réparer", callback_data="phone_garage_repair"),
         InlineKeyboardButton("⛽ Faire le plein", callback_data="phone_garage_refuel")],
        [InlineKeyboardButton("✨ Accès VIP", callback_data="phone_garage_vip"),
         InlineKeyboardButton("📦 Bonus cargo", callback_data="phone_garage_cargo")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="phone_home")]
    ]
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def app_garage_sub(query, user_id: int, data: str):
    action = data.replace("phone_garage_", "")
    
    if action == "home":
        await show_home(query, user_id)
        return
    
    if action == "view":
        vehicles = await get_vehicles(user_id)
        if not vehicles:
            text = "📭 Garage vide. Achetez un véhicule avec <code>/acheterv</code>"
        else:
            text = "🏎️ <b>Vos véhicules</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
            for v in vehicles[:5]:
                veh_data = await get_vehicle_stats(v["veh_type"])
                text += (
                    f"{veh_data.get('emoji', '🚗')} <b>{escape_html(v['veh_type'])}</b>\n"
                    f"   ✨ Luxe: {v.get('luxury', 0) or veh_data.get('luxury', 0)}/100\n"
                    f"   🛠️ État: {v.get('condition', 100)}%\n"
                )
                if v.get("insured"):
                    text += "   ✅ Assuré\n"
                text += "\n"
            if len(vehicles) > 5:
                text += f"_... et {len(vehicles)-5} autres_"
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_garage_home")]])
        )
        return
    
    if action == "buy":
        await query.edit_message_text(
            "🏎️ <b>Acheter un véhicule</b>\n\n"
            "Catalogue : <code>/vehicules</code>\n"
            "Acheter : <code>/acheterv [nom]</code>\n\n"
            "💡 Luxe ≥ 70 → Accès VIP",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_garage_home")]])
        )
        return
    
    if action == "repair":
        await query.edit_message_text(
            "🔧 <b>Réparer</b>\n\n"
            "Véhicule actif : <code>/repair</code>\n"
            "Spécifique : <code>/repair [numéro]</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_garage_home")]])
        )
        return
    
    if action == "refuel":
        await query.edit_message_text(
            "⛽ <b>Faire le plein</b>\n\n"
            "Véhicule actif : <code>/refuel</code>\n\n"
            "💰 10 coins par unité",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_garage_home")]])
        )
        return
    
    if action == "vip":
        active = await get_active_vehicle(user_id)
        if not active:
            text = "🚫 Aucun véhicule actif."
        else:
            veh_data = await get_vehicle_stats(active["veh_type"])
            vehicle_luxe = active.get("luxury", 0) or veh_data.get("luxury", 0)
            if vehicle_luxe >= VIP_LUXE_REQUIRED:
                text = f"✅ <b>Accès VIP accordé</b>\n\nLuxe : {vehicle_luxe}/100\n\n🥂 Profitez des salons privés !"
            else:
                missing = VIP_LUXE_REQUIRED - vehicle_luxe
                text = f"🔒 <b>Accès refusé</b>\n\nLuxe : {vehicle_luxe}/100\nSeuil : {VIP_LUXE_REQUIRED}\n\nIl manque {missing} pts"
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_garage_home")]])
        )
        return
    
    if action == "cargo":
        active = await get_active_vehicle(user_id)
        if not active:
            text = "🚫 Aucun véhicule actif."
        else:
            veh_data = await get_vehicle_stats(active["veh_type"])
            cargo = active.get("cargo", 0) or veh_data.get("cargo", 0)
            bonus = 1 + (cargo / 500)
            bonus = min(1.20, bonus)
            text = (
                f"📦 <b>Bonus Cargo</b>\n\n"
                f"🚗 {active.get('veh_type', 'Inconnu')}\n"
                f"📦 Cargo : {cargo}/100\n"
                f"💰 Bonus : +{int((bonus - 1) * 100)}% sur les ventes"
            )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_garage_home")]])
        )
        return


# ============================================================
# APPLICATION C : VOIDGRAM
# ============================================================

_status_cache = {}

async def app_social(query, user_id: int):
    u = await get_user(user_id)
    
    status_text = "📭 Aucun statut publié"
    if _status_cache:
        latest = sorted(_status_cache.items(), key=lambda x: x[1]["time"], reverse=True)[:5]
        status_lines = []
        for uid, data in latest:
            if uid != user_id:
                user_data = await get_user(uid)
                name = user_data.get("full_name", f"Joueur {uid}")
                status_lines.append(f"👤 <b>{escape_html(name)}</b>\n   {escape_html(data['text'])}")
        if status_lines:
            status_text = "\n\n".join(status_lines)
    
    text = (
        f"🌐 <b>VOIDGRAM</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {escape_html(u.get('full_name', 'Joueur'))}\n"
        f"📱 Abonnés : {u.get('social_followers', 0)}\n"
        f"💎 SocialCoins : {u.get('social_coins', 0)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>📰 Derniers statuts</b>\n"
        f"{status_text}"
    )
    
    keyboard = [
        [InlineKeyboardButton("📝 Publier", callback_data="phone_social_post"),
         InlineKeyboardButton("🏆 Classement", callback_data="phone_social_rank")],
        [InlineKeyboardButton("💎 SocialCoins", callback_data="phone_social_coins"),
         InlineKeyboardButton("⬅️ Retour", callback_data="phone_home")]
    ]
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def app_social_sub(query, user_id: int, data: str):
    action = data.replace("phone_social_", "")
    
    if action == "home":
        await show_home(query, user_id)
        return
    
    if action == "post":
        await query.edit_message_text(
            "📝 <b>Publier un statut</b>\n\n"
            "<code>/poster [plateforme] [message]</code>\n\n"
            "📱 Instagram, TikTok, Twitter, YouTube, Twitch, Podcast",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_social_home")]])
        )
        return
    
    if action == "rank":
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT full_name, social_followers FROM users WHERE social_followers > 0 ORDER BY social_followers DESC LIMIT 5"
            )
            rows = await cursor.fetchall()
        if not rows:
            text = "📊 Aucun influenceur."
        else:
            text = "🏆 <b>Top Influenceurs</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            for i, (name, followers) in enumerate(rows):
                text += f"{medals[i]} <b>{escape_html(name)}</b> — {followers:,} abonnés\n"
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_social_home")]])
        )
        return
    
    if action == "coins":
        u = await get_user(user_id)
        text = (
            f"💎 <b>SocialCoins</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Solde : <b>{u.get('social_coins', 0)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 Gagnez des SocialCoins en postant\n"
            f"🎁 Donnez-en avec /donner_socialcoins"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_social_home")]])
        )
        return


# ============================================================
# APPLICATION D : CLASSEMENTS
# ============================================================

async def app_rankings(query, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT full_name, balance FROM users WHERE registered=1 ORDER BY balance DESC LIMIT 3") as cur:
            rich = await cur.fetchall()
        async with db.execute("SELECT full_name, xp FROM users WHERE registered=1 ORDER BY xp DESC LIMIT 3") as cur:
            xp_top = await cur.fetchall()
        async with db.execute("SELECT full_name, prestige FROM users WHERE prestige > 0 ORDER BY prestige DESC LIMIT 3") as cur:
            prestige_top = await cur.fetchall()
        async with db.execute("SELECT full_name, social_followers FROM users WHERE social_followers > 0 ORDER BY social_followers DESC LIMIT 3") as cur:
            social_top = await cur.fetchall()
    
    medals = ["🥇", "🥈", "🥉"]
    
    text = "📊 <b>CLASSEMENTS</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    
    text += "💰 <b>Richesse</b>\n"
    for i, (name, bal) in enumerate(rich):
        text += f"  {medals[i]} {escape_html(name)} — {fmt(bal)}\n"
    
    text += "\n⭐ <b>XP</b>\n"
    for i, (name, xp) in enumerate(xp_top):
        text += f"  {medals[i]} {escape_html(name)} — {xp:,} XP\n"
    
    text += "\n👑 <b>Prestige</b>\n"
    for i, (name, prestige) in enumerate(prestige_top):
        text += f"  {medals[i]} {escape_html(name)} — {prestige} pts\n"
    
    text += "\n📱 <b>Influence</b>\n"
    for i, (name, followers) in enumerate(social_top):
        text += f"  {medals[i]} {escape_html(name)} — {followers:,} abonnés\n"
    
    keyboard = [[InlineKeyboardButton("⬅️ Retour", callback_data="phone_home")]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ============================================================
# APPLICATION E : INVENTAIRE
# ============================================================

async def app_inventory(query, user_id: int):
    items = await get_inventory(user_id)
    
    if not items:
        text = "📦 <b>INVENTAIRE</b>\n━━━━━━━━━━━━━━━━━━━━━━\n📭 Inventaire vide."
    else:
        text = f"📦 <b>INVENTAIRE</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
        for item in items[:8]:
            text += f"• {item.get('item_name', 'Objet')} x{item.get('quantity', 1)}\n"
        if len(items) > 8:
            text += f"\n_... et {len(items)-8} autres_"
    
    keyboard = [
        [InlineKeyboardButton("📦 Utiliser", callback_data="phone_inventory_use")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="phone_home")]
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ============================================================
# APPLICATION F : MARCHÉ
# ============================================================

async def app_market(query, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT ml.listing_id, ml.price, ml.quantity, i.name, i.emoji, u.full_name as seller
            FROM market_listings ml
            JOIN items i ON i.item_id = ml.item_id
            JOIN users u ON u.user_id = ml.seller_id
            WHERE ml.status = 'active' AND ml.expires_at > ?
            ORDER BY ml.price ASC
            LIMIT 5
        """, (now(),)) as cur:
            listings = await cur.fetchall()
    
    if not listings:
        text = "🏪 <b>MARCHÉ</b>\n━━━━━━━━━━━━━━━━━━━━━━\n📭 Aucune annonce."
    else:
        text = "🏪 <b>MARCHÉ</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
        for l in listings:
            text += f"{l['emoji']} <b>{escape_html(l['name'])}</b>\n"
            text += f"   💰 {fmt(l['price'])}/u x{l['quantity']}\n"
            text += f"   Vendeur : {escape_html(l['seller'])}\n\n"
    
    keyboard = [
        [InlineKeyboardButton("🛒 Tout voir", callback_data="phone_market_all")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="phone_home")]
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ============================================================
# APPLICATION G : IMMOBILIER
# ============================================================

async def app_realestate(query, user_id: int):
    properties = await get_properties(user_id)
    u = await get_user(user_id)
    
    text = (
        f"🏠 <b>IMMOBILIER</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏘️ Propriétés : <b>{len(properties)}</b>\n"
        f"💰 Valeur totale : <b>{fmt(sum(p.get('price', 0) for p in properties))}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>/proprietes pour le marché</i>\n"
        f"💡 <i>/acheter pour acheter</i>"
    )
    
    keyboard = [
        [InlineKeyboardButton("📋 Mes biens", callback_data="phone_realestate_list")],
        [InlineKeyboardButton("🏪 Marché", callback_data="phone_realestate_market")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="phone_home")]
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ============================================================
# APPLICATION H : RELATIONS
# ============================================================

async def app_relations(query, user_id: int):
    marriage = await get_marriage(user_id)
    u = await get_user(user_id)
    
    partner_text = "💔 Célibataire"
    if marriage:
        partner = await get_user(marriage["partner_id"])
        partner_text = f"💍 {escape_html(partner.get('full_name', 'Inconnu'))}"
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM friendships WHERE user_id = ?",
            (user_id,)
        ) as cur:
            friends_count = (await cur.fetchone())[0] or 0
    
    text = (
        f"❤️ <b>RELATIONS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{partner_text}\n"
        f"👥 Amis : <b>{friends_count}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>/mariage pour se marier</i>\n"
        f"💡 <i>/ami pour ajouter un ami</i>"
    )
    
    keyboard = [
        [InlineKeyboardButton("👥 Mes amis", callback_data="phone_relations_friends")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="phone_home")]
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ============================================================
# APPLICATION I : PROFIL
# ============================================================

async def app_profile(query, user_id: int):
    u = await get_user(user_id)
    
    text = (
        f"👤 <b>PROFIL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {escape_html(u.get('full_name', 'Joueur'))}\n"
        f"🆔 {user_id}\n"
        f"⭐ Niveau {u.get('level', 1)} — {u.get('xp', 0)} XP\n"
        f"💰 {fmt(u.get('balance', 0))}\n"
        f"🌟 Karma : {u.get('karma', 0)}\n"
        f"👑 Prestige : {u.get('prestige', 0)}\n"
        f"💼 {escape_html(u.get('job', 'Sans emploi'))}\n"
        f"🎓 {escape_html(u.get('diplome', 'Aucun'))}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Followers : {u.get('social_followers', 0)}\n"
        f"🏠 {escape_html(u.get('location', 'Inconnu'))}"
    )
    
    keyboard = [
        [InlineKeyboardButton("📊 Stats", callback_data="phone_profile_stats")],
        [InlineKeyboardButton("📝 Modifier bio", callback_data="phone_profile_bio")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="phone_home")]
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def app_profile_sub(query, user_id: int, data: str):
    action = data.replace("phone_profile_", "")
    
    if action == "stats":
        u = await get_user(user_id)
        text = (
            f"📊 <b>STATS DÉTAILLÉES</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"❤️ Santé : {u.get('health', 100)}%\n"
            f"⚡ Énergie : {u.get('energy', 100)}%\n"
            f"🍽️ Faim : {u.get('hunger', 100)}%\n"
            f"😊 Bonheur : {u.get('happiness', 100)}%\n"
            f"😰 Stress : {u.get('stress', 0)}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💼 Crimes : {u.get('crimes_done', 0)}\n"
            f"🥊 Arène : {u.get('arena_wins', 0)}W/{u.get('arena_losses', 0)}L\n"
            f"✈️ Voyages : {u.get('travel_count', 0)}"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_profile_home")]])
        )
        return
    
    if action == "bio":
        await query.edit_message_text(
            "📝 <b>Modifier la bio</b>\n\n"
            "<code>/bio [votre texte]</code>\n\n"
            "Ex: <code>/bio Passionné de voitures</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_profile_home")]])
        )
        return


# ============================================================
# APPLICATION J : PARAMÈTRES
# ============================================================

async def app_settings(query, user_id: int):
    u = await get_user(user_id)
    theme = u.get("phone_theme", "dark")
    ringtone = u.get("phone_ringtone", "classic")
    
    text = (
        f"⚙️ <b>PARAMÈTRES</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎨 Thème : <b>{'Sombre' if theme == 'dark' else 'Clair'}</b>\n"
        f"🔔 Sonnerie : <b>{'Classique' if ringtone == 'classic' else 'Moderne'}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Personnalisez votre expérience"
    )
    
    keyboard = [
        [InlineKeyboardButton("🎨 Thème", callback_data="phone_settings_theme"),
         InlineKeyboardButton("🔔 Sonnerie", callback_data="phone_settings_ringtone")],
        [InlineKeyboardButton("📩 Notifications", callback_data="phone_settings_notifs")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="phone_home")]
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def app_settings_sub(query, user_id: int, data: str):
    action = data.replace("phone_settings_", "")
    
    if action == "theme":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT phone_theme FROM users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
            current = row[0] if row else "dark"
            new_theme = "light" if current == "dark" else "dark"
            await db.execute("UPDATE users SET phone_theme = ? WHERE user_id = ?", (new_theme, user_id))
            await db.commit()
        await query.answer(f"Thème changé en {new_theme}")
        await app_settings(query, user_id)
        return
    
    if action == "ringtone":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT phone_ringtone FROM users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
            current = row[0] if row else "classic"
            new_ringtone = "modern" if current == "classic" else "classic"
            await db.execute("UPDATE users SET phone_ringtone = ? WHERE user_id = ?", (new_ringtone, user_id))
            await db.commit()
        await query.answer(f"Sonnerie changée en {new_ringtone}")
        await app_settings(query, user_id)
        return
    
    if action == "notifs":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND sent = 0", (user_id,)) as cur:
                pending = (await cur.fetchone())[0] or 0
            async with db.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND sent = 1", (user_id,)) as cur:
                sent = (await cur.fetchone())[0] or 0
        
        text = (
            f"📩 <b>NOTIFICATIONS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ En attente : {pending}\n"
            f"✅ Reçues : {sent}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 <i>/notifications pour voir tout</i>"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="phone_settings_home")]])
        )
        return


# ============================================================
# RETOUR À L'ACCUEIL
# ============================================================

async def show_home(query, user_id: int):
    await asyncio.sleep(0.3)
    text = await get_smartphone_home_text(user_id)
    reply_markup = await get_smartphone_home_keyboard(user_id)
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except:
        pass


# ============================================================
# COMMANDES D'ÉCRITURE
# ============================================================

@require_registered
async def cmd_phone_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            "📝 <b>Publier un statut</b>\n\n"
            "Usage : <code>/status [message]</code>",
            parse_mode="HTML"
        )
        return
    
    message = " ".join(context.args)[:200]
    _status_cache[user.id] = {"text": message, "time": now()}
    await update.message.reply_text(f"✅ Statut publié !\n\n📝 {escape_html(message)}", parse_mode="HTML")


@require_registered
async def cmd_phone_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage : /phone_event \"titre\" [date] ou /phone_event \"titre\" [jour] [mois] [année]"
        )
        return

    text = " ".join(context.args)
    match = re.match(r'^"(.+?)"\s+(.+)$', text)
    if not match:
        await update.message.reply_text("❌ Titre invalide. Utilise des guillemets.")
        return
    title = match.group(1)
    rest = match.group(2)

    date_ts = None
    parts = rest.split()
    if len(parts) == 1 and parts[0].isdigit():
        date_ts = int(parts[0])
    elif len(parts) == 3 and all(p.isdigit() for p in parts):
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
        try:
            date_ts = calendar.timegm((year, month, day, 0, 0, 0))
        except:
            await update.message.reply_text("❌ Date invalide.")
            return
    else:
        await update.message.reply_text("❌ Format invalide.")
        return

    if date_ts < now():
        await update.message.reply_text("❌ La date doit être dans le futur.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO game_calendar (date, title, description, created_at, user_id) VALUES (?, ?, ?, ?, ?)",
            (date_ts, title, f"Événement de {user.full_name}", now(), user.id)
        )
        await db.commit()
    
    date_str = time.strftime('%d/%m/%Y', time.gmtime(date_ts))
    await update.message.reply_text(f"✅ Événement **{title}** ajouté pour le {date_str}.")


@require_registered
async def cmd_phone_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage : <code>/phone_msg @user [message]</code>",
            parse_mode="HTML"
        )
        return

    target_username = context.args[0].lstrip("@")
    target = None
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, full_name FROM users WHERE username = ? AND registered = 1",
            (target_username,)
        ) as cur:
            row = await cur.fetchone()
        if not row and target_username.isdigit():
            async with db.execute(
                "SELECT user_id, full_name FROM users WHERE user_id = ? AND registered = 1",
                (int(target_username),)
            ) as cur2:
                row = await cur2.fetchone()
        if not row:
            await update.message.reply_text("❌ Joueur introuvable.")
            return
        target_id, target_name = row

    if target_id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas t'envoyer un message.")
        return

    message = " ".join(context.args[1:])
    if len(message) > 500:
        await update.message.reply_text("❌ Message trop long (max 500).")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO phone_messages (from_id, to_id, message, created_at, read) VALUES (?, ?, ?, ?, 0)",
            (user.id, target_id, message, now())
        )
        await db.commit()

    await add_notification(target_id, f"📱 Message de {user.full_name} : {message[:100]}{'...' if len(message) > 100 else ''}")

    await update.message.reply_text(
        f"✅ Message envoyé à <b>{escape_html(target_name)}</b>\n"
        f"📝 <i>{escape_html(message[:100])}{'...' if len(message) > 100 else ''}</i>",
        parse_mode="HTML"
    )


# ============================================================
# ÉVÉNEMENTS ALÉATOIRES
# ============================================================

async def add_random_calendar_events():
    tomorrow_ts = now() + 86400
    day_start = tomorrow_ts - (tomorrow_ts % 86400)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM game_calendar WHERE date = ?", (day_start,)) as cur:
            if await cur.fetchone():
                return
        events = [
            ("📈 Jour de paie", "Tous les joueurs reçoivent leur salaire."),
            ("🎉 Fête nationale", "Bonheur +10%."),
            ("🌪️ Tempête", "Jardins -20% récoltes."),
            ("🏛️ Élection", "Votez pour un nouveau leader."),
            ("🎪 Carnaval", "-20% sur les articles de luxe."),
            ("🔬 Découverte", "R&D +5%."),
            ("💥 Crash boursier", "Marché -15%."),
            ("🚀 Lancement spatial", "Voyages -30%."),
            ("🎭 Festival", "Prestige bonus."),
            ("☕ Journée du café", "Énergie gratuite."),
        ]
        chosen = random.choice(events)
        await db.execute(
            "INSERT INTO game_calendar (date, title, description, created_at) VALUES (?, ?, ?, ?)",
            (day_start, chosen[0], chosen[1], now())
        )
        await db.commit()