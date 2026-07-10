"""
handlers/profile.py — LIFESIM ULTRA V2
═══════════════════════════════════════════════════════════════════════
Affichages de profil enrichis & immersifs.
"""
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes

from database import (
    DB_PATH, get_user, update_field, get_all_skills,
    get_properties, get_vehicles, get_portfolio, get_inventory, get_marriage,
)
from utils.decorators import require_registered, require_free
from utils.helpers import (
    fmt, get_title, get_level, xp_for_level, xp_progress,
    karma_label, wealth_class, now,
)
from utils.aesthetics import (
    card, stat_card, mini_stat, alert, section, age_stage,
    bullet_list, keyed_list, stars, SEP_LIGHT,
)


# ═══════════════════════════════════════════════════════════════════
#                           /profil
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_profil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        user = update.message.reply_to_message.from_user
    else:
        user = update.effective_user

    u = await get_user(user.id, user.username or "", user.full_name or "")
    if not u.get("registered"):
        await update.message.reply_text(alert("error", "Ce joueur n'est pas enregistré."), parse_mode="HTML")
        return

    lvl, xp_in, xp_for_next = xp_progress(u["xp"])
    title = get_title(u["balance"])
    age = u.get("age", 20)
    age_label, age_icon = age_stage(age)

    marriage = await get_marriage(user.id)
    partner = "💔 Célibataire"
    if marriage:
        p = await get_user(marriage["partner_id"])
        partner = f"💍 Marié(e) à <b>{p['full_name']}</b>"

    # Famille
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT f.name FROM family_members fm JOIN family f ON f.family_id=fm.family_id WHERE fm.user_id=?",
            (user.id,)
        ) as cur:
            fam = await cur.fetchone()
    fam_text = f"🏠 Famille <b>{fam[0]}</b>" if fam else "🏠 Sans famille"

    from database import get_user_company
    company = await get_user_company(user.id)
    job_text = (
        f"{u.get('job', '—')} chez <b>{company['name']}</b>"
        if company else u.get('job', 'Sans emploi')
    )

    props = await get_properties(user.id)
    vehs = await get_vehicles(user.id)
    portfolio = await get_portfolio(user.id)

    bio = u.get("bio", "")
    location = u.get("location", "")

    body = [
        f"{u.get('profile_color', '🔵')} <b>{u['full_name']}</b>" + (f"  @{u['username']}" if u['username'] else ""),
        f"{age_icon} <b>{age} ans</b> · {age_label} · 📍 {location or '<i>lieu inconnu</i>'}",
        f"👑 <b>{title}</b>",
        f"⭐ Niveau <b>{lvl}</b> · XP <b>{xp_in}/{xp_for_next}</b> · 👑 Prestige <b>{u.get('prestige', 0)}</b>",
        f"🌟 Karma : <b>{karma_label(u.get('karma', 0))}</b> ({u.get('karma', 0):+d})",
        "",
        "<b>💗 ÉTAT</b>",
        mini_stat("Santé", u.get("health", 100), emoji="❤️"),
        mini_stat("Énergie", u.get("energy", 100), emoji="⚡"),
        mini_stat("Faim", u.get("hunger", 100), emoji="🍽️"),
        mini_stat("Bonheur", u.get("happiness", 100), emoji="😊"),
        mini_stat("Stress", u.get("stress", 0), emoji="😰", inverted=True),
        "",
        "<b>💼 PROFIL</b>",
        f"💵 Solde : <b>{fmt(u['balance'])}</b> · 🏷️ <b>{wealth_class(u['balance'])}</b>",
        f"💼 {job_text}",
        f"🎓 Diplôme : <b>{u.get('diplome', '—') or 'aucun'}</b>",
        f"{partner}",
        f"{fam_text}",
        "",
        "<b>🏠 PATRIMOINE</b>",
        f"🏘️ {len(props)} · 🚗 {len(vehs)} · 📈 {len(portfolio)}",
    ]
    if bio:
        body += ["", f"<i>📝 « {bio} »</i>"]

    texte = card(f"PROFIL DE {user.full_name.upper()}", body,
                 icon="👤", style="thick",
                 footer="Tape /stats pour les statistiques détaillées.")

    # Récupérer la photo de profil Telegram
    try:
        photos = await context.bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count > 0:
            file_id = photos.photos[0][-1].file_id
            await update.message.reply_photo(
                photo=file_id,
                caption=texte,
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(texte, parse_mode="HTML")
    except Exception:
        # En cas d'erreur (pas d'accès, etc.), on envoie le texte seul
        await update.message.reply_text(texte, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                           /stats
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    body = [
        "<b>💰 ARGENT</b>",
        f"  💵 Solde actuel : <b>{fmt(u['balance'])}</b>",
        f"  📈 Total gagné : <b>{fmt(u.get('total_earned', 0))}</b>",
        f"  📉 Total dépensé : <b>{fmt(u.get('total_spent', 0))}</b>",
        f"  🤲 Don à la charité : <b>{fmt(u.get('charity_given', 0))}</b>",
        "",
        "<b>⚔️ PVP & CRIME</b>",
        f"  🥊 Combats : <b>{u.get('arena_wins', 0)}W</b> / <b>{u.get('arena_losses', 0)}L</b>",
        f"  💼 Crimes tentés : <b>{u.get('crimes_done', 0)}</b>",
        f"  ✅ Crimes réussis : <b>{u.get('crimes_success', 0)}</b>",
        f"  💻 Hacks : <b>{u.get('hack_attempts', 0)}</b>",
        "",
        "<b>🎯 PROGRESSION</b>",
        f"  ✅ Missions accomplies : <b>{u.get('missions_done', 0)}</b>",
        f"  ⭐ XP cumulée : <b>{u['xp']}</b>",
        f"  👑 Prestige : <b>{u.get('prestige', 0)}</b>",
        "",
        "<b>🎲 LOISIRS</b>",
        f"  🎰 Loteries gagnées : <b>{u.get('lottery_wins', 0)}</b>",
        f"  🎲 Total casino misé : <b>{fmt(u.get('casino_total_bet', 0))}</b>",
        f"  💰 Total casino gagné : <b>{fmt(u.get('casino_total_win', 0))}</b>",
        f"  🌱 Plantes cultivées : <b>{u.get('plants_grown', 0)}</b>",
        f"  ✈️ Voyages : <b>{u.get('travel_count', 0)}</b>",
        "",
        "<b>📱 SOCIAL</b>",
        f"  📱 Followers : <b>{u.get('social_followers', 0)}</b>",
        f"  📊 Influence : <b>{u.get('influence_score', 0)}</b>",
    ]
    await update.message.reply_text(
        card("STATISTIQUES DÉTAILLÉES", body, icon="📊", style="thick"),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════
#                           /badges
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_badges(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        # Correction : utiliser user_badges au lieu de badges
        try:
            async with db.execute("SELECT badge, earned_at FROM user_badges WHERE user_id=?", (user.id,)) as cur:
                rows = await cur.fetchall()
        except aiosqlite.OperationalError:
            rows = []

    if not rows:
        await update.message.reply_text(
            alert("info", "Tu n'as encore aucun badge.\nEnchaîne missions et succès pour en gagner !"),
            parse_mode="HTML")
        return

    body = [f"  🏅 <b>{b}</b>" for b, _ in rows]
    await update.message.reply_text(
        card(f"BADGES de {user.full_name}", body, icon="🏅", style="stars",
             footer=f"Total : {len(rows)} badges"),
        parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                           /bio
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        u = await get_user(user.id)
        bio = u.get("bio", "")
        await update.message.reply_text(
            card("Ta biographie",
                 [f"<i>« {bio} »</i>" if bio else "<i>(vide)</i>",
                  "",
                  "Pour modifier : <code>/bio Ton nouveau texte</code>"],
                 icon="📝", style="round"),
            parse_mode="HTML")
        return
    new_bio = " ".join(context.args)[:200]
    await update_field(user.id, "bio", new_bio)
    await update.message.reply_text(
        card("📝 Bio mise à jour",
             [f"<i>« {new_bio} »</i>"], icon="✍️", style="round"),
        parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                           /lieu
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_setlocation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            alert("info", "Usage : <code>/lieu Paris</code>"),
            parse_mode="HTML")
        return
    loc = " ".join(context.args)[:50]
    await update_field(user.id, "location", loc)
    await update.message.reply_text(
        card("📍 Localisation changée", [f"Tu vis désormais à <b>{loc}</b>"],
             icon="📍", style="round"),
        parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                           /niveau
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_niveau(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    lvl, xp_in, xp_for_next = xp_progress(u["xp"])
    pct = int(xp_in / max(1, xp_for_next) * 100)
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    body = [
        f"⭐ Niveau actuel : <b>{lvl}</b>",
        f"📊 XP totale : <b>{u['xp']}</b>",
        "",
        f"Progression vers niveau {lvl+1} :",
        f"<code>{bar}</code> <b>{pct}%</b>",
        f"<b>{xp_in}</b> / <b>{xp_for_next}</b> XP",
    ]
    await update.message.reply_text(card("NIVEAU", body, icon="⭐", style="thick"), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                           /inventaire
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_inventaire(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    items = await get_inventory(user.id)
    if not items:
        await update.message.reply_text(
            alert("info", "Ton inventaire est vide."), parse_mode="HTML")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        body = []
        for i in items:
            item_id = i.get("item_id")
            if item_id:
                # Chercher dans la table items
                async with db.execute("SELECT emoji, name, rarity FROM items WHERE item_id = ?", (item_id,)) as cur:
                    item_def = await cur.fetchone()
                if item_def:
                    emoji = item_def["emoji"]
                    name = item_def["name"]
                    rarity = item_def["rarity"]
                    body.append(f"  `#{item_id}` {emoji} **{name}** ({rarity}) ×{i['quantity']}")
                else:
                    # Item legacy avec item_id mais non trouvé dans items (normalement pas)
                    body.append(f"  `#{item_id}` {i.get('item_name', 'Inconnu')} ×{i['quantity']}")
            else:
                # Ancien item (sans item_id) – on le laisse tel quel
                body.append(f"  📦 **{i.get('item_name', 'Objet')}** ×{i['quantity']}")
    await update.message.reply_text(
        card("📦 INVENTAIRE", body, icon="📦", style="thick",
             footer=f"Total : {len(items)} types d'items | `/useitem [id]` pour consommer, `/sellitem [id] [prix] [qty]` pour vendre"),
        parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                           /titres
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_titres(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from config import TITLES
    user = update.effective_user
    u = await get_user(user.id)
    current = get_title(u["balance"])
    body = []
    for t in TITLES:
        if u["balance"] >= t["min"]:
            body.append(f"  ✅ <b>{t['title']}</b> ─ {fmt(t['min'])}")
        else:
            body.append(f"  🔒 {t['title']} ─ {fmt(t['min'])}")
    await update.message.reply_text(
        card(f"TITRES (actuel : {current})", body, icon="👑", style="thick"),
        parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                           /karma
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_karma_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    k = u.get("karma", 0)
    body = [
        f"🌟 Karma : <b>{k:+d}</b>",
        f"📛 Statut : <b>{karma_label(k)}</b>",
        "",
        "<b>Effets actuels :</b>",
        "  • Bonnes actions / charité ↑ Karma",
        "  • Crimes / hacks ↓ Karma",
        "  • Karma haut : +25% revenus, succès",
        "  • Karma bas : −25% chance, pénalités",
    ]
    await update.message.reply_text(card("KARMA", body, icon="🌟", style="thick"), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                           /topxp
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_topxp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT full_name, xp FROM users WHERE registered=1 AND banned=0 "
            "ORDER BY xp DESC LIMIT 15") as cur:
            rows = await cur.fetchall()
    body = [f"  <b>{i+1}.</b> {r['full_name']} ─ {r['xp']} XP (niv. {get_level(r['xp'])})"
            for i, r in enumerate(rows)]
    await update.message.reply_text(
        card("🏆 TOP XP MONDIAL", body, icon="🏆", style="thick"),
        parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                       /historiquetitres
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_historiquetitres(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from config import TITLES
    user = update.effective_user
    u = await get_user(user.id)
    body = ["<b>Hiérarchie des titres :</b>", ""]
    for t in TITLES:
        unlocked = "✅" if u["balance"] >= t["min"] else "🔒"
        body.append(f"  {unlocked} <b>{t['title']}</b> ─ requiert {fmt(t['min'])}")
    await update.message.reply_text(
        card("HISTORIQUE DES TITRES", body, icon="👑", style="thick"),
        parse_mode="HTML")
