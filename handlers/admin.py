"""
handlers/admin.py — LIFESIM ULTRA V2
═══════════════════════════════════════════════════════════════════════
Console d'administration TOUTE-PUISSANTE (version étendue).
Ajoute de nombreuses commandes pour contrôler tous les aspects du jeu.
"""
import aiosqlite
import time
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import DB_PATH, ADMIN_IDS
from database import get_user, update_field, increment_field, update_balance, now, get_all_skills
from utils.decorators import admin_only
from utils.helpers import fmt, parse_amount, now, fmt_time, escape_html
from utils.aesthetics import card, alert, section, celebrate, SEP_LIGHT

# ═══════════════════════════════════════════════════════════════════
#                       Helpers internes
# ═══════════════════════════════════════════════════════════════════
async def _resolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    if not context.args:
        return None
    arg = context.args[0].lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username, full_name FROM users WHERE username=? OR user_id=?",
            (arg, arg if arg.isdigit() else 0)
        ) as cur:
            row = await cur.fetchone()
    if not row: return None
    class Fake:
        def __init__(s, uid, un, fn): s.id, s.username, s.full_name = uid, un, fn
    return Fake(row[0], row[1] or "", row[2] or "Joueur")

async def _log_admin(admin_id: int, action: str, target_id: int = 0, detail: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO admin_logs(admin_id, target_id, action, details, timestamp) VALUES(?,?,?,?,?)",
            (admin_id, target_id, action, detail, now())
        )
        await db.commit()

async def _get_item_id(item_name: str) -> int | None:
    """Retourne l'item_id à partir du nom, ou None si inexistant."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT item_id FROM items WHERE name = ?", (item_name,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None

async def _add_item_to_inventory(user_id: int, item_id: int, quantity: int = 1, item_type: str = "admin"):
    """Ajoute un item à l'inventaire d'un joueur (avec gestion de quantité)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id)
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            await db.execute(
                "UPDATE inventory SET quantity = quantity + ? WHERE user_id = ? AND item_id = ?",
                (quantity, user_id, item_id)
            )
        else:
            async with db.execute("SELECT name FROM items WHERE item_id = ?", (item_id,)) as cur2:
                name_row = await cur2.fetchone()
            item_name = name_row[0] if name_row else "Item"
            await db.execute("""
                INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, item_id, item_type, item_name, quantity, now()))
        await db.commit()

async def _remove_item_from_inventory(user_id: int, item_id: int, quantity: int = 1) -> bool:
    """Retire un item de l'inventaire. Retourne True si réussi."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id)
        ) as cur:
            row = await cur.fetchone()
        if not row or row[0] < quantity:
            return False
        new_qty = row[0] - quantity
        if new_qty <= 0:
            await db.execute("DELETE FROM inventory WHERE user_id = ? AND item_id = ?", (user_id, item_id))
        else:
            await db.execute(
                "UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND item_id = ?",
                (quantity, user_id, item_id)
            )
        await db.commit()
    return True

# ═══════════════════════════════════════════════════════════════════
#                          /admin (aide)
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_admin_aide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    body = [
        "<b>💰 ÉCONOMIE</b>",
        "  /addmoney @u N · /removemoney @u N · /setmoney @u N",
        "  /addbank @u N [banque] · /rembank @u N [banque] · /setbank @u N [banque]",
        "  /cleardebt @u · /addprestige @u N · /remprestige @u N · /setprestige @u N",
        "  /addxp @u N · /remxp @u N · /setxp @u N · /setlevel @u N",
        "  /addkarma @u N · /remkarma @u N · /setkarma @u N",
        "",
        "<b>👤 PERSONNAGE & STATS</b>",
        "  /setage @u N · /setjob @u nom · /setdiplome @u nom",
        "  /sethp @u N · /setenergy @u N · /sethappiness @u N",
        "  /sethunger @u N · /setstress @u N · /setlocation @u ville",
        "  /setbio @u texte · /setcolor @u code",
        "",
        "<b>🎁 ITEMS & INVENTAIRE</b>",
        "  /giveitem @u item_id qty · /takeitem @u item_id qty",
        "  /setitem @u item_id qty · /clearinventory @u",
        "  /giveallitems item_id qty · /listitems",
        "",
        "<b>🏠 PATRIMOINE</b>",
        "  /setproperty @u prop_type · /setpropertycond @u prop_id condition",
        "  /setvehicle @u veh_type · /setvehiclecond @u veh_id condition",
        "  /setluxury @u article · /setblackmarket @u article",
        "",
        "<b>🏢 ENTREPRISES</b>",
        "  /setcompanytreasury @u entreprise montant",
        "  /setcompanyreputation @u entreprise valeur",
        "  /setcompanylevel @u entreprise niveau",
        "  /addcompanytreasury entreprise montant",
        "  /deletecompany nom · /listcompanies",
        "  /forcehire @u entreprise poste",
        "",
        "<b>⚔️ GUILDES & GANGS</b>",
        "  /guild_create_admin nom · /guild_delete guild_id",
        "  /guild_add @u guild_id · /guild_remove @u",
        "  /guild_set_treasury guild_id N · /guild_set_level guild_id N",
        "  /guild_set_xp guild_id N",
        "  /gang_create_admin nom · /gang_delete gang_id",
        "  /gang_add @u gang_id · /gang_remove @u",
        "  /gang_set_treasury gang_id N · /gang_set_reputation gang_id N",
        "",
        "<b>💍 RELATIONS & FAMILLE</b>",
        "  /setrelation @u1 @u2 score · /setmarriage @u1 @u2",
        "  /divorce @u · /setfamily @u famille_id · /removefamily @u",
        "",
        "<b>🎯 MISSIONS & COMPÉTITIONS</b>",
        "  /resetmissions · /mission_force @u nom progression",
        "  /comp_start [type] · /comp_end · /comp_add_score @u points",
        "",
        "<b>🌍 MONDE & MARCHÉ</b>",
        "  /broadcast msg · /createevent nom durée effet",
        "  /endevent · /crashmarket · /boommarket",
        "  /resetmarket · /setprice asset N",
        "  /setstock asset price · /lotowin @u",
        "  /maintenance on/off",
        "",
        "<b>🛡️ MODÉRATION</b>",
        "  /banuser @u · /unbanuser @u",
        "  /warn @u msg · /clearwarn @u",
        "  /freezeuser @u duree · /unfreezeuser @u",
        "  /forceprison @u duree · /freeprison @u",
        "  /sethospital @u duree · /settravel @u duree",
        "  /resetuser @u  (reset complet)",
        "",
        "<b>⚡ DIEU</b>",
        "  /godmode @u on/off",
        "  /timetravel @u heures",
        "  /killuser @u",
        "  /spawn @u argent xp prestige",
        "  /resetworld (DANGER)",
        "",
        "<b>🔧 OUTILS</b>",
        "  /userinfo @u · /userstats @u",
        "  /topstats · /globalstats · /serverstats",
        "  /adminlogs · /resetcooldown @u champ",
        "  /adminpanel · /raidstatus · /forceraid id finish|cancel",
        "  /squadinfo id",
        "  /reloadconfig",
    ]
    await update.message.reply_text(
        card("👑 CONSOLE ADMIN COMPLÈTE", body,
             icon="👑", style="thick",
             footer=f"Tu es l'un des {len(ADMIN_IDS)} administrateurs du jeu."),
        parse_mode="HTML",
    )

