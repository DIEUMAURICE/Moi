"""
handlers/multiplayer.py — LIFESIM ULTRA V2  ◀ NOUVEAU MODULE
═══════════════════════════════════════════════════════════════════════
Système multijoueur complet :
  • /cadeau @user montant  ─ Offrir un cadeau (boost relation)
  • /don @user montant     ─ Charity (boost karma)
  • /echange @user         ─ Échange sécurisé inter-joueur
  • /troc @user offre→demande ─ Troc d'items
  • /salutations @user     ─ Interaction sociale
  • /relations             ─ Vue des relations
  • /classements           ─ Top 10 dans plusieurs catégories
  • /tradeaccept / /tradedecline ─ Réponse aux échanges
"""
import aiosqlite
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import DB_PATH
from database import get_user, update_field
from utils.decorators import require_registered, require_free
from utils.helpers import fmt, parse_amount, now, fmt_money
from utils.aesthetics import card, alert, celebrate, section, SEP_LIGHT


# ═══════════════════════════════════════════════════════════════════
#               INITIALISATION DES TABLES (à appeler 1×)
# ═══════════════════════════════════════════════════════════════════
async def _ensure_column(db, table: str, column_def: str):
    try:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
    except aiosqlite.OperationalError:
        pass


async def init_mp_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        # Échanges en cours
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mp_trades (
            trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER, to_id INTEGER,
            offer_money INTEGER DEFAULT 0,
            request_money INTEGER DEFAULT 0,
            offer_items TEXT DEFAULT '',
            request_items TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at INTEGER, expires_at INTEGER
        )""")
        # Relations sociales
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mp_relations (
            user_id INTEGER, other_id INTEGER,
            score INTEGER DEFAULT 0,
            last_interaction INTEGER DEFAULT 0,
            relation_type TEXT DEFAULT 'connu',
            PRIMARY KEY(user_id, other_id)
        )""")
        # Cadeaux reçus
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mp_gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER, to_id INTEGER,
            amount INTEGER, message TEXT,
            created_at INTEGER
        )""")
        # Escouades coopératives
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mp_squads (
            squad_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            leader_id INTEGER NOT NULL,
            max_members INTEGER DEFAULT 4,
            created_at INTEGER NOT NULL
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mp_squad_members (
            squad_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL UNIQUE,
            role TEXT DEFAULT 'member',
            contribution INTEGER DEFAULT 0,
            joined_at INTEGER NOT NULL,
            PRIMARY KEY(squad_id, user_id)
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mp_squad_invites (
            invite_id INTEGER PRIMARY KEY AUTOINCREMENT,
            squad_id INTEGER NOT NULL,
            from_id INTEGER NOT NULL,
            to_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )""")
        # Raids coop
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mp_raids (
            raid_id INTEGER PRIMARY KEY AUTOINCREMENT,
            squad_id INTEGER NOT NULL,
            boss_name TEXT NOT NULL,
            boss_level INTEGER DEFAULT 1,
            boss_hp INTEGER NOT NULL,
            max_hp INTEGER NOT NULL,
            reward_pool INTEGER DEFAULT 0,
            created_by INTEGER NOT NULL,
            started_at INTEGER NOT NULL,
            ends_at INTEGER NOT NULL,
            status TEXT DEFAULT 'active'
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mp_raid_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raid_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            damage INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )""")
        await _ensure_column(db, "mp_squads", "xp INTEGER DEFAULT 0")
        await _ensure_column(db, "mp_squads", "level INTEGER DEFAULT 1")
        await _ensure_column(db, "mp_squads", "vault INTEGER DEFAULT 0")
        await _ensure_column(db, "mp_squads", "wins INTEGER DEFAULT 0")
        await _ensure_column(db, "mp_squads", "motto TEXT DEFAULT ''")
        await db.commit()


async def process_multiplayer_maintenance():
    current = now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE mp_squad_invites SET status='expired' "
            "WHERE status='pending' AND expires_at < ?",
            (current,)
        )
        await db.execute(
            "UPDATE mp_raids SET status='expired' "
            "WHERE status='active' AND ends_at < ?",
            (current,)
        )
        await db.commit()


# ─── Helpers ──────────────────────────────────────────────────────
async def _resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Résout le joueur cible depuis un reply ou @username/id."""
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
    if not row:
        return None

    class Fake:
        def __init__(self, uid, username, fn):
            self.id = uid; self.username = username; self.full_name = fn
    return Fake(row[0], row[1] or "", row[2] or "Joueur")


async def _bump_relation(user_id: int, other_id: int, delta: int, kind: str = None):
    """Met à jour la relation entre deux joueurs (dans les 2 sens)."""
    if user_id == other_id:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        for a, b in [(user_id, other_id), (other_id, user_id)]:
            await db.execute(
                "INSERT INTO mp_relations(user_id, other_id, score, last_interaction, relation_type) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(user_id, other_id) DO UPDATE SET "
                "score = score + ?, last_interaction = ?, "
                "relation_type = COALESCE(?, relation_type)",
                (a, b, delta, now(), kind or "connu", delta, now(), kind),
            )
        await db.commit()


async def _safe_send(bot, user_id: int, text: str, reply_markup=None):
    try:
        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        pass


def _squad_capacity(squad: dict) -> int:
    level = max(1, int((squad or {}).get("level", 1) or 1))
    base_cap = max(4, int((squad or {}).get("max_members", 4) or 4))
    return max(base_cap, 4 + min(4, level - 1))


