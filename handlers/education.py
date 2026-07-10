import random
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_field, update_balance, increment_field, upgrade_skill, get_all_skills
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, fmt_time, now, roll_success
from config import (
    DIPLOMES, DIPLOME_COSTS, DIPLOME_STUDY_TIME, DIPLOME_PASS_RATE, DIPLOME_SALARY_BONUS,
    SKILLS, SKILL_LEVEL_COST,
    DIPLOMES_EXTENDED, DIPLOME_COSTS_EXTENDED, DIPLOME_STUDY_TIME_EXTENDED,
    DIPLOME_PASS_RATE_EXTENDED, DIPLOME_SALARY_BONUS_EXTENDED
)
from handlers.competitions import on_xp_gain   # ← AJOUT

# Fusion des listes et dictionnaires
ALL_DIPLOMES = DIPLOMES + DIPLOMES_EXTENDED
ALL_COSTS = {**DIPLOME_COSTS, **DIPLOME_COSTS_EXTENDED}
ALL_STUDY_TIME = {**DIPLOME_STUDY_TIME, **DIPLOME_STUDY_TIME_EXTENDED}
ALL_PASS_RATE = {**DIPLOME_PASS_RATE, **DIPLOME_PASS_RATE_EXTENDED}
ALL_SALARY_BONUS = {**DIPLOME_SALARY_BONUS, **DIPLOME_SALARY_BONUS_EXTENDED}

DIPLOME_ORDER = ALL_DIPLOMES


def next_diplome(current: str) -> str | None:
    if not current:
        return DIPLOME_ORDER[0]
    if current in DIPLOME_ORDER:
        idx = DIPLOME_ORDER.index(current)
        if idx + 1 < len(DIPLOME_ORDER):
            return DIPLOME_ORDER[idx + 1]
    return None


@require_registered
@require_free
async def cmd_etudes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    current = u.get("diplome", "")

    text = "🎓 **Formations disponibles**\n\n"
    for dip in DIPLOME_ORDER:
        cost = ALL_COSTS[dip]
        time_req = ALL_STUDY_TIME[dip]
        chance = int(ALL_PASS_RATE[dip] * 100)
        bonus = ALL_SALARY_BONUS[dip]
        status = ""
        if current == dip:
            status = " ✅ OBTENU"
        elif current and DIPLOME_ORDER.index(dip) < DIPLOME_ORDER.index(current):
            status = " ✅ OBTENU"
        next_d = next_diplome(current)
        if dip == next_d:
            status += " 👈 SUIVANT"
        text += (
            f"📚 **{dip}**{status}\n"
            f"  💰 Coût : {fmt(cost)} | ⏱️ Étude : {fmt_time(time_req)}\n"
            f"  🎲 Chance de réussite : {chance}% | 💼 Bonus salaire : x{bonus}\n\n"
        )

    text += "_Utilise /etudier [diplôme] pour commencer._\n"
    text += "_Attention : l'examen est difficile — prépare-toi bien !_"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
