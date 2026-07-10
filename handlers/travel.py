# handlers/travel.py
import random
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, update_field, increment_field
from utils.decorators import require_registered, require_free
from utils.helpers import fmt, fmt_time, now
from config import DESTINATIONS
from handlers.competitions import on_travel
from handlers.missions import update_mission_progress
from handlers.vehicles import get_active_vehicle  # Import pour la vitesse

# Constante pour le cooldown après un voyage (2 heures)
TRAVEL_COOLDOWN = 7200  # secondes

# Fonction de secours si handlers.events n'existe pas
async def get_event_effect():
    try:
        from handlers.events import get_event_effect as _get_event_effect
        return await _get_event_effect()
    except ImportError:
        return {}


@require_registered
@require_free
async def cmd_destinations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT destination FROM travel_log WHERE user_id=?", (user.id,)
        ) as cur:
            visited = {row[0] for row in await cur.fetchall()}

    text = "🌍 **Destinations disponibles**\n\n"
    for dest, data in DESTINATIONS.items():
        status = "✅" if dest in visited else "🔒"
        text += (
            f"{status} {data['emoji']} **{dest}**\n"
            f"  💰 Coût : {fmt(data['cost'])}\n"
            f"  😊 Bonheur : +{data['happiness']}%\n"
            f"  ✨ XP : +{data['xp']}\n\n"
        )
    text += "_/voyager [destination] pour partir !_\n"
    text += "_Chaque voyage prend du temps — tu ne peux rien faire pendant et après (cooldown de 2h)._\n"
    text += "_🏎️ La vitesse de ton véhicule réduit le temps de trajet !_"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
