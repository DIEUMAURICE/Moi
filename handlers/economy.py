# handlers/economy.py
import random
import aiosqlite
import logging
import html
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import ContextTypes

from database import (
    DB_PATH, get_user, update_balance, update_field, increment_field,
    get_top_rich, add_life_journal, get_user_company,
    transfer_money, debit_balance, db_connection
)
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, fmt_time, now, parse_amount, get_karma_multiplier
from utils.simulation import compute_condition_penalties
from config import (
    DAILY_MIN, DAILY_MAX, WORK_COOLDOWN, DAILY_COOLDOWN, JOBS, DIPLOME_SALARY_BONUS,
    LOTTERY_TICKET_COST, LOTTERY_JACKPOT_BASE, CITIES, EVOLUTIVE_JOBS, SEASON_EFFECTS,
    WEALTH_TAX_BRACKETS
)
from handlers.competitions import on_xp_gain, on_wealth_gain
from handlers.multiplayer import _bump_relation
from handlers.general import process_referral_progress
from handlers.missions import update_mission_progress

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Helper : multiplicateurs de ville (corrigé : utilise 'location')
# ─────────────────────────────────────────────────────────────────────────────
async def get_city_multipliers(user_id: int) -> dict:
    u = await get_user(user_id)
    city = u.get("location", "Paris")
    return CITIES.get(city, {"realestate_mult": 1.0, "vehicle_mult": 1.0,
                             "salary_mult": 1.0, "market_mult": 1.0, "crime_mult": 1.0})

# ─────────────────────────────────────────────────────────────────────────────
# Helper : effet de la saison actuelle (sans dépendance externe)
# ─────────────────────────────────────────────────────────────────────────────
def current_season() -> str:
    m = datetime.now(timezone.utc).month
    if m in (12, 1, 2):
        return "hiver"
    if m in (3, 4, 5):
        return "printemps"
    if m in (6, 7, 8):
        return "été"
    return "automne"

async def get_season_effect() -> dict:
    season = current_season()
    return SEASON_EFFECTS.get(season, {"happiness": 0, "energy": 0, "icon": "🌍"})