@require_free
@cooldown("study_cooldown", 10, "⏳ Attends un instant avant de t'inscrire à une nouvelle formation.")
async def cmd_etudier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not context.args:
        await update.message.reply_text("Usage : /etudier [diplôme]\nEx: /etudier Master")
        return

    dip = " ".join(context.args).capitalize()
    matched = None
    for d in DIPLOME_ORDER:
        if d.lower() == dip.lower():
            matched = d
            break
    if not matched:
        await update.message.reply_text(f"❌ Diplôme inconnu. Choix : {', '.join(DIPLOME_ORDER)}")
        return

    current = u.get("diplome", "")
    next_d = next_diplome(current)
    if matched != next_d:
        if current and DIPLOME_ORDER.index(matched) <= DIPLOME_ORDER.index(current):
            await update.message.reply_text("❌ Tu as déjà ce diplôme ou supérieur !")
        else:
            await update.message.reply_text(
                f"❌ Tu dois d'abord obtenir **{next_d}** avant de viser **{matched}**.",
                parse_mode="Markdown"
            )
        return

    cost = ALL_COSTS[matched]
    if u["balance"] < cost:
        await update.message.reply_text(
            f"❌ Fonds insuffisants ! Formation coûte **{fmt(cost)}**.\n"
            f"💰 Ton solde : {fmt(u['balance'])}",
            parse_mode="Markdown"
        )
        return

    if u.get("study_start", 0) > 0 and now() < u["study_start"] + ALL_STUDY_TIME.get(u.get("study_diplome", ""), 0):
        rem = (u["study_start"] + ALL_STUDY_TIME[u["study_diplome"]]) - now()
        await update.message.reply_text(
            f"📚 Tu étudies déjà pour **{u['study_diplome']}** !\n"
            f"⏱️ Temps restant : {fmt_time(rem)}\n"
            f"👉 Utilise /examen quand le temps est écoulé.",
            parse_mode="Markdown"
        )
        return

    await update_balance(user.id, -cost)
    await update_field(user.id, "study_start", now())
    await update_field(user.id, "study_diplome", matched)
    await update_field(user.id, "study_effort", 1.0)
    await update_field(user.id, "study_revisions", 0)
    await update_field(user.id, "energy", max(0, u.get("energy", 100) - 20))
    await update_field(user.id, "stress", min(100, u.get("stress", 0) + 15))

    study_time = ALL_STUDY_TIME[matched]
    await update.message.reply_text(
        f"📚 **Inscription à la formation : {matched}**\n\n"
        f"💰 Frais payés : {fmt(cost)}\n"
        f"⏱️ Durée d'étude : {fmt_time(study_time)}\n"
        f"🎲 Taux de réussite de base : {int(ALL_PASS_RATE[matched] * 100)}%\n\n"
        f"💡 **Conseils :**\n"
        f"• Utilise /reviser pour augmenter tes chances (max 5 révisions)\n"
        f"• Un bon karma augmente tes chances\n"
        f"• L'énergie et le stress t'affectent\n\n"
        f"⏰ Reviens dans {fmt_time(study_time)} pour passer l'examen avec /examen",
        parse_mode="Markdown"
    )