# ═══════════════════════════════════════════════════════════════════
#                          ÉCONOMIE (étendue)
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_addmoney(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2:
        await update.message.reply_text(alert("info", "Usage : /addmoney @user montant"), parse_mode="HTML"); return
    amount = parse_amount(context.args[-1])
    if not amount:
        await update.message.reply_text(alert("error", "Montant invalide"), parse_mode="HTML"); return
    u = await get_user(t.id)
    await update_field(t.id, "balance", u["balance"] + amount)
    await _log_admin(update.effective_user.id, "addmoney", t.id, str(amount))
    await update.message.reply_text(
        card("💰 Argent ajouté",
             [f"À : <b>{t.full_name}</b>", f"💵 +{fmt(amount)}", f"Nouveau solde : {fmt(u['balance'] + amount)}"],
             icon="💰", style="stars"), parse_mode="HTML")

@admin_only
async def cmd_removemoney(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2:
        await update.message.reply_text(alert("info", "Usage : /removemoney @user montant"), parse_mode="HTML"); return
    amount = parse_amount(context.args[-1])
    u = await get_user(t.id)
    new_bal = max(0, u["balance"] - amount)
    await update_field(t.id, "balance", new_bal)
    await _log_admin(update.effective_user.id, "removemoney", t.id, str(amount))
    await update.message.reply_text(alert("success", f"Retiré {fmt(amount)} à {t.full_name}. Nouveau solde : {fmt(new_bal)}"), parse_mode="HTML")

@admin_only
async def cmd_setmoney(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    amount = parse_amount(context.args[-1])
    await update_field(t.id, "balance", amount)
    await _log_admin(update.effective_user.id, "setmoney", t.id, str(amount))
    await update.message.reply_text(alert("success", f"Solde de {t.full_name} fixé à {fmt(amount)}"), parse_mode="HTML")

@admin_only
async def cmd_addbank(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    amount = parse_amount(context.args[-1])
    bank_name = " ".join(context.args[1:-1]) if len(context.args) > 2 else None
    async with aiosqlite.connect(DB_PATH) as db:
        if bank_name:
            await db.execute("UPDATE bank_accounts SET balance = balance + ? WHERE user_id = ? AND bank_name = ?",
                             (amount, t.id, bank_name))
        else:
            await db.execute("UPDATE bank_accounts SET balance = balance + ? WHERE user_id = ? ORDER BY balance DESC LIMIT 1",
                             (amount, t.id))
        await db.commit()
    await _log_admin(update.effective_user.id, "addbank", t.id, f"{amount} ({bank_name or 'principal'})")
    await update.message.reply_text(alert("success", f"Ajouté {fmt(amount)} au compte bancaire de {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_rembank(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    amount = parse_amount(context.args[-1])
    bank_name = " ".join(context.args[1:-1]) if len(context.args) > 2 else None
    async with aiosqlite.connect(DB_PATH) as db:
        if bank_name:
            await db.execute("UPDATE bank_accounts SET balance = balance - ? WHERE user_id = ? AND bank_name = ? AND balance >= ?",
                             (amount, t.id, bank_name, amount))
        else:
            await db.execute("UPDATE bank_accounts SET balance = balance - ? WHERE user_id = ? AND balance >= ? ORDER BY balance DESC LIMIT 1",
                             (amount, t.id, amount))
        await db.commit()
    await _log_admin(update.effective_user.id, "rembank", t.id, f"{amount} ({bank_name or 'principal'})")
    await update.message.reply_text(alert("success", f"Retiré {fmt(amount)} du compte bancaire de {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_setbank(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    amount = parse_amount(context.args[-1])
    bank_name = " ".join(context.args[1:-1]) if len(context.args) > 2 else None
    async with aiosqlite.connect(DB_PATH) as db:
        if bank_name:
            await db.execute("UPDATE bank_accounts SET balance = ? WHERE user_id = ? AND bank_name = ?", (amount, t.id, bank_name))
        else:
            await db.execute("UPDATE bank_accounts SET balance = ? WHERE user_id = ?", (amount, t.id))
        await db.commit()
    await _log_admin(update.effective_user.id, "setbank", t.id, f"{amount} ({bank_name or 'principal'})")
    await update.message.reply_text(alert("success", f"Compte bancaire de {t.full_name} fixé à {fmt(amount)}"), parse_mode="HTML")

@admin_only
async def cmd_cleardebt(update, context):
    t = await _resolve(update, context)
    if not t: return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE bank_accounts SET loan=0, loan_due=0 WHERE user_id=?", (t.id,))
        await db.commit()
    await _log_admin(update.effective_user.id, "cleardebt", t.id)
    await update.message.reply_text(alert("success", f"Dettes effacées pour {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_addprestige(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    amount = int(context.args[-1])
    await increment_field(t.id, "prestige", amount)
    await _log_admin(update.effective_user.id, "addprestige", t.id, str(amount))
    await update.message.reply_text(alert("success", f"+{amount} prestige pour {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_remprestige(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    amount = int(context.args[-1])
    u = await get_user(t.id)
    new_val = max(0, u.get("prestige", 0) - amount)
    await update_field(t.id, "prestige", new_val)
    await _log_admin(update.effective_user.id, "remprestige", t.id, str(amount))
    await update.message.reply_text(alert("success", f"-{amount} prestige pour {t.full_name} (maintenant {new_val})"), parse_mode="HTML")

@admin_only
async def cmd_setprestige(update, context):
    await _set_field(update, context, "prestige", "Prestige")

@admin_only
async def cmd_addxp(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    amount = int(context.args[-1])
    await increment_field(t.id, "xp", amount)
    await _log_admin(update.effective_user.id, "addxp", t.id, str(amount))
    await update.message.reply_text(alert("success", f"+{amount} XP pour {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_remxp(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    amount = int(context.args[-1])
    u = await get_user(t.id)
    new_val = max(0, u.get("xp", 0) - amount)
    await update_field(t.id, "xp", new_val)
    await _log_admin(update.effective_user.id, "remxp", t.id, str(amount))
    await update.message.reply_text(alert("success", f"-{amount} XP pour {t.full_name} (maintenant {new_val})"), parse_mode="HTML")

@admin_only
async def cmd_setxp(update, context):
    await _set_field(update, context, "xp", "XP")

@admin_only
async def cmd_setlevel(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    level = int(context.args[-1])
    xp_needed = max(0, (level - 1) ** 2 * 500)
    await update_field(t.id, "xp", xp_needed)
    await _log_admin(update.effective_user.id, "setlevel", t.id, str(level))
    await update.message.reply_text(alert("success", f"Niveau de {t.full_name} forcé à {level} (XP = {xp_needed})"), parse_mode="HTML")

@admin_only
async def cmd_addkarma(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    amount = int(context.args[-1])
    await increment_field(t.id, "karma", amount)
    await _log_admin(update.effective_user.id, "addkarma", t.id, str(amount))
    await update.message.reply_text(alert("success", f"+{amount} karma pour {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_remkarma(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    amount = int(context.args[-1])
    u = await get_user(t.id)
    new_val = u.get("karma", 0) - amount
    await update_field(t.id, "karma", new_val)
    await _log_admin(update.effective_user.id, "remkarma", t.id, str(amount))
    await update.message.reply_text(alert("success", f"-{amount} karma pour {t.full_name} (maintenant {new_val})"), parse_mode="HTML")

@admin_only
async def cmd_setkarma(update, context):
    await _set_field(update, context, "karma", "Karma")

# ═══════════════════════════════════════════════════════════════════
#                          PERSONNAGE
# ═══════════════════════════════════════════════════════════════════
async def _set_field(update, context, field, label, parser=int):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    try:
        val = parser(context.args[-1])
    except Exception:
        await update.message.reply_text(alert("error", "Valeur invalide"), parse_mode="HTML"); return
    await update_field(t.id, field, val)
    await _log_admin(update.effective_user.id, f"set_{field}", t.id, str(val))
    await update.message.reply_text(alert("success", f"{label} de {t.full_name} → {val}"), parse_mode="HTML")

@admin_only
async def cmd_setage(update, context):   await _set_field(update, context, "age", "Âge")
@admin_only
async def cmd_sethp(update, context):    await _set_field(update, context, "health", "Santé")
@admin_only
async def cmd_setenergy(update, context): await _set_field(update, context, "energy", "Énergie")
@admin_only
async def cmd_sethappiness(update, context): await _set_field(update, context, "happiness", "Bonheur")
@admin_only
async def cmd_sethunger(update, context): await _set_field(update, context, "hunger", "Faim")
@admin_only
async def cmd_setstress(update, context): await _set_field(update, context, "stress", "Stress")
@admin_only
async def cmd_setjob(update, context):   await _set_field(update, context, "job", "Métier", parser=str)
@admin_only
async def cmd_setdiplome(update, context): await _set_field(update, context, "diplome", "Diplôme", parser=str)
async def cmd_setdiplome(update, context): await _set_field(update, context, "diplome", "Diplôme", parser=str)
@admin_only
async def cmd_setbio(update, context): await _set_field(update, context, "bio", "Bio", parser=str)
@admin_only
async def cmd_setcolor(update, context): await _set_field(update, context, "profile_color", "Couleur de profil", parser=str)

@admin_only
async def cmd_setskill(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 3: return
    skill = context.args[1]
    lvl = int(context.args[2])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO skills(user_id, skill_name, level) VALUES(?,?,?)",
                         (t.id, skill, lvl))
        await db.commit()
    await _log_admin(update.effective_user.id, "setskill", t.id, f"{skill} → {lvl}")
    await update.message.reply_text(alert("success", f"Skill {skill} de {t.full_name} → niv. {lvl}"), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
#                          MODÉRATION
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_banuser(update, context):
    t = await _resolve(update, context)
    if not t: return
    await update_field(t.id, "banned", 1)
    await _log_admin(update.effective_user.id, "ban", t.id)
    await update.message.reply_text(alert("danger", f"🔨 {t.full_name} a été banni du jeu."), parse_mode="HTML")

@admin_only
async def cmd_unbanuser(update, context):
    t = await _resolve(update, context)
    if not t: return
    await update_field(t.id, "banned", 0)
    await update.message.reply_text(alert("success", f"✅ {t.full_name} a été débanni."), parse_mode="HTML")

@admin_only
async def cmd_warn(update, context):
    t = await _resolve(update, context)
    if not t: return
    u = await get_user(t.id)
    await update_field(t.id, "warnings", u.get("warnings", 0) + 1)
    msg = " ".join(context.args[1:]) if len(context.args) > 1 else "(aucune raison)"
    await _log_admin(update.effective_user.id, "warn", t.id, msg)
    await update.message.reply_text(
        card("⚠️ Avertissement", [f"À : {t.full_name}", f"Raison : {msg}", f"Total : {u.get('warnings', 0) + 1}/3"],
             icon="⚠️", style="thick"), parse_mode="HTML")

@admin_only
async def cmd_clearwarn(update, context):
    t = await _resolve(update, context)
    if not t: return
    await update_field(t.id, "warnings", 0)
    await update.message.reply_text(alert("success", f"Avertissements de {t.full_name} effacés."), parse_mode="HTML")

@admin_only
async def cmd_freezeuser(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    secs = int(context.args[-1])
    await update_field(t.id, "frozen_until", now() + secs)
    await update.message.reply_text(alert("warning", f"❄️ {t.full_name} gelé pour {fmt_time(secs)}"), parse_mode="HTML")

@admin_only
async def cmd_unfreezeuser(update, context):
    t = await _resolve(update, context)
    if not t: return
    await update_field(t.id, "frozen_until", 0)
    await update.message.reply_text(alert("success", f"{t.full_name} dégelé."), parse_mode="HTML")

@admin_only
async def cmd_forceprison(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    secs = int(context.args[-1])
    await update_field(t.id, "prison_until", now() + secs)
    await update.message.reply_text(alert("danger", f"⛓️ {t.full_name} en prison pour {fmt_time(secs)}"), parse_mode="HTML")

@admin_only
async def cmd_freeprison(update, context):
    t = await _resolve(update, context)
    if not t: return
    await update_field(t.id, "prison_until", 0)
    await update.message.reply_text(alert("success", f"🔓 {t.full_name} libéré."), parse_mode="HTML")

@admin_only
async def cmd_sethospital(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    secs = int(context.args[-1])
    await update_field(t.id, "hospital_until", now() + secs)
    await update.message.reply_text(alert("warning", f"🏥 {t.full_name} hospitalisé pour {fmt_time(secs)}"), parse_mode="HTML")

@admin_only
async def cmd_settravel(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    secs = int(context.args[-1])
    await update_field(t.id, "travel_until", now() + secs)
    await update.message.reply_text(alert("warning", f"✈️ {t.full_name} bloqué (voyage) pour {fmt_time(secs)}"), parse_mode="HTML")

@admin_only
async def cmd_resetuser(update, context):
    t = await _resolve(update, context)
    if not t: return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""UPDATE users SET balance=10000, xp=0, level=1, karma=0, prestige=0,
                            health=100, energy=100, happiness=100, hunger=100, stress=0,
                            job='Livreur', diplome='', prison_until=0, hospital_until=0,
                            warnings=0, banned=0 WHERE user_id=?""", (t.id,))
        await db.commit()
    await _log_admin(update.effective_user.id, "RESET_FULL", t.id)
    await update.message.reply_text(
        card("🔄 RESET COMPLET", [f"<b>{t.full_name}</b>", "Tout a été remis à zéro.",
             "Solde : 10K · Niveau 1 · Sans diplôme · Stats max"],
             icon="🔄", style="thick"), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
#                          ITEMS & INVENTAIRE (étendu)
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_giveitem(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 3:
        await update.message.reply_text("Usage : /giveitem @user item_id quantité")
        return
    try:
        item_id = int(context.args[1])
        qty = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ item_id et quantité doivent être des nombres.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name FROM items WHERE item_id = ?", (item_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        await update.message.reply_text(f"❌ Item avec ID {item_id} inexistant. Utilisez /listitems.")
        return
    await _add_item_to_inventory(t.id, item_id, qty, "admin")
    await _log_admin(update.effective_user.id, "giveitem", t.id, f"item_id:{item_id} qty:{qty}")
    await update.message.reply_text(alert("success", f"Donné {qty}× {row[0]} (ID {item_id}) à {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_takeitem(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 3:
        await update.message.reply_text("Usage : /takeitem @user item_id quantité")
        return
    try:
        item_id = int(context.args[1])
        qty = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ item_id et quantité doivent être des nombres.")
        return
    if await _remove_item_from_inventory(t.id, item_id, qty):
        await _log_admin(update.effective_user.id, "takeitem", t.id, f"item_id:{item_id} qty:{qty}")
        await update.message.reply_text(alert("success", f"Retiré {qty} exemplaire(s) de l'item {item_id} à {t.full_name}"), parse_mode="HTML")
    else:
        await update.message.reply_text(alert("error", f"Quantité insuffisante ou item non possédé."), parse_mode="HTML")

@admin_only
async def cmd_setitem(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 3:
        await update.message.reply_text("Usage : /setitem @user item_id quantité")
        return
    try:
        item_id = int(context.args[1])
        qty = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ item_id et quantité doivent être des nombres.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name FROM items WHERE item_id = ?", (item_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        await update.message.reply_text(f"❌ Item avec ID {item_id} inexistant.")
        return
    if qty <= 0:
        # Supprimer totalement l'item
        async with aiosqlite.connect(DB_PATH) as db2:
            await db2.execute("DELETE FROM inventory WHERE user_id = ? AND item_id = ?", (t.id, item_id))
            await db2.commit()
    else:
        async with aiosqlite.connect(DB_PATH) as db2:
            await db2.execute("DELETE FROM inventory WHERE user_id = ? AND item_id = ?", (t.id, item_id))
            await db2.execute("""
                INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at)
                VALUES (?, ?, 'admin', ?, ?, ?)
            """, (t.id, item_id, row[0], qty, now()))
            await db2.commit()
    await _log_admin(update.effective_user.id, "setitem", t.id, f"item_id:{item_id} qty:{qty}")
    await update.message.reply_text(alert("success", f"Inventaire de {t.full_name} pour l'item {row[0]} (ID {item_id}) fixé à {qty}"), parse_mode="HTML")

@admin_only
async def cmd_clearinventory(update, context):
    t = await _resolve(update, context)
    if not t: return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM inventory WHERE user_id=?", (t.id,))
        await db.commit()
    await _log_admin(update.effective_user.id, "clearinventory", t.id)
    await update.message.reply_text(alert("success", f"Inventaire de {t.full_name} vidé."), parse_mode="HTML")

@admin_only
async def cmd_giveallitems(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /giveallitems item_id quantité")
        return
    try:
        item_id = int(context.args[0])
        qty = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ item_id et quantité doivent être des nombres.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name FROM items WHERE item_id = ?", (item_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        await update.message.reply_text(f"❌ Item avec ID {item_id} inexistant.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE registered=1") as cur:
            users = await cur.fetchall()
        for (uid,) in users:
            await _add_item_to_inventory(uid, item_id, qty, "admin_gift")
        await db.commit()
    await _log_admin(update.effective_user.id, "giveallitems", 0, f"item_id:{item_id} qty:{qty}")
    await update.message.reply_text(alert("success", f"Donné {qty}× {row[0]} (ID {item_id}) à tous les joueurs."), parse_mode="HTML")

@admin_only
async def cmd_listitems(update, context):
    """Liste tous les items disponibles dans la base (catalogue)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT item_id, name, type, rarity FROM items ORDER BY item_id") as cur:
            items = await cur.fetchall()
    if not items:
        await update.message.reply_text("📦 Aucun item trouvé.")
        return
    text = "📦 **Catalogue des items**\n\n"
    for i in items:
        text += f"`#{i['item_id']}` **{i['name']}** ({i['type']}, {i['rarity']})\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ═══════════════════════════════════════════════════════════════════
#                          PATRIMOINE
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_setproperty(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    ptype = context.args[1]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO properties(user_id, prop_type, purchased_at, condition) VALUES(?,?,?,100)",
            (t.id, ptype, now()))
        await db.commit()
    await _log_admin(update.effective_user.id, "setproperty", t.id, ptype)
    await update.message.reply_text(alert("success", f"Propriété {ptype} ajoutée à {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_setpropertycond(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 3: return
    try:
        prop_id = int(context.args[1])
        condition = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ ID de propriété et condition doivent être des nombres.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE properties SET condition = ? WHERE prop_id = ? AND user_id = ?", (condition, prop_id, t.id))
        await db.commit()
    await _log_admin(update.effective_user.id, "setpropertycond", t.id, f"prop_id:{prop_id} cond:{condition}")
    await update.message.reply_text(alert("success", f"État de la propriété #{prop_id} de {t.full_name} fixé à {condition}%"), parse_mode="HTML")

@admin_only
async def cmd_setvehicle(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    veh_type = context.args[1]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO vehicles(user_id, veh_type, condition, insured, purchased_at) VALUES(?,?,100,0,?)",
            (t.id, veh_type, now()))
        await db.commit()
    await _log_admin(update.effective_user.id, "setvehicle", t.id, veh_type)
    await update.message.reply_text(alert("success", f"Véhicule {veh_type} ajouté à {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_setvehiclecond(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 3: return
    try:
        veh_id = int(context.args[1])
        condition = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ ID de véhicule et condition doivent être des nombres.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE vehicles SET condition = ? WHERE veh_id = ? AND user_id = ?", (condition, veh_id, t.id))
        await db.commit()
    await _log_admin(update.effective_user.id, "setvehiclecond", t.id, f"veh_id:{veh_id} cond:{condition}")
    await update.message.reply_text(alert("success", f"État du véhicule #{veh_id} de {t.full_name} fixé à {condition}%"), parse_mode="HTML")

@admin_only
async def cmd_setluxury(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    article = " ".join(context.args[1:])
    from config import LUXURY_ITEMS
    if article not in LUXURY_ITEMS:
        await update.message.reply_text("❌ Article de luxe inconnu.")
        return
    data = LUXURY_ITEMS[article]
    # Créer ou récupérer l'item
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT item_id FROM items WHERE name = ?", (article,)) as cur:
            row = await cur.fetchone()
        if row:
            item_id = row[0]
        else:
            await db.execute("""
                INSERT INTO items (name, type, rarity, value, effect_type, effect_value, emoji, description)
                VALUES (?, 'luxury', 'epic', ?, NULL, 0, ?, ?)
            """, (article, data["price"], data["emoji"], f"Article de luxe : {article}"))
            async with db.execute("SELECT last_insert_rowid()") as cur2:
                item_id = (await cur2.fetchone())[0]
        await _add_item_to_inventory(t.id, item_id, 1, "luxury")
        await db.commit()
    await _log_admin(update.effective_user.id, "setluxury", t.id, article)
    await update.message.reply_text(alert("success", f"Article de luxe {article} ajouté à {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_setblackmarket(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    article = " ".join(context.args[1:])
    from config import BLACK_MARKET_ITEMS
    if article not in BLACK_MARKET_ITEMS:
        await update.message.reply_text("❌ Article du marché noir inconnu.")
        return
    data = BLACK_MARKET_ITEMS[article]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT item_id FROM items WHERE name = ?", (article,)) as cur:
            row = await cur.fetchone()
        if row:
            item_id = row[0]
        else:
            await db.execute("""
                INSERT INTO items (name, type, rarity, value, effect_type, effect_value, emoji, description)
                VALUES (?, 'blackmarket', 'rare', ?, NULL, 0, ?, ?)
            """, (article, data["price"], data["emoji"], f"Article du marché noir : {article}"))
            async with db.execute("SELECT last_insert_rowid()") as cur2:
                item_id = (await cur2.fetchone())[0]
        await _add_item_to_inventory(t.id, item_id, 1, "blackmarket")
        await db.commit()
    await _log_admin(update.effective_user.id, "setblackmarket", t.id, article)
    await update.message.reply_text(alert("success", f"Article {article} du marché noir ajouté à {t.full_name}"), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
#                          RELATIONS & FAMILLE
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_setrelation(update, context):
    if len(context.args) < 3:
        await update.message.reply_text("Usage : /setrelation @user1 @user2 score")
        return
    target1 = await _resolve(update, context)
    # Pour le second, on prend le deuxième argument
    if not target1:
        await update.message.reply_text("❌ Premier utilisateur invalide.")
        return
    arg2 = context.args[1].lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, full_name FROM users WHERE username=? OR user_id=?", (arg2, arg2 if arg2.isdigit() else 0)) as cur:
            row2 = await cur.fetchone()
    if not row2:
        await update.message.reply_text("❌ Second utilisateur invalide.")
        return
    try:
        score = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ Score doit être un nombre.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO mp_relations(user_id, other_id, score, last_interaction, relation_type)
            VALUES(?,?,?,?,'admin') ON CONFLICT(user_id, other_id) DO UPDATE SET score=?, last_interaction=?
        """, (target1.id, row2[0], score, now(), score, now()))
        await db.execute("""
            INSERT INTO mp_relations(user_id, other_id, score, last_interaction, relation_type)
            VALUES(?,?,?,?,'admin') ON CONFLICT(user_id, other_id) DO UPDATE SET score=?, last_interaction=?
        """, (row2[0], target1.id, score, now(), score, now()))
        await db.commit()
    await _log_admin(update.effective_user.id, "setrelation", target1.id, f"avec {row2[0]} score={score}")
    await update.message.reply_text(alert("success", f"Relation entre {target1.full_name} et {row2[1]} fixée à {score}"), parse_mode="HTML")

@admin_only
async def cmd_setmarriage(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /setmarriage @user1 @user2")
        return
    target1 = await _resolve(update, context)
    if not target1:
        await update.message.reply_text("❌ Premier utilisateur invalide.")
        return
    arg2 = context.args[1].lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, full_name FROM users WHERE username=? OR user_id=?", (arg2, arg2 if arg2.isdigit() else 0)) as cur:
            row2 = await cur.fetchone()
    if not row2:
        await update.message.reply_text("❌ Second utilisateur invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        # Supprimer les anciens mariages
        await db.execute("DELETE FROM marriages WHERE user_id IN (?,?)", (target1.id, row2[0]))
        await db.execute("INSERT INTO marriages(user_id, partner_id, married_at, status) VALUES(?,?,?,'active')", (target1.id, row2[0], now()))
        await db.execute("INSERT INTO marriages(user_id, partner_id, married_at, status) VALUES(?,?,?,'active')", (row2[0], target1.id, now()))
        await db.commit()
    await _log_admin(update.effective_user.id, "setmarriage", target1.id, f"avec {row2[0]}")
    await update.message.reply_text(alert("success", f"Mariage forcé entre {target1.full_name} et {row2[1]}"), parse_mode="HTML")

@admin_only
async def cmd_divorce(update, context):
    t = await _resolve(update, context)
    if not t:
        await update.message.reply_text("Usage : /divorce @user")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE marriages SET status='divorced', divorced_at=? WHERE user_id=? AND status='active'", (now(), t.id))
        await db.execute("UPDATE marriages SET status='divorced', divorced_at=? WHERE partner_id=? AND status='active'", (now(), t.id))
        await db.commit()
    await _log_admin(update.effective_user.id, "divorce", t.id)
    await update.message.reply_text(alert("success", f"{t.full_name} a été divorcé d'office."), parse_mode="HTML")

@admin_only
async def cmd_setfamily(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2:
        await update.message.reply_text("Usage : /setfamily @user famille_id")
        return
    try:
        fam_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ famille_id doit être un nombre.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        # Vérifier si la famille existe
        async with db.execute("SELECT 1 FROM family WHERE family_id = ?", (fam_id,)) as cur:
            if not await cur.fetchone():
                await update.message.reply_text(f"❌ Famille {fam_id} inexistante.")
                return
        await db.execute("INSERT OR REPLACE INTO family_members(user_id, family_id, role, joined_at) VALUES(?,?,?,?)",
                         (t.id, fam_id, "Membre", now()))
        await db.commit()
    await _log_admin(update.effective_user.id, "setfamily", t.id, str(fam_id))
    await update.message.reply_text(alert("success", f"{t.full_name} ajouté à la famille {fam_id}"), parse_mode="HTML")

@admin_only
async def cmd_removefamily(update, context):
    t = await _resolve(update, context)
    if not t:
        await update.message.reply_text("Usage : /removefamily @user")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM family_members WHERE user_id = ?", (t.id,))
        await db.commit()
    await _log_admin(update.effective_user.id, "removefamily", t.id)
    await update.message.reply_text(alert("success", f"{t.full_name} retiré de toute famille."), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
#                          ENTREPRISES (étendu)
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_setcompanytreasury(update, context):
    if len(context.args) < 3:
        await update.message.reply_text("Usage : /setcompanytreasury @user entreprise montant")
        return
    t = await _resolve(update, context)
    if not t:
        await update.message.reply_text("❌ Utilisateur invalide.")
        return
    company_name = context.args[1]
    try:
        amount = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT company_id FROM companies WHERE name = ? AND dissolved = 0", (company_name,)) as cur:
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text(f"❌ Entreprise '{company_name}' introuvable.")
            return
        await db.execute("UPDATE companies SET treasury = ? WHERE company_id = ?", (amount, row[0]))
        await db.commit()
    await _log_admin(update.effective_user.id, "setcompanytreasury", t.id, f"{company_name} -> {amount}")
    await update.message.reply_text(alert("success", f"Trésorerie de {company_name} fixée à {fmt(amount)}"), parse_mode="HTML")

@admin_only
async def cmd_setcompanyreputation(update, context):
    if len(context.args) < 3:
        await update.message.reply_text("Usage : /setcompanyreputation @user entreprise valeur")
        return
    t = await _resolve(update, context)
    if not t:
        await update.message.reply_text("❌ Utilisateur invalide.")
        return
    company_name = context.args[1]
    try:
        rep = int(context.args[2])
        if rep < 0 or rep > 100:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Valeur de réputation invalide (0-100).")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT company_id FROM companies WHERE name = ? AND dissolved = 0", (company_name,)) as cur:
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text(f"❌ Entreprise '{company_name}' introuvable.")
            return
        await db.execute("UPDATE companies SET reputation = ? WHERE company_id = ?", (rep, row[0]))
        await db.commit()
    await _log_admin(update.effective_user.id, "setcompanyreputation", t.id, f"{company_name} -> {rep}")
    await update.message.reply_text(alert("success", f"Réputation de {company_name} fixée à {rep}"), parse_mode="HTML")

@admin_only
async def cmd_setcompanylevel(update, context):
    if len(context.args) < 3:
        await update.message.reply_text("Usage : /setcompanylevel @user entreprise niveau")
        return
    t = await _resolve(update, context)
    if not t:
        await update.message.reply_text("❌ Utilisateur invalide.")
        return
    company_name = context.args[1]
    try:
        level = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ Niveau invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT company_id FROM companies WHERE name = ? AND dissolved = 0", (company_name,)) as cur:
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text(f"❌ Entreprise '{company_name}' introuvable.")
            return
        await db.execute("UPDATE companies SET level = ? WHERE company_id = ?", (level, row[0]))
        await db.commit()
    await _log_admin(update.effective_user.id, "setcompanylevel", t.id, f"{company_name} -> {level}")
    await update.message.reply_text(alert("success", f"Niveau de {company_name} fixé à {level}"), parse_mode="HTML")

@admin_only
async def cmd_addcompanytreasury(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /addcompanytreasury entreprise montant")
        return
    company_name = context.args[0]
    amount = parse_amount(context.args[1])
    if not amount:
        await update.message.reply_text("❌ Montant invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE companies SET treasury = treasury + ? WHERE name = ? AND dissolved = 0", (amount, company_name))
        await db.commit()
    await _log_admin(update.effective_user.id, "addcompanytreasury", 0, f"{company_name} +{amount}")
    await update.message.reply_text(alert("success", f"Ajouté {fmt(amount)} à la trésorerie de {company_name}"), parse_mode="HTML")

@admin_only
async def cmd_deletecompany(update, context):
    if not context.args: return
    name = context.args[0]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE companies SET dissolved=1 WHERE name=?", (name,))
        await db.commit()
    await update.message.reply_text(alert("success", f"Entreprise {name} dissoute."), parse_mode="HTML")

@admin_only
async def cmd_listcompanies(update, context):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM companies WHERE dissolved=0 LIMIT 20") as cur:
            rows = await cur.fetchall()
    body = [f"  {r['name']} ({r['sector']}) ─ {fmt(r['treasury'])}" for r in rows]
    await update.message.reply_text(card("Entreprises actives", body or ["(aucune)"], icon="🏢", style="thick"), parse_mode="HTML")

@admin_only
async def cmd_forcehire(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 3: return
    company = context.args[1]
    poste = context.args[2]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT company_id FROM companies WHERE name=?", (company,)) as cur:
            cid_row = await cur.fetchone()
        if not cid_row: return
        await db.execute(
            "INSERT OR REPLACE INTO company_members(user_id, company_id, poste, salary, joined_at) VALUES(?,?,?,?,?)",
            (t.id, cid_row[0], poste, 1500, now()))
        await db.commit()
    await update.message.reply_text(alert("success", f"{t.full_name} engagé chez {company} en {poste}"), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
#                          GUILDES & GANGS
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_guild_create_admin(update, context):
    if not context.args:
        await update.message.reply_text("Usage : /guild_create_admin [nom]")
        return
    name = " ".join(context.args)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO guilds (name, owner_id, treasury, level, xp, created_at) VALUES (?,?,0,1,0,?)",
            (name, 0, now())
        )
        async with db.execute("SELECT last_insert_rowid()") as cur:
            guild_id = (await cur.fetchone())[0]
        await db.commit()
    await update.message.reply_text(alert("success", f"Guilde **{name}** créée (ID {guild_id})"), parse_mode="HTML")

@admin_only
async def cmd_guild_delete(update, context):
    if not context.args:
        await update.message.reply_text("Usage : /guild_delete [guild_id]")
        return
    try:
        guild_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM guilds WHERE guild_id=?", (guild_id,))
        await db.execute("DELETE FROM guild_members WHERE guild_id=?", (guild_id,))
        await db.execute("DELETE FROM guild_invites WHERE guild_id=?", (guild_id,))
        await db.commit()
    await update.message.reply_text(alert("success", f"Guilde {guild_id} supprimée."), parse_mode="HTML")

@admin_only
async def cmd_guild_add(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /guild_add @user [guild_id]")
        return
    t = await _resolve(update, context)
    if not t: return
    try:
        guild_id = int(context.args[1])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ ID guilde invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM guild_members WHERE user_id=?", (t.id,)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Ce joueur est déjà dans une guilde.")
                return
        await db.execute(
            "INSERT INTO guild_members (guild_id, user_id, role, joined_at) VALUES (?,?, 'Membre', ?)",
            (guild_id, t.id, now())
        )
        await db.commit()
    await update.message.reply_text(alert("success", f"{t.full_name} ajouté à la guilde {guild_id}"), parse_mode="HTML")

@admin_only
async def cmd_guild_remove(update, context):
    t = await _resolve(update, context)
    if not t: return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM guild_members WHERE user_id=?", (t.id,))
        await db.commit()
    await update.message.reply_text(alert("success", f"{t.full_name} retiré de sa guilde."), parse_mode="HTML")

@admin_only
async def cmd_guild_set_treasury(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /guild_set_treasury [guild_id] [montant]")
        return
    try:
        guild_id = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE guilds SET treasury=? WHERE guild_id=?", (amount, guild_id))
        await db.commit()
    await update.message.reply_text(alert("success", f"Trésorerie de la guilde {guild_id} fixée à {fmt(amount)}"), parse_mode="HTML")

@admin_only
async def cmd_guild_set_level(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /guild_set_level [guild_id] [niveau]")
        return
    try:
        guild_id = int(context.args[0])
        level = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE guilds SET level=? WHERE guild_id=?", (level, guild_id))
        await db.commit()
    await update.message.reply_text(alert("success", f"Niveau de la guilde {guild_id} fixé à {level}"), parse_mode="HTML")

@admin_only
async def cmd_guild_set_xp(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /guild_set_xp [guild_id] [xp]")
        return
    try:
        guild_id = int(context.args[0])
        xp = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE guilds SET xp=? WHERE guild_id=?", (xp, guild_id))
        await db.commit()
    await update.message.reply_text(alert("success", f"XP de la guilde {guild_id} fixée à {xp}"), parse_mode="HTML")

# Gangs
@admin_only
async def cmd_gang_create_admin(update, context):
    if not context.args:
        await update.message.reply_text("Usage : /gang_create_admin [nom]")
        return
    name = " ".join(context.args)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO gangs (name, founder_id, treasury, reputation, created_at) VALUES (?,?,0,0,?)",
            (name, 0, now())
        )
        async with db.execute("SELECT last_insert_rowid()") as cur:
            gang_id = (await cur.fetchone())[0]
        await db.commit()
    await update.message.reply_text(alert("success", f"Gang **{name}** créé (ID {gang_id})"), parse_mode="HTML")

@admin_only
async def cmd_gang_delete(update, context):
    if not context.args:
        await update.message.reply_text("Usage : /gang_delete [gang_id]")
        return
    try:
        gang_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM gangs WHERE gang_id=?", (gang_id,))
        await db.execute("DELETE FROM gang_members WHERE gang_id=?", (gang_id,))
        await db.commit()
    await update.message.reply_text(alert("success", f"Gang {gang_id} supprimé."), parse_mode="HTML")

@admin_only
async def cmd_gang_add(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /gang_add @user [gang_id]")
        return
    t = await _resolve(update, context)
    if not t: return
    try:
        gang_id = int(context.args[1])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ ID gang invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM gang_members WHERE user_id=?", (t.id,)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Ce joueur est déjà dans un gang.")
                return
        await db.execute(
            "INSERT INTO gang_members (gang_id, user_id, role, joined_at) VALUES (?,?, 'Membre', ?)",
            (gang_id, t.id, now())
        )
        await db.commit()
    await update.message.reply_text(alert("success", f"{t.full_name} ajouté au gang {gang_id}"), parse_mode="HTML")

@admin_only
async def cmd_gang_remove(update, context):
    t = await _resolve(update, context)
    if not t: return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM gang_members WHERE user_id=?", (t.id,))
        await db.commit()
    await update.message.reply_text(alert("success", f"{t.full_name} retiré de son gang."), parse_mode="HTML")

@admin_only
async def cmd_gang_set_treasury(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /gang_set_treasury [gang_id] [montant]")
        return
    try:
        gang_id = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE gangs SET treasury=? WHERE gang_id=?", (amount, gang_id))
        await db.commit()
    await update.message.reply_text(alert("success", f"Trésorerie du gang {gang_id} fixée à {fmt(amount)}"), parse_mode="HTML")

@admin_only
async def cmd_gang_set_reputation(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /gang_set_reputation [gang_id] [valeur]")
        return
    try:
        gang_id = int(context.args[0])
        rep = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE gangs SET reputation=? WHERE gang_id=?", (rep, gang_id))
        await db.commit()
    await update.message.reply_text(alert("success", f"Réputation du gang {gang_id} fixée à {rep}"), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
#                          MISSIONS & COMPÉTITIONS
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_resetmissions(update, context):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM missions")
        await db.commit()
    await update.message.reply_text(alert("success", "Toutes les missions ont été réinitialisées."), parse_mode="HTML")

@admin_only
async def cmd_mission_force(update, context):
    if len(context.args) < 3:
        await update.message.reply_text("Usage : /mission_force @user [nom_mission] [progression]")
        return
    t = await _resolve(update, context)
    if not t: return
    mission_name = context.args[1]
    try:
        progress = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ Progression invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE missions SET progress=? WHERE user_id=? AND mission_name=?",
            (progress, t.id, mission_name)
        )
        await db.commit()
    await update.message.reply_text(alert("success", f"Mission {mission_name} de {t.full_name} forcée à {progress}"), parse_mode="HTML")

@admin_only
async def cmd_comp_start(update, context):
    comp_type = context.args[0] if context.args else "wealth"
    from handlers.competitions import COMPETITION_TYPES
    if comp_type not in COMPETITION_TYPES:
        await update.message.reply_text(f"❌ Type inconnu. Types : {', '.join(COMPETITION_TYPES.keys())}")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE competitions SET ended=1 WHERE ended=0")
        await db.execute(
            "INSERT INTO competitions (comp_type, starts_at, ends_at) VALUES (?, ?, ?)",
            (comp_type, now(), now() + COMPETITION_TYPES[comp_type]["duration"])
        )
        await db.commit()
    await update.message.reply_text(alert("success", f"Compétition {comp_type} démarrée."), parse_mode="HTML")

@admin_only
async def cmd_comp_end(update, context):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE competitions SET ended=1 WHERE ended=0")
        await db.commit()
    await update.message.reply_text(alert("success", "Compétition en cours terminée."), parse_mode="HTML")

@admin_only
async def cmd_comp_add_score(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /comp_add_score @user [points]")
        return
    t = await _resolve(update, context)
    if not t: return
    try:
        points = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Points invalides.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT comp_id FROM competitions WHERE ended=0 LIMIT 1") as cur:
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text("❌ Aucune compétition active.")
            return
        comp_id = row[0]
        await db.execute(
            "INSERT INTO competition_scores (comp_id, user_id, score) VALUES (?,?,?) ON CONFLICT(comp_id, user_id) DO UPDATE SET score = score + ?",
            (comp_id, t.id, points, points)
        )
        await db.commit()
    await update.message.reply_text(alert("success", f"+{points} points à {t.full_name} dans la compétition en cours."), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
#                          MONDE & MARCHÉ
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_broadcast(update, context):
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text(alert("info", "Usage : /broadcast message"), parse_mode="HTML"); return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE registered=1 AND banned=0") as cur:
            ids = [r[0] for r in await cur.fetchall()]
    sent = 0
    for uid in ids:
        try:
            await context.bot.send_message(
                uid,
                card("📢 ANNONCE OFFICIELLE", [msg], icon="📢", style="thick"),
                parse_mode="HTML")
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(alert("success", f"📡 Broadcast envoyé à {sent} joueurs."), parse_mode="HTML")

@admin_only
async def cmd_announce(update, context):
    await cmd_broadcast(update, context)

@admin_only
async def cmd_createevent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/createevent [nom] [durée_secondes] [effet]"""
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            alert("info", "Usage : /createevent \"nom de l'événement\" [durée en secondes] [effet]\n"
                 "Effets : market_crash, market_boom, health_crisis, tech_boom, trade_war, political_shift, disaster, energy_shift, realestate_crash, happiness_boost"),
            parse_mode="HTML"
        )
        return

    try:
        duration = int(args[-2])
        effect = args[-1].lower()
        name = " ".join(args[:-2])
    except ValueError:
        await update.message.reply_text(alert("error", "❌ Durée invalide. Utilise un nombre de secondes."), parse_mode="HTML")
        return

    valid_effects = [
        "market_crash", "market_boom", "health_crisis", "tech_boom",
        "trade_war", "political_shift", "disaster", "energy_shift",
        "realestate_crash", "happiness_boost"
    ]
    if effect not in valid_effects:
        await update.message.reply_text(alert("error", f"❌ Effet inconnu. Choisis parmi : {', '.join(valid_effects)}"), parse_mode="HTML")
        return

    if duration <= 0:
        await update.message.reply_text(alert("error", "❌ La durée doit être positive."), parse_mode="HTML")
        return

    now_ts = int(time.time())
    ends_at = now_ts + duration

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO world_events (name, effect, severity, started_at, ends_at, active) VALUES (?, ?, ?, ?, ?, 1)",
            (name, effect, 0.5, now_ts, ends_at)
        )
        await db.commit()

    await update.message.reply_text(
        card("⚡ Événement mondial créé", [
            f"Nom : <b>{escape_html(name)}</b>",
            f"Durée : {fmt_time(duration)}",
            f"Effet : {effect}",
            f"Fin : {time.strftime('%d/%m/%Y %H:%M:%S', time.gmtime(ends_at))}"
        ], icon="⚡", style="thick"),
        parse_mode="HTML"
    )

@admin_only
async def cmd_endevent(update, context):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE world_events SET active=0 WHERE ends_at > ?", (now(),))
        await db.commit()
    await update.message.reply_text(alert("success", "Tous les événements actifs ont été terminés."), parse_mode="HTML")

@admin_only
async def cmd_crashmarket(update, context):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE market_prices SET price = price * 0.3")
        await db.commit()
    await update.message.reply_text(
        card("💥 KRACH BOURSIER", ["Tous les prix divisés par ~3", "Les investisseurs paniquent !"],
             icon="💥", style="thick"), parse_mode="HTML")

@admin_only
async def cmd_boommarket(update, context):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE market_prices SET price = price * 2.5")
        await db.commit()
    await update.message.reply_text(
        card("🚀 BOOM DU MARCHÉ", ["Tous les prix ×2.5", "Les actionnaires font la fête !"],
             icon="🚀", style="stars"), parse_mode="HTML")

@admin_only
async def cmd_resetmarket(update, context):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE market_prices SET price = 100")
        await db.commit()
    await update.message.reply_text(alert("success", "Marché remis à 100 partout."), parse_mode="HTML")

@admin_only
async def cmd_setprice(update, context):
    if len(context.args) < 2: return
    asset = context.args[0]; price = float(context.args[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE market_prices SET price=? WHERE asset_name=?", (price, asset))
        await db.commit()
    await update.message.reply_text(alert("success", f"Prix de {asset} fixé à {price}"), parse_mode="HTML")

@admin_only
async def cmd_setstock(update, context):
    await cmd_setprice(update, context)

@admin_only
async def cmd_lotowin(update, context):
    t = await _resolve(update, context)
    if not t: return
    u = await get_user(t.id)
    win = 5_000_000
    await update_field(t.id, "balance", u["balance"] + win)
    await update_field(t.id, "lottery_wins", u.get("lottery_wins", 0) + 1)
    await update.message.reply_text(alert("success", f"🎰 {t.full_name} gagne 5M$ à la loterie !"), parse_mode="HTML")
    try:
        await context.bot.send_message(t.id,
            card("🎰 JACKPOT !", ["Tu remportes la loterie nationale !", "💰 +5 000 000 $"], icon="🎰", style="stars"),
            parse_mode="HTML")
    except Exception: pass

@admin_only
async def cmd_maintenance(update, context):
    mode = context.args[0].lower() if context.args else "on"
    import os
    flag = "maintenance.flag"
    if mode == "on":
        open(flag, "w").close()
        await update.message.reply_text(alert("warning", "🔧 Mode maintenance ACTIVÉ"), parse_mode="HTML")
    else:
        if os.path.exists(flag): os.remove(flag)
        await update.message.reply_text(alert("success", "✅ Maintenance désactivée"), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
#                          OUTILS / DEBUG
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_userinfo(update, context):
    t = await _resolve(update, context)
    if not t: return
    u = await get_user(t.id)
    body = [
        f"<b>{t.full_name}</b> (ID : <code>{t.id}</code>)",
        f"@{t.username}" if t.username else "—",
        "",
        f"💰 Solde : {fmt(u['balance'])}",
        f"⭐ XP : {u['xp']}  ·  Niveau {u['level']}",
        f"🌟 Karma : {u['karma']}",
        f"👑 Prestige : {u['prestige']}",
        f"💼 Job : {u['job']}",
        f"🎓 Diplôme : {u.get('diplome', '—')}",
        f"🎂 Âge : {u['age']}",
        "",
        f"❤️ {u['health']}% ⚡ {u['energy']}% 🍽️ {u['hunger']}% 😊 {u['happiness']}% 😰 {u['stress']}%",
        "",
        f"📅 Créé : {time.strftime('%Y-%m-%d', time.gmtime(u['created_at']))}",
        f"👀 Dernière connexion : {fmt_time(now() - u['last_seen'])} il y a",
        f"⚠️ Warnings : {u['warnings']}/3",
        f"🚫 Banni : {'OUI' if u['banned'] else 'non'}",
        f"⛓️ Prison : {fmt_time(max(0, u['prison_until'] - now())) if u['prison_until'] > now() else 'libre'}",
        f"⚡ God Mode : {'OUI' if u.get('god_mode') else 'non'}",
    ]
    await update.message.reply_text(card("INFO JOUEUR", body, icon="🔍", style="thick"), parse_mode="HTML")

@admin_only
async def cmd_userstats(update, context):
    t = await _resolve(update, context)
    if not t: return
    u = await get_user(t.id)
    body = [
        f"<b>{t.full_name}</b>",
        "",
        f"💰 Total gagné : {fmt(u.get('total_earned', 0))}",
        f"💸 Total dépensé : {fmt(u.get('total_spent', 0))}",
        f"🥊 Arène : {u.get('arena_wins', 0)}W / {u.get('arena_losses', 0)}L",
        f"💼 Crimes : {u.get('crimes_done', 0)} (✓{u.get('crimes_success', 0)})",
        f"🎯 Missions : {u.get('missions_done', 0)}",
        f"✈️ Voyages : {u.get('travel_count', 0)}",
        f"🎰 Loteries gagnées : {u.get('lottery_wins', 0)}",
        f"🌱 Plantes : {u.get('plants_grown', 0)}",
        f"💻 Hacks : {u.get('hack_attempts', 0)}",
        f"📱 Followers : {u.get('social_followers', 0)}",
        f"🤲 Charité totale : {fmt(u.get('charity_given', 0))}",
    ]
    await update.message.reply_text(card("STATS JOUEUR", body, icon="📊", style="thick"), parse_mode="HTML")

@admin_only
async def cmd_topstats(update, context):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT full_name, balance, xp, karma FROM users WHERE registered=1 ORDER BY balance DESC LIMIT 10") as cur:
            rows = await cur.fetchall()
    body = [f"  {i+1}. <b>{r['full_name']}</b> ─ {fmt(r['balance'])}" for i, r in enumerate(rows)]
    await update.message.reply_text(card("Top 10 mondial", body, icon="🏆", style="thick"), parse_mode="HTML")

@admin_only
async def cmd_botstats(update, context):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*), SUM(balance), AVG(level), MAX(balance) FROM users WHERE registered=1") as cur:
            count, total_bal, avg_lvl, max_bal = await cur.fetchone()
    body = [
        f"👥 Joueurs : <b>{count or 0}</b>",
        f"💰 Argent total en circulation : <b>{fmt(total_bal or 0)}</b>",
        f"📊 Niveau moyen : <b>{avg_lvl or 0:.1f}</b>",
        f"💎 Plus riche : <b>{fmt(max_bal or 0)}</b>",
    ]
    await update.message.reply_text(card("STATS SERVEUR", body, icon="🌐", style="thick"), parse_mode="HTML")

@admin_only
async def cmd_serverstats(update, context):
    await cmd_stats(update, context)

@admin_only
async def cmd_adminlogs(update, context):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute("SELECT * FROM admin_logs ORDER BY timestamp DESC LIMIT 15") as cur:
                rows = await cur.fetchall()
        except aiosqlite.OperationalError:
            rows = []
    if not rows:
        await update.message.reply_text(alert("info", "Aucun log admin."), parse_mode="HTML"); return
    body = []
    for r in rows:
        body.append(f"<code>{time.strftime('%m-%d %H:%M', time.gmtime(r['timestamp']))}</code> "
                    f"<b>{r['action']}</b> on {r['target_id']} ({r['details'][:30]})")
    await update.message.reply_text(card("📜 LOGS ADMIN", body, icon="📜", style="thick"), parse_mode="HTML")

@admin_only
async def cmd_resetcooldown(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    field = context.args[1]
    await update_field(t.id, field, 0)
    await update.message.reply_text(alert("success", f"Cooldown {field} de {t.full_name} reset."), parse_mode="HTML")

@admin_only
async def cmd_reloadconfig(update, context):
    import importlib, config
    importlib.reload(config)
    await update.message.reply_text(alert("success", "♻️ Config rechargée."), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
#                          DIEU
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_godmode(update, context):
    t = await _resolve(update, context)
    if not t: return
    mode = "on"
    if len(context.args) > 1: mode = context.args[1].lower()
    elif context.args and context.args[0].lower() in ("on", "off"): mode = context.args[0].lower()
    new_val = 1 if mode == "on" else 0
    await update_field(t.id, "god_mode", new_val)
    if new_val:
        await update.message.reply_text(
            card("⚡ MODE DIEU ACTIVÉ", [f"<b>{t.full_name}</b> est désormais invincible.",
                 "Immunité totale aux pénalités, accès admin temporaire."],
                 icon="⚡", style="stars"), parse_mode="HTML")
    else:
        await update.message.reply_text(alert("warning", f"Mode Dieu désactivé pour {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_killuser(update, context):
    t = await _resolve(update, context)
    if not t: return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET health=0, hospital_until=? WHERE user_id=?",
                         (now() + 3600, t.id))
        await db.execute(
            "INSERT INTO life_journal(user_id, category, summary, severity, created_at) VALUES(?,?,?,?,?)",
            (t.id, "death", "💀 Ton personnage est mort. Tu peux ressusciter via /reincarnate", "danger", now()))
        await db.commit()
    await update.message.reply_text(
        card("💀 MORT INFLIGÉE", [f"<b>{t.full_name}</b> est mort.",
             "Ressuscite via /reincarnate ─ légère pénalité de prestige."],
             icon="💀", style="thick"), parse_mode="HTML")

@admin_only
async def cmd_timetravel(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 2: return
    hours = int(context.args[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_life_tick = last_life_tick - ? WHERE user_id=?",
                         (hours * 3600, t.id))
        await db.commit()
    from utils.simulation import apply_passive_simulation
    await apply_passive_simulation(t.id)
    await update.message.reply_text(alert("success", f"⏳ {hours}h simulées pour {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_spawn(update, context):
    t = await _resolve(update, context)
    if not t or len(context.args) < 4: return
    money = parse_amount(context.args[1])
    xp = int(context.args[2])
    prestige = int(context.args[3])
    u = await get_user(t.id)
    await update_field(t.id, "balance", u["balance"] + money)
    await update_field(t.id, "xp", u["xp"] + xp)
    await update_field(t.id, "prestige", u["prestige"] + prestige)
    await update.message.reply_text(
        card("⚡ SPAWN BOOST", [f"<b>{t.full_name}</b>", f"💰 +{fmt(money)}",
             f"⭐ +{xp} XP", f"👑 +{prestige} prestige"],
             icon="⚡", style="stars"), parse_mode="HTML")

@admin_only
async def cmd_resetworld(update, context):
    if not context.args or context.args[0].lower() != "confirm":
        await update.message.reply_text(
            card("💀 RESET MONDE", [
                "⚠️ **CETTE ACTION EST IRRÉVERSIBLE**",
                "Tous les joueurs seront réinitialisés.",
                "Les entreprises, guildes, etc. seront supprimées.",
                "Pour confirmer : `/resetworld confirm`"
            ], icon="💀", style="thick"), parse_mode="HTML")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        # Réinitialiser toutes les tables utilisateur
        await db.execute("DELETE FROM users WHERE user_id != 0")
        await db.execute("DELETE FROM bank_accounts")
        await db.execute("DELETE FROM properties")
        await db.execute("DELETE FROM vehicles")
        await db.execute("DELETE FROM skills")
        await db.execute("DELETE FROM garden")
        await db.execute("DELETE FROM investments")
        await db.execute("DELETE FROM companies")
        await db.execute("DELETE FROM company_members")
        await db.execute("DELETE FROM company_applications")
        await db.execute("DELETE FROM company_logs")
        await db.execute("DELETE FROM company_shares")
        await db.execute("DELETE FROM company_products")
        await db.execute("DELETE FROM company_ads")
        await db.execute("DELETE FROM company_invitations")
        await db.execute("DELETE FROM family")
        await db.execute("DELETE FROM family_members")
        await db.execute("DELETE FROM marriages")
        await db.execute("DELETE FROM marriage_requests")
        await db.execute("DELETE FROM friendships")
        await db.execute("DELETE FROM inventory")
        await db.execute("DELETE FROM auction_listings")
        await db.execute("DELETE FROM crime_log")
        await db.execute("DELETE FROM travel_log")
        await db.execute("DELETE FROM missions")
        await db.execute("DELETE FROM politics")
        await db.execute("DELETE FROM social_media")
        await db.execute("DELETE FROM blackmarket_log")
        await db.execute("DELETE FROM world_events")
        await db.execute("DELETE FROM lottery_tickets")
        await db.execute("DELETE FROM lottery_draws")
        await db.execute("DELETE FROM hack_log")
        await db.execute("DELETE FROM pvp_challenges")
        await db.execute("DELETE FROM insurance")
        await db.execute("DELETE FROM admin_logs")
        await db.execute("DELETE FROM price_history")
        await db.execute("DELETE FROM gangs")
        await db.execute("DELETE FROM gang_members")
        await db.execute("DELETE FROM adoptions")
        await db.execute("DELETE FROM casino_log")
        await db.execute("DELETE FROM bank_loans")
        await db.execute("DELETE FROM user_badges")
        await db.execute("DELETE FROM title_history")
        await db.execute("DELETE FROM mission_log")
        await db.execute("DELETE FROM social_log")
        await db.execute("DELETE FROM guilds")
        await db.execute("DELETE FROM guild_members")
        await db.execute("DELETE FROM guild_invites")
        await db.execute("DELETE FROM guild_logs")
        await db.execute("DELETE FROM guild_quest_proposals")
        await db.execute("DELETE FROM guild_votes")
        await db.execute("DELETE FROM guild_competitions")
        await db.execute("DELETE FROM user_achievements")
        await db.execute("DELETE FROM competitions")
        await db.execute("DELETE FROM competition_scores")
        await db.execute("DELETE FROM competition_participants")
        await db.execute("DELETE FROM notifications")
        await db.execute("DELETE FROM life_journal")
        await db.execute("DELETE FROM reports")
        await db.execute("DELETE FROM mp_trades")
        await db.execute("DELETE FROM mp_relations")
        await db.execute("DELETE FROM mp_gifts")
        await db.execute("DELETE FROM sqlite_sequence")
        await db.commit()
    await update.message.reply_text(alert("danger", "🌍 LE MONDE A ÉTÉ RÉINITIALISÉ COMPLÈTEMENT."), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
#                          LISTE DES JOUEURS
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = 1
    if context.args and context.args[0].isdigit():
        page = int(context.args[0])
    per_page = 20
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) as total FROM users WHERE registered=1") as cur:
            total = (await cur.fetchone())["total"]
        total_pages = (total + per_page - 1) // per_page
        if page < 1: page = 1
        if page > total_pages and total_pages > 0: page = total_pages
        offset = (page - 1) * per_page
        async with db.execute(
            "SELECT user_id, username, full_name FROM users WHERE registered=1 ORDER BY user_id LIMIT ? OFFSET ?",
            (per_page, offset)
        ) as cur:
            users = await cur.fetchall()
    if not users:
        await update.message.reply_text("📭 Aucun joueur enregistré.")
        return
    lines = []
    for u in users:
        username = f"@{escape_html(u['username'])}" if u['username'] else "pas de pseudo"
        name = escape_html(u['full_name'])
        lines.append(f"<code>{u['user_id']}</code> — {name} ({username})")
    text = f"👥 <b>Liste des joueurs</b> (page {page}/{total_pages})\n\n" + "\n".join(lines)
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(f"<code>/listusers {page-1}</code> ◀️")
        if page < total_pages:
            nav.append(f"<code>/listusers {page+1}</code> ▶️")
        text += "\n\n" + "  ".join(nav)
    await update.message.reply_text(text, parse_mode="HTML")
    
# ═══════════════════════════════════════════════════════════════════
#                     HISTORIQUE COMPLET D'UN JOUEUR
# ═══════════════════════════════════════════════════════════════════
@admin_only
async def cmd_playerhistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await _resolve(update, context)
    if not target:
        await update.message.reply_text("Usage : /playerhistory @user (ou en répondant à un message)")
        return

    page = 1
    if context.args and len(context.args) > 1 and context.args[1].isdigit():
        page = int(context.args[1])

    per_page = 10
    events = []

    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row

        # 1. Crimes
        async with db.execute(
            "SELECT crime_type, success, reward, jail_time, timestamp FROM crime_log WHERE user_id=? ORDER BY timestamp DESC",
            (target.id,)
        ) as cur:
            for row in await cur.fetchall():
                ts = time.strftime("%d/%m %H:%M", time.localtime(row["timestamp"]))
                if row["success"]:
                    events.append(("💀 Crime", f"{ts} | {row['crime_type']} → +{fmt(row['reward'])}", row["timestamp"]))
                else:
                    events.append(("⛓️ Crime échoué", f"{ts} | {row['crime_type']} → prison {fmt_time(row['jail_time'])}", row["timestamp"]))

        # 2. Voyages
        async with db.execute(
            "SELECT destination, cost, timestamp FROM travel_log WHERE user_id=? ORDER BY timestamp DESC LIMIT 50",
            (target.id,)
        ) as cur:
            for row in await cur.fetchall():
                ts = time.strftime("%d/%m %H:%M", time.localtime(row["timestamp"]))
                events.append(("✈️ Voyage", f"{ts} | {row['destination']} → -{fmt(row['cost'])}", row["timestamp"]))

        # 3. Achats de propriété
        async with db.execute(
            "SELECT prop_type, purchased_at FROM properties WHERE user_id=? ORDER BY purchased_at DESC LIMIT 20",
            (target.id,)
        ) as cur:
            for row in await cur.fetchall():
                ts = time.strftime("%d/%m %H:%M", time.localtime(row["purchased_at"]))
                events.append(("🏠 Achat", f"{ts} | {row['prop_type']}", row["purchased_at"]))

        # 4. Journal de vie
        async with db.execute(
            "SELECT category, summary, severity, created_at FROM life_journal WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
            (target.id,)
        ) as cur:
            for row in await cur.fetchall():
                ts = time.strftime("%d/%m %H:%M", time.localtime(row["created_at"]))
                summary = row["summary"].replace("\n", " ").strip()[:80]
                events.append(("📔 Vie", f"{ts} | {row['category']} : {summary}", row["created_at"]))

        # 5. Missions complétées (xp_reward)
        async with db.execute(
            "SELECT mission_name, reward, xp_reward, completed_at FROM missions WHERE user_id=? AND completed=1 ORDER BY completed_at DESC LIMIT 20",
            (target.id,)
        ) as cur:
            for row in await cur.fetchall():
                ts = time.strftime("%d/%m %H:%M", time.localtime(row["completed_at"]))
                events.append(("🎯 Mission", f"{ts} | {row['mission_name']} → +{fmt(row['reward'])} +{row['xp_reward']}XP", row["completed_at"]))

        # 6. Échanges (mp_trades)
        async with db.execute(
            "SELECT from_id, to_id, offer_money, status, created_at FROM mp_trades WHERE from_id=? OR to_id=? ORDER BY created_at DESC LIMIT 20",
            (target.id, target.id)
        ) as cur:
            for row in await cur.fetchall():
                ts = time.strftime("%d/%m %H:%M", time.localtime(row["created_at"]))
                if row["from_id"] == target.id:
                    txt = f"Envoyé {fmt(row['offer_money'])} à {row['to_id']} | statut {row['status']}"
                else:
                    txt = f"Reçu {fmt(row['offer_money'])} de {row['from_id']} | statut {row['status']}"
                events.append(("🤝 Échange", f"{ts} | {txt}", row["created_at"]))

        # 7. Emploi
        async with db.execute(
            "SELECT c.name, cm.joined_at FROM company_members cm JOIN companies c ON c.company_id=cm.company_id WHERE cm.user_id=? ORDER BY cm.joined_at DESC LIMIT 10",
            (target.id,)
        ) as cur:
            for row in await cur.fetchall():
                ts = time.strftime("%d/%m %H:%M", time.localtime(row["joined_at"]))
                events.append(("🏢 Emploi", f"{ts} | Entrée chez {row['name']}", row["joined_at"]))

        # 8. Gang
        async with db.execute(
            "SELECT g.name, gm.role, gm.joined_at FROM gang_members gm JOIN gangs g ON g.gang_id=gm.gang_id WHERE gm.user_id=? ORDER BY gm.joined_at DESC",
            (target.id,)
        ) as cur:
            for row in await cur.fetchall():
                ts = time.strftime("%d/%m %H:%M", time.localtime(row["joined_at"]))
                events.append(("🔫 Gang", f"{ts} | Rejoint {row['name']} en tant que {row['role']}", row["joined_at"]))

        events.sort(key=lambda x: x[2], reverse=True)

    total = len(events)
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    start = (page - 1) * per_page
    end = start + per_page
    page_events = events[start:end]

    if not page_events:
        await update.message.reply_text(f"📭 Aucun historique trouvé pour {escape_html(target.full_name)}.")
        return

    lines = []
    for icon, desc, _ in page_events:
        lines.append(f"{icon} {escape_html(desc)}")
    text = f"📜 <b>Historique de {escape_html(target.full_name)}</b> (page {page}/{total_pages})\n\n" + "\n".join(lines)
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(f"<code>/playerhistory {target.id} {page-1}</code> ◀️")
        if page < total_pages:
            nav.append(f"<code>/playerhistory {target.id} {page+1}</code> ▶️")
        text += "\n\n" + "  ".join(nav)
    await update.message.reply_text(text, parse_mode="HTML")

@admin_only
async def cmd_deleteuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Supprimer complètement un joueur de la base de données (toutes ses données)."""
    target = await _resolve(update, context)
    if not target:
        await update.message.reply_text("Usage : /deleteuser @utilisateur (ou en répondant)")
        return

    # Demander confirmation
    if not context.args or context.args[-1].lower() != "confirm":
        await update.message.reply_text(
            f"⚠️ **SUPPRESSION DÉFINITIVE DE {target.full_name}**\n\n"
            f"Toutes ses données seront effacées : profil, argent, banque, propriétés, véhicules, entreprises, etc.\n"
            f"Cette action est IRRÉVERSIBLE.\n\n"
            f"Pour confirmer : `/deleteuser {target.id} confirm`"
        )
        return

    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        # Supprimer dans l'ordre (respect des clés étrangères)
        await db.execute("DELETE FROM user_achievements WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM user_badges WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM title_history WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM notifications WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM life_journal WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM reports WHERE user_id = ? OR target_id = ?", (target.id, target.id))
        await db.execute("DELETE FROM mp_trades WHERE from_id = ? OR to_id = ?", (target.id, target.id))
        await db.execute("DELETE FROM mp_relations WHERE user_id = ? OR other_id = ?", (target.id, target.id))
        await db.execute("DELETE FROM mp_gifts WHERE from_id = ? OR to_id = ?", (target.id, target.id))
        await db.execute("DELETE FROM inventory WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM missions WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM mission_log WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM crime_log WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM travel_log WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM adoptions WHERE parent_id = ? OR child_id = ?", (target.id, target.id))
        await db.execute("DELETE FROM friendships WHERE user_id = ? OR friend_id = ?", (target.id, target.id))
        await db.execute("DELETE FROM marriage_requests WHERE from_id = ? OR to_id = ?", (target.id, target.id))
        await db.execute("DELETE FROM marriages WHERE user_id = ? OR partner_id = ?", (target.id, target.id))
        await db.execute("DELETE FROM family_members WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM family_invites WHERE invited_id = ? OR invited_by = ?", (target.id, target.id))
        # Ne pas supprimer la famille si d'autres membres existent – on supprime juste le lien
        await db.execute("DELETE FROM social_media WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM politics WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM insurance WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM bank_accounts WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM bank_loans WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM investments WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM garden WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM properties WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM vehicles WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM skills WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM company_members WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM company_applications WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM company_shares WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM company_invitations WHERE user_id = ? OR invited_by = ?", (target.id, target.id))
        await db.execute("DELETE FROM gang_members WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM guild_members WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM guild_invites WHERE invited_id = ? OR invited_by = ?", (target.id, target.id))
        await db.execute("DELETE FROM guild_votes WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM guild_quest_proposals WHERE proposer_id = ?", (target.id,))
        await db.execute("DELETE FROM auction_listings WHERE seller_id = ?", (target.id,))
        await db.execute("DELETE FROM pvp_challenges WHERE challenger_id = ? OR target_id = ?", (target.id, target.id))
        await db.execute("DELETE FROM casino_log WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM hack_log WHERE hacker_id = ?", (target.id,))
        await db.execute("DELETE FROM lottery_tickets WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM blackmarket_log WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM competition_scores WHERE user_id = ?", (target.id,))
        await db.execute("DELETE FROM competition_participants WHERE user_id = ?", (target.id,))
        # Enfin, supprimer l'utilisateur lui-même
        await db.execute("DELETE FROM users WHERE user_id = ?", (target.id,))
        await db.commit()

    await _log_admin(update.effective_user.id, "DELETE_USER", target.id, f"Joueur {target.full_name} supprimé")
    await update.message.reply_text(
        f"🗑️ **Joueur {target.full_name} (ID {target.id}) supprimé définitivement.**"
    )

@admin_only
async def cmd_givebadge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Donne un badge à un joueur."""
    t = await _resolve(update, context)
    if not t or len(context.args) < 2:
        await update.message.reply_text("Usage : /givebadge @user [nom_du_badge]")
        return
    badge = " ".join(context.args[1:])[:50]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO user_badges(user_id, badge, earned_at) VALUES(?,?,?)",
                         (t.id, badge, now()))
        await db.commit()
    await _log_admin(update.effective_user.id, "givebadge", t.id, badge)
    await update.message.reply_text(alert("success", f"Badge **{badge}** donné à {t.full_name}"), parse_mode="HTML")

@admin_only
async def cmd_removetreasury(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retire de l'argent de la trésorerie d'une entreprise."""
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /removetreasury [nom_entreprise] [montant]")
        return
    company_name = " ".join(context.args[:-1])
    amount = parse_amount(context.args[-1])
    if not amount or amount <= 0:
        await update.message.reply_text("❌ Montant invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT company_id, treasury FROM companies WHERE name = ? AND dissolved = 0", (company_name,)) as cur:
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text(f"❌ Entreprise '{company_name}' introuvable.")
            return
        company_id, treasury = row
        if amount > treasury:
            await update.message.reply_text(f"❌ Trésorerie insuffisante. Disponible : {fmt(treasury)}", parse_mode="Markdown")
            return
        await db.execute("UPDATE companies SET treasury = treasury - ? WHERE company_id = ?", (amount, company_id))
        await db.commit()
    from database import log_company_action
    await log_company_action(company_id, "retrait_admin", update.effective_user.id, f"{fmt(amount)}")
    await _log_admin(update.effective_user.id, "removetreasury", 0, f"{company_name} -{amount}")
    await update.message.reply_text(f"✅ Retrait de {fmt(amount)} de la trésorerie de **{company_name}**. Nouvelle trésorerie : {fmt(treasury - amount)}", parse_mode="Markdown")


async def cmd_fix_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Admin only.")
        return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # 1. Sauvegarder les données existantes
            await db.execute("CREATE TABLE inventory_backup AS SELECT * FROM inventory")
            # 2. Supprimer l'ancienne table
            await db.execute("DROP TABLE inventory")
            # 3. Recréer avec la bonne contrainte
            await db.execute("""
                CREATE TABLE inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    item_id INTEGER,
                    item_type TEXT,
                    item_name TEXT,
                    quantity INTEGER DEFAULT 1,
                    acquired_at INTEGER,
                    UNIQUE(user_id, item_id)
                )
            """)
            # 4. Recopier les données (en regroupant par user_id, item_id)
            await db.execute("""
                INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at)
                SELECT user_id, item_id, item_type, item_name, SUM(quantity), MIN(acquired_at)
                FROM inventory_backup
                GROUP BY user_id, item_id
            """)
            # 5. Supprimer la sauvegarde
            await db.execute("DROP TABLE inventory_backup")
            await db.commit()
        await update.message.reply_text("✅ Table inventory corrigée avec succès (données conservées).")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur : {e}")


@admin_only
async def cmd_adminpanel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        stats = {}
        queries = {
            "players": "SELECT COUNT(*) AS n FROM users WHERE registered=1",
            "banned": "SELECT COUNT(*) AS n FROM users WHERE banned=1",
            "reports": "SELECT COUNT(*) AS n FROM reports WHERE status='pending'",
            "trades": "SELECT COUNT(*) AS n FROM mp_trades WHERE status='pending'",
            "squads": "SELECT COUNT(*) AS n FROM mp_squads",
            "raids": "SELECT COUNT(*) AS n FROM mp_raids WHERE status='active'",
            "companies": "SELECT COUNT(*) AS n FROM companies WHERE dissolved=0",
            "guilds": "SELECT COUNT(*) AS n FROM guilds",
            "economy": "SELECT COALESCE(SUM(balance), 0) AS n FROM users WHERE registered=1",
        }
        for key, query in queries.items():
            async with db.execute(query) as cur:
                stats[key] = (await cur.fetchone())["n"]

    await update.message.reply_text(
        card(
            "👑 Admin panel",
            [
                f"👥 Joueurs actifs : <b>{stats['players']}</b>",
                f"🚫 Bannis : <b>{stats['banned']}</b>",
                f"📩 Reports en attente : <b>{stats['reports']}</b>",
                f"🤝 Échanges en attente : <b>{stats['trades']}</b>",
                f"🛡️ Escouades : <b>{stats['squads']}</b>",
                f"🐉 Raids actifs : <b>{stats['raids']}</b>",
                f"🏢 Entreprises vivantes : <b>{stats['companies']}</b>",
                f"🏰 Guildes : <b>{stats['guilds']}</b>",
                f"💰 Masse monétaire : <b>{fmt(stats['economy'])}</b>",
            ],
            icon="👑", style="thick"
        ),
        parse_mode="HTML"
    )


@admin_only
async def cmd_raidstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT r.raid_id, r.boss_name, r.boss_level, r.boss_hp, r.max_hp, r.reward_pool,
                   r.ends_at, s.name AS squad_name
            FROM mp_raids r
            JOIN mp_squads s ON s.squad_id = r.squad_id
            WHERE r.status='active'
            ORDER BY r.raid_id DESC
            LIMIT 10
        """) as cur:
            raids = await cur.fetchall()

    if not raids:
        await update.message.reply_text(alert("info", "Aucun raid actif."), parse_mode="HTML")
        return

    lines = []
    for raid in raids:
        remaining = max(0, raid["ends_at"] - now())
        lines += [
            f"<b>#{raid['raid_id']}</b> · {raid['boss_name']} niv.{raid['boss_level']}",
            f"Escouade : <b>{raid['squad_name']}</b>",
            f"PV : <b>{fmt(raid['boss_hp'])}</b> / {fmt(raid['max_hp'])}",
            f"Jackpot : <b>{fmt(raid['reward_pool'])}</b> · fin dans <b>{fmt_time(remaining)}</b>",
            "",
        ]

    await update.message.reply_text(
        card("🐉 Raids actifs", lines[:-1], icon="🐉", style="thick"),
        parse_mode="HTML"
    )


@admin_only
async def cmd_forceraid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(
            alert("info", "Usage : <code>/forceraid id finish|cancel</code>"),
            parse_mode="HTML"
        )
        return

    raid_id = int(context.args[0])
    action = context.args[1].lower()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mp_raids WHERE raid_id=?", (raid_id,)) as cur:
            raid = await cur.fetchone()
        if not raid:
            await update.message.reply_text(alert("error", "Raid introuvable."), parse_mode="HTML")
            return

        if action == "cancel":
            await db.execute("UPDATE mp_raids SET status='cancelled' WHERE raid_id=?", (raid_id,))
            await db.commit()
            await _log_admin(update.effective_user.id, "forceraid_cancel", 0, str(raid_id))
            await update.message.reply_text(alert("success", f"Raid #{raid_id} annulé."), parse_mode="HTML")
            return

        if action == "finish":
            async with db.execute("""
                SELECT h.user_id, SUM(h.damage) AS total_damage, u.full_name
                FROM mp_raid_hits h
                JOIN users u ON u.user_id = h.user_id
                WHERE h.raid_id=?
                GROUP BY h.user_id
                ORDER BY total_damage DESC
            """, (raid_id,)) as cur:
                participants = await cur.fetchall()

            if participants:
                total_damage = sum(max(1, row["total_damage"]) for row in participants)
                for row in participants:
                    share = max(1, int(raid["reward_pool"] * (max(1, row["total_damage"]) / total_damage)))
                    xp_gain = max(20, int(row["total_damage"] / 8))
                    await db.execute(
                        "UPDATE users SET balance=balance+?, total_earned=total_earned+?, xp=xp+? WHERE user_id=?",
                        (share, share, xp_gain, row["user_id"])
                    )
                    await db.execute(
                        "UPDATE mp_squad_members SET contribution = contribution + ? "
                        "WHERE squad_id=? AND user_id=?",
                        (row["total_damage"], raid["squad_id"], row["user_id"])
                    )
            await db.execute("UPDATE mp_raids SET status='completed', boss_hp=0 WHERE raid_id=?", (raid_id,))
            await db.commit()
            await _log_admin(update.effective_user.id, "forceraid_finish", 0, str(raid_id))
            await update.message.reply_text(alert("success", f"Raid #{raid_id} marqué comme terminé."), parse_mode="HTML")
            return

    await update.message.reply_text(alert("error", "Action invalide. Utilise finish ou cancel."), parse_mode="HTML")


@admin_only
async def cmd_squadinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            alert("info", "Usage : <code>/squadinfo id</code>"),
            parse_mode="HTML"
        )
        return

    squad_id = int(context.args[0])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT squad_id, name, leader_id, max_members, created_at FROM mp_squads WHERE squad_id=?",
            (squad_id,)
        ) as cur:
            squad = await cur.fetchone()
        if not squad:
            await update.message.reply_text(alert("error", "Escouade introuvable."), parse_mode="HTML")
            return
        async with db.execute("""
            SELECT m.user_id, m.role, m.contribution, u.full_name
            FROM mp_squad_members m
            JOIN users u ON u.user_id = m.user_id
            WHERE m.squad_id=?
            ORDER BY CASE WHEN m.role='leader' THEN 0 ELSE 1 END, m.contribution DESC
        """, (squad_id,)) as cur:
            members = await cur.fetchall()

    body = [
        f"Nom : <b>{squad['name']}</b>",
        f"Chef ID : <code>{squad['leader_id']}</code>",
        f"Taille max : <b>{squad['max_members']}</b>",
        f"Créée : <b>{fmt_time(max(0, now() - squad['created_at']))}</b> plus tôt",
        "",
        "<b>Membres</b>",
    ]
    for member in members:
        body.append(
            f"<code>{member['user_id']}</code> · <b>{member['full_name']}</b> · "
            f"{member['role']} · contrib {fmt(member['contribution'])}"
        )

    await update.message.reply_text(
        card("🛡️ Inspection escouade", body, icon="🛡️", style="thick"),
        parse_mode="HTML"
    )