async def _get_squad_for_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                s.squad_id, s.name, s.leader_id, s.max_members,
                COALESCE(s.xp, 0) AS xp,
                COALESCE(s.level, 1) AS level,
                COALESCE(s.vault, 0) AS vault,
                COALESCE(s.wins, 0) AS wins,
                COALESCE(s.motto, '') AS motto,
                m.role, m.contribution
            FROM mp_squad_members m
            JOIN mp_squads s ON s.squad_id = m.squad_id
            WHERE m.user_id=?
        """, (user_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def _get_squad_members(squad_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT m.user_id, m.role, m.contribution, m.joined_at, u.full_name
            FROM mp_squad_members m
            JOIN users u ON u.user_id = m.user_id
            WHERE m.squad_id=?
            ORDER BY CASE WHEN m.role='leader' THEN 0 ELSE 1 END, m.contribution DESC, u.full_name ASC
        """, (squad_id,)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _get_squad_synergy(squad_id: int) -> float:
    members = await _get_squad_members(squad_id)
    ids = [m["user_id"] for m in members]
    if len(ids) < 2:
        return 0.0

    placeholders = ",".join("?" for _ in ids)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"""
            SELECT score FROM mp_relations
            WHERE user_id IN ({placeholders})
              AND other_id IN ({placeholders})
              AND user_id != other_id
            """,
            tuple(ids + ids),
        ) as cur:
            rows = await cur.fetchall()

    pair_count = len(ids) * (len(ids) - 1)
    positive_score = sum(max(0, min(row[0], 60)) for row in rows)
    average_score = positive_score / max(1, pair_count)
    return min(0.35, average_score / 180)


