# handlers/health.py
import random
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, update_field, increment_field
from utils.decorators import require_registered, cooldown, require_free
from utils.helpers import fmt, fmt_time, now, health_bar
from config import MEDICINES, GYM_COST, GYM_ENERGY_BONUS, DOCTOR_BASE
from handlers.competitions import on_xp_gain


# ─────────────────────────────────────────────────────────────────────────────
# Helper : vérifier si hospitalisé
# ─────────────────────────────────────────────────────────────────────────────
async def _check_hospitalized(u: dict) -> tuple[bool, str]:
    """Vérifie si le joueur est hospitalisé et renvoie (True, message)."""
    if u.get("hospital_until", 0) > now():
        remaining = u["hospital_until"] - now()
        return True, f"🏥 Tu es hospitalisé pour encore {fmt_time(remaining)}. Repose-toi !"
    return False, None


# ─────────────────────────────────────────────────────────────────────────────
# cmd_sante (inchangé)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
async def cmd_sante(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    def status(val):
        if val >= 80: return "🟢 Excellent"
        if val >= 60: return "🟡 Bon"
        if val >= 40: return "🟠 Moyen"
        if val >= 20: return "🔴 Mauvais"
        return "⚫ Critique"

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM insurance WHERE user_id=?", (user.id,)) as cur:
            ins = await cur.fetchone()
    ins_text = f"✅ Assuré(e) ({ins[1]})" if ins else "❌ Non assuré(e)"

    hospital_until = u.get("hospital_until", 0)
    if hospital_until > now():
        remaining = hospital_until - now()
        hospital_status = f"\n🏥 **Hospitalisé** : {fmt_time(remaining)} restant"
    else:
        hospital_status = ""

    text = (
        f"❤️ **Santé de {user.full_name}**\n\n"
        f"❤️ Santé : [{health_bar(u['health'])}] {u['health']}% — {status(u['health'])}\n"
        f"⚡ Énergie : [{health_bar(u['energy'])}] {u['energy']}% — {status(u['energy'])}\n"
        f"😊 Bonheur : [{health_bar(u['happiness'])}] {u['happiness']}% — {status(u['happiness'])}\n"
        f"🍽️ Faim : [{health_bar(u['hunger'])}] {u['hunger']}% — {status(u['hunger'])}\n"
        f"😰 Stress : [{health_bar(u['stress'])}] {u['stress']}% — {status(100 - u['stress'])}\n\n"
        f"🏥 Assurance : {ins_text}{hospital_status}\n\n"
        f"━━━ **Actions** ━━━\n"
        f"/medecin — consulter un médecin ({fmt(DOCTOR_BASE)})\n"
        f"/gym — aller à la salle de sport ({fmt(GYM_COST)})\n"
        f"/medicaments — acheter des médicaments\n"
        f"/assurance — souscrire une assurance\n"
        f"/dormir — se reposer\n"
        f"/manger — manger"
    )

    warnings = []
    if u["health"] < 30:   warnings.append("⚠️ Ta santé est critique ! Va voir un médecin !")
    if u["energy"] < 20:   warnings.append("⚠️ Tu es épuisé(e) ! Dors avant de travailler.")
    if u["hunger"] < 20:   warnings.append("⚠️ Tu as très faim ! Mange quelque chose.")
    if u["stress"] > 80:   warnings.append("⚠️ Ton stress est dangereux ! Prends des vacances.")

    if warnings:
        text += "\n\n" + "\n".join(warnings)

    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# cmd_medecin (avec assurance fonctionnelle)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("medecin_last", 3600, "⏳ Le médecin est déjà venu récemment. Reviens dans 1h.")
async def cmd_medecin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    hospitalized, msg = await _check_hospitalized(u)
    if hospitalized:
        await update.message.reply_text(msg)
        return

    if u["health"] >= 100:
        await update.message.reply_text("❤️ Ta santé est parfaite ! Pas besoin du médecin.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        # Récupérer l'assurance active (depuis moins de 30 jours)
        async with db.execute(
            "SELECT type, coverage, claims, since FROM insurance WHERE user_id=? AND since > ?",
            (user.id, now() - 30*86400)
        ) as cur:
            ins = await cur.fetchone()

        base_cost = DOCTOR_BASE
        discount = 1.0
        if ins:
            coverage = ins[1]      # 0.3, 0.5, 0.7, 0.9
            claims = ins[2] or 0
            max_claims = {"basique": 3, "standard": 5, "premium": 10, "vip": 20}.get(ins[0], 3)
            if claims < max_claims:
                discount = 1.0 - coverage
                # Incrémenter les sinistres après la consultation
                await db.execute("UPDATE insurance SET claims = claims + 1 WHERE user_id=?", (user.id,))
        cost = int(base_cost * discount)

    if u["balance"] < cost:
        await update.message.reply_text(
            f"❌ Fonds insuffisants.\n🏥 Consultation : {fmt(cost)}\n💵 Solde : {fmt(u['balance'])}\n"
            f"💡 Prends une assurance pour réduire les frais !"
        )
        return

    heal = random.randint(20, 40)
    new_health = min(100, u["health"] + heal)
    new_stress = max(0, u["stress"] - 10)

    await update_balance(user.id, -cost)
    await update_field(user.id, "health", new_health)
    await update_field(user.id, "stress", new_stress)
    await update_field(user.id, "medecin_last", now())

    diagnoses = [
        "Légère fatigue. Repos recommandé.",
        "Carence en vitamines. Prescriptions données.",
        "Stress élevé. Activité physique recommandée.",
        "Excellente constitution. Continuez ainsi !",
        "Quelques tensions musculaires. Massages conseillés.",
    ]

    await update.message.reply_text(
        f"🏥 **Consultation médicale**\n\n"
        f"👨‍⚕️ Diagnostic : {random.choice(diagnoses)}\n\n"
        f"❤️ Santé : {u['health']}% → **{new_health}%**\n"
        f"💰 Coût : {fmt(cost)}\n"
        f"{'🔖 Remboursement appliqué (assurance)' if discount < 1 else ''}",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# cmd_hopital (avec assurance)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("hopital_last", 86400, "⏳ L'hôpital ne peut être utilisé qu'une fois par jour.")
async def cmd_hopital(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    hospitalized, msg = await _check_hospitalized(u)
    if hospitalized:
        await update.message.reply_text(msg)
        return

    if u["health"] >= 50:
        await update.message.reply_text("🏥 Tu n'as pas besoin d'être hospitalisé(e). /medecin suffit.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT type, coverage, claims, since FROM insurance WHERE user_id=? AND since > ?",
            (user.id, now() - 30*86400)
        ) as cur:
            ins = await cur.fetchone()

        base_cost = 50_000
        discount = 1.0
        if ins:
            coverage = ins[1]
            claims = ins[2] or 0
            max_claims = {"basique": 3, "standard": 5, "premium": 10, "vip": 20}.get(ins[0], 3)
            if claims < max_claims:
                discount = 1.0 - coverage
                await db.execute("UPDATE insurance SET claims = claims + 1 WHERE user_id=?", (user.id,))

    cost = int(base_cost * discount)
    if u["balance"] < cost:
        await update.message.reply_text(
            f"❌ Fonds insuffisants pour l'hôpital.\n🏥 Coût : {fmt(cost)}\n💡 Essaie /medicaments pour un soin d'urgence."
        )
        return

    stay_hours = random.randint(2, 8) * 3600
    await update_balance(user.id, -cost)
    await update_field(user.id, "health", min(100, u["health"] + 60))
    await update_field(user.id, "energy", min(100, u["energy"] + 50))
    await update_field(user.id, "hospital_until", now() + stay_hours)
    await update_field(user.id, "hopital_last", now())

    await update.message.reply_text(
        f"🏥 **Hospitalisation !**\n\n"
        f"Tu es hospitalisé(e) pour {fmt_time(stay_hours)}.\n"
        f"❤️ Santé : {u['health']}% → {min(100, u['health'] + 60)}%\n"
        f"💰 Coût : {fmt(cost)}\n"
        f"{'🔖 Remboursement appliqué' if discount < 1 else ''}\n\n"
        f"_Tu ne pourras pas agir pendant ce temps._",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# cmd_gym (inchangé)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("gym_last", 43200, "⏳ Tu es fatigué de la salle. Reviens dans 12h.")
async def cmd_gym(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    hospitalized, msg = await _check_hospitalized(u)
    if hospitalized:
        await update.message.reply_text(msg)
        return

    if u["balance"] < GYM_COST:
        await update.message.reply_text(f"❌ La salle de sport coûte {fmt(GYM_COST)}.")
        return

    if u["energy"] < 20:
        await update.message.reply_text("😴 Tu es trop fatigué(e) pour faire du sport !")
        return

    if u["hunger"] < 10:
        await update.message.reply_text("🍽️ Tu as trop faim pour faire du sport ! Mange d'abord.")
        return

    await update_balance(user.id, -GYM_COST)

    energy_cost = random.randint(25, 35)
    health_gain = random.randint(5, 15)
    happiness_gain = random.randint(5, 15)
    stress_loss = random.randint(10, 20)
    hunger_loss = random.randint(10, 20)

    from database import upgrade_skill
    force_boost = random.random() < 0.3
    if force_boost:
        await upgrade_skill(user.id, "Force")
        force_text = "\n💪 +1 niveau en Force !"
    else:
        force_text = ""

    new_energy = max(0, u["energy"] - energy_cost)
    new_health = min(100, u["health"] + health_gain)
    new_happiness = min(100, u["happiness"] + happiness_gain)
    new_stress = max(0, u["stress"] - stress_loss)
    new_hunger = max(0, u["hunger"] - hunger_loss)

    await update_field(user.id, "energy", new_energy)
    await update_field(user.id, "health", new_health)
    await update_field(user.id, "happiness", new_happiness)
    await update_field(user.id, "stress", new_stress)
    await update_field(user.id, "hunger", new_hunger)
    xp_gain = 80
    await increment_field(user.id, "xp", xp_gain)
    await on_xp_gain(user.id, xp_gain)
    await update_field(user.id, "gym_last", now())

    workouts = [
        "🏋️ Musculation intensive",
        "🏃 Cardio 5km",
        "🤸 Yoga & stretching",
        "🥊 Boxe",
        "🏊 Natation",
        "🚴 Vélo d'appartement",
    ]

    await update.message.reply_text(
        f"💪 **Séance de sport !**\n\n"
        f"🏋️ Entraînement : {random.choice(workouts)}\n\n"
        f"❤️ Santé : +{health_gain}% → {new_health}%\n"
        f"😊 Bonheur : +{happiness_gain}% → {new_happiness}%\n"
        f"😰 Stress : -{stress_loss}% → {new_stress}%\n"
        f"⚡ Énergie : -{energy_cost}% → {new_energy}%\n"
        f"🍽️ Faim : -{hunger_loss}% → {new_hunger}%\n"
        f"✨ +{xp_gain} XP{force_text}\n"
        f"💰 Coût : {fmt(GYM_COST)}",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# cmd_medicaments (inchangé)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("medicaments_last", 3600, "⏳ Tu ne peux prendre des médicaments qu'une fois par heure.")
async def cmd_medicaments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    hospitalized, msg = await _check_hospitalized(u)
    if hospitalized:
        await update.message.reply_text(msg)
        return

    if not context.args:
        text = "💊 **Pharmacie**\n\n"
        for name, data in MEDICINES.items():
            text += (
                f"{data['emoji']} **{name}**\n"
                f"  💰 Prix : {fmt(data['price'])} | ❤️ +{data['health']}% santé\n\n"
            )
        text += "_/medicaments [nom] pour acheter_"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    med_name = " ".join(context.args).capitalize()
    matched = None
    for m in MEDICINES:
        if m.lower() == med_name.lower():
            matched = m
            break
    if not matched:
        await update.message.reply_text("❌ Médicament inconnu.")
        return

    med = MEDICINES[matched]
    if u["balance"] < med["price"]:
        await update.message.reply_text(f"❌ Fonds insuffisants. Coût : {fmt(med['price'])}")
        return

    if u["health"] >= 100:
        await update.message.reply_text("❤️ Tu es déjà en pleine santé ! Pas besoin de médicaments.")
        return

    await update_balance(user.id, -med["price"])
    new_health = min(100, u["health"] + med["health"])
    await update_field(user.id, "health", new_health)
    await update_field(user.id, "medicaments_last", now())

    await update.message.reply_text(
        f"{med['emoji']} **{matched} pris !**\n\n"
        f"❤️ Santé : {u['health']}% → **{new_health}%**\n"
        f"💰 Coût : {fmt(med['price'])}",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# cmd_assurance (avec durée de 30 jours et plafond de sinistres)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("assurance_subscribe", 3600, "⏳ Attends un peu avant de modifier ton assurance.")
async def cmd_assurance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    plans = {
        "basique":   {"cost": 5_000,   "coverage": 0.3, "premium": False, "emoji": "🔵", "claims_limit": 3},
        "standard":  {"cost": 20_000,  "coverage": 0.5, "premium": False, "emoji": "🟡", "claims_limit": 5},
        "premium":   {"cost": 100_000, "coverage": 0.7, "premium": True,  "emoji": "🟢", "claims_limit": 10},
        "vip":       {"cost": 1_000_000,"coverage": 0.9, "premium": True,  "emoji": "💎", "claims_limit": 20},
    }

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT type, coverage, claims, since FROM insurance WHERE user_id=? AND since > ?",
            (user.id, now() - 30*86400)
        ) as cur:
            ins = await cur.fetchone()

    if not context.args:
        text = "🏥 **Assurance Santé**\n\n"
        if ins:
            plan_name = ins[0]
            plan_data = plans.get(plan_name, {})
            text += f"✅ Tu es déjà assuré(e) : Plan **{plan_name.capitalize()}**\n"
            text += f"🔖 Sinistres déclarés : {ins[2] or 0}/{plan_data.get('claims_limit', 5)}\n"
            text += f"📅 Valable jusqu'au : {fmt_time(ins[3] + 30*86400)}\n\n"
        text += "Choisir un plan :\n\n"
        for plan, data in plans.items():
            text += (
                f"{data['emoji']} **{plan.capitalize()}**\n"
                f"  💰 {fmt(data['cost'])}/mois\n"
                f"  🏥 Remboursement : {int(data['coverage'] * 100)}%\n"
                f"  🔖 {data['claims_limit']} sinistres max\n\n"
            )
        text += "_/assurance [plan] pour souscrire_"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    plan = context.args[0].lower()
    if plan not in plans:
        await update.message.reply_text(f"❌ Plan inconnu. Choix : {', '.join(plans.keys())}")
        return

    p = plans[plan]
    if u["balance"] < p["cost"]:
        await update.message.reply_text(f"❌ Fonds insuffisants. Coût : {fmt(p['cost'])}/mois")
        return

    await update_balance(user.id, -p["cost"])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO insurance (user_id, type, premium, coverage, since, claims)
            VALUES (?,?,?,?,?,0)
            ON CONFLICT(user_id) DO UPDATE SET
                type=?, premium=?, coverage=?, since=?, claims=0
        """, (user.id, plan, 1 if p["premium"] else 0, p["coverage"], now(),
              plan, 1 if p["premium"] else 0, p["coverage"], now()))
        await db.commit()

    await update.message.reply_text(
        f"🏥 **Assurance souscrite !**\n\n"
        f"📋 Plan : **{plan.capitalize()}**\n"
        f"💰 Coût mensuel : {fmt(p['cost'])}\n"
        f"🔖 Remboursement : {int(p['coverage'] * 100)}%\n"
        f"🔢 Sinistres max : {p['claims_limit']}\n"
        f"📅 Valable 30 jours\n\n"
        f"_Tu économiseras sur les frais médicaux jusqu'à épuisement des sinistres._",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance : dégradation quotidienne de la santé et réinitialisation hospitalisation
# ─────────────────────────────────────────────────────────────────────────────
async def process_health_maintenance():
    """Détériore légèrement la santé de tous les joueurs chaque jour (fatigue naturelle)."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Réinitialiser hospital_until expirés
        await db.execute("UPDATE users SET hospital_until=0 WHERE hospital_until < ?", (now(),))
        # Dégradation quotidienne légère
        await db.execute(
            "UPDATE users SET health = MAX(0, health - ABS(RANDOM() % 5) - 1), "
            "energy = MAX(0, energy - ABS(RANDOM() % 7) - 2), "
            "stress = MIN(100, stress + ABS(RANDOM() % 5) + 1), "
            "hunger = MAX(0, hunger - ABS(RANDOM() % 11) - 5) "
            "WHERE registered = 1 AND hospital_until <= ?",
            (now(),)
        )
        # Supprimer les assurances expirées (plus de 30 jours)
        await db.execute("DELETE FROM insurance WHERE since < ?", (now() - 30*86400,))
        await db.commit()