@require_free
async def cmd_voyager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not context.args:
        await update.message.reply_text(
            "Usage : /voyager [destination]\n"
            "/destinations pour voir les options."
        )
        return

    dest_name = " ".join(context.args).title()
    matched = None
    for d in DESTINATIONS:
        if d.lower() == dest_name.lower():
            matched = d
            break
    if not matched:
        await update.message.reply_text(f"❌ Destination inconnue. /destinations pour voir la liste.")
        return

    data = DESTINATIONS[matched]

    # Vérifier si déjà en voyage ou en cooldown (travel_until > now)
    if u.get("travel_until", 0) > now():
        remaining = u["travel_until"] - now()
        await update.message.reply_text(
            f"✈️ Tu es déjà en voyage ou en période de repos !\n"
            f"⏳ Reviens dans {fmt_time(remaining)}."
        )
        return

    if u["balance"] < data["cost"]:
        await update.message.reply_text(
            f"❌ Fonds insuffisants !\n"
            f"✈️ Coût du voyage : {fmt(data['cost'])}\n"
            f"💵 Ton solde : {fmt(u['balance'])}"
        )
        return

    # Impact des événements mondiaux
    event_effect = await get_event_effect()
    cost_mult = event_effect.get("travel_cost_mult", 1.0)
    happiness_mult = event_effect.get("travel_happiness_mult", 1.0)
    actual_cost = int(data["cost"] * cost_mult)
    actual_happiness = int(data["happiness"] * happiness_mult)

    if u["balance"] < actual_cost:
        await update.message.reply_text(
            f"❌ Fonds insuffisants !\n"
            f"✈️ Coût du voyage (événement) : {fmt(actual_cost)}\n"
            f"💵 Ton solde : {fmt(u['balance'])}"
        )
        return

    # ─── RÉCUPÉRATION DU VÉHICULE ACTIF POUR LA VITESSE ─────────────
    active_vehicle = await get_active_vehicle(user.id)
    vehicle_speed = active_vehicle.get("speed", 0) if active_vehicle else 0

    # Durée du voyage de base (aléatoire entre 1 et 3 heures)
    travel_duration = random.randint(3600, 10800)
    
    # Calcul du temps de trajet avec réduction selon la vitesse
    # Temps_Final = Temps_Base * (1 - (veh_speed / 200))
    # Si speed = 100 → réduction de 50%
    # Si speed = 0 → aucune réduction
    speed_reduction = 1 - (vehicle_speed / 200)
    speed_reduction = max(0.3, min(1.0, speed_reduction))  # Limiter entre 30% et 100%
    travel_duration = int(travel_duration * speed_reduction)
    
    # Bonus XP supplémentaire pour la vitesse
    xp_bonus = int(data["xp"] * (1 + vehicle_speed / 200))
    
    # Message sur la réduction de temps
    speed_msg = ""
    if active_vehicle and vehicle_speed > 0:
        reduction_pct = int((1 - speed_reduction) * 100)
        speed_msg = f"\n🏎️ Vitesse du véhicule : {vehicle_speed}/100 → temps de trajet réduit de {reduction_pct}% !"
    elif active_vehicle and vehicle_speed == 0:
        speed_msg = "\n🏎️ Ton véhicule est très lent (vitesse 0)... Le trajet prendra tout son temps."
    else:
        speed_msg = "\n🚫 Aucun véhicule actif → temps de trajet normal."

    total_blocked = travel_duration + TRAVEL_COOLDOWN

    await update_balance(user.id, -actual_cost)
    await update_field(user.id, "travel_until", now() + total_blocked)
    await update_field(user.id, "happiness", min(100, u["happiness"] + actual_happiness))
    await update_field(user.id, "stress", max(0, u["stress"] - 20))
    await increment_field(user.id, "xp", xp_bonus)
    await increment_field(user.id, "travel_count")
    await on_travel(user.id)
    # Mise à jour de la mission "Voyager"
    await update_mission_progress(user.id, "travel", 1)

    travel_count = u.get("travel_count", 0) + 1
    prestige_msg = ""
    if travel_count == 5:
        await increment_field(user.id, "prestige", 25)
        prestige_msg = "\n🏆 +25 Prestige (5 voyages) !"
    elif travel_count == 10:
        await increment_field(user.id, "prestige", 50)
        prestige_msg = "\n🏆 +50 Prestige (10 voyages) !"
    elif travel_count == 25:
        await increment_field(user.id, "prestige", 150)
        prestige_msg = "\n🏆 +150 Prestige (25 voyages) !"
    elif travel_count == 50:
        await increment_field(user.id, "prestige", 500)
        prestige_msg = "\n🏆 +500 Prestige (50 voyages) !"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO travel_log (user_id, destination, cost, timestamp) VALUES (?,?,?,?)",
            (user.id, matched, actual_cost, now())
        )
        await db.commit()

    # Événement aléatoire pendant le voyage
    events = [
        (f"✨ Tu rencontres une célébrité locale et obtiens un contact VIP !", 50_000, 10),
        (f"🍽️ Tu découvres un restaurant exceptionnel — expérience inoubliable !", 0, 5),
        (f"🎲 Au casino de {matched}, tu gagnes une petite somme !", random.randint(10_000, 50_000), 5),
        (f"😊 Voyage parfait, aucun incident. Retour ressourcé !", 0, 10),
        (f"📸 Tes photos deviennent virales ! Tes abonnés augmentent.", 0, 15),
        (f"💼 Tu rencontres un business partner potentiel !", 0, 20),
        (f"🌧️ Mauvaise météo... ton voyage est gâché.", -10_000, -10),
        (f"🦠 Épidémie locale ! Tu tombes malade.", -20_000, -20),
    ]
    event, bonus, karma = random.choices(
        events,
        weights=[5, 20, 15, 40, 10, 10, 5, 2]
    )[0]

    if bonus > 0:
        await update_balance(user.id, bonus)
        bonus_text = f"\n💰 Bonus : +{fmt(bonus)}"
    elif bonus < 0:
        if u["balance"] + bonus < 0:
            bonus = -u["balance"]
            if bonus != 0:
                await update_balance(user.id, bonus)
        else:
            await update_balance(user.id, bonus)
        bonus_text = f"\n💸 Perte : {fmt(abs(bonus))}"
    else:
        bonus_text = ""

    if karma != 0:
        await increment_field(user.id, "karma", karma)
        karma_text = f"\n🌟 Karma : {karma:+d}"
    else:
        karma_text = ""

    await update.message.reply_text(
        f"✈️ **Voyage à {matched} !**\n\n"
        f"{data['emoji']} Destination : **{matched}**\n"
        f"💰 Coût : {fmt(actual_cost)}\n"
        f"⏳ Voyage : {fmt_time(travel_duration)} + repos {fmt_time(TRAVEL_COOLDOWN)}\n"
        f"{speed_msg}\n\n"
        f"**Événement :** _{event}_{bonus_text}{karma_text}\n\n"
        f"😊 +{actual_happiness}% bonheur | 😰 -20% stress\n"
        f"✨ +{xp_bonus} XP (incluant bonus vitesse){prestige_msg}\n\n"
        f"_Tu pourras refaire un voyage dans {fmt_time(total_blocked)}_",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_monstimbre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT destination, COUNT(*) as visits FROM travel_log WHERE user_id=? GROUP BY destination ORDER BY visits DESC",
            (user.id,)
        ) as cur:
            trips = await cur.fetchall()

    if not trips:
        await update.message.reply_text(
            "✈️ Tu n'as jamais voyagé !\n"
            "/destinations pour voir les options."
        )
        return

    text = f"🗺️ **Collection de voyages — {user.full_name}**\n\n"
    total_trips = 0
    for t in trips:
        dest_data = DESTINATIONS.get(t["destination"], {})
        emoji = dest_data.get("emoji", "🌍")
        text += f"{emoji} **{t['destination']}** — {t['visits']}x visité\n"
        total_trips += t["visits"]

    not_visited = [d for d in DESTINATIONS if d not in [t["destination"] for t in trips]]
    if not_visited:
        text += f"\n🔒 **Non visités :** {', '.join(not_visited)}"

    text += f"\n\n✈️ Total voyages : **{total_trips}**"

    if total_trips >= len(DESTINATIONS):
        text += "\n\n🏆 **Globe-trotter accompli ! Tu as visité toutes les destinations !**\n🎖️ +100 Prestige !"
        async with aiosqlite.connect(DB_PATH) as db2:
            async with db2.execute("SELECT 1 FROM user_badges WHERE user_id=? AND badge='globe_trotter'", (user.id,)) as cur:
                if not await cur.fetchone():
                    await increment_field(user.id, "prestige", 100)
                    await db2.execute("INSERT INTO user_badges (user_id, badge, earned_at) VALUES (?,?,?)", (user.id, "globe_trotter", now()))
                    await db2.commit()

    await update.message.reply_text(text, parse_mode="Markdown")


async def process_travel_maintenance():
    """Remet à zéro les `travel_until` expirés (fin de voyage + cooldown)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET travel_until=0 WHERE travel_until < ?", (now(),))
        await db.commit()