# ─────────────────────────────────────────────────────────────────────────────
# Bonus quotidien (réduit)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("daily_last", DAILY_COOLDOWN, "⏳ Bonus quotidien déjà récupéré ! Reviens demain.")
async def cmd_quotidien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    penalties = compute_condition_penalties(u)

    karma_mult = get_karma_multiplier(u.get("karma", 0))
    level_bonus = 1 + (u.get("level", 1) - 1) * 0.01  # réduit de 2% à 1% par niveau
    base = random.randint(DAILY_MIN, DAILY_MAX)
    amount = int(base * karma_mult * level_bonus * penalties["passive_income_mult"])
    lifestyle = penalties["score"]

    await update_balance(user.id, amount)
    await on_wealth_gain(user.id, amount)
    await update_field(user.id, "daily_last", now())
    await increment_field(user.id, "xp", 30)
    await on_xp_gain(user.id, 30)

    await update_field(user.id, "hunger", min(100, u.get("hunger", 100) + 15))
    await update_field(user.id, "happiness", min(100, u.get("happiness", 100) + 3))
    if lifestyle < 35:
        await update_field(user.id, "stress", min(100, u.get("stress", 0) + 4))
    await add_life_journal(user.id, "revenu", f"Bonus quotidien encaissé : {fmt(amount)}.", severity="success")
    await process_referral_progress(user.id, context.bot)

    msg = f"🎁 **Bonus quotidien !**\n\n💰 Montant : **{fmt(amount)}**\n"
    if karma_mult > 1:
        bonus_pct = int((karma_mult - 1) * 100)
        msg += f"😇 Bonus karma (+{bonus_pct}%) : +{fmt(amount - base)}\n"
    elif karma_mult < 1:
        malus_pct = int((1 - karma_mult) * 100)
        msg += f"😈 Malus karma (-{malus_pct}%) : {fmt(base - amount)}\n"
    if penalties["passive_income_mult"] < 1:
        malus = int((1 - penalties["passive_income_mult"]) * 100)
        msg += f"🧬 Cohérence de vie : -{malus}% (état actuel)\n"
    if lifestyle < 35:
        msg += "⚠️ Ton mauvais état général te tend davantage.\n"
    msg += f"💵 Nouveau solde : {fmt(u['balance'] + amount)}"
    await update.message.reply_text(msg, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# Travailler (inchangé)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("work_last", WORK_COOLDOWN, "⏳ Tu es fatigué(e) ! Repos nécessaire avant de retravailler.")
async def cmd_travailler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    penalties = compute_condition_penalties(u)

    if u.get("energy", 100) < 20:
        await update.message.reply_text(
            "😴 Tu es épuisé(e) ! Tu as besoin de te reposer.\n"
            "👉 /dormir — récupérer de l'énergie\n"
            "👉 /manger — restaurer ta faim"
        )
        return
    if u.get("hunger", 100) < 12:
        await update.message.reply_text("🍽️ Tu es trop affamé(e) pour travailler efficacement. Mange d'abord avec /manger.")
        return
    if u.get("health", 100) < 15:
        await update.message.reply_text("🏥 Ton état est trop critique pour travailler. Soigne-toi avec /medecin ou /hopital.")
        return

    job = u.get("job", "")
    if not job or job not in JOBS:
        job = "Livreur"

    job_data = JOBS[job]
    dip_bonus = DIPLOME_SALARY_BONUS.get(u.get("diplome", ""), 1.0)
    karma_mult = get_karma_multiplier(u.get("karma", 0))

    from database import get_skill
    tech_skill = await get_skill(user.id, "Technique")
    neg_skill = await get_skill(user.id, "Négociation")
    skill_mult = 1 + (tech_skill * 0.03) + (neg_skill * 0.02)

    city_mult = await get_city_multipliers(user.id)
    await get_season_effect()  # juste pour homogénéité

    base = random.randint(job_data["min"], job_data["max"])
    amount = int(base * dip_bonus * karma_mult * skill_mult * city_mult.get("salary_mult", 1.0) * penalties["productivity_mult"])

    energy_cost = random.randint(15, 25) + (5 if u.get("stress", 0) > 70 else 0)
    new_energy = max(0, u.get("energy", 100) - energy_cost)
    new_stress = min(100, u.get("stress", 0) + random.randint(3, 8) + (3 if u.get("hunger", 100) < 30 else 0))
    new_hunger = max(0, u.get("hunger", 100) - (12 if u.get("hunger", 100) < 30 else 10))
    new_health = max(0, u.get("health", 100) - (4 if penalties["severe_combo"] else 0))

    await update_balance(user.id, amount)
    await on_wealth_gain(user.id, amount)
    await update_field(user.id, "work_last", now())
    await update_field(user.id, "energy", new_energy)
    await update_field(user.id, "stress", new_stress)
    await update_field(user.id, "hunger", new_hunger)
    await update_field(user.id, "health", new_health)
    await increment_field(user.id, "xp", job_data["xp"])
    await update_mission_progress(user.id, "work", 1)
    await on_xp_gain(user.id, job_data["xp"])
    await process_referral_progress(user.id, context.bot)

    company = await get_user_company(user.id)
    if company:
        async with db_connection() as db:   # ← remplacement
            await db.execute(
                "UPDATE company_members SET activity_score = activity_score + 1 "
                "WHERE user_id=? AND company_id=?",
                (user.id, company["company_id"])
            )
            await db.commit()

    events = [
        ("🍀 Bonne journée ! Bonus surprise.", random.randint(1000, 5000)),
        ("😤 Client difficile... Aucun bonus.", 0),
        ("⭐ Performance exceptionnelle !", int(amount * 0.2)),
        ("😔 Journée ordinaire.", 0),
        ("🏆 Promu à un projet spécial !", int(amount * 0.5)),
    ]
    w = random.choices(events, weights=[10, 20, 10, 50, 10])[0]
    bonus = w[1]
    if bonus > 0:
        await update_balance(user.id, bonus)
        amount += bonus

    await add_life_journal(user.id, "travail", f"Travail effectué comme {job} : gain total {fmt(amount)}, énergie {u.get('energy', 100)}%→{new_energy}%.", severity="success")

    msg = (
        f"💼 **Journée de travail !**\n\n"
        f"👔 Métier : **{job}**\n"
        f"💰 Salaire : **{fmt(amount)}**\n"
    )
    if dip_bonus > 1:
        msg += f"🎓 Bonus diplôme : x{dip_bonus}\n"
    if city_mult.get("salary_mult", 1.0) != 1.0:
        msg += f"🏙️ Ville (x{city_mult['salary_mult']})\n"
    if penalties["productivity_mult"] < 1:
        malus = int((1 - penalties["productivity_mult"]) * 100)
        msg += f"🧬 Rendement pénalisé : -{malus}%\n"
    msg += f"⚡ Énergie : {u.get('energy', 100)}% → {new_energy}%\n"
    msg += f"😰 Stress : {u.get('stress', 0)}% → {new_stress}%\n"
    if new_health != u.get("health", 100):
        msg += f"❤️ Santé : {u.get('health', 100)}% → {new_health}%\n"
    msg += f"✨ +{job_data['xp']} XP\n"
    msg += f"\n_{w[0]}_\n"
    msg += f"\n💵 Solde : {fmt(u['balance'] + amount)}"
    await update.message.reply_text(msg, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# Métiers évolutifs : promotion (inchangé)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_promotion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    job_name = u.get("job")
    if not job_name:
        await update.message.reply_text("❌ Tu n'as pas de métier.")
        return

    base_job = None
    current_level = 0
    levels = []
    for bj, lvls in EVOLUTIVE_JOBS.items():
        for idx, lvl in enumerate(lvls, 1):
            if lvl["name"] == job_name:
                base_job = bj
                current_level = idx
                levels = lvls
                break
        if base_job:
            break

    if not base_job:
        await update.message.reply_text("❌ Ton métier n'est pas évolutif.")
        return
    if current_level >= len(levels):
        await update.message.reply_text("🏆 Tu es déjà au niveau maximum pour ce métier.")
        return

    next_level_data = levels[current_level]
    need = next_level_data.get("need")
    if need:
        current_dip = u.get("diplome", "")
        from config import DIPLOMES, DIPLOMES_EXTENDED
        DIPLOME_ORDER = DIPLOMES + DIPLOMES_EXTENDED
        if current_dip not in DIPLOME_ORDER:
            current_rank = -1
        else:
            current_rank = DIPLOME_ORDER.index(current_dip)
        need_rank = DIPLOME_ORDER.index(need) if need in DIPLOME_ORDER else -1
        if current_rank < need_rank:
            await update.message.reply_text(f"🔒 Pour passer à **{next_level_data['name']}**, tu as besoin du diplôme **{need}** ou supérieur.")
            return

    cost = int((next_level_data["min"] + next_level_data["max"]) // 2 * 0.1)
    if u["balance"] < cost:
        await update.message.reply_text(f"❌ Fonds insuffisants. Coût : {fmt(cost)}")
        return

    await update_balance(user.id, -cost)
    await update_field(user.id, "job", next_level_data["name"])
    await update_field(user.id, "job_level", current_level + 1)
    await increment_field(user.id, "xp", 200)
    await on_xp_gain(user.id, 200)
    await process_referral_progress(user.id, context.bot)

    await update.message.reply_text(
        f"🎉 **Félicitations !**\n\n"
        f"Tu as été promu **{next_level_data['name']}** !\n"
        f"💰 Nouveau salaire : {fmt(next_level_data['min'])} – {fmt(next_level_data['max'])}\n"
        f"✨ +200 XP",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Changer de métier (inchangé)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_metier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not context.args:
        current_dip = u.get("diplome", "")
        dip_order = ["", "Brevet", "Bac", "BTS", "Licence", "Master", "MBA", "Doctorat", "Habilitation"]
        dip_rank = dip_order.index(current_dip) if current_dip in dip_order else 0

        text = "💼 **Métiers disponibles**\n\n"
        text += f"🎓 Ton diplôme : **{current_dip or 'Aucun'}**\n\n"

        for job, data in JOBS.items():
            need = data.get("need", "")
            need_rank = dip_order.index(need) if need in dip_order else 0
            unlocked = dip_rank >= need_rank
            status = "✅" if unlocked else f"🔒 Besoin : {need}"
            current_mark = " 👈 ACTUEL" if job == u.get("job", "") else ""
            text += (
                f"{'✅' if unlocked else '🔒'} **{job}**{current_mark}\n"
                f"  💰 {fmt(data['min'])} – {fmt(data['max'])}/travail\n"
                f"  📋 {data['sector']} | {status}\n\n"
            )

        text += "**Métiers évolutifs** (commence par le niveau 1):\n"
        for bj, levels in EVOLUTIVE_JOBS.items():
            first = levels[0]
            need = first.get("need", "")
            need_rank = dip_order.index(need) if need in dip_order else 0
            unlocked = dip_rank >= need_rank
            status = "✅" if unlocked else f"🔒 Besoin : {need}"
            current_mark = " 👈 ACTUEL" if first["name"] == u.get("job", "") else ""
            text += (
                f"{'✅' if unlocked else '🔒'} **{first['name']}**{current_mark}\n"
                f"  💰 {fmt(first['min'])} – {fmt(first['max'])}/travail\n"
                f"  📋 {first['sector']} | {status}\n\n"
            )

        text += "_Utilise /metier [nom du métier] pour changer._\n"
        text += "_Pour les métiers évolutifs, progresse avec `/promotion`._"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    job_name = " ".join(context.args).title()
    matched = None
    job_data = None
    if job_name in JOBS:
        matched = job_name
        job_data = JOBS[matched]
    else:
        for bj, levels in EVOLUTIVE_JOBS.items():
            for lvl in levels:
                if lvl["name"].lower() == job_name.lower():
                    matched = lvl["name"]
                    job_data = lvl
                    break
            if matched:
                break
    if not matched:
        await update.message.reply_text(f"❌ Métier inconnu. Utilise /metier pour voir la liste.")
        return

    need = job_data.get("need", "")
    current_dip = u.get("diplome", "")
    dip_order = ["", "Brevet", "Bac", "BTS", "Licence", "Master", "MBA", "Doctorat", "Habilitation"]
    dip_rank = dip_order.index(current_dip) if current_dip in dip_order else 0
    need_rank = dip_order.index(need) if need in dip_order else 0
    if dip_rank < need_rank:
        await update.message.reply_text(
            f"🔒 Ce métier nécessite : **{need}**\n"
            f"Ton diplôme actuel : {current_dip or 'Aucun'}\n"
            f"👉 /etudes pour voir les formations",
            parse_mode="Markdown"
        )
        return

    await update_field(user.id, "job", matched)

    evolutive = False
    for bj, levels in EVOLUTIVE_JOBS.items():
        for idx, lvl in enumerate(levels, 1):
            if lvl["name"] == matched:
                await update_field(user.id, "job_level", idx)
                evolutive = True
                break
        if evolutive:
            break
    if not evolutive:
        await update_field(user.id, "job_level", 1)

    await update.message.reply_text(
        f"✅ **Changement de métier réussi !**\n\n"
        f"👔 Nouveau métier : **{matched}**\n"
        f"💰 Salaire : {fmt(job_data['min'])} – {fmt(job_data['max'])} par session\n"
        f"📋 Secteur : {job_data['sector']}\n\n"
        f"👉 /travailler pour commencer à gagner de l'argent !",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Payer un autre joueur (avec taxe sur gros montants) – VERSION CORRIGÉE
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
async def cmd_payer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Réponds au message du joueur à payer.\nUsage : /payer montant (en répondant à qqn)")
        return

    if not context.args:
        await update.message.reply_text("Usage : /payer montant")
        return

    amount = parse_amount(context.args[0], u["balance"])
    if not amount or amount <= 0:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if amount > u["balance"]:
        await update.message.reply_text(f"❌ Fonds insuffisants ! Solde : {fmt(u['balance'])}")
        return

    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te payer toi-même !")
        return

    tu = await get_user(target.id, target.username or "", target.full_name or "")
    if not tu.get("registered"):
        await update.message.reply_text("❌ Ce joueur n'a pas encore commencé sa partie.")
        return

    # Taxe sur les gros transferts (1% au-delà de 100k)
    tax = int(amount * 0.01) if amount > 100_000 else 0

    # Transfert atomique
    ok = await transfer_money(user.id, target.id, amount, tax=tax)
    if not ok:
        await update.message.reply_text(f"❌ Fonds insuffisants ! Solde : {fmt(u['balance'])}")
        return

    # Mises à jour annexes
    await on_wealth_gain(target.id, amount - tax)
    await update_mission_progress(user.id, "pay", 1)
    if amount >= 100_000:
        await increment_field(user.id, "karma", 1)
    if tax > 0:
        await add_life_journal(user.id, "taxe", f"Taxe de 1% sur transfert de {fmt(amount)} : {fmt(tax)} prélevé.", severity="warning")

    msg = f"💸 **Transfert réussi !**\n\n"
    msg += f"👤 De : {user.full_name}\n"
    msg += f"👤 Vers : {target.full_name}\n"
    msg += f"💰 Montant : **{fmt(amount)}**\n"
    if tax > 0:
        msg += f"💸 Taxe : -{fmt(tax)}\n"
    msg += f"💵 Ton solde : {fmt(u['balance'] - amount)}"
    await update.message.reply_text(msg, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# Compte et impôts (avec db_connection)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
async def cmd_compte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    async with db_connection() as db:
        async with db.execute("SELECT SUM(balance), SUM(loan) FROM bank_accounts WHERE user_id=?", (user.id,)) as cur:
            row = await cur.fetchone()
    bank_total = row[0] or 0
    loan_total = row[1] or 0

    total = u["balance"] + bank_total
    impots = _get_impots_rate(u["balance"])

    await update.message.reply_text(
        f"💳 **Compte de {user.full_name}**\n\n"
        f"💵 Espèces : **{fmt(u['balance'])}**\n"
        f"🏦 Banque : {fmt(bank_total)}\n"
        f"💳 Prêts actifs : -{fmt(loan_total)}\n"
        f"━━━━━━━━━━━━\n"
        f"💰 Total net : **{fmt(total - loan_total)}**\n\n"
        f"📊 Taux d'imposition : {impots}%\n"
        f"📅 Impôts estimés/mois : {fmt(int(u['balance'] * impots / 100 / 30))}",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_impots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    rate = _get_impots_rate(u["balance"])

    await update.message.reply_text(
        f"🏛️ **Système d'imposition**\n\n"
        f"💰 Ton solde : {fmt(u['balance'])}\n"
        f"📊 Ton taux : **{rate}%**\n\n"
        f"📋 **Barème progressif :**\n"
        f"• < 50K$ → 0%\n"
        f"• 50K – 500K$ → 5%\n"
        f"• 500K – 5M$ → 12%\n"
        f"• 5M – 50M$ → 20%\n"
        f"• 50M – 500M$ → 30%\n"
        f"• > 500M$ → 40%\n\n"
        f"💡 _Les impôts sont prélevés automatiquement chaque semaine._\n"
        f"_Un poste politique peut réduire ton taux !_",
        parse_mode="Markdown"
    )


def _get_impots_rate(balance: int) -> int:
    if balance < 50_000:
        return 0
    if balance < 500_000:
        return 5
    if balance < 5_000_000:
        return 12
    if balance < 50_000_000:
        return 20
    if balance < 500_000_000:
        return 30
    return 40


# ─────────────────────────────────────────────────────────────────────────────
# IMPÔTS QUOTIDIENS RENFORCÉS (appelé par le scheduler) – version corrigée
# ─────────────────────────────────────────────────────────────────────────────
async def process_daily_tax():
    """Prélève l'impôt progressif quotidien sur la fortune des joueurs (version renforcée)."""
    async with db_connection() as db:
        async with db.execute("SELECT user_id, balance FROM users WHERE registered=1 AND banned=0") as cur:
            users = await cur.fetchall()
        for uid, balance in users:
            # Barème plus agressif
            if balance < 100_000:
                rate = 0
            elif balance < 500_000:
                rate = 2   # 2% mensuel
            elif balance < 2_000_000:
                rate = 5
            elif balance < 10_000_000:
                rate = 10
            elif balance < 50_000_000:
                rate = 15
            else:
                rate = 25
            if rate == 0:
                continue
            tax = int(balance * rate / 100 / 30)   # quotidien = taux mensuel / 30
            if tax > 0:
                ok = await debit_balance(uid, tax)
                if ok and tax > 1000:
                    await add_life_journal(uid, "impots", f"Impôt quotidien : {fmt(tax)} prélevé.", severity="warning")
        logger.info("💰 Impôts quotidiens prélevés (taux renforcés).")


# ─────────────────────────────────────────────────────────────────────────────
# Classement des riches (inchangé)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
async def cmd_richesse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await get_top_rich(15)
    medals = ["🥇", "🥈", "🥉"] + ["💰"] * 12
    text = "👑 <b>Top 15 des plus riches</b>\n\n"
    for i, r in enumerate(rows):
        name = r.get("full_name") or r.get("username") or f"Joueur#{r['user_id']}"
        safe_name = html.escape(name)
        text += f"{medals[i]} <b>{safe_name}</b> — {fmt(r['balance'])}\n"
    await update.message.reply_text(text, parse_mode="HTML")


# ─────────────────────────────────────────────────────────────────────────────
# Dormir (inchangé)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("sleep_last", 43200, "😴 Tu as déjà dormi récemment. Attends un peu avant de te rendormir.")
async def cmd_dormir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    penalties = compute_condition_penalties(u)

    hours = 8
    energy_gain = min(100 - u.get("energy", 100), int(hours * 10 * penalties["recovery_mult"]))
    stress_loss = min(u.get("stress", 0), int(hours * 5 * penalties["recovery_mult"]))
    hunger_cost = 6 if u.get("hunger", 100) < 25 else 0
    new_hunger = max(0, u.get("hunger", 100) - hunger_cost)
    rest_warning = "⚠️ Le repos est moins efficace quand ton état général est dégradé.\n\n" if penalties["recovery_mult"] < 1 else ""

    await update_field(user.id, "energy", u.get("energy", 100) + energy_gain)
    await update_field(user.id, "stress", u.get("stress", 0) - stress_loss)
    await update_field(user.id, "hunger", new_hunger)
    await update_field(user.id, "sleep_last", now())
    await add_life_journal(user.id, "repos", f"Sommeil réparateur : énergie +{energy_gain}%, stress -{stress_loss}%.", severity="success")

    await update.message.reply_text(
        f"😴 **Bonne nuit !**\n\n"
        f"⚡ Énergie : {u.get('energy', 100)}% → {u.get('energy', 100) + energy_gain}%\n"
        f"😰 Stress : {u.get('stress', 0)}% → {u.get('stress', 0) - stress_loss}%\n\n"
        f"🍽️ Faim : {u.get('hunger', 100)}% → {new_hunger}%\n"
        f"{rest_warning}"
        f"_Reposé(e) et prêt(e) à conquérir le monde !_",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Manger (coûts augmentés)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_manger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    meals = {
        "snack":     {"cost": 800,    "hunger": 20, "energy": 10, "stress": 2,  "name": "🍕 Snack"},
        "repas":     {"cost": 3_500,  "hunger": 40, "energy": 20, "stress": -4, "name": "🍽️ Repas normal"},
        "gastro":    {"cost": 35_000, "hunger": 100, "energy": 40, "stress": -8, "happiness": 12, "name": "🍾 Restaurant gastronomique"},
        "etoile":    {"cost": 350_000,"hunger": 100, "energy": 60, "stress": -15, "happiness": 30, "name": "⭐ Restaurant étoilé"},
    }

    if not context.args or context.args[0].lower() not in meals:
        text = "🍽️ **Options de repas**\n\n"
        for key, m in meals.items():
            text += f"• `/manger {key}` — {m['name']}\n  💰 {fmt(m['cost'])} | 🍽️ +{m['hunger']}% faim\n\n"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    choice = context.args[0].lower()
    m = meals[choice]

    if u["balance"] < m["cost"]:
        await update.message.reply_text(f"❌ Pas assez d'argent. Coût : {fmt(m['cost'])}")
        return

    await update_balance(user.id, -m["cost"])
    await update_field(user.id, "hunger", min(100, u.get("hunger", 100) + m["hunger"]))
    await update_field(user.id, "energy", min(100, u.get("energy", 100) + m.get("energy", 0)))
    await update_field(user.id, "stress", max(0, min(100, u.get("stress", 0) + m.get("stress", 0))))
    if "happiness" in m:
        await update_field(user.id, "happiness", min(100, u.get("happiness", 100) + m["happiness"]))
    stress_line = ""
    if m.get("stress", 0):
        sign = "+" if m.get("stress", 0) > 0 else ""
        stress_line = f"😰 Stress : {sign}{m.get('stress', 0)}%\n"
    happiness_line = f"😊 Bonheur : +{m.get('happiness', 0)}%" if m.get("happiness") else ""

    await add_life_journal(user.id, "nutrition", f"Repas pris : {m['name']} pour {fmt(m['cost'])}.", severity="info")

    await update.message.reply_text(
        f"{m['name']}\n\n"
        f"💰 Coût : {fmt(m['cost'])}\n"
        f"🍽️ Faim restaurée : +{m['hunger']}%\n"
        f"⚡ Énergie : +{m.get('energy', 0)}%\n"
        f"{stress_line}"
        f"{happiness_line}",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Loterie (inchangée)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
async def cmd_loterie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM lottery_draws WHERE drawn_at != 0 ORDER BY draw_id DESC LIMIT 1") as cur:
            last_completed = await cur.fetchone()
        async with db.execute("SELECT * FROM lottery_draws WHERE drawn_at = 0 ORDER BY draw_id DESC LIMIT 1") as cur2:
            current_draw = await cur2.fetchone()
    if not current_draw:
        await _create_initial_lottery_draw()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT * FROM lottery_draws WHERE drawn_at = 0 ORDER BY draw_id DESC LIMIT 1") as cur:
                current_draw = await cur.fetchone()
    current_draw = dict(current_draw) if current_draw else {"jackpot": LOTTERY_JACKPOT_BASE}
    if last_completed and last_completed["drawn_at"]:
        last_time = last_completed["drawn_at"]
        next_time = max(now(), last_time + 86400)
    else:
        next_time = now() + 3600
    remaining = max(0, next_time - now())
    if remaining < 60:
        remaining_str = f"{remaining} secondes"
    elif remaining < 3600:
        remaining_str = f"{remaining // 60} minutes"
    elif remaining < 86400:
        remaining_str = f"{remaining // 3600} heures"
    else:
        remaining_str = f"{remaining // 86400} jours"
    next_date = datetime.fromtimestamp(next_time).strftime("%d/%m/%Y à %H:%M")
    text = (
        f"🎫 **Loterie Nationale**\n\n"
        f"💰 Jackpot actuel : **{fmt(current_draw['jackpot'])}**\n"
        f"🎟️ Prix d'un ticket : {fmt(LOTTERY_TICKET_COST)}\n"
        f"⏳ Prochain tirage dans : **{remaining_str}**\n"
        f"📅 ({next_date})\n\n"
        f"👉 /acheterticket — acheter un ticket\n"
        f"👉 /mestickets — voir tes tickets\n"
        f"👉 /tirage — résultats du dernier tirage"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
async def cmd_acheterticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if u["balance"] < LOTTERY_TICKET_COST:
        await update.message.reply_text(f"❌ Ticket coûte {fmt(LOTTERY_TICKET_COST)}.")
        return

    nums = sorted(random.sample(range(1, 50), 5))
    nums_str = "-".join(map(str, nums))

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT draw_id FROM lottery_draws ORDER BY draw_id DESC LIMIT 1") as cur:
            draw = await cur.fetchone()
        if not draw:
            await _create_initial_lottery_draw()
            async with db.execute("SELECT draw_id FROM lottery_draws ORDER BY draw_id DESC LIMIT 1") as cur2:
                draw = await cur2.fetchone()
        draw_id = draw[0]
        await db.execute(
            "INSERT INTO lottery_tickets (user_id, draw_id, numbers, purchased_at) VALUES (?,?,?,?)",
            (user.id, draw_id, nums_str, now())
        )
        await db.execute("UPDATE lottery_draws SET jackpot = jackpot + ? WHERE draw_id = ?", (LOTTERY_TICKET_COST // 2, draw_id))
        await db.commit()

    await update_balance(user.id, -LOTTERY_TICKET_COST)
    await update.message.reply_text(
        f"🎫 **Ticket acheté !**\n\n"
        f"🔢 Numéros : **{nums_str}**\n"
        f"💰 Coût : {fmt(LOTTERY_TICKET_COST)}\n\n"
        f"_Le jackpot augmente avec chaque ticket vendu !_",
        parse_mode="Markdown"
    )


async def _create_initial_lottery_draw():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO lottery_draws (draw_id, jackpot, drawn_at) VALUES (1, ?, 0)", (LOTTERY_JACKPOT_BASE,))
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Don (avec taxe)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("don_cooldown", 60, "⏳ Tu donnes trop vite ! Patiente 1 minute.")
async def cmd_don(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Donner de l'argent à un autre joueur (don charitable entre joueurs)."""
    sender = update.effective_user
    u = await get_user(sender.id)

    target = None
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        args = context.args
    elif context.args:
        mention = context.args[0]
        if mention.startswith("@"):
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT user_id, full_name FROM users WHERE username = ?", (mention[1:],)) as cur:
                    row = await cur.fetchone()
                    if row:
                        class FakeUser:
                            def __init__(self, uid, name):
                                self.id = uid
                                self.full_name = name
                        target = FakeUser(row[0], row[1])
        else:
            try:
                uid = int(mention)
                t = await get_user(uid)
                if t.get("registered"):
                    class FakeUser:
                        def __init__(self, uid, name):
                            self.id = uid
                            self.full_name = name
                    target = FakeUser(uid, t.get("full_name", "Joueur"))
            except:
                pass
        args = context.args[1:]
    else:
        await update.message.reply_text(
            "Usage : `/don @utilisateur montant` ou `/don montant` en répondant à un message.\n"
            "_Faire un don à un autre joueur augmente ton karma et votre relation._"
        )
        return

    if not target or target.id == sender.id:
        await update.message.reply_text("❌ Destinataire invalide ou toi-même.")
        return

    if not args:
        await update.message.reply_text("❌ Montant manquant.")
        return

    amount = parse_amount(args[0], u["balance"])
    if not amount or amount <= 0:
        await update.message.reply_text("❌ Montant invalide.")
        return
    if amount > u["balance"]:
        await update.message.reply_text(f"❌ Fonds insuffisants. Solde : {fmt(u['balance'])}")
        return

    # Taxe sur les dons importants (1% au-delà de 50k)
    tax = 0
    if amount > 50_000:
        tax = int(amount * 0.01)
        amount_after_tax = amount - tax
        ok = await transfer_money(sender.id, target.id, amount, tax=tax)
        if not ok:
            await update.message.reply_text(f"❌ Erreur de transfert, vérifie ton solde.")
            return
    else:
        ok = await transfer_money(sender.id, target.id, amount, tax=0)
        if not ok:
            await update.message.reply_text(f"❌ Erreur de transfert, vérifie ton solde.")
            return
        amount_after_tax = amount

    await on_wealth_gain(target.id, amount_after_tax)   # déjà fait par transfer_money mais gardé pour cohérence

    karma_gain = max(1, amount_after_tax // 5_000)
    karma_gain = min(50, karma_gain)
    await increment_field(sender.id, "karma", karma_gain)
    await increment_field(sender.id, "charity_given", amount)

    await _bump_relation(sender.id, target.id, 5, "bienfaiteur")

    msg = (
        f"🎁 **Don effectué !**\n\n"
        f"👤 De : {sender.full_name}\n"
        f"👤 À : {target.full_name}\n"
        f"💰 Montant : {fmt(amount)}\n"
    )
    if tax:
        msg += f"💸 Taxe (1%) : -{fmt(tax)}\n"
    msg += (
        f"🌟 Karma gagné : +{karma_gain}\n"
        f"💞 Relation avec {target.full_name} : +5\n\n"
        f"_La générosité renforce les liens._"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

    try:
        await context.bot.send_message(
            target.id,
            f"🎁 **Tu as reçu un don !**\n\n"
            f"De : {sender.full_name}\n"
            f"💰 Montant : {fmt(amount_after_tax)}"
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Commandes supplémentaires pour la loterie
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
async def cmd_mestickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT numbers, purchased_at FROM lottery_tickets WHERE user_id=? AND won=0 ORDER BY purchased_at DESC",
            (user.id,)
        ) as cur:
            tickets = await cur.fetchall()
    if not tickets:
        await update.message.reply_text("📭 Tu n'as aucun ticket en attente.")
        return
    text = "🎫 **Tes tickets en attente**\n\n"
    for t in tickets[:10]:
        ts = fmt_time(now() - t[1]) if t[1] else "récent"
        text += f"🔢 {t[0]} — acheté il y a {ts}\n"
    if len(tickets) > 10:
        text += f"_... et {len(tickets)-10} autres_"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
async def cmd_tirage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM lottery_draws WHERE drawn_at != 0 ORDER BY draw_id DESC LIMIT 1") as cur:
            last = await cur.fetchone()
    if not last:
        await update.message.reply_text("📭 Aucun tirage n'a encore eu lieu.")
        return
    nums = last["winning_numbers"] or "?"
    last_ts = last["drawn_at"]
    if last_ts > now() or last_ts < now() - 86400 * 7:
        last_date = "date inconnue (correction automatique)"
        next_time = now() + 86400
    else:
        last_date = datetime.fromtimestamp(last_ts).strftime("%d/%m/%Y à %H:%M")
        next_time = max(now(), last_ts + 86400)
    next_date = datetime.fromtimestamp(next_time).strftime("%d/%m/%Y à %H:%M")
    text = (
        f"🎰 **Dernier tirage**\n\n"
        f"🔢 Numéros gagnants : **{nums}**\n"
        f"💰 Jackpot : {fmt(last['jackpot'])}\n"
        f"📅 Tirage effectué le {last_date}\n\n"
        f"⏳ Prochain tirage : **{next_date}**"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance (appelée par le scheduler)
# ─────────────────────────────────────────────────────────────────────────────
async def process_lottery_draw():
    import random
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM lottery_draws WHERE drawn_at = 0 ORDER BY draw_id DESC LIMIT 1") as cur:
            draw = await cur.fetchone()
        if not draw:
            return
        draw_id = draw["draw_id"]
        jackpot = draw["jackpot"]
        winning_nums = sorted(random.sample(range(1, 50), 5))
        win_str = "-".join(map(str, winning_nums))

        async with db.execute("SELECT user_id, numbers FROM lottery_tickets WHERE draw_id=? AND won=0", (draw_id,)) as cur2:
            tickets = await cur2.fetchall()

        await db.execute("UPDATE lottery_tickets SET won=1 WHERE draw_id=?", (draw_id,))

        winners = []
        for ticket in tickets:
            nums = list(map(int, ticket["numbers"].split("-")))
            matches = len(set(nums) & set(winning_nums))
            if matches == 5:
                winners.append(ticket["user_id"])

        if winners:
            prize_per_winner = jackpot // len(winners)
            for uid in winners:
                await update_balance(uid, prize_per_winner)
                await increment_field(uid, "lottery_wins")
                await increment_field(uid, "xp", 500)
                await on_xp_gain(uid, 500)
            await db.execute("UPDATE lottery_draws SET winning_numbers=?, drawn_at=? WHERE draw_id=?", (win_str, now(), draw_id))
            new_jackpot = LOTTERY_JACKPOT_BASE
            await db.execute("INSERT INTO lottery_draws (jackpot, drawn_at) VALUES (?,0)", (new_jackpot,))
        else:
            await db.execute("UPDATE lottery_draws SET winning_numbers=?, drawn_at=? WHERE draw_id=?", (win_str, now(), draw_id))
            await db.execute("INSERT INTO lottery_draws (jackpot, drawn_at) VALUES (?,0)", (jackpot,))
        await db.commit()