async def _grant_squad_progress(db, squad_id: int, xp_gain: int, vault_gain: int = 0, win: bool = False):
    async with db.execute(
        "SELECT name, COALESCE(level, 1), COALESCE(xp, 0), COALESCE(vault, 0), COALESCE(wins, 0) "
        "FROM mp_squads WHERE squad_id=?",
        (squad_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return {"leveled_up": False, "new_level": 0, "vault": 0, "name": "Escouade"}

    name, level, xp, vault, wins = row
    xp += max(0, xp_gain)
    vault += max(0, vault_gain)
    leveled_up = False
    while level < 10 and xp >= level * 250:
        xp -= level * 250
        level += 1
        leveled_up = True

    await db.execute(
        "UPDATE mp_squads SET level=?, xp=?, vault=?, wins=? WHERE squad_id=?",
        (level, xp, vault, wins + (1 if win else 0), squad_id),
    )
    return {"leveled_up": leveled_up, "new_level": level, "vault": vault, "name": name}


async def _get_active_raid_for_squad(squad_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM mp_raids WHERE squad_id=? AND status='active' ORDER BY raid_id DESC LIMIT 1",
            (squad_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def _reward_raid(db, raid: dict):
    async with db.execute("""
        SELECT h.user_id, SUM(h.damage) AS total_damage, u.full_name
        FROM mp_raid_hits h
        JOIN users u ON u.user_id = h.user_id
        WHERE h.raid_id=?
        GROUP BY h.user_id
        ORDER BY total_damage DESC
    """, (raid["raid_id"],)) as cur:
        participants = await cur.fetchall()

    if not participants:
        await db.execute("UPDATE mp_raids SET status='completed' WHERE raid_id=?", (raid["raid_id"],))
        return []

    total_damage = sum(max(1, row[1]) for row in participants)
    rewards = []
    for user_id, damage, full_name in participants:
        share = max(1, int(raid["reward_pool"] * (max(1, damage) / total_damage)))
        xp_gain = max(20, int(damage / 8))
        social_gain = max(2, share // 10000 + damage // 250)
        rewards.append((user_id, full_name, damage, share, xp_gain, social_gain))
        await db.execute(
            "UPDATE users SET balance=balance+?, total_earned=total_earned+?, xp=xp+?, social_coins=COALESCE(social_coins,0)+? WHERE user_id=?",
            (share, share, xp_gain, social_gain, user_id)
        )
        await db.execute(
            "UPDATE mp_squad_members SET contribution = contribution + ? "
            "WHERE squad_id=? AND user_id=?",
            (damage, raid["squad_id"], user_id)
        )
    await db.execute("UPDATE mp_raids SET status='completed', boss_hp=0 WHERE raid_id=?", (raid["raid_id"],))
    return rewards


# ═══════════════════════════════════════════════════════════════════
#                          /cadeau
# ═══════════════════════════════════════════════════════════════════
@require_registered
@require_free
async def cmd_cadeau(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender = update.effective_user
    target = await _resolve_target(update, context)
    if not target:
        await update.message.reply_text(
            alert("info",
                  "Usage : <code>/cadeau @username montant [message]</code>\n"
                  "Ou réponds à un message avec <code>/cadeau montant</code>"),
            parse_mode="HTML")
        return

    args = context.args[1:] if context.args and not update.message.reply_to_message else context.args
    if not args:
        await update.message.reply_text(alert("warning", "Précise un montant."), parse_mode="HTML")
        return

    sender_data = await get_user(sender.id)
    amount = parse_amount(args[0], sender_data["balance"])
    if not amount or amount <= 0:
        await update.message.reply_text(alert("error", "Montant invalide."), parse_mode="HTML")
        return
    if amount > sender_data["balance"]:
        await update.message.reply_text(alert("error", f"Solde insuffisant. Tu as {fmt(sender_data['balance'])}."), parse_mode="HTML")
        return
    if target.id == sender.id:
        await update.message.reply_text(alert("warning", "Tu ne peux pas t'offrir un cadeau à toi-même."), parse_mode="HTML")
        return

    message = " ".join(args[1:]) if len(args) > 1 else "Sans mot doux"

    # Transaction
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ?, total_spent = total_spent + ? WHERE user_id=?",
                         (amount, amount, sender.id))
        await db.execute("UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id=?",
                         (amount, amount, target.id))
        await db.execute(
            "INSERT INTO mp_gifts(from_id, to_id, amount, message, created_at) VALUES(?,?,?,?,?)",
            (sender.id, target.id, amount, message, now()),
        )
        await db.commit()

    # Relations & karma léger
    await _bump_relation(sender.id, target.id, 3, "ami")
    await update_field(sender.id, "karma", sender_data.get("karma", 0) + 2)

    await update.message.reply_text(
        card(
            "🎁 Cadeau envoyé !",
            [
                f"De : <b>{sender.full_name}</b>",
                f"À : <b>{target.full_name}</b>",
                f"💰 Montant : <b>{fmt(amount)}</b>",
                f"💌 Message : <i>{message}</i>",
                "",
                "✨ Relation +3  ·  🌟 Karma +2",
            ],
            icon="🎁", style="stars",
        ),
        parse_mode="HTML",
    )

    # Notif au destinataire
    try:
        await context.bot.send_message(
            target.id,
            card(
                "🎁 Tu as reçu un cadeau !",
                [
                    f"De : <b>{sender.full_name}</b>",
                    f"💰 Montant : <b>{fmt(amount)}</b>",
                    f"💌 « {message} »",
                ],
                icon="🎁", style="stars",
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#                          /salutations
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_salutations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender = update.effective_user
    target = await _resolve_target(update, context)
    if not target:
        await update.message.reply_text(
            alert("info", "Usage : <code>/salutations @user</code>"),
            parse_mode="HTML")
        return
    if target.id == sender.id:
        await update.message.reply_text(alert("warning", "Tu peux pas te saluer toi-même 🤔"), parse_mode="HTML")
        return

    await _bump_relation(sender.id, target.id, 1, "connu")
    await update.message.reply_text(
        card(
            "👋 Salutations !",
            [
                f"<b>{sender.full_name}</b> salue <b>{target.full_name}</b>.",
                "",
                "✨ Relation +1",
                "<i>Les liens se tissent lentement, mais ils tiennent longtemps.</i>",
            ],
            icon="👋", style="round",
        ),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════
#                          /echange
# ═══════════════════════════════════════════════════════════════════
@require_registered
@require_free
async def cmd_echange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /echange @user montant_offert montant_demande
    Crée une proposition d'échange sécurisée.
    """
    sender = update.effective_user
    if not context.args or len(context.args) < 3:
        await update.message.reply_text(
            card(
                "Échange sécurisé",
                [
                    "Propose un échange d'argent à un autre joueur.",
                    "",
                    "<b>Usage :</b>",
                    "<code>/echange @user montant_offert montant_demande</code>",
                    "",
                    "<b>Exemple :</b>",
                    "<code>/echange @bob 10000 8500</code>",
                    "→ tu offres 10K et tu demandes 8.5K en retour",
                    "",
                    "<i>Cas d'usage : conversion de devises informelles, dette, etc.</i>",
                ],
                icon="🤝", style="thick",
            ),
            parse_mode="HTML",
        )
        return

    target = await _resolve_target(update, context)
    if not target or target.id == sender.id:
        await update.message.reply_text(alert("error", "Cible invalide."), parse_mode="HTML")
        return

    sender_data = await get_user(sender.id)
    offer = parse_amount(context.args[1], sender_data["balance"])
    request = parse_amount(context.args[2])
    if not offer or not request or offer <= 0 or request <= 0:
        await update.message.reply_text(alert("error", "Montants invalides."), parse_mode="HTML")
        return
    if offer > sender_data["balance"]:
        await update.message.reply_text(alert("error", "Tu n'as pas assez pour offrir cela."), parse_mode="HTML")
        return

    # Bloque le montant offert
    await update_field(sender.id, "balance", sender_data["balance"] - offer)

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO mp_trades(from_id, to_id, offer_money, request_money, created_at, expires_at) "
            "VALUES(?,?,?,?,?,?)",
            (sender.id, target.id, offer, request, now(), now() + 3600),
        )
        trade_id = cur.lastrowid
        await db.commit()

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Accepter (-{fmt(request)})", callback_data=f"trade_accept_{trade_id}"),
        InlineKeyboardButton("❌ Refuser", callback_data=f"trade_decline_{trade_id}"),
    ]])

    await update.message.reply_text(
        card(
            "🤝 Proposition d'échange",
            [
                f"De : <b>{sender.full_name}</b>",
                f"À : <b>{target.full_name}</b>",
                "",
                f"💰 Offre : <b>{fmt(offer)}</b>",
                f"💸 Demande : <b>{fmt(request)}</b>",
                "",
                f"⏳ Expire dans 1h.",
                f"🔒 Le montant offert est bloqué côté offrant.",
            ],
            icon="🤝", style="thick",
        ),
        parse_mode="HTML",
    )

    try:
        await context.bot.send_message(
            target.id,
            card(
                "🔔 Nouvelle proposition d'échange",
                [
                    f"De : <b>{sender.full_name}</b>",
                    f"💰 Tu reçois : <b>{fmt(offer)}</b>",
                    f"💸 Tu paies : <b>{fmt(request)}</b>",
                    "",
                    "Réponds dans l'heure :",
                ],
                icon="🤝", style="thick",
            ),
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#                          Callback échange
# ═══════════════════════════════════════════════════════════════════
async def trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("_")
    action = parts[1]
    trade_id = int(parts[2])
    user_id = q.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mp_trades WHERE trade_id=?", (trade_id,)) as cur:
            trade = await cur.fetchone()

        if not trade:
            await q.edit_message_text("❌ Échange introuvable.")
            return
        if trade["status"] != "pending":
            await q.edit_message_text(f"⚠️ Échange déjà traité ({trade['status']}).")
            return
        if trade["to_id"] != user_id:
            await q.answer("⛔ Tu n'es pas le destinataire.", show_alert=True)
            return
        if now() > trade["expires_at"]:
            # Rembourse l'offrant
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?",
                             (trade["offer_money"], trade["from_id"]))
            await db.execute("UPDATE mp_trades SET status='expired' WHERE trade_id=?", (trade_id,))
            await db.commit()
            await q.edit_message_text("⏰ Échange expiré.")
            return

        if action == "decline":
            # Rembourse l'offrant
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?",
                             (trade["offer_money"], trade["from_id"]))
            await db.execute("UPDATE mp_trades SET status='declined' WHERE trade_id=?", (trade_id,))
            await db.commit()
            await q.edit_message_text("❌ Échange refusé.")
            try:
                await context.bot.send_message(trade["from_id"], "❌ Ta proposition d'échange a été refusée.")
            except Exception:
                pass
            return

        # accept
        # Vérifie solde du destinataire
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)) as cur:
            bal = (await cur.fetchone())[0]
        if bal < trade["request_money"]:
            # Rembourse offrant
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?",
                             (trade["offer_money"], trade["from_id"]))
            await db.execute("UPDATE mp_trades SET status='failed' WHERE trade_id=?", (trade_id,))
            await db.commit()
            await q.edit_message_text(f"❌ Tu n'as pas {fmt(trade['request_money'])} pour finaliser.")
            return

        # Transfer
        await db.execute("UPDATE users SET balance = balance - ?, total_spent = total_spent + ? WHERE user_id=?",
                         (trade["request_money"], trade["request_money"], user_id))
        await db.execute("UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id=?",
                         (trade["request_money"], trade["request_money"], trade["from_id"]))
        # Offerant garde l'argent débité, destinataire reçoit l'offre
        await db.execute("UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id=?",
                         (trade["offer_money"], trade["offer_money"], user_id))
        await db.execute("UPDATE mp_trades SET status='accepted' WHERE trade_id=?", (trade_id,))
        await db.commit()

    await _bump_relation(trade["from_id"], user_id, 2, "partenaire")
    await q.edit_message_text(
        card(
            "✅ Échange accepté !",
            [
                f"💰 Reçu : <b>{fmt(trade['offer_money'])}</b>",
                f"💸 Payé : <b>{fmt(trade['request_money'])}</b>",
                "✨ Relation +2",
            ],
            icon="🤝", style="stars",
        ),
        parse_mode="HTML",
    )
    try:
        await context.bot.send_message(
            trade["from_id"],
            card("✅ Ton échange a été accepté !",
                 [f"💸 Tu as donné : {fmt(trade['offer_money'])}",
                  f"💰 Tu as reçu : {fmt(trade['request_money'])}"],
                 icon="🤝", style="stars"),
            parse_mode="HTML",
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#                          /relations
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_relations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT r.other_id, r.score, r.relation_type, u.full_name "
            "FROM mp_relations r JOIN users u ON u.user_id = r.other_id "
            "WHERE r.user_id=? ORDER BY r.score DESC LIMIT 15",
            (user.id,)
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text(
            alert("info", "Tu n'as encore aucune relation enregistrée.\nInteragis avec d'autres joueurs !"),
            parse_mode="HTML")
        return

    body = []
    for r in rows:
        score = r["score"]
        if score >= 50:  emoji = "💖"
        elif score >= 20: emoji = "💚"
        elif score >= 0:  emoji = "⚪"
        elif score >= -20: emoji = "💔"
        else: emoji = "🖤"
        body.append(f"{emoji} <b>{r['full_name']}</b> ─ {score:+d}  <i>({r['relation_type']})</i>")

    await update.message.reply_text(
        card("Mes relations", body,
             icon="💞", style="thick",
             footer="Cadeaux et interactions augmentent les liens."),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════
#                          /classements
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_classements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Top 10 selon plusieurs catégories."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Top richesse
        async with db.execute(
            "SELECT full_name, balance FROM users WHERE registered=1 AND banned=0 "
            "ORDER BY balance DESC LIMIT 5") as cur:
            top_rich = await cur.fetchall()
        async with db.execute(
            "SELECT full_name, xp FROM users WHERE registered=1 AND banned=0 "
            "ORDER BY xp DESC LIMIT 5") as cur:
            top_xp = await cur.fetchall()
        async with db.execute(
            "SELECT full_name, karma FROM users WHERE registered=1 AND banned=0 "
            "ORDER BY karma DESC LIMIT 5") as cur:
            top_karma = await cur.fetchall()
        async with db.execute(
            "SELECT full_name, prestige FROM users WHERE registered=1 AND banned=0 "
            "ORDER BY prestige DESC LIMIT 5") as cur:
            top_prestige = await cur.fetchall()
        async with db.execute(
            "SELECT name, COALESCE(level, 1) AS level, COALESCE(wins, 0) AS wins, COALESCE(vault, 0) AS vault "
            "FROM mp_squads ORDER BY level DESC, wins DESC, vault DESC, name ASC LIMIT 5"
        ) as cur:
            top_squads = await cur.fetchall()
        async with db.execute(
            "SELECT name, level, COALESCE(war_score, 0) AS war_score, treasury "
            "FROM guilds ORDER BY level DESC, war_score DESC, treasury DESC LIMIT 5"
        ) as cur:
            top_guilds = await cur.fetchall()

    def fmt_top(rows, key, val_fmt=fmt):
        if not rows:
            return ["<i>(vide)</i>"]
        return [f"  {i+1}. <b>{r['full_name']}</b> ─ {val_fmt(r[key])}"
                for i, r in enumerate(rows)]

    def fmt_squads(rows):
        if not rows:
            return ["<i>(vide)</i>"]
        return [
            f"  {i+1}. <b>{r['name']}</b> ─ niv.{r['level']} · {r['wins']} victoires · coffre {fmt(r['vault'])}"
            for i, r in enumerate(rows)
        ]

    def fmt_guilds(rows):
        if not rows:
            return ["<i>(vide)</i>"]
        return [
            f"  {i+1}. <b>{r['name']}</b> ─ niv.{r['level']} · score guerre {r['war_score']} · {fmt(r['treasury'])}"
            for i, r in enumerate(rows)
        ]

    body = (
        ["<b>💰 PLUS RICHES</b>"] + fmt_top(top_rich, "balance") +
        ["", "<b>⭐ PLUS HAUT NIVEAU</b>"] + fmt_top(top_xp, "xp", lambda x: f"{x} XP") +
        ["", "<b>😇 PLUS HAUT KARMA</b>"] + fmt_top(top_karma, "karma", lambda x: f"{x:+d}") +
        ["", "<b>👑 PLUS DE PRESTIGE</b>"] + fmt_top(top_prestige, "prestige", str) +
        ["", "<b>🛡️ ESCOUADES STAR</b>"] + fmt_squads(top_squads) +
        ["", "<b>🏰 GUILDES DOMINANTES</b>"] + fmt_guilds(top_guilds)
    )

    await update.message.reply_text(
        card("🏆 CLASSEMENTS MONDIAUX", body,
             icon="🏆", style="thick",
             footer="Mis à jour en temps réel."),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════
#                     Escouades coopératives
# ═══════════════════════════════════════════════════════════════════
@require_registered
@require_free
async def cmd_creerescouade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            alert("info", "Usage : <code>/creerescouade Nom de l'escouade</code>"),
            parse_mode="HTML"
        )
        return

    existing = await _get_squad_for_user(user.id)
    if existing:
        await update.message.reply_text(alert("warning", "Tu es déjà dans une escouade."), parse_mode="HTML")
        return

    name = " ".join(context.args).strip()[:32]
    if len(name) < 3:
        await update.message.reply_text(alert("error", "Le nom doit contenir au moins 3 caractères."), parse_mode="HTML")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cur = await db.execute(
                "INSERT INTO mp_squads(name, leader_id, created_at) VALUES(?,?,?)",
                (name, user.id, now())
            )
            squad_id = cur.lastrowid
            await db.execute(
                "INSERT INTO mp_squad_members(squad_id, user_id, role, joined_at) VALUES(?,?,?,?)",
                (squad_id, user.id, "leader", now())
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            await update.message.reply_text(alert("error", "Ce nom d'escouade est déjà pris."), parse_mode="HTML")
            return

    await update.message.reply_text(
        card(
            "🛡️ Escouade créée",
            [
                f"Nom : <b>{name}</b>",
                f"Chef : <b>{user.full_name}</b>",
                "Niveau : <b>1</b>",
                f"Capacité : <b>{_squad_capacity({'level': 1, 'max_members': 4})} joueurs</b>",
                "",
                "Commandes utiles :",
                "<code>/inviterescouade @joueur</code>",
                "<code>/escouade</code>",
                "<code>/raid lancer [mise]</code>",
                "<code>/quitterescouade</code>",
            ],
            icon="🛡️", style="thick"
        ),
        parse_mode="HTML"
    )


@require_registered
@require_free
async def cmd_escouade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    squad = await _get_squad_for_user(user.id)
    if not squad:
        await update.message.reply_text(
            card(
                "🛡️ Escouades",
                [
                    "Tu n'es dans aucune escouade.",
                    "",
                    "<code>/creerescouade Nom</code>",
                    "<code>/inviterescouade @joueur</code> après création",
                    "<code>/raid lancer 25000</code> pour ouvrir un raid coop",
                ],
                icon="🛡️", style="round"
            ),
            parse_mode="HTML"
        )
        return

    members = await _get_squad_members(squad["squad_id"])
    leader_name = next((m["full_name"] for m in members if m["user_id"] == squad["leader_id"]), f"#{squad['leader_id']}")
    synergy = await _get_squad_synergy(squad["squad_id"])
    capacity = _squad_capacity(squad)
    xp_needed = max(250, squad["level"] * 250)

    raid = await _get_active_raid_for_squad(squad["squad_id"])
    body = [
        f"Nom : <b>{squad['name']}</b>",
        f"Chef : <b>{leader_name}</b>",
        f"Niveau : <b>{squad['level']}</b> · XP : <b>{fmt(squad['xp'])}/{fmt(xp_needed)}</b>",
        f"Coffre d'escouade : <b>{fmt(squad['vault'])}</b>",
        f"Victoires de raid : <b>{squad['wins']}</b>",
        f"Synergie : <b>+{int(synergy * 100)}%</b>",
        f"Membres : <b>{len(members)}/{capacity}</b>",
        ""
    ]
    if squad.get("motto"):
        body.extend([f"Devise : <i>{squad['motto']}</i>", ""])

    for row in members:
        role = "👑 Chef" if row["role"] == "leader" else "⚔️ Membre"
        body.append(f"{role} <b>{row['full_name']}</b> · contribution <b>{fmt(row['contribution'])}</b>")

    if raid:
        body += [
            "",
            "<b>RAID ACTIF</b>",
            f"Boss : <b>{raid['boss_name']}</b> niv.{raid['boss_level']}",
            f"PV : <b>{fmt(raid['boss_hp'])}</b> / {fmt(raid['max_hp'])}",
            f"Récompense : <b>{fmt(raid['reward_pool'])}</b>",
            f"Bonus d'escouade : <b>+{int((synergy + squad['level'] * 0.03) * 100)}%</b>",
            "Commande : <code>/raid attaquer</code>",
            "<code>/quitterescouade</code>",
        ]

    await update.message.reply_text(
        card("🛡️ Mon escouade", body, icon="🛡️", style="thick"),
        parse_mode="HTML"
    )


@require_registered
@require_free
async def cmd_inviterescouade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    squad = await _get_squad_for_user(user.id)
    if not squad or squad["leader_id"] != user.id:
        await update.message.reply_text(alert("error", "Seul le chef d'escouade peut inviter."), parse_mode="HTML")
        return

    target = await _resolve_target(update, context)
    if not target or target.id == user.id:
        await update.message.reply_text(alert("error", "Cible invalide."), parse_mode="HTML")
        return

    target_squad = await _get_squad_for_user(target.id)
    if target_squad:
        await update.message.reply_text(alert("warning", "Ce joueur est déjà dans une escouade."), parse_mode="HTML")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM mp_squad_members WHERE squad_id=?", (squad["squad_id"],)) as cur:
            count = (await cur.fetchone())[0]
        if count >= _squad_capacity(squad):
            await update.message.reply_text(alert("warning", "L'escouade est déjà pleine."), parse_mode="HTML")
            return

        await db.execute(
            "UPDATE mp_squad_invites SET status='expired' WHERE to_id=? AND status='pending' AND expires_at < ?",
            (target.id, now())
        )
        cur = await db.execute(
            "INSERT INTO mp_squad_invites(squad_id, from_id, to_id, created_at, expires_at) VALUES(?,?,?,?,?)",
            (squad["squad_id"], user.id, target.id, now(), now() + 1800)
        )
        invite_id = cur.lastrowid
        await db.commit()

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Rejoindre", callback_data=f"squad_accept_{invite_id}"),
        InlineKeyboardButton("❌ Refuser", callback_data=f"squad_decline_{invite_id}"),
    ]])
    await update.message.reply_text(
        alert("success", f"Invitation envoyée à <b>{target.full_name}</b>."), parse_mode="HTML"
    )
    await _safe_send(
        context.bot,
        target.id,
        card(
            "🛡️ Invitation d'escouade",
            [
                f"Escouade : <b>{squad['name']}</b>",
                f"Chef : <b>{user.full_name}</b>",
                "Invitation valable 30 minutes.",
            ],
            icon="🛡️", style="thick"
        ),
        reply_markup=kb
    )


async def squad_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("_")
    action = parts[1]
    invite_id = int(parts[2])
    user_id = q.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mp_squad_invites WHERE invite_id=?", (invite_id,)) as cur:
            invite = await cur.fetchone()
        if not invite:
            await q.edit_message_text("❌ Invitation introuvable.")
            return
        if invite["to_id"] != user_id:
            await q.answer("⛔ Cette invitation n'est pas pour toi.", show_alert=True)
            return
        if invite["status"] != "pending":
            await q.edit_message_text(f"⚠️ Invitation déjà traitée ({invite['status']}).")
            return
        if now() > invite["expires_at"]:
            await db.execute("UPDATE mp_squad_invites SET status='expired' WHERE invite_id=?", (invite_id,))
            await db.commit()
            await q.edit_message_text("⏰ Invitation expirée.")
            return

        async with db.execute("SELECT squad_id, name, leader_id, max_members FROM mp_squads WHERE squad_id=?", (invite["squad_id"],)) as cur:
            squad = await cur.fetchone()
        if not squad:
            await db.execute("UPDATE mp_squad_invites SET status='cancelled' WHERE invite_id=?", (invite_id,))
            await db.commit()
            await q.edit_message_text("❌ Cette escouade n'existe plus.")
            return

        async with db.execute("SELECT 1 FROM mp_squad_members WHERE user_id=?", (user_id,)) as cur:
            already = await cur.fetchone()
        if already:
            await db.execute("UPDATE mp_squad_invites SET status='cancelled' WHERE invite_id=?", (invite_id,))
            await db.commit()
            await q.edit_message_text("⚠️ Tu es déjà dans une escouade.")
            return

        if action == "decline":
            await db.execute("UPDATE mp_squad_invites SET status='declined' WHERE invite_id=?", (invite_id,))
            await db.commit()
            await q.edit_message_text("❌ Invitation refusée.")
            await _safe_send(context.bot, invite["from_id"], "❌ Ton invitation d'escouade a été refusée.")
            return

        async with db.execute("SELECT COUNT(*) FROM mp_squad_members WHERE squad_id=?", (squad["squad_id"],)) as cur:
            members = (await cur.fetchone())[0]
        if members >= _squad_capacity(dict(squad)):
            await db.execute("UPDATE mp_squad_invites SET status='cancelled' WHERE invite_id=?", (invite_id,))
            await db.commit()
            await q.edit_message_text("⚠️ L'escouade est déjà pleine.")
            return

        await db.execute(
            "INSERT INTO mp_squad_members(squad_id, user_id, role, joined_at) VALUES(?,?,?,?)",
            (squad["squad_id"], user_id, "member", now())
        )
        await db.execute("UPDATE mp_squad_invites SET status='accepted' WHERE invite_id=?", (invite_id,))
        await db.commit()

    await _bump_relation(invite["from_id"], user_id, 4, "allié")
    await q.edit_message_text(f"✅ Tu as rejoint l'escouade {squad['name']}.")
    await _safe_send(
        context.bot,
        invite["from_id"],
        f"✅ <b>{q.from_user.full_name}</b> a rejoint ton escouade <b>{squad['name']}</b>."
    )


@require_registered
@require_free
async def cmd_quitterescouade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    squad = await _get_squad_for_user(user.id)
    if not squad:
        await update.message.reply_text(alert("warning", "Tu n'es dans aucune escouade."), parse_mode="HTML")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, role FROM mp_squad_members WHERE squad_id=?", (squad["squad_id"],)) as cur:
            members = await cur.fetchall()
        if squad["leader_id"] == user.id and len(members) > 1:
            successor = next((m[0] for m in members if m[0] != user.id), None)
            await db.execute("UPDATE mp_squads SET leader_id=? WHERE squad_id=?", (successor, squad["squad_id"]))
            await db.execute("UPDATE mp_squad_members SET role='leader' WHERE squad_id=? AND user_id=?", (squad["squad_id"], successor))
        await db.execute("DELETE FROM mp_squad_members WHERE squad_id=? AND user_id=?", (squad["squad_id"], user.id))
        await db.execute("DELETE FROM mp_squad_invites WHERE squad_id=? AND (from_id=? OR to_id=?)", (squad["squad_id"], user.id, user.id))
        async with db.execute("SELECT COUNT(*) FROM mp_squad_members WHERE squad_id=?", (squad["squad_id"],)) as cur:
            remain = (await cur.fetchone())[0]
        if remain == 0:
            await db.execute("UPDATE mp_raids SET status='cancelled' WHERE squad_id=? AND status='active'", (squad["squad_id"],))
            await db.execute("DELETE FROM mp_squads WHERE squad_id=?", (squad["squad_id"],))
        await db.commit()

    await update.message.reply_text(alert("success", "Tu as quitté l'escouade."), parse_mode="HTML")


@require_registered
@require_free
async def cmd_chatescouade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    squad = await _get_squad_for_user(user.id)
    if not squad:
        await update.message.reply_text(alert("warning", "Tu n'es dans aucune escouade."), parse_mode="HTML")
        return
    if not context.args:
        await update.message.reply_text(
            alert("info", "Usage : <code>/chatescouade message</code>"),
            parse_mode="HTML"
        )
        return

    message = " ".join(context.args).strip()[:300]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM mp_squad_members WHERE squad_id=? AND user_id!=?", (squad["squad_id"], user.id)) as cur:
            targets = [row[0] for row in await cur.fetchall()]
        await db.execute(
            "UPDATE mp_squad_members SET contribution = contribution + 3 WHERE squad_id=? AND user_id=?",
            (squad["squad_id"], user.id)
        )
        await db.commit()

    delivered = 0
    for target_id in targets:
        await _safe_send(
            context.bot,
            target_id,
            card(
                "💬 Canal escouade",
                [
                    f"Escouade : <b>{squad['name']}</b>",
                    f"De : <b>{user.full_name}</b>",
                    f"Message : {message}",
                ],
                icon="💬", style="round"
            )
        )
        delivered += 1

    await update.message.reply_text(
        alert("success", f"Message envoyé à {delivered} équipier(s)."),
        parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════════════════════
#                           Raids coop
# ═══════════════════════════════════════════════════════════════════
@require_registered
@require_free
async def cmd_raid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    squad = await _get_squad_for_user(user.id)
    if not squad:
        await update.message.reply_text(
            alert("warning", "Tu dois être dans une escouade pour lancer un raid."),
            parse_mode="HTML"
        )
        return

    raid = await _get_active_raid_for_squad(squad["squad_id"])
    if not context.args:
        if raid:
            await update.message.reply_text(
                card(
                    "🐉 Raid actif",
                    [
                        f"Escouade : <b>{squad['name']}</b>",
                        f"Boss : <b>{raid['boss_name']}</b> niv.{raid['boss_level']}",
                        f"PV : <b>{fmt(raid['boss_hp'])}</b> / {fmt(raid['max_hp'])}",
                        f"Récompense : <b>{fmt(raid['reward_pool'])}</b>",
                        "",
                        "<code>/raid attaquer</code> pour infliger des dégâts",
                    ],
                    icon="🐉", style="thick"
                ),
                parse_mode="HTML"
            )
            return
        await update.message.reply_text(
            card(
                "🐉 Raids coop",
                [
                    "<code>/raid lancer [mise]</code> pour créer un boss d'escouade",
                    "<code>/raid attaquer</code> pour frapper le boss",
                    "",
                    "Plus l'escouade est forte, plus le boss et le jackpot grossissent.",
                ],
                icon="🐉", style="round"
            ),
            parse_mode="HTML"
        )
        return

    action = context.args[0].lower()
    if action in ("lancer", "start", "create"):
        if squad["leader_id"] != user.id:
            await update.message.reply_text(alert("error", "Seul le chef peut lancer un raid."), parse_mode="HTML")
            return
        if raid:
            await update.message.reply_text(alert("warning", "Un raid est déjà actif pour ton escouade."), parse_mode="HTML")
            return

        u = await get_user(user.id)
        stake = parse_amount(context.args[1], u["balance"]) if len(context.args) > 1 else min(25_000, u["balance"])
        if not stake or stake <= 0 or stake > u["balance"]:
            await update.message.reply_text(alert("error", "Mise de raid invalide."), parse_mode="HTML")
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT COUNT(*) AS count, COALESCE(SUM(u.level + u.job_level + u.defense_level), 0) AS power
                FROM mp_squad_members m
                JOIN users u ON u.user_id = m.user_id
                WHERE m.squad_id=?
            """, (squad["squad_id"],)) as cur:
                row = await cur.fetchone()
            members = row[0]
            power = row[1] or 1
            synergy = await _get_squad_synergy(squad["squad_id"])
            boss_level = max(1, int(power // max(1, members) + squad["level"] * 0.8))
            max_hp = int(180 + power * (24 + squad["level"] * 2) + members * 90 + synergy * 500)
            boss_name = random.choice([
                "Hydre du Néon", "Titan des Ombres", "Roi Cyber-Loup", "Dragon des Ruines", "Colosse Quantique"
            ])
            reward_pool = int(stake * 2 + power * 60 + squad["vault"] * 0.15 + synergy * 25000)
            await db.execute(
                "UPDATE users SET balance=balance-?, total_spent=total_spent+? WHERE user_id=?",
                (stake, stake, user.id)
            )
            await db.execute("""
                INSERT INTO mp_raids(
                    squad_id, boss_name, boss_level, boss_hp, max_hp,
                    reward_pool, created_by, started_at, ends_at, status
                ) VALUES(?,?,?,?,?,?,?,?,?, 'active')
            """, (squad["squad_id"], boss_name, boss_level, max_hp, max_hp, reward_pool, user.id, now(), now() + 7200))
            await db.commit()

        await update.message.reply_text(
            card(
                "🐉 Raid lancé",
                [
                    f"Escouade : <b>{squad['name']}</b>",
                    f"Boss : <b>{boss_name}</b> niv.{boss_level}",
                    f"PV : <b>{fmt(max_hp)}</b>",
                    f"Jackpot : <b>{fmt(reward_pool)}</b>",
                    f"Synergie d'escouade : <b>+{int(synergy * 100)}%</b>",
                    f"Niveau d'escouade : <b>{squad['level']}</b>",
                    "",
                    "Tous les membres peuvent maintenant utiliser <code>/raid attaquer</code>.",
                ],
                icon="🐉", style="thick"
            ),
            parse_mode="HTML"
        )
        return

    if action in ("attaquer", "attack", "hit"):
        if not raid:
            await update.message.reply_text(alert("warning", "Aucun raid actif pour ton escouade."), parse_mode="HTML")
            return

        u = await get_user(user.id)
        if u.get("energy", 0) < 10:
            await update.message.reply_text(alert("warning", "Il te faut au moins 10 énergie pour attaquer."), parse_mode="HTML")
            return

        synergy = await _get_squad_synergy(squad["squad_id"])
        base_damage = max(10, int(
            random.randint(8, 20)
            + u.get("level", 1) * 3
            + u.get("job_level", 1) * 2
            + u.get("defense_level", 0) * 2
            + u.get("prestige", 0) * 0.15
        ))

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM mp_raids WHERE raid_id=?", (raid["raid_id"],)) as cur:
                live_raid = await cur.fetchone()
            if not live_raid or live_raid["status"] != "active":
                await update.message.reply_text(alert("warning", "Le raid n'est plus actif."), parse_mode="HTML")
                return

            async with db.execute(
                "SELECT COUNT(*), COALESCE(SUM(damage), 0) FROM mp_raid_hits WHERE raid_id=? AND user_id=?",
                (raid["raid_id"], user.id)
            ) as cur:
                previous_hits, previous_damage = await cur.fetchone()

            combo_multiplier = 1 + min(0.30, previous_hits * 0.05)
            squad_multiplier = 1 + synergy + squad["level"] * 0.03
            enrage_multiplier = 1.15 if live_raid["boss_hp"] <= live_raid["max_hp"] * 0.4 else 1.0
            critical = random.random() < min(0.35, 0.08 + squad["level"] * 0.02 + synergy * 0.45)
            critical_multiplier = 1.75 if critical else 1.0
            damage = max(10, int(base_damage * combo_multiplier * squad_multiplier * enrage_multiplier * critical_multiplier))

            new_hp = max(0, live_raid["boss_hp"] - damage)
            await db.execute("UPDATE users SET energy = MAX(0, energy - 10) WHERE user_id=?", (user.id,))
            await db.execute("UPDATE mp_raids SET boss_hp=? WHERE raid_id=?", (new_hp, raid["raid_id"]))
            await db.execute(
                "INSERT INTO mp_raid_hits(raid_id, user_id, damage, created_at) VALUES(?,?,?,?)",
                (raid["raid_id"], user.id, damage, now())
            )
            await db.execute(
                "UPDATE mp_squad_members SET contribution = contribution + ? WHERE squad_id=? AND user_id=?",
                (damage, squad["squad_id"], user.id)
            )

            rewards = []
            if new_hp <= 0:
                rewards = await _reward_raid(db, dict(live_raid))
                squad_progress = await _grant_squad_progress(
                    db,
                    squad["squad_id"],
                    xp_gain=max(60, live_raid["boss_level"] * 65),
                    vault_gain=max(10_000, live_raid["reward_pool"] // 5),
                    win=True,
                )
            else:
                squad_progress = await _grant_squad_progress(
                    db,
                    squad["squad_id"],
                    xp_gain=max(4, damage // 14),
                    vault_gain=max(0, damage // 10),
                    win=False,
                )
            await db.commit()

        if rewards:
            top_lines = []
            for idx, (_, full_name, hit_damage, money, xp_gain, social_gain) in enumerate(rewards[:5], start=1):
                top_lines.append(
                    f"{idx}. <b>{full_name}</b> · {fmt(hit_damage)} dmg · {fmt(money)} · {xp_gain} XP · {social_gain} 💎"
                )
            await update.message.reply_text(
                card(
                    "🏆 Boss éliminé",
                    [
                        f"Boss : <b>{raid['boss_name']}</b>",
                        f"Coup final de <b>{user.full_name}</b> : <b>{fmt(damage)}</b>",
                        f"Coffre d'escouade : <b>{fmt(squad_progress['vault'])}</b>",
                        "",
                        "<b>Répartition</b>",
                        *top_lines,
                        *(["", f"🛡️ Escouade niveau {squad_progress['new_level']} !"] if squad_progress["leveled_up"] else []),
                    ],
                    icon="🏆", style="stars"
                ),
                parse_mode="HTML"
            )
            for member_id, full_name, hit_damage, money, xp_gain, social_gain in rewards:
                await _safe_send(
                    context.bot,
                    member_id,
                    card(
                        "🎁 Récompense de raid",
                        [
                            f"Boss vaincu : <b>{raid['boss_name']}</b>",
                            f"Dégâts perso : <b>{fmt(hit_damage)}</b>",
                            f"💰 Gain : <b>{fmt(money)}</b>",
                            f"⭐ XP : <b>{xp_gain}</b>",
                            f"💎 SocialCoins : <b>{social_gain}</b>",
                        ],
                        icon="🎁", style="stars"
                    )
                )
            return

        modifiers = [f"Combo x{combo_multiplier:.2f}", f"Synergie x{squad_multiplier:.2f}"]
        if enrage_multiplier > 1:
            modifiers.append("Boss enragé")
        if critical:
            modifiers.append("Coup critique")

        await update.message.reply_text(
            card(
                "⚔️ Attaque de raid",
                [
                    f"Boss : <b>{raid['boss_name']}</b>",
                    f"Ton attaque inflige <b>{fmt(damage)}</b> dégâts.",
                    f"PV restants : <b>{fmt(max(0, raid['boss_hp'] - damage))}</b>",
                    "Coût : <b>10 énergie</b>",
                    f"Bonus : <b>{' · '.join(modifiers)}</b>",
                    f"Coffre d'escouade : <b>{fmt(squad_progress['vault'])}</b>",
                    *( [f"🛡️ Escouade niveau {squad_progress['new_level']} !"] if squad_progress["leveled_up"] else [] ),
                ],
                icon="⚔️", style="round"
            ),
            parse_mode="HTML"
        )
        return

    await update.message.reply_text(
        alert("info", "Usage : <code>/raid</code>, <code>/raid lancer [mise]</code>, <code>/raid attaquer</code>"),
        parse_mode="HTML"
    )
