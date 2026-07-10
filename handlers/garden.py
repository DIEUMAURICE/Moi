import random
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, update_field, increment_field
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, fmt_time, now, roll_success
from config import PLANTS, SEASON_EFFECTS
from handlers.competitions import on_xp_gain   # ← AJOUT
from handlers.missions import update_mission_progress

MAX_PLOTS = 10


def get_season_multiplier() -> float:
    """Retourne le multiplicateur de jardinage en fonction de la saison."""
    import datetime
    m = datetime.datetime.utcnow().month
    if m in (12, 1, 2):
        season = "hiver"
    elif m in (3, 4, 5):
        season = "printemps"
    elif m in (6, 7, 8):
        season = "été"
    else:
        season = "automne"
    return SEASON_EFFECTS.get(season, {}).get("garden_mult", 1.0)


async def get_garden(user_id: int):
    """Récupère toutes les parcelles d'un utilisateur."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM garden WHERE user_id = ? ORDER BY plot_id",
            (user_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows] if rows else []


@require_registered
async def cmd_jardin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    plots = await get_garden(user.id)
    t = now()
    season_mult = get_season_multiplier()

    if not plots:
        plants_text = "\n".join(
            f"• {data['emoji']} **{plant}** — {fmt_time(int(data['grow_time'] / season_mult))} — 💰 {fmt(int(data['value'] * season_mult))}"
            for plant, data in PLANTS.items()
        )
        await update.message.reply_text(
            f"🌿 **Ton jardin est vide !**\n\n"
            f"Tu as {MAX_PLOTS} parcelles disponibles.\n\n"
            f"**Plantes disponibles :**\n{plants_text}\n\n"
            f"_/planter [plante] pour commencer !_",
            parse_mode="Markdown"
        )
        return

    text = f"🌿 **Ton jardin ({len(plots)}/{MAX_PLOTS} parcelles)**\n\n"
    for plot in plots:
        data = PLANTS.get(plot["plant_type"], {})
        needed = int(data.get("grow_time", 3600) / season_mult)
        elapsed = t - plot["planted_at"]
        ready = elapsed >= needed
        remaining = max(0, needed - elapsed)
        water_needed = data.get("water_needed", 1)
        water_count = plot.get("water_count", 0)

        status = "✅ PRÊTE !" if ready else f"⏳ {fmt_time(remaining)}"
        water_status = f"💧 Eau : {water_count}/{water_needed}"
        illegal = "⚠️ Illégale" if data.get("illegal") else ""

        text += (
            f"#{plot['plot_id']} {data.get('emoji','🌱')} **{plot['plant_type']}** {illegal}\n"
            f"  {status} | {water_status}\n"
            f"  💰 Valeur (saison) : {fmt(int(data.get('value', 0) * season_mult))}\n\n"
        )

    text += "_/planter [plante] | /arroser [id] | /recolter [id]_"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
@require_free
@cooldown("plant_cooldown", 5, "⏳ Attends un instant avant de replanter.")
async def cmd_planter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    plots = await get_garden(user.id)

    if len(plots) >= MAX_PLOTS:
        await update.message.reply_text(
            f"❌ Ton jardin est plein ! ({MAX_PLOTS}/{MAX_PLOTS} parcelles)\n"
            "Récolte d'abord les plantes prêtes avec /recolter [id]."
        )
        return

    if not context.args:
        season_mult = get_season_multiplier()
        text = "🌱 **Plantes disponibles**\n\n"
        for plant, data in PLANTS.items():
            illegal = "⚠️ Illégal" if data.get("illegal") else ""
            text += (
                f"{data['emoji']} **{plant}** {illegal}\n"
                f"  ⏱️ {fmt_time(int(data['grow_time'] / season_mult))}\n"
                f"  💰 Valeur (saison) : {fmt(int(data['value'] * season_mult))}\n"
                f"  💧 Arrosages : {data['water_needed']}x\n\n"
            )
        text += "_/planter [nom] pour planter_"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    plant_name = " ".join(context.args).capitalize()
    matched = None
    for p in PLANTS:
        if p.lower() == plant_name.lower():
            matched = p
            break
    if not matched:
        await update.message.reply_text("❌ Plante inconnue. /planter pour voir la liste.")
        return

    plant_data = PLANTS[matched]
    season_mult = get_season_multiplier()

    # Vérification pour plantes illégales (simplifiée : accepter 'oui' ou '--force')
    if plant_data.get("illegal"):
        if len(context.args) >= 2 and context.args[1].lower() in ("oui", "yes", "--force"):
            pass  # confirmation reçue
        else:
            await update.message.reply_text(
                f"⚠️ **Attention !** {matched} est une plante illégale.\n"
                f"Planter et récolter peut conduire à la prison.\n\n"
                f"Si tu veux continuer, tape : `/planter {matched} oui`"
            )
            return

    # Coût des graines (basé sur la valeur saisonnière)
    seed_cost = int(plant_data["value"] * season_mult // 10)
    if u["balance"] < seed_cost:
        await update.message.reply_text(
            f"❌ Graines trop chères !\n"
            f"💰 Coût des graines (saison) : {fmt(seed_cost)}\n"
            f"💵 Ton solde : {fmt(u['balance'])}"
        )
        return

    await update_balance(user.id, -seed_cost)
    # Mise à jour de la mission "Planter 3 plantes"
    await update_mission_progress(user.id, "plant", 1)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO garden (user_id, plant_type, planted_at, watered_at, water_count, ready) VALUES (?,?,?,?,0,0)",
            (user.id, matched, now(), now())
        )
        await db.commit()

    await update.message.reply_text(
        f"🌱 **{matched} planté !**\n\n"
        f"{plant_data['emoji']} Dans ta parcelle #{len(plots) + 1}\n"
        f"💰 Graines (saison) : {fmt(seed_cost)}\n"
        f"⏱️ Temps de croissance (saison) : {fmt_time(int(plant_data['grow_time'] / season_mult))}\n"
        f"💧 Arrosages nécessaires : {plant_data['water_needed']}x\n"
        f"💰 Valeur à la récolte (saison) : {fmt(int(plant_data['value'] * season_mult))}\n\n"
        f"_/arroser [id] régulièrement pour de meilleurs rendements !_",
        parse_mode="Markdown"
    )


@require_registered
@cooldown("water_cooldown", 3600, "⏳ Cette parcelle vient d'être arrosée ! Reviens dans 1h.")
async def cmd_arroser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    plots = await get_garden(user.id)

    if not context.args:
        await update.message.reply_text("Usage : /arroser [id parcelle]\n/jardin pour voir tes parcelles.")
        return

    try:
        plot_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return

    plot = next((p for p in plots if p["plot_id"] == plot_id), None)
    if not plot:
        await update.message.reply_text("❌ Parcelle introuvable.")
        return

    plant_data = PLANTS.get(plot["plant_type"], {})
    water_needed = plant_data.get("water_needed", 1)
    water_count = plot.get("water_count", 0)

    if water_count >= water_needed:
        await update.message.reply_text(f"💧 Cette plante a déjà assez d'eau (max {water_needed}).")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE garden SET water_count = water_count + 1, watered_at = ? WHERE plot_id = ?",
            (now(), plot_id)
        )
        await db.commit()

    new_water = water_count + 1
    await update.message.reply_text(
        f"💧 **{plot['plant_type']} arrosé !**\n\n"
        f"💧 Arrosages : {new_water}/{water_needed}\n"
        f"{'✅ Suffisamment arrosé !' if new_water >= water_needed else f'⏳ Encore {water_needed - new_water}x nécessaire.'}"
    )


@require_registered
@require_free
@cooldown("harvest_cooldown", 5, "⏳ Attends un instant avant de récolter à nouveau.")
async def cmd_recolter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    plots = await get_garden(user.id)
    t = now()
    season_mult = get_season_multiplier()

    if not context.args:
        # Récolte groupée
        ready_plots = []
        for plot in plots:
            plant_data = PLANTS.get(plot["plant_type"], {})
            grow_time = int(plant_data.get("grow_time", 0) / season_mult)
            ready = t >= plot["planted_at"] + grow_time
            if ready:
                ready_plots.append(plot)
        if not ready_plots:
            await update.message.reply_text("🌿 Aucune plante prête à récolter.")
            return

        total = 0
        harvested = []
        for plot in ready_plots:
            plant_data = PLANTS.get(plot["plant_type"], {})
            water_count = plot.get("water_count", 0)
            water_needed = plant_data.get("water_needed", 1)
            water_bonus = min(1.5, 1.0 + (water_count / max(1, water_needed)) * 0.5)
            base_val = plant_data.get("value", 0)
            value = int(base_val * water_bonus * season_mult)
            total += value
            harvested.append((plot["plant_type"], value, plant_data.get("emoji", "🌿")))

            # Risque pour plantes illégales
            if plant_data.get("illegal"):
                arrest_risk = 0.2 - (u.get("karma", 0) / 5000)
                if random.random() < max(0.05, min(0.4, arrest_risk)):
                    jail_time = 7200
                    await update_field(user.id, "prison_until", now() + jail_time)
                    await update.message.reply_text(
                        f"🚨 **ARRESTATION !**\n\n"
                        f"La police t'a surpris en récoltant du **{plot['plant_type']}** !\n"
                        f"⛓️ Prison : {fmt_time(jail_time)}\n"
                        f"🌿 La plante a été confisquée.",
                        parse_mode="Markdown"
                    )
                    total -= value
                    harvested.pop()
                    continue

            async with aiosqlite.connect(DB_PATH) as db2:
                await db2.execute("DELETE FROM garden WHERE plot_id = ?", (plot["plot_id"],))
                await db2.commit()

        if total > 0:
            await update_balance(user.id, total)
            await increment_field(user.id, "plants_grown", len(harvested))
            xp_gain = len(harvested) * 50
            # Mise à jour de la mission "Récolter des plantes"
            await update_mission_progress(user.id, "harvest", 1)
            await increment_field(user.id, "xp", xp_gain)
            await on_xp_gain(user.id, xp_gain)   # ← AJOUT

        text = f"🌾 **Récolte groupée !**\n\n"
        for name, val, emoji in harvested:
            text += f"{emoji} {name} — {fmt(val)}\n"
        if total > 0:
            text += f"\n💰 **Total : {fmt(total)}**\n"
            text += f"✨ +{len(harvested) * 50} XP"
        else:
            text += "\n❌ Aucune récolte valide."
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    # Récolte d'une parcelle spécifique
    try:
        plot_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide. /recolter sans argument pour tout récolter.")
        return

    plot = next((p for p in plots if p["plot_id"] == plot_id), None)
    if not plot:
        await update.message.reply_text("❌ Parcelle introuvable.")
        return

    plant_data = PLANTS.get(plot["plant_type"], {})
    grow_time = int(plant_data.get("grow_time", 0) / season_mult)
    if t < plot["planted_at"] + grow_time:
        rem = (plot["planted_at"] + grow_time) - t
        await update.message.reply_text(f"⏳ Pas encore prête ! Attends {fmt_time(rem)}.")
        return

    water_count = plot.get("water_count", 0)
    water_needed = plant_data.get("water_needed", 1)
    water_bonus = min(1.5, 1.0 + (water_count / max(1, water_needed)) * 0.5)
    base_val = plant_data.get("value", 0)
    value = int(base_val * water_bonus * season_mult)

    # Risque pour plantes illégales
    arrested = False
    if plant_data.get("illegal"):
        arrest_risk = 0.2 - (u.get("karma", 0) / 5000)
        if random.random() < max(0.05, min(0.4, arrest_risk)):
            jail_time = 7200
            await update_field(user.id, "prison_until", now() + jail_time)
            arrested = True
            await update.message.reply_text(
                f"🚨 **ARRESTATION !**\n\n"
                f"La police t'a surpris en récoltant du **{plot['plant_type']}** !\n"
                f"⛓️ Prison : {fmt_time(jail_time)}",
                parse_mode="Markdown"
            )
            async with aiosqlite.connect(DB_PATH) as db2:
                await db2.execute("DELETE FROM garden WHERE plot_id = ?", (plot_id,))
                await db2.commit()
            return

    async with aiosqlite.connect(DB_PATH) as db2:
        await db2.execute("DELETE FROM garden WHERE plot_id = ?", (plot_id,))
        await db2.commit()

    if not arrested:
        await update_balance(user.id, value)
        await increment_field(user.id, "plants_grown")
        xp_gain = 50
        # Mise à jour de la mission "Récolter des plantes"
        await update_mission_progress(user.id, "harvest", 1)
        await increment_field(user.id, "xp", xp_gain)
        await on_xp_gain(user.id, xp_gain)   # ← AJOUT

        msg = (
            f"🌾 **Récolte !**\n\n"
            f"{plant_data.get('emoji','🌿')} **{plot['plant_type']}**\n"
            f"💰 Valeur (saison) : {fmt(value)}\n"
        )
        if water_bonus > 1.0:
            msg += f"💧 Bonus arrosage : x{round(water_bonus, 1)}\n"
        msg += f"✨ +{xp_gain} XP"
        await update.message.reply_text(msg, parse_mode="Markdown")


@require_registered
async def cmd_vendrecolte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Vendre toutes les plantes prêtes à récolter (appel de /recolter groupé)."""
    await cmd_recolter(update, context)


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance : nettoyer les parcelles oubliées
# ─────────────────────────────────────────────────────────────────────────────
async def process_garden_maintenance():
    """Supprime les parcelles dont la plante est morte (pas arrosée / trop vieille)."""
    from config import PLANTS
    async with aiosqlite.connect(DB_PATH) as db:
        t = now()
        for plant_type, data in PLANTS.items():
            grow_time = data["grow_time"]
            await db.execute(
                "DELETE FROM garden WHERE plant_type = ? AND planted_at + ? < ?",
                (plant_type, grow_time * 2, t)
            )
        await db.commit()