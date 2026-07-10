import random
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_active_event, get_user, update_field, increment_field, now
from utils.decorators import require_registered
from utils.helpers import fmt, fmt_time, parse_amount
from config import WORLD_EVENTS


@require_registered
async def cmd_evenements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche l'événement actif et l'historique."""
    user = update.effective_user
    event = await get_active_event()

    text = "🌍 **Événements mondiaux**\n\n"

    if event:
        time_left = max(0, event["ends_at"] - now())
        severity = event.get("severity", 0.3)
        text += (
            f"⚡ **ÉVÉNEMENT EN COURS :**\n"
            f"📋 {event['name']}\n"
            f"⏳ Se termine dans : {fmt_time(time_left)}\n"
            f"⚡ Intensité : {int(severity * 100)}%\n\n"
        )

        effect_desc = {
            "market_crash":    "📉 Marché en chute libre (-30%)",
            "market_boom":     "📈 Marché en pleine explosion (+30%)",
            "health_crisis":   "🏥 Crise sanitaire : coûts médicaux +100%",
            "tech_boom":       "💻 Boom technologique : actions Tech +100%",
            "trade_war":       "⚔️ Guerre commerciale : impôts +5%",
            "disaster":        "🌪️ Catastrophe naturelle : propriétés -20% valeur",
            "energy_shift":    "⚡ Révolution énergétique : carburant -50%",
            "happiness_boost": "🎉 Festival mondial : +10 bonheur/jour",
            "crime_wave":      "🔫 Vague de criminalité : crimes +20% succès mais +50% prison",
            "economic_crisis": "💥 Crise économique : revenus divisés par 2",
            "gold_rush":       "💰 Ruée vers l'or : gains miniers x5",
        }
        text += f"📊 Effet : {effect_desc.get(event['effect'], 'Impact global')}\n\n"
    else:
        text += "_Aucun événement actif en ce moment._\n\n"

    # Afficher les 3 derniers événements passés
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT name, started_at, ends_at FROM world_events WHERE active=0 AND ends_at > 0 ORDER BY ends_at DESC LIMIT 3"
        ) as cur:
            past = await cur.fetchall()

    if past:
        text += "📜 **Derniers événements :**\n"
        for ev in past:
            date = fmt_time(ev["started_at"]) if ev["started_at"] else "?"
            text += f"• {ev['name']} — {date}\n"

    text += "\n_Utilise /eventinfo [nom] pour plus de détails._"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
async def cmd_eventinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les détails d'un événement spécifique."""
    if not context.args:
        await update.message.reply_text("Usage : /eventinfo [nom de l'événement]")
        return

    name = " ".join(context.args).lower()
    event = None
    for e in WORLD_EVENTS:
        if e["name"].lower() == name or any(w in e["name"].lower() for w in name.split()):
            event = e
            break

    if not event:
        await update.message.reply_text("❌ Événement inconnu. /evenements pour voir la liste.")
        return

    effect_desc = {
        "market_crash":    "Le marché boursier s'effondre, les actions perdent 30% de leur valeur.",
        "market_boom":     "Les actions grimpent de 30% en quelques heures.",
        "health_crisis":   "Les hôpitaux sont submergés, les soins coûtent deux fois plus cher.",
        "tech_boom":       "Les entreprises technologiques explosent, actions Tech x2.",
        "trade_war":       "Les taxes douanières augmentent, impôts +5% pour tous.",
        "disaster":        "Une catastrophe naturelle dévalue les propriétés de 20%.",
        "energy_shift":    "Découverte énergétique, le carburant est deux fois moins cher.",
        "happiness_boost": "Festivals mondiaux, tous les joueurs gagnent +10 de bonheur par jour.",
        "crime_wave":      "Les criminels sont plus audacieux : +20% chance de succès mais peine +50%.",
        "economic_crisis": "Chômage massif, les salaires sont divisés par deux.",
        "gold_rush":       "Nouveaux gisements, les gains miniers sont multipliés par 5.",
    }

    text = (
        f"📋 **{event['name']}**\n\n"
        f"🔧 Effet : {effect_desc.get(event['effect'], event['effect'])}\n"
        f"⚡ Intensité par défaut : {int(event.get('severity', 0.3) * 100)}%\n"
        f"🎲 Probabilité d'apparition : {int(event.get('weight', 10))}%\n\n"
        f"💡 _Conseil : adapte tes actions pendant l'événement pour maximiser les gains ou minimiser les pertes._"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def trigger_random_event(bot, user_ids: list):
    """Déclenche un événement mondial aléatoire et notifie tous les joueurs."""
    # Sélection pondérée par 'weight'
    total_weight = sum(e.get("weight", 10) for e in WORLD_EVENTS)
    roll = random.uniform(0, total_weight)
    cumul = 0
    chosen = WORLD_EVENTS[0]
    for e in WORLD_EVENTS:
        cumul += e.get("weight", 10)
        if roll <= cumul:
            chosen = e
            break

    duration = random.randint(6, 48) * 3600  # entre 6 et 48 heures
    severity = chosen.get("severity", random.uniform(0.2, 0.6))

    async with aiosqlite.connect(DB_PATH) as db:
        # Désactiver l'ancien événement
        await db.execute("UPDATE world_events SET active=0")
        await db.execute(
            "INSERT INTO world_events (name, effect, severity, started_at, ends_at, active) VALUES (?,?,?,?,?,1)",
            (chosen["name"], chosen["effect"], severity, now(), now() + duration)
        )
        await db.commit()

    # Appliquer les effets immédiats sur le marché
    if chosen["effect"] == "market_crash":
        from database import get_market_price, update_market_price
        from config import ASSETS
        for asset in ASSETS:
            price = await get_market_price(asset["name"])
            await update_market_price(asset["name"], price * (1 - severity))
    elif chosen["effect"] == "market_boom":
        from database import get_market_price, update_market_price
        from config import ASSETS
        for asset in ASSETS:
            price = await get_market_price(asset["name"])
            await update_market_price(asset["name"], price * (1 + severity * 0.8))

    # Préparer l'annonce
    flavor = {
        "market_crash":    "Les cours s'effondrent ! Les investisseurs paniquent.",
        "market_boom":     "Une bulle spéculative fait flamber les actions !",
        "health_crisis":   "Un virus se répand. Restez chez vous.",
        "tech_boom":       "Une innovation révolutionnaire secoue la bourse.",
        "trade_war":       "Les tensions internationales font monter les taxes.",
        "disaster":        "Un séisme endommage de nombreuses infrastructures.",
        "energy_shift":    "Une nouvelle source d'énergie propre est découverte.",
        "happiness_boost": "La fête mondiale bat son plein !",
        "crime_wave":      "Les gangs font la loi dans les rues.",
        "economic_crisis": "Les entreprises licencient en masse.",
        "gold_rush":       "Des prospecteurs trouvent de l'or dans tout le pays.",
    }

    announcement = (
        f"🌍 **ÉVÉNEMENT MONDIAL : {chosen['name']}**\n\n"
        f"{flavor.get(chosen['effect'], 'Le monde change...')}\n\n"
        f"⚡ Effet : **{chosen['effect'].replace('_', ' ').title()}**\n"
        f"📉 Intensité : {int(severity * 100)}%\n"
        f"⏳ Durée : {fmt_time(duration)}\n\n"
        f"_Utilise /evenements pour plus d'informations._"
    )

    # Notifier les joueurs (limité à 500 pour éviter les timeout)
    count = 0
    for uid in user_ids[:500]:
        try:
            await bot.send_message(uid, announcement, parse_mode="Markdown")
            count += 1
        except Exception:
            pass
    print(f"📢 Événement '{chosen['name']}' notifié à {count} joueurs.")


# ─────────────────────────────────────────────────────────────────────────────
# Effets des événements sur les actions des joueurs (à appeler depuis autres handlers)
# ─────────────────────────────────────────────────────────────────────────────
async def get_event_effect() -> dict:
    """Retourne l'effet de l'événement actif sous forme de multiplicateurs."""
    event = await get_active_event()
    if not event:
        return {
            "work_mult": 1.0,
            "crime_mult": 1.0,
            "crime_jail_mult": 1.0,
            "health_cost_mult": 1.0,
            "tax_mult": 1.0,
            "happiness_gain_mult": 1.0,
            "market_mult": 1.0,
        }

    effect = event["effect"]
    severity = event.get("severity", 0.3)

    defaults = {
        "work_mult": 1.0,
        "crime_mult": 1.0,
        "crime_jail_mult": 1.0,
        "health_cost_mult": 1.0,
        "tax_mult": 1.0,
        "happiness_gain_mult": 1.0,
        "market_mult": 1.0,
    }

    if effect == "economic_crisis":
        defaults["work_mult"] = 0.5
    elif effect == "gold_rush":
        defaults["work_mult"] = 5.0  # pour le travail minier, mais à adapter
    elif effect == "crime_wave":
        defaults["crime_mult"] = 1.2
        defaults["crime_jail_mult"] = 1.5
    elif effect == "health_crisis":
        defaults["health_cost_mult"] = 2.0
    elif effect == "trade_war":
        defaults["tax_mult"] = 1.05
    elif effect == "happiness_boost":
        defaults["happiness_gain_mult"] = 1.5
    elif effect == "market_crash":
        defaults["market_mult"] = 1 - severity
    elif effect == "market_boom":
        defaults["market_mult"] = 1 + severity * 0.8

    return defaults


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance : nettoyer les événements expirés
# ─────────────────────────────────────────────────────────────────────────────
async def process_expired_events():
    """Réinitialise les effets après la fin d'un événement."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Désactiver les événements expirés
        await db.execute("UPDATE world_events SET active=0 WHERE ends_at < ?", (now(),))
        await db.commit()