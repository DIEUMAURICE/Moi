"""
handlers/life.py — LIFESIM ULTRA V2
═══════════════════════════════════════════════════════════════════════
Tableau de bord de vie, routine intelligente, journal narratif.
"""
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes

from config import DB_PATH
from database import get_user
from utils.decorators import require_registered
from utils.helpers import (
    fmt, get_level, lifestyle_score, life_state_label, karma_label,
    wealth_class, xp_progress, fmt_duration, now,
)
from utils.aesthetics import (
    card, stat_card, alert, age_stage, section,
    SEP_LIGHT, fmt_money, mini_stat,
)
from utils.simulation import current_season, SEASON_EFFECTS


# ═══════════════════════════════════════════════════════════════════
#                        /vie ─ TABLEAU DE BORD
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_vie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    score = lifestyle_score(u)
    label = life_state_label(score)
    age = u.get("age", 20)
    age_label, age_icon = age_stage(age)
    lvl, xp_in, xp_for = xp_progress(u.get("xp", 0))
    season = current_season()
    s_fx = SEASON_EFFECTS[season]

    body = [
        f"{age_icon} <b>{age} ans</b> · {age_label} · {s_fx['icon']} <b>{season.capitalize()}</b>",
        f"📊 État global : <b>{label}</b> ({score}/100)",
        "",
        "<b>💗 STATUT</b>",
        mini_stat("Santé", u.get("health", 100), emoji="❤️"),
        mini_stat("Énergie", u.get("energy", 100), emoji="⚡"),
        mini_stat("Faim", u.get("hunger", 100), emoji="🍽️"),
        mini_stat("Bonheur", u.get("happiness", 100), emoji="😊"),
        mini_stat("Stress", u.get("stress", 0), emoji="😰", inverted=True),
        "",
        "<b>💰 ÉCONOMIE</b>",
        f"💵 Solde : <b>{fmt(u['balance'])}</b> · 🏷️ <b>{wealth_class(u['balance'])}</b>",
        f"📈 Gagné : <b>{fmt(u.get('total_earned', 0))}</b> · 📉 Dépensé : <b>{fmt(u.get('total_spent', 0))}</b>",
        "",
        "<b>🎓 PARCOURS</b>",
        f"🏷️ {u.get('diplome') or 'Aucun'} · 💼 {u.get('job') or 'Sans emploi'}",
        f"⭐ Niveau <b>{lvl}</b> · XP {xp_in}/{xp_for} · 🌟 {u.get('karma', 0):+d}",
        f"👑 Prestige : <b>{u.get('prestige', 0)}</b> · Karma : <b>{karma_label(u.get('karma', 0))}</b>",
    ]

    # Suggestions intelligentes
    suggestions = await _smart_suggestions(update, u)
    if suggestions:
        body.append("")
        body.append("<b>🧭 SUGGESTIONS DU JOUR</b>")
        body.extend(f"  ▸ {s}" for s in suggestions)

    await update.message.reply_text(
        card("DIAGNOSTIC DE VIE", body,
             icon="🧬", style="thick",
             footer="Tape /routine pour un plan d'action automatique."),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════
#                       /routine ─ PLAN INTELLIGENT
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_routine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    actions = []

    # Survie d'abord
    if u.get("hunger", 100) < 40:
        actions.append("🍽️ <code>/manger</code> ─ <b>URGENCE</b> : ta faim est critique")
    elif u.get("hunger", 100) < 60:
        actions.append("🍽️ <code>/manger</code> ─ restaure ta faim")

    if u.get("energy", 100) < 30:
        actions.append("🛌 <code>/dormir</code> ─ <b>URGENCE</b> : tu es épuisé(e)")
    elif u.get("energy", 100) < 50:
        actions.append("🛌 <code>/dormir</code> ─ regagne de l'énergie")

    if u.get("health", 100) < 50:
        actions.append("🏥 <code>/medecin</code> ─ consulte vite")
    if u.get("stress", 0) > 60:
        actions.append("🧘 <code>/gym</code> ou <code>/jardin</code> ─ baisse ton stress")

    # Économie
    if (now() - u.get("daily_last", 0)) >= 86400:
        actions.append("💰 <code>/quotidien</code> ─ ton bonus journalier est dispo !")
    if (now() - u.get("work_last", 0)) >= 14400:
        actions.append("💼 <code>/travailler</code> ─ ton job peut te payer")

    # Études
    if u.get("study_start", 0):
        actions.append("📚 <code>/examen</code> ─ tu es en formation, passe l'examen")
    elif u.get("diplome", "") in ("", "Brevet"):
        actions.append("🎓 <code>/etudes</code> ─ vise un meilleur diplôme")

    # Missions / progression
    actions.append("🎯 <code>/missions</code> ─ vérifie tes objectifs")

    # Bourse
    if u["balance"] > 50_000:
        actions.append("📈 <code>/marche</code> ─ ton argent dort, investis-le")

    if not actions:
        body = [
            "🏆 <b>Tu es au top !</b>",
            "Tes besoins sont satisfaits, ton économie tourne.",
            "",
            "💡 Idées :",
            "  ▸ Tente une nouvelle activité dans /menu",
            "  ▸ Lance-toi en politique /election",
            "  ▸ Bâtis ton empire /creerboite",
            "  ▸ Affronte les autres /defier",
        ]
    else:
        body = ["<b>Voici ton plan d'action optimal :</b>", ""] + actions

    await update.message.reply_text(
        card("ROUTINE INTELLIGENTE", body,
             icon="🧭", style="thick",
             footer="Exécute ces actions dans l'ordre pour optimiser ta vie."),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════
#                        /journal ─ HISTORIQUE
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_journal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT category, summary, severity, created_at FROM life_journal "
            "WHERE user_id=? ORDER BY created_at DESC LIMIT 15",
            (user.id,)
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text(
            alert("info", "Ton journal de vie est encore vierge.\nDes événements s'y inscriront à mesure que tu joues."),
            parse_mode="HTML",
        )
        return

    icons = {
        "success": "✅", "info": "💡", "warning": "⚠️",
        "danger": "🚨", "error": "❌",
    }
    body = []
    current = now()
    for r in rows:
        elapsed = current - r["created_at"]
        ago = fmt_duration(elapsed)
        ic = icons.get(r["severity"], "•")
        cat = r["category"].upper()
        body.append(f"{ic} <b>[{cat}]</b>  <i>il y a {ago}</i>")
        body.append(f"   {r['summary']}")
        body.append("")

    await update.message.reply_text(
        card("📔 JOURNAL DE VIE",
             body[:-1] if body else ["(aucune entrée)"],
             icon="📔", style="thick",
             footer="15 dernières entrées affichées."),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════
#                  Logique de suggestions (asynchrone)
# ═══════════════════════════════════════════════════════════════════
async def _smart_suggestions(update: Update, u: dict) -> list[str]:
    out = []
    if u.get("hunger", 100) < 50:
        out.append("Ta faim baisse → <code>/manger</code>")
    if u.get("energy", 100) < 50:
        out.append("Tu manques d'énergie → <code>/dormir</code>")
    if u.get("stress", 0) > 60:
        out.append("Stress élevé → <code>/gym</code>")
    if (now() - u.get("daily_last", 0)) >= 86400:
        out.append("Bonus quotidien dispo → <code>/quotidien</code>")
    if not u.get("diplome") and not u.get("study_start"):
        out.append("Sans diplôme → <code>/etudes</code>")
    # Suggestion sur le marché si solde > 100k
    if u["balance"] > 100_000:
        out.append("Cash inutilisé → <code>/marche</code>")
    return out[:5]