@require_registered
@require_free
@cooldown("revision_cooldown", 300, "⏳ Tu es fatigué(e) intellectuellement. Révise dans quelques minutes.")
async def cmd_reviser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not u.get("study_diplome"):
        await update.message.reply_text("❌ Tu n'es pas inscrit à une formation.")
        return

    rev_count = u.get("study_revisions", 0)
    if rev_count >= 5:
        await update.message.reply_text("📚 Tu as déjà révisé le maximum (5 fois) ! Attends l'examen.")
        return

    if u.get("energy", 100) < 15:
        await update.message.reply_text(
            "😴 Tu es trop fatigué(e) pour réviser ! Va dormir avec /dormir."
        )
        return

    bonus = 0.03
    new_effort = min(1.5, u.get("study_effort", 1.0) + bonus)
    new_revisions = rev_count + 1
    new_energy = max(0, u.get("energy", 100) - 15)
    new_stress = min(100, u.get("stress", 0) + 8)

    await update_field(user.id, "study_effort", new_effort)
    await update_field(user.id, "study_revisions", new_revisions)
    await update_field(user.id, "energy", new_energy)
    await update_field(user.id, "stress", new_stress)

    await update.message.reply_text(
        f"📖 **Révisions terminées !**\n\n"
        f"📈 Multiplicateur chance : x{new_effort:.2f}\n"
        f"🔁 Révisions effectuées : {new_revisions}/5\n"
        f"⚡ Énergie : {new_energy}%\n"
        f"😰 Stress : {new_stress}%\n\n"
        f"_Continue à réviser pour maximiser tes chances !_",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_examen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    dip = u.get("study_diplome", "")
    if not dip:
        await update.message.reply_text("❌ Tu n'es inscrit à aucune formation.")
        return

    study_time = ALL_STUDY_TIME.get(dip, 0)
    elapsed = now() - u.get("study_start", 0)
    if elapsed < study_time:
        rem = study_time - elapsed
        await update.message.reply_text(
            f"⏳ Tu n'as pas fini d'étudier !\n"
            f"📚 Formation : **{dip}**\n"
            f"⏱️ Temps restant : {fmt_time(rem)}\n\n"
            f"💡 Utilise /reviser pour améliorer tes chances en attendant.",
            parse_mode="Markdown"
        )
        return

    # Calcul du taux de réussite final
    base_rate = ALL_PASS_RATE[dip]
    effort = u.get("study_effort", 1.0)
    karma_bonus = (max(0, u.get("karma", 0)) / 5000) * 0.1
    energy_bonus = (u.get("energy", 100) - 50) / 1000
    stress_malus = -(u.get("stress", 0) / 1000)
    from database import get_skill
    intel = await get_skill(user.id, "Intelligence")
    intel_bonus = intel * 0.01

    final_rate = min(0.95, max(0.05, base_rate * effort + karma_bonus + energy_bonus + stress_malus + intel_bonus))

    # UN SEUL tirage pour déterminer la réussite
    success = roll_success(final_rate)

    # Matières selon le diplôme
    subjects = {
        "Brevet":        ["Mathématiques", "Français", "Histoire", "Sciences"],
        "Bac":           ["Philo", "Maths", "Sciences", "Langues", "Histoire"],
        "BTS":           ["Technique", "Gestion", "Communication", "Anglais"],
        "Licence":       ["Analyse", "Statistiques", "Économie", "Droit", "Management"],
        "Master":        ["Recherche", "Stratégie", "Management", "Finance", "Projet"],
        "MBA":           ["Leadership", "Innovation", "Marketing", "Finance", "Stratégie"],
        "Doctorat":      ["Thèse", "Méthodologie", "Publication", "Soutenance", "Revue"],
        "Habilitation":  ["HDR", "Jury expert", "Bilan scientifique", "Perspectives"],
        "École de commerce": ["Marketing", "Finance", "Stratégie", "RH", "Négociation"],
        "Grande École":      ["Culture générale", "Sciences", "Langues", "Sports", "Leadership"],
        "Doctorat honoris causa": ["Discours", "Cérémonie", "Prestige", "Mécénat", "Influence"],
    }
    subs = subjects.get(dip, ["Matière 1", "Matière 2", "Matière 3"])
    results = []
    if success:
        for sub in subs:
            score = random.randint(12, 20)
            results.append(f"✅ {sub} : {score}/20")
    else:
        for sub in subs:
            score = random.randint(0, 11)
            results.append(f"❌ {sub} : {score}/20")

    # Nettoyer les champs d'étude
    await update_field(user.id, "study_start", 0)
    await update_field(user.id, "study_diplome", "")
    await update_field(user.id, "study_effort", 1.0)
    await update_field(user.id, "study_revisions", 0)

    if success:
        await update_field(user.id, "diplome", dip)
        xp_gain = {
            "Brevet": 500, "Bac": 1000, "BTS": 1500, "Licence": 3000,
            "Master": 6000, "MBA": 10000, "Doctorat": 20000, "Habilitation": 50000,
            "École de commerce": 15000, "Grande École": 25000, "Doctorat honoris causa": 75000,
        }.get(dip, 500)
        await increment_field(user.id, "xp", xp_gain)
        await on_xp_gain(user.id, xp_gain)   # ← AJOUT
        await increment_field(user.id, "prestige", 10)
        await increment_field(user.id, "missions_done")
        await update_field(user.id, "energy", max(0, u.get("energy", 100) - 30))

        mention = "Très Bien" if final_rate > 0.8 else "Bien" if final_rate > 0.6 else "Passable"
        await update.message.reply_text(
            f"🎉 **FÉLICITATIONS ! Tu as obtenu ton {dip} !**\n\n"
            f"📋 **Résultats :**\n"
            + "\n".join(results) + "\n\n"
            f"✅ Mention : **{mention}**\n\n"
            f"🎁 Récompenses :\n"
            f"  • +{xp_gain:,} XP\n"
            f"  • +10 Prestige\n"
            f"  • Bonus salaire : x{ALL_SALARY_BONUS[dip]}\n\n"
            f"🔓 Nouveau métier disponible !\n"
            f"👉 Utilise /metier pour changer de poste.",
            parse_mode="Markdown"
        )
    else:
        cost_retry = ALL_COSTS[dip] // 3
        if cost_retry < 1:
            cost_retry = 1
        await update_field(user.id, "stress", min(100, u.get("stress", 0) + 30))
        await update_field(user.id, "energy", max(0, u.get("energy", 100) - 10))

        await update.message.reply_text(
            f"😢 **Tu as ÉCHOUÉ à l'examen de {dip} !**\n\n"
            f"📋 **Résultats :**\n"
            + "\n".join(results) + "\n\n"
            f"📊 Taux de réussite était : {int(final_rate * 100)}%\n"
            f"😰 Ton stress augmente...\n\n"
            f"💡 **Pour réussir la prochaine fois :**\n"
            f"  • Révise davantage avec /reviser\n"
            f"  • Repose-toi avec /dormir\n"
            f"  • Augmente ton karma en faisant des dons\n"
            f"  • Augmente ta compétence Intelligence\n\n"
            f"💰 Pour repasser : {fmt(cost_retry)}\n"
            f"👉 /etudier {dip} pour recommencer",
            parse_mode="Markdown"
        )


@require_registered
@require_free
@cooldown("formation_cooldown", 300, "⏳ Tu viens de suivre une formation. Attends un peu.")
async def cmd_formation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not context.args:
        skills_text = "\n".join(f"• {s}" for s in SKILLS)
        await update.message.reply_text(
            f"🧠 **Compétences disponibles**\n\n{skills_text}\n\n"
            f"Usage : /formation [compétence]\n"
            f"💰 Coût : {fmt(SKILL_LEVEL_COST)} par niveau\n"
            f"_Chaque niveau améliore tes performances dans le domaine._",
            parse_mode="Markdown"
        )
        return

    skill = " ".join(context.args).capitalize()
    matched = None
    for s in SKILLS:
        if s.lower() == skill.lower():
            matched = s
            break
    if not matched:
        await update.message.reply_text(f"❌ Compétence inconnue. Choix : {', '.join(SKILLS)}")
        return

    current_level = await get_skill_level(user.id, matched)
    if current_level >= 100:
        await update.message.reply_text("🏆 Tu as déjà atteint le niveau maximum (100) pour cette compétence.")
        return

    cost = SKILL_LEVEL_COST * (current_level + 1)
    if u["balance"] < cost:
        await update.message.reply_text(
            f"❌ Fonds insuffisants. Formation coûte **{fmt(cost)}**.\n"
            f"💵 Solde : {fmt(u['balance'])}",
            parse_mode="Markdown"
        )
        return

    await update_balance(user.id, -cost)
    new_level = await upgrade_skill(user.id, matched)
    xp_gain = 200
    await increment_field(user.id, "xp", xp_gain)
    await on_xp_gain(user.id, xp_gain)   # ← AJOUT

    await update.message.reply_text(
        f"🧠 **Formation : {matched}**\n\n"
        f"📈 Niveau : {current_level} → **{new_level}**\n"
        f"💰 Coût : {fmt(cost)}\n"
        f"✨ +200 XP\n\n"
        f"_Prochain niveau : {fmt(SKILL_LEVEL_COST * (new_level + 1))}_",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_competences(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    skills = await get_all_skills(user.id)

    if not skills:
        await update.message.reply_text(
            "🧠 Tu n'as aucune compétence améliorée.\n"
            "👉 Utilise /formation [compétence] pour commencer."
        )
        return

    text = f"🧠 **Compétences de {user.full_name}**\n\n"
    for skill, level in sorted(skills.items(), key=lambda x: -x[1]):
        stars = "⭐" * min(10, level // 10) + "☆" * max(0, 10 - level // 10)
        desc = _skill_desc(skill, level)
        text += f"**{skill}** Niv.{level}\n{stars}\n_{desc}_\n\n"
        if len(text) > 3800:
            text += "_..._"
            break
    await update.message.reply_text(text, parse_mode="Markdown")


def _skill_desc(skill: str, level: int) -> str:
    descs = {
        "Charisme":      f"+{level * 5}% succès social",
        "Intelligence":  f"+{level * 3}% succès examen",
        "Force":         f"+{level * 5}% puissance combat",
        "Agilité":       f"+{level * 4}% esquive",
        "Négociation":   f"-{level * 2}% prix achat",
        "Leadership":    f"+{level * 3}% moral employés",
        "Créativité":    f"+{level * 5}% revenus artistiques",
        "Technique":     f"+{level * 4}% succès hack",
        "Endurance":     f"+{level * 10} énergie max",
        "Discrétion":    f"+{level * 5}% succès crime",
    }
    return descs.get(skill, f"Niv.{level}")


async def get_skill_level(user_id: int, skill_name: str) -> int:
    from database import get_skill
    return await get_skill(user_id, skill_name)


async def process_education_maintenance():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET study_start=0, study_diplome='', study_effort=1.0, study_revisions=0 "
            "WHERE study_start > 0 AND study_start < ?",
            (now() - 30 * 86400,)
        )
        await db.commit()