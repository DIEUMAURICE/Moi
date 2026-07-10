# handlers/guilds.py
import random
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, update_field, increment_field, now, add_notification
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, fmt_time, parse_amount

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────
GUILD_CREATION_COST = 200_000
GUILD_LEVEL_BONUSES = {
    1: {"donation_bonus": 1.0, "xp_bonus": 1.0, "members_cap": 50},
    2: {"donation_bonus": 1.05, "xp_bonus": 1.05, "members_cap": 55},
    3: {"donation_bonus": 1.10, "xp_bonus": 1.10, "members_cap": 60},
    4: {"donation_bonus": 1.15, "xp_bonus": 1.15, "members_cap": 65},
    5: {"donation_bonus": 1.20, "xp_bonus": 1.20, "members_cap": 70},
}
GUILD_QUESTS = {
    "collect_donations": {
        "name": "Collecte de fonds",
        "desc": "Les membres doivent donner un total de X coins à la caisse de la guilde.",
        "target": 1_000_000,
        "reward_exp": 500,
        "reward_prestige": 10,
        "reward_guild_exp": 100,
        "duration": 86400,
        "type": "donation"
    },
    "win_arena": {
        "name": "Domination de l'arène",
        "desc": "Les membres doivent gagner un total de X combats en arène.",
        "target": 20,
        "reward_exp": 300,
        "reward_prestige": 5,
        "reward_guild_exp": 75,
        "duration": 604800,
        "type": "arena_win"
    },
    "crime_spree": {
        "name": "Virée criminelle",
        "desc": "Les membres doivent réussir X crimes (collectif).",
        "target": 50,
        "reward_exp": 400,
        "reward_prestige": 8,
        "reward_guild_exp": 120,
        "duration": 604800,
        "type": "crime_success"
    },
    "travel_bonanza": {
        "name": "Voyages collectifs",
        "desc": "Les membres doivent effectuer X voyages.",
        "target": 30,
        "reward_exp": 350,
        "reward_prestige": 6,
        "reward_guild_exp": 90,
        "duration": 604800,
        "type": "travel"
    },
    "social_clout": {
        "name": "Influence sociale",
        "desc": "Les membres doivent gagner X followers sur les réseaux sociaux.",
        "target": 500_000,
        "reward_exp": 450,
        "reward_prestige": 12,
        "reward_guild_exp": 150,
        "duration": 86400,
        "type": "social_followers"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
async def get_user_guild(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT g.*, gm.role, gm.joined_at as member_since
            FROM guild_members gm
            JOIN guilds g ON g.guild_id = gm.guild_id
            WHERE gm.user_id = ?
        """, (user_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

async def get_guild_by_name(name: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guilds WHERE LOWER(name) = LOWER(?)", (name,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

async def get_guild_by_id(guild_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guilds WHERE guild_id = ?", (guild_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

async def get_active_guild_war(guild_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_wars WHERE status = 'active' AND (guild_a = ? OR guild_b = ?) ORDER BY war_id DESC LIMIT 1",
            (guild_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

async def get_guild_members(guild_id: int) -> list:
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.user_id, u.full_name, u.balance, u.xp, gm.role, gm.joined_at
            FROM guild_members gm
            JOIN users u ON u.user_id = gm.user_id
            WHERE gm.guild_id = ?
            ORDER BY 
                CASE gm.role
                    WHEN 'Chef' THEN 1
                    WHEN 'Officier' THEN 2
                    ELSE 3
                END,
                u.xp DESC
        """, (guild_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def update_guild_field(guild_id: int, field: str, value):
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute(f"UPDATE guilds SET {field} = ? WHERE guild_id = ?", (value, guild_id))
        await db.commit()

async def add_guild_xp(guild_id: int, amount: int):
    g = await get_guild_by_id(guild_id)
    if not g: return
    new_xp = g["xp"] + amount
    level = g["level"]
    xp_needed = level * 1000
    if new_xp >= xp_needed:
        new_level = min(level + 1, 5)
        new_xp -= xp_needed
        await update_guild_field(guild_id, "level", new_level)
        await update_guild_field(guild_id, "xp", new_xp)
        await guild_broadcast(guild_id, f"🏆 **Niveau de guilde augmenté !**\nNiveau {new_level} atteint ! De nouveaux bonus sont débloqués.", None)
    else:
        await update_guild_field(guild_id, "xp", new_xp)

async def get_guild_activity_leaders(guild_id: int, limit: int = 5) -> list:
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.full_name, COUNT(*) AS actions
            FROM guild_logs gl
            JOIN users u ON u.user_id = gl.actor_id
            WHERE gl.guild_id = ? AND gl.timestamp > ?
            GROUP BY gl.actor_id
            ORDER BY actions DESC, u.full_name ASC
            LIMIT ?
        """, (guild_id, now() - 14 * 86400, limit)) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

async def guild_broadcast(guild_id: int, message: str, bot):
    if bot is None:
        return
    members = await get_guild_members(guild_id)
    for m in members:
        try:
            await bot.send_message(m["user_id"], message, parse_mode="Markdown")
        except:
            pass

async def log_guild_action(guild_id: int, action: str, actor_id: int, details: str = ""):
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute(
            "INSERT INTO guild_logs (guild_id, action, actor_id, user_id, details, timestamp) VALUES (?,?,?,?,?,?)",
            (guild_id, action, actor_id, actor_id, details, now())
        )
        await db.commit()


async def _accept_guild_invite(invite_id: int, user_id: int, user_full_name: str, bot=None):
    """Accepte une invitation de guilde et centralise la logique partagée."""
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_invites WHERE invite_id = ? AND invited_id = ? AND status = 'pending' AND expires_at > ?",
            (invite_id, user_id, now())
        ) as cur:
            inv = await cur.fetchone()
        if not inv:
            return False, "❌ Cette invitation a expiré ou n'existe pas.", None

        async with db.execute(
            "SELECT 1 FROM guild_members WHERE user_id = ? LIMIT 1",
            (user_id,)
        ) as cur:
            if await cur.fetchone():
                return False, "❌ Tu es déjà dans une guilde.", None

        guild = await get_guild_by_id(inv["guild_id"])
        if not guild:
            return False, "❌ La guilde n'existe plus.", None

        level_bonus = GUILD_LEVEL_BONUSES.get(guild["level"], GUILD_LEVEL_BONUSES[1])
        max_members = level_bonus["members_cap"]
        members = await get_guild_members(guild["guild_id"])
        if len(members) >= max_members:
            return False, f"❌ La guilde {guild['name']} est pleine (limite {max_members}).", None

        await db.execute(
            "INSERT INTO guild_members (guild_id, user_id, role, joined_at) VALUES (?,?,'Membre',?)",
            (guild["guild_id"], user_id, now())
        )
        await db.execute("UPDATE guild_invites SET status = 'accepted' WHERE invite_id = ?", (invite_id,))
        await db.commit()

    await guild_broadcast(guild["guild_id"], f"🎉 **{user_full_name}** a rejoint la guilde !", bot=bot)
    await log_guild_action(guild["guild_id"], "member_joined", user_id, f"Rejoint via invitation {invite_id}")
    return True, f"✅ Tu as rejoint la guilde **{guild['name']}** !", guild

# ─────────────────────────────────────────────────────────────────────────────
# Commandes principales
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_guild(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild:
        await update.message.reply_text(
            "🏰 **Tu n'appartiens à aucune guilde.**\n\n"
            "👉 `/guild_create [nom]` – créer une guilde\n"
            "👉 `/guild_join [nom]` – rejoindre une guilde (sur invitation)",
            parse_mode="Markdown"
        )
        return

    members = await get_guild_members(guild["guild_id"])
    chef = next((m for m in members if m["role"] == "Chef"), None)
    chef_name = chef["full_name"] if chef else "?"
    level_bonus = GUILD_LEVEL_BONUSES.get(guild["level"], GUILD_LEVEL_BONUSES[1])
    max_members = level_bonus["members_cap"]
    war = await get_active_guild_war(guild["guild_id"])
    activity = await get_guild_activity_leaders(guild["guild_id"])

    # Utilisation de HTML au lieu de Markdown pour éviter les erreurs de parsing
    from utils.helpers import escape_html
    text = (
        f"🏰 <b>{escape_html(guild['name'])}</b>\n"
        f"👑 Chef : {escape_html(chef_name)}\n"
        f"📜 Description : {escape_html(guild.get('description', 'Aucune'))}\n"
        f"⭐ Niveau : {guild['level']} | 🎓 XP guilde : {guild['xp']}\n"
        f"💰 Trésorerie : {fmt(guild['treasury'])}\n"
        f"👥 Membres : {len(members)}/{max_members}\n"
        f"📅 Créée le : {fmt_time(guild['created_at'])}\n\n"
        f"<b>Quête active :</b>\n"
    )
    if guild.get("quest_type"):
        quest = GUILD_QUESTS.get(guild["quest_type"])
        if quest:
            progress = guild.get("quest_progress", 0)
            target = quest["target"]
            bar = "█" * int((progress / target) * 10) + "░" * (10 - int((progress / target) * 10))
            text += (
                f"📋 {quest['name']}\n"
                f"{bar} {progress}/{target}\n"
                f"Récompense : {quest['reward_prestige']} Prestige, {quest['reward_exp']} XP, {quest['reward_guild_exp']} XP guilde\n"
                f"⏳ {fmt_time(guild['quest_ends_at'] - now())} restant\n"
            )
        else:
            text += "Aucune quête active.\n"
    else:
        text += "Aucune quête active. <code>/guild_quest propose</code> pour en proposer une.\n"

    if war:
        enemy_id = war["guild_b"] if war["guild_a"] == guild["guild_id"] else war["guild_a"]
        enemy = await get_guild_by_id(enemy_id)
        my_score = war["score_a"] if war["guild_a"] == guild["guild_id"] else war["score_b"]
        enemy_score = war["score_b"] if war["guild_a"] == guild["guild_id"] else war["score_a"]
        text += (
            f"\n<b>Guerre active :</b>\n"
            f"⚔️ Contre {escape_html(enemy['name'] if enemy else 'Guilde inconnue')}\n"
            f"📊 Score : {my_score} - {enemy_score}\n"
            f"⏳ Temps restant : {fmt_time(max(0, war['ends_at'] - now()))}\n"
        )

    if activity:
        text += "\n<b>Membres les plus actifs</b>\n"
        for idx, row in enumerate(activity, start=1):
            text += f"{idx}. {escape_html(row['full_name'])} — {row['actions']} actions\n"

    text += (
        f"\n<i>/guild_members – voir la liste des membres</i>\n"
        f"<i>/guild_donate [montant] – donner à la trésorerie</i>\n"
        f"<i>/guild_chat [message] – envoyer un message à la guilde</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")

@require_registered
@require_free
async def cmd_guild_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    if not context.args:
        await update.message.reply_text("Usage : `/guild_create [nom de la guilde]`")
        return
    name = " ".join(context.args)[:30]
    if await get_guild_by_name(name):
        await update.message.reply_text(f"❌ Une guilde nommée **{name}** existe déjà.")
        return
    if await get_user_guild(user.id):
        await update.message.reply_text("❌ Tu es déjà dans une guilde.")
        return
    if u["balance"] < GUILD_CREATION_COST:
        await update.message.reply_text(f"❌ Créer une guilde coûte **{fmt(GUILD_CREATION_COST)}**.\nSolde : {fmt(u['balance'])}")
        return
    await update_balance(user.id, -GUILD_CREATION_COST)
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute(
            "INSERT INTO guilds (name, owner_id, treasury, level, xp, created_at) VALUES (?,?,0,1,0,?)",
            (name, user.id, now())
        )
        async with db.execute("SELECT last_insert_rowid()") as cur:
            guild_id = (await cur.fetchone())[0]
        await db.execute(
            "INSERT INTO guild_members (guild_id, user_id, role, joined_at) VALUES (?,?,'Chef',?)",
            (guild_id, user.id, now())
        )
        await db.commit()
    await update.message.reply_text(
        f"🏰 **Guilde {name} créée !**\n\n"
        f"💰 Coût : {fmt(GUILD_CREATION_COST)}\n"
        f"👑 Tu es le chef.\n\n"
        f"👉 `/guild_invite` (en répondant à un joueur) pour inviter.\n"
        f"👉 `/guild_desc [texte]` pour ajouter une description."
    )
    await log_guild_action(guild_id, "creation", user.id, f"Guilde {name} créée")

@require_registered
@require_free
async def cmd_guild_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild or guild["role"] not in ("Chef", "Officier"):
        await update.message.reply_text("❌ Réservé au Chef et aux Officiers.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Réponds au message du joueur à inviter.")
        return
    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("Tu ne peux pas t'inviter toi-même.")
        return
    t_guild = await get_user_guild(target.id)
    if t_guild:
        await update.message.reply_text(f"❌ {target.full_name} est déjà dans une guilde.")
        return
    expires_at = now() + 48 * 3600
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute("DELETE FROM guild_invites WHERE guild_id = ? AND invited_id = ? AND status = 'pending'", (guild["guild_id"], target.id))
        await db.execute(
            "INSERT INTO guild_invites (guild_id, invited_id, invited_by, created_at, expires_at, status) VALUES (?,?,?,?,?,'pending')",
            (guild["guild_id"], target.id, user.id, now(), expires_at)
        )
        async with db.execute("SELECT last_insert_rowid()") as cur:
            invite_id = (await cur.fetchone())[0]
        await db.commit()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accepter", callback_data=f"guild_accept_{invite_id}"),
         InlineKeyboardButton("❌ Refuser", callback_data=f"guild_refuse_{invite_id}")]
    ])
    await update.message.reply_text(f"📨 Invitation envoyée à {target.full_name} pour rejoindre {guild['name']}.")
    await context.bot.send_message(
        target.id,
        f"🏰 **Invitation à rejoindre {guild['name']}**\nDe la part de {user.full_name}\nExpire dans 48h.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await log_guild_action(guild["guild_id"], "invite_sent", user.id, f"Invitation envoyée à {target.id}")

# Callback pour les invitations
async def guild_invite_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("_")
    if len(parts) != 3:
        await query.edit_message_text("Format invalide.")
        return
    action = parts[1]  # accept ou refuse
    invite_id = int(parts[2])
    user = query.from_user
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_invites WHERE invite_id = ? AND invited_id = ? AND status = 'pending' AND expires_at > ?",
            (invite_id, user.id, now())
        ) as cur:
            inv = await cur.fetchone()
        if not inv:
            await query.edit_message_text("❌ Cette invitation a expiré ou n'existe pas.")
            return
        if action == "accept":
            success, message, _guild = await _accept_guild_invite(invite_id, user.id, user.full_name, bot=context.bot)
            await query.edit_message_text(message, parse_mode="Markdown" if success else None)
        else:
            await db.execute("UPDATE guild_invites SET status = 'refused' WHERE invite_id = ?", (invite_id,))
            await db.commit()
            await query.edit_message_text("❌ Invitation refusée.")
            inviter = await get_user(inv["invited_by"])
            if inviter:
                await add_notification(inviter["user_id"], f"{user.full_name} a refusé votre invitation de guilde.")

@require_registered
@require_free
async def cmd_guild_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild:
        await update.message.reply_text("❌ Tu n'es dans aucune guilde.")
        return
    if guild["role"] == "Chef":
        members = await get_guild_members(guild["guild_id"])
        if len(members) > 1:
            await update.message.reply_text(
                "❌ Tu es le chef ! Tu ne peux pas quitter sans dissoudre la guilde.\n"
                "👉 `/guild_transfer @joueur` – transférer le leadership\n"
                "👉 `/guild_dissolve confirm` – dissoudre la guilde (irréversible)"
            )
            return
        else:
            async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
                await db.execute("DELETE FROM guilds WHERE guild_id = ?", (guild["guild_id"],))
                await db.execute("DELETE FROM guild_members WHERE guild_id = ?", (guild["guild_id"],))
                await db.commit()
            await update.message.reply_text(f"💀 La guilde **{guild['name']}** a été dissoute car tu étais le dernier membre.")
            return
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute("DELETE FROM guild_members WHERE user_id = ? AND guild_id = ?", (user.id, guild["guild_id"]))
        await db.commit()
    await update.message.reply_text(f"👋 Tu as quitté la guilde **{guild['name']}**.")
    await guild_broadcast(guild["guild_id"], f"👋 **{user.full_name}** a quitté la guilde.", bot=context.bot)

@require_registered
@require_free
async def cmd_guild_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild:
        await update.message.reply_text("❌ Tu n'es dans aucune guilde.")
        return
    members = await get_guild_members(guild["guild_id"])
    if not members:
        await update.message.reply_text("Aucun membre ? C'est étrange...")
        return
    text = f"👥 **Membres de {guild['name']}** ({len(members)}/{GUILD_LEVEL_BONUSES.get(guild['level'], GUILD_LEVEL_BONUSES[1])['members_cap']})\n\n"
    for m in members:
        text += f"👤 **{m['full_name']}** — {m['role']}\n   💰 {fmt(m['balance'])} | XP: {m['xp']}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

@require_registered
@require_free
async def cmd_guild_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild or guild["role"] not in ("Chef", "Officier"):
        await update.message.reply_text("❌ Réservé au Chef et aux Officiers.")
        return
    if not context.args:
        await update.message.reply_text("Usage : `/guild_desc [texte]`")
        return
    desc = " ".join(context.args)[:200]
    await update_guild_field(guild["guild_id"], "description", desc)
    await update.message.reply_text(f"✅ Description mise à jour :\n_{desc}_", parse_mode="Markdown")

@require_registered
@require_free
async def cmd_guild_promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild or guild["role"] != "Chef":
        await update.message.reply_text("❌ Réservé au Chef.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Réponds au message du membre à promouvoir.")
        return
    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("Tu ne peux pas te promouvoir toi-même.")
        return
    target_member = await get_user_guild(target.id)
    if not target_member or target_member["guild_id"] != guild["guild_id"]:
        await update.message.reply_text("Ce joueur n'est pas dans ta guilde.")
        return
    current_role = target_member["role"]
    if current_role == "Officier":
        await update.message.reply_text("Ce membre est déjà Officier. Pour le faire Chef, utilise `/guild_transfer`.")
        return
    if current_role == "Chef":
        await update.message.reply_text("Impossible, c'est déjà le chef.")
        return
    new_role = "Officier"
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute("UPDATE guild_members SET role = ? WHERE user_id = ? AND guild_id = ?", (new_role, target.id, guild["guild_id"]))
        await db.commit()
    await update.message.reply_text(f"✅ **{target.full_name}** promu au rang de **{new_role}** !")
    await guild_broadcast(guild["guild_id"], f"🏅 **{target.full_name}** a été promu(e) **{new_role}** !", bot=context.bot)

@require_registered
@require_free
async def cmd_guild_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild or guild["role"] != "Chef":
        await update.message.reply_text("❌ Réservé au Chef.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Réponds au message du nouveau chef.")
        return
    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("Tu ne peux pas te transférer à toi-même.")
        return
    target_member = await get_user_guild(target.id)
    if not target_member or target_member["guild_id"] != guild["guild_id"]:
        await update.message.reply_text("Ce joueur n'est pas dans ta guilde.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute("UPDATE guild_members SET role = 'Chef' WHERE user_id = ? AND guild_id = ?", (target.id, guild["guild_id"]))
        await db.execute("UPDATE guild_members SET role = 'Officier' WHERE user_id = ? AND guild_id = ?", (user.id, guild["guild_id"]))
        await db.execute("UPDATE guilds SET owner_id = ? WHERE guild_id = ?", (target.id, guild["guild_id"]))
        await db.commit()
    await update.message.reply_text(f"👑 **{target.full_name}** est maintenant le chef de la guilde **{guild['name']}**.")
    await guild_broadcast(guild["guild_id"], f"👑 **{target.full_name}** est le nouveau chef de la guilde !", bot=context.bot)

@require_registered
@require_free
async def cmd_guild_dissolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild or guild["role"] != "Chef":
        await update.message.reply_text("❌ Réservé au Chef.")
        return
    if not context.args or context.args[0].lower() != "confirm":
        await update.message.reply_text(
            f"⚠️ **Dissolution de la guilde {guild['name']}**\n\n"
            f"Cette action est IRRÉVERSIBLE !\n"
            f"Pour confirmer : `/guild_dissolve confirm`"
        )
        return
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute("DELETE FROM guilds WHERE guild_id = ?", (guild["guild_id"],))
        await db.execute("DELETE FROM guild_members WHERE guild_id = ?", (guild["guild_id"],))
        await db.execute("DELETE FROM guild_invites WHERE guild_id = ?", (guild["guild_id"],))
        await db.commit()
    await update.message.reply_text(f"💀 La guilde **{guild['name']}** a été dissoute.")
    await log_guild_action(guild["guild_id"], "dissolution", user.id, "Guilde dissoute")

@require_registered
@require_free
async def cmd_guild_donate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    guild = await get_user_guild(user.id)
    if not guild:
        await update.message.reply_text("❌ Tu n'es dans aucune guilde.")
        return
    if not context.args:
        await update.message.reply_text("Usage : `/guild_donate [montant]`")
        return
    amount = parse_amount(context.args[0], u["balance"])
    if not amount or amount <= 0 or amount > u["balance"]:
        await update.message.reply_text("❌ Montant invalide.")
        return
    level_bonus = GUILD_LEVEL_BONUSES.get(guild["level"], GUILD_LEVEL_BONUSES[1])
    effective_amount = int(amount * level_bonus["donation_bonus"])
    await update_balance(user.id, -amount)
    new_treasury = guild["treasury"] + effective_amount
    await update_guild_field(guild["guild_id"], "treasury", new_treasury)
    xp_gain = max(10, amount // 5000)
    guild_xp_gain = max(5, effective_amount // 10000)
    await increment_field(user.id, "xp", xp_gain)
    await add_guild_xp(guild["guild_id"], guild_xp_gain)
    await update.message.reply_text(
        f"🏦 **{fmt(amount)}** donné à la trésorerie de **{guild['name']}**.\n"
        f"Bonus de guilde appliqué : {fmt(effective_amount)}\n"
        f"Nouvelle trésorerie : {fmt(new_treasury)}\n"
        f"✨ +{xp_gain} XP perso · +{guild_xp_gain} XP guilde"
    )
    if guild.get("quest_type") == "collect_donations":
        async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
            await db.execute("UPDATE guilds SET quest_progress = quest_progress + ? WHERE guild_id = ?", (effective_amount, guild["guild_id"]))
            await db.commit()
    await log_guild_action(guild["guild_id"], "donation", user.id, f"Don de {fmt(effective_amount)}")

@require_registered
@require_free
@cooldown("guild_chat", 300, "⏳ Attends un peu avant d'envoyer un autre message.")
async def cmd_guild_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild:
        await update.message.reply_text("❌ Tu n'es dans aucune guilde.")
        return
    if not context.args:
        await update.message.reply_text("Usage : `/guild_chat [message]`")
        return
    msg = " ".join(context.args)
    members = await get_guild_members(guild["guild_id"])
    for m in members:
        if m["user_id"] != user.id:
            try:
                await context.bot.send_message(m["user_id"], f"💬 **{guild['name']}** – {user.full_name} : {msg}")
            except:
                pass
    await log_guild_action(guild["guild_id"], "chat", user.id, msg[:120])
    await increment_field(user.id, "xp", 5)
    await update.message.reply_text("📨 Message envoyé à tous les membres.")

@require_registered
@require_free
async def cmd_guild_quest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild:
        await update.message.reply_text("❌ Tu n'es dans aucune guilde.")
        return
    if not context.args:
        text = "📋 **Gestion des quêtes**\n\n"
        text += "▪ `/guild_quest list` – voir les quêtes disponibles\n"
        text += "▪ `/guild_quest propose [nom]` – proposer une quête (les membres votent)\n"
        text += "▪ `/guild_quest vote [id] [oui/non]` – voter pour une proposition (via callback)\n"
        await update.message.reply_text(text, parse_mode="Markdown")
        return
    sub = context.args[0].lower()
    if sub == "list":
        text = "📋 **Quêtes de guilde disponibles**\n\n"
        for key, q in GUILD_QUESTS.items():
            text += (
                f"**{q['name']}**\n"
                f"  {q['desc']}\n"
                f"  🎯 Objectif : {fmt(q['target'])}\n"
                f"  ⏳ Durée : {fmt_time(q['duration'])}\n"
                f"  🎁 Récompenses : {q['reward_prestige']} Prestige, {q['reward_exp']} XP, {q['reward_guild_exp']} XP guilde\n\n"
            )
        await update.message.reply_text(text, parse_mode="Markdown")
    elif sub == "propose":
        if len(context.args) < 2:
            await update.message.reply_text("Usage : `/guild_quest propose [nom quête]`")
            return
        quest_name = " ".join(context.args[1:]).lower()
        matched = None
        for key, q in GUILD_QUESTS.items():
            if q["name"].lower() == quest_name:
                matched = key
                break
        if not matched:
            await update.message.reply_text("❌ Quête inconnue.")
            return
        if guild.get("quest_type"):
            await update.message.reply_text("❌ Une quête est déjà en cours. Terminez-la d'abord.")
            return
        proposal_id = random.randint(10000, 99999)
        async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
            await db.execute(
                "INSERT INTO guild_quest_proposals (guild_id, proposer_id, quest_key, proposal_id, created_at) VALUES (?,?,?,?,?)",
                (guild["guild_id"], user.id, matched, proposal_id, now())
            )
            await db.commit()
        members = await get_guild_members(guild["guild_id"])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Oui", callback_data=f"guild_vote_{proposal_id}_yes"),
             InlineKeyboardButton("❌ Non", callback_data=f"guild_vote_{proposal_id}_no")]
        ])
        for m in members:
            if m["user_id"] != user.id:
                try:
                    await context.bot.send_message(
                        m["user_id"],
                        f"📢 Proposition de quête : **{GUILD_QUESTS[matched]['name']}** par {user.full_name}\nVotez !",
                        reply_markup=keyboard
                    )
                except:
                    pass
        await update.message.reply_text("📢 Proposition envoyée aux membres. Ils ont 1 heure pour voter.")
    else:
        await update.message.reply_text("Sous-commande inconnue. Utilise `/guild_quest` pour voir l'aide.")

# Callback pour les votes de quêtes (simplifié)
async def guild_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("_")
    if len(parts) != 4:
        return
    proposal_id = int(parts[2])
    vote = parts[3]  # yes ou no
    user = query.from_user
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        async with db.execute(
            "SELECT * FROM guild_quest_proposals WHERE proposal_id = ? AND guild_id IN (SELECT guild_id FROM guild_members WHERE user_id = ?) AND created_at > ? AND status = 'pending'",
            (proposal_id, user.id, now() - 3600)
        ) as cur:
            prop = await cur.fetchone()
        if not prop:
            await query.edit_message_text("Cette proposition a expiré ou n'existe pas.")
            return
        await db.execute(
            "INSERT INTO guild_votes (proposal_id, user_id, vote) VALUES (?,?,?) ON CONFLICT(proposal_id, user_id) DO UPDATE SET vote=?",
            (proposal_id, user.id, vote, vote)
        )
        await db.commit()
    await query.edit_message_text(f"Vote enregistré : {'✅ Oui' if vote == 'yes' else '❌ Non'}")

# Fonctions de maintenance à appeler depuis le scheduler
async def process_guild_proposals():
    """Toutes les heures, vérifie les propositions expirées et lance la quête si majorité oui."""
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guild_quest_proposals WHERE status = 'pending' AND created_at < ?", (now() - 3600,)) as cur:
            props = await cur.fetchall()
        for prop in props:
            async with db.execute("SELECT vote, COUNT(*) FROM guild_votes WHERE proposal_id = ? GROUP BY vote", (prop["proposal_id"],)) as cur2:
                votes = {row["vote"]: row["COUNT(*)"] for row in await cur2.fetchall()}
            total_votes = sum(votes.values())
            yes_votes = votes.get("yes", 0)
            if total_votes > 0 and yes_votes > total_votes / 2:
                guild_id = prop["guild_id"]
                quest = GUILD_QUESTS[prop["quest_key"]]
                ends_at = now() + quest["duration"]
                await db.execute(
                    "UPDATE guilds SET quest_type = ?, quest_target = ?, quest_progress = 0, quest_ends_at = ? WHERE guild_id = ?",
                    (prop["quest_key"], quest["target"], ends_at, guild_id)
                )
                await guild_broadcast(guild_id, f"🏁 La quête **{quest['name']}** est lancée ! Objectif : {fmt(quest['target'])}", None)
            await db.execute("UPDATE guild_quest_proposals SET status = 'closed' WHERE proposal_id = ?", (prop["proposal_id"],))
        await db.commit()

async def clean_expired_invites():
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute("UPDATE guild_invites SET status = 'expired' WHERE status = 'pending' AND expires_at < ?", (now(),))
        await db.commit()

async def start_guild_competition():
    challenges = [
        {"type": "donation", "desc": "Le plus de dons collectés", "field": "treasury"},
        {"type": "xp", "desc": "Le plus d'XP gagnée par la guilde", "field": "xp"},
        {"type": "members", "desc": "Le plus de nouveaux membres", "field": "member_count"}
    ]
    challenge = random.choice(challenges)
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        async with db.execute("SELECT 1 FROM guild_competitions WHERE ended = 0 AND ends_at > ?", (now(),)) as cur:
            if await cur.fetchone():
                return
        await db.execute(
            "INSERT INTO guild_competitions (challenge_type, challenge_desc, starts_at, ends_at) VALUES (?,?,?,?)",
            (challenge["type"], challenge["desc"], now(), now() + 7*86400)
        )
        await db.commit()

async def end_guild_competition():
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guild_competitions WHERE ended = 0 AND ends_at < ?", (now(),)) as cur:
            comp = await cur.fetchone()
        if not comp:
            return
        if comp["challenge_type"] == "donation":
            async with db.execute("SELECT guild_id, treasury FROM guilds ORDER BY treasury DESC LIMIT 3") as cur2:
                winners = await cur2.fetchall()
        elif comp["challenge_type"] == "xp":
            async with db.execute("SELECT guild_id, xp FROM guilds ORDER BY xp DESC LIMIT 3") as cur2:
                winners = await cur2.fetchall()
        else:
            async with db.execute("SELECT guild_id, COUNT(*) as count FROM guild_members GROUP BY guild_id ORDER BY count DESC LIMIT 3") as cur2:
                winners = await cur2.fetchall()
        rewards = [1_000_000, 500_000, 250_000]
        for i, winner in enumerate(winners):
            guild_id = winner["guild_id"]
            await update_guild_field(guild_id, "treasury", (await get_guild_by_id(guild_id))["treasury"] + rewards[i])
            await guild_broadcast(guild_id, f"🏆 Votre guilde a terminé {i+1}ème de la compétition ! +{fmt(rewards[i])} en trésorerie.", None)
        await db.execute("UPDATE guild_competitions SET ended = 1 WHERE id = ?", (comp["id"],))
        await db.commit()

async def process_guild_quest_completion():
    completed = []
    failed = []
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT guild_id, name, quest_type, quest_target, quest_progress, quest_ends_at
            FROM guilds
            WHERE quest_type IS NOT NULL AND quest_type != ''
        """) as cur:
            guilds = await cur.fetchall()
        for guild in guilds:
            quest = GUILD_QUESTS.get(guild["quest_type"])
            if not quest:
                continue
            completed_now = guild["quest_progress"] >= max(1, guild["quest_target"] or quest["target"])
            expired = guild["quest_ends_at"] and guild["quest_ends_at"] < now()
            if not completed_now and not expired:
                continue
            if completed_now:
                async with db.execute("SELECT user_id FROM guild_members WHERE guild_id = ?", (guild["guild_id"],)) as cur_members:
                    member_ids = [row["user_id"] for row in await cur_members.fetchall()]
                for member_id in member_ids:
                    await db.execute(
                        "UPDATE users SET xp = xp + ?, prestige = prestige + ? WHERE user_id = ?",
                        (quest["reward_exp"], quest["reward_prestige"], member_id)
                    )
                completed.append((guild["guild_id"], guild["name"], quest["reward_guild_exp"]))
            else:
                failed.append(guild["name"])
            await db.execute(
                "UPDATE guilds SET quest_type = NULL, quest_target = 0, quest_progress = 0, quest_ends_at = 0 WHERE guild_id = ?",
                (guild["guild_id"],)
            )
        await db.commit()

    for guild_id, guild_name, guild_xp in completed:
        await add_guild_xp(guild_id, guild_xp)
    return completed, failed

async def process_guild_maintenance():
    """Appelée quotidiennement par le scheduler."""
    await clean_expired_invites()
    await process_guild_proposals()
    await process_guild_quest_completion()
    await end_guild_competition()
    await start_guild_competition()
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        # Guerres actives dont la date de fin est dépassée
        async with db.execute("SELECT * FROM guild_wars WHERE status = 'active' AND ends_at < ?", (now(),)) as cur:
            wars = await cur.fetchall()
        for war in wars:
            # Déterminer le vainqueur
            winner = war["guild_a"] if war["score_a"] > war["score_b"] else war["guild_b"]
            if war["score_a"] == war["score_b"]:
                winner = 0  # match nul
            await db.execute("UPDATE guild_wars SET status = 'finished', winner = ? WHERE war_id = ?", (winner, war["war_id"]))
            if winner:
                # Récompenses
                await db.execute("UPDATE guilds SET war_score = war_score + 10, treasury = treasury + 10000 WHERE guild_id = ?", (winner,))
                loser = war["guild_b"] if winner == war["guild_a"] else war["guild_a"]
                await db.execute("UPDATE guilds SET war_score = war_score - 5 WHERE guild_id = ?", (loser,))
                # Notifications
                winner_guild = await get_guild_by_id(winner)
                loser_guild = await get_guild_by_id(loser)
                await guild_broadcast(winner, f"🏆 Victoire dans la guerre contre {loser_guild['name']} ! +10 war_score, +10000 trésorerie.", None)
                await guild_broadcast(loser, f"😞 Défaite dans la guerre contre {winner_guild['name']}. -5 war_score.", None)
        await db.commit()

# Commande de classement des guildes
@require_registered
async def cmd_guild_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT g.guild_id, g.name, g.level, g.xp, g.treasury, g.war_score, COUNT(gm.user_id) as members
            FROM guilds g
            LEFT JOIN guild_members gm ON gm.guild_id = g.guild_id
            GROUP BY g.guild_id
            ORDER BY g.level DESC, g.xp DESC, g.war_score DESC, g.treasury DESC
            LIMIT 10
        """) as cur:
            guilds = await cur.fetchall()
    if not guilds:
        await update.message.reply_text("🏆 Aucune guilde enregistrée.")
        return
    medals = ["🥇", "🥈", "🥉"] + ["🏰"] * 7
    text = "🏆 **Classement des guildes**\n\n"
    for i, g in enumerate(guilds):
        text += f"{medals[i]} **{g['name']}** – Niv.{g['level']} | {g['members']} membres | 💰 {fmt(g['treasury'])} | ⚔️ Score guerre {g['war_score']}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

@require_registered
@require_free
async def cmd_guild_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rejoindre une guilde sur invitation (via le nom de la guilde)."""
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : `/guild_join [nom guilde]`")
        return
    name = " ".join(context.args)
    guild = await get_guild_by_name(name)
    if not guild:
        await update.message.reply_text(f"❌ Guilde **{name}** introuvable.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        async with db.execute("SELECT * FROM guild_invites WHERE guild_id = ? AND invited_id = ? AND status = 'pending' AND expires_at > ?", (guild["guild_id"], user.id, now())) as cur:
            inv = await cur.fetchone()
        if not inv:
            await update.message.reply_text(f"❌ Tu n'as pas d'invitation pour **{guild['name']}**.")
            return
        invite_id = inv[0]
    success, message, _guild = await _accept_guild_invite(invite_id, user.id, user.full_name, bot=context.bot)
    await update.message.reply_text(message, parse_mode="Markdown" if success else None)

# ─────────────────────────────────────────────────────────────────────────────
# Commandes de guerre de guildes
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
@require_free
async def cmd_guild_declare_war(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild or guild["role"] != "Chef":
        await update.message.reply_text("❌ Seul le chef de guilde peut déclarer la guerre.")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("Usage : /guild_declare_war [ID_guilde]")
        return
    
    try:
        target_guild_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID de guilde invalide.")
        return
    
    if target_guild_id == guild["guild_id"]:
        await update.message.reply_text("❌ Tu ne peux pas déclarer la guerre à ta propre guilde.")
        return
    
    target_guild = await get_guild_by_id(target_guild_id)
    if not target_guild:
        await update.message.reply_text("❌ Guilde cible introuvable.")
        return
    
    # Vérifier s'il y a déjà une guerre active
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM guild_wars WHERE status = 'active' AND (guild_a = ? OR guild_b = ? OR guild_a = ? OR guild_b = ?)",
            (guild["guild_id"], guild["guild_id"], target_guild_id, target_guild_id)
        ) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ L'une des guildes est déjà en guerre.")
                return
        
        # Coût de déclaration de guerre (10% de la trésorerie)
        cost = int(guild["treasury"] * 0.1)
        if guild["treasury"] < cost:
            await update.message.reply_text(f"❌ Trésorerie insuffisante. Coût : {fmt(cost)}")
            return
        
        await db.execute("UPDATE guilds SET treasury = treasury - ? WHERE guild_id = ?", (cost, guild["guild_id"]))
        ends_at = now() + 7 * 86400
        await db.execute("""
            INSERT INTO guild_wars (guild_a, guild_b, started_at, ends_at, status)
            VALUES (?, ?, ?, ?, 'active')
        """, (guild["guild_id"], target_guild_id, now(), ends_at))
        await db.commit()
    
    await guild_broadcast(guild["guild_id"], f"⚔️ **GUERRE DÉCLARÉE** contre {target_guild['name']} ! Durée : 7 jours.", context.bot)
    await guild_broadcast(target_guild_id, f"⚔️ **GUERRE DÉCLARÉE** par {guild['name']} ! Préparez-vous à vous défendre.", context.bot)
    await update.message.reply_text(f"✅ Guerre déclarée contre **{target_guild['name']}**. Coût : {fmt(cost)}")

@require_registered
@require_free
async def cmd_guild_war_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild:
        await update.message.reply_text("❌ Tu n'es dans aucune guilde.")
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_wars WHERE status = 'active' AND (guild_a = ? OR guild_b = ?)",
            (guild["guild_id"], guild["guild_id"])
        ) as cur:
            war = await cur.fetchone()
    
    if not war:
        await update.message.reply_text("🏳️ Ta guilde n'est actuellement en guerre contre personne.")
        return
    
    target_guild_id = war["guild_b"] if war["guild_a"] == guild["guild_id"] else war["guild_a"]
    target_guild = await get_guild_by_id(target_guild_id)
    my_score = war["score_a"] if war["guild_a"] == guild["guild_id"] else war["score_b"]
    enemy_score = war["score_b"] if war["guild_a"] == guild["guild_id"] else war["score_a"]
    time_left = war["ends_at"] - now()
    text = (
        f"⚔️ **État de la guerre**\n\n"
        f"Guerre contre : {target_guild['name']}\n"
        f"Score : {my_score} - {enemy_score}\n"
        f"⏳ Temps restant : {fmt_time(time_left)}\n"
        f"👉 `/guild_attack @user` pour attaquer un membre adverse.\n"
        f"👉 `/guild_surrender` pour se rendre (perte de 20% de la trésorerie)."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

@require_registered
@require_free
async def cmd_guild_attack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild:
        await update.message.reply_text("❌ Tu n'es dans aucune guilde.")
        return
    
    # Vérifier la guerre active
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_wars WHERE status = 'active' AND (guild_a = ? OR guild_b = ?)",
            (guild["guild_id"], guild["guild_id"])
        ) as cur:
            war = await cur.fetchone()
    
    if not war:
        await update.message.reply_text("❌ Ta guilde n'est pas en guerre.")
        return
    
    # Cible : un joueur de la guilde adverse
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Réponds au message d'un membre de la guilde adverse pour l'attaquer.")
        return
    
    target = update.message.reply_to_message.from_user
    target_guild = await get_user_guild(target.id)
    enemy_guild_id = war["guild_b"] if war["guild_a"] == guild["guild_id"] else war["guild_a"]
    if not target_guild or target_guild["guild_id"] != enemy_guild_id:
        await update.message.reply_text("❌ Cette personne n'est pas dans la guilde adverse.")
        return

    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT timestamp FROM guild_logs WHERE guild_id = ? AND actor_id = ? AND action = 'war_attack' ORDER BY timestamp DESC LIMIT 1",
            (guild["guild_id"], user.id)
        ) as cur:
            last_attack = await cur.fetchone()
    if last_attack and now() - last_attack["timestamp"] < 600:
        await update.message.reply_text(f"⏳ Attends encore {fmt_time(600 - (now() - last_attack['timestamp']))} avant une nouvelle attaque.")
        return
    
    # Lancer un duel spécial (guerre)
    # On réutilise le système de duel existant mais avec une mise obligatoire de 5000 coins (prélevée du trésor de guerre?)
    # Pour simplifier, on crée un duel normal sans mise, mais qui rapporte des points de guerre.
    # Ici, on va juste incrémenter le score si le membre de la guilde actuelle gagne.
    
    # Défi classique via /defier, mais en mode guerre ? Plutôt que de réinventer, on peut faire un duel spécial.
    # On va créer un duel rapide : tirage aléatoire (force + niveau).
    challenger = user
    defender = target
    
    from database import get_skill
    c_force = await get_skill(challenger.id, "Force") or 0
    t_force = await get_skill(defender.id, "Force") or 0
    c_level = (await get_user(challenger.id)).get("level", 1)
    t_level = (await get_user(defender.id)).get("level", 1)
    
    c_power = random.randint(40, 100) + c_force*5 + c_level*3 + guild["level"] * 8 + max(0, guild.get("war_score", 0)) * 0.4
    t_power = random.randint(40, 100) + t_force*5 + t_level*3 + target_guild["level"] * 8 + max(0, target_guild.get("war_score", 0)) * 0.4
    
    if c_power > t_power:
        winner = challenger
        loser = defender
    elif t_power > c_power:
        winner = defender
        loser = challenger
    else:
        await update.message.reply_text("🤝 Combat nul ! Aucun point.")
        return
    
    winner_guild = await get_user_guild(winner.id)
    loser_guild = await get_user_guild(loser.id)
    power_gap = abs(c_power - t_power)
    war_points = 1 + min(2, power_gap // 80)
    treasury_steal = min(loser_guild["treasury"], random.randint(3000, 8000) * war_points)
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db2:
        if winner_guild["guild_id"] == war["guild_a"]:
            await db2.execute("UPDATE guild_wars SET score_a = score_a + ? WHERE war_id = ?", (war_points, war["war_id"]))
        else:
            await db2.execute("UPDATE guild_wars SET score_b = score_b + ? WHERE war_id = ?", (war_points, war["war_id"]))
        if treasury_steal > 0:
            await db2.execute("UPDATE guilds SET treasury = treasury + ? WHERE guild_id = ?", (treasury_steal, winner_guild["guild_id"]))
            await db2.execute("UPDATE guilds SET treasury = MAX(0, treasury - ?) WHERE guild_id = ?", (treasury_steal, loser_guild["guild_id"]))
        await db2.execute(
            "INSERT INTO guild_logs (guild_id, action, actor_id, user_id, details, timestamp) VALUES (?,?,?,?,?,?)",
            (guild["guild_id"], "war_attack", user.id, target.id, f"{winner.full_name} vs {loser.full_name}", now())
        )
        await db2.commit()
    
    await update.message.reply_text(
        f"⚔️ **Combat de guilde !**\n\n"
        f"{winner.full_name} a vaincu {loser.full_name} !\n"
        f"➕ +{war_points} points pour {winner_guild['name']}\n"
        f"💰 Butin de guerre : {fmt(treasury_steal)}"
    )
    
    # Ajouter XP et autres récompenses
    await increment_field(winner.id, "xp", 60 + war_points * 15)
    await increment_field(loser.id, "xp", 20)

@require_registered
@require_free
async def cmd_guild_surrender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guild = await get_user_guild(user.id)
    if not guild or guild["role"] != "Chef":
        await update.message.reply_text("❌ Seul le chef peut se rendre.")
        return
    
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_wars WHERE status = 'active' AND (guild_a = ? OR guild_b = ?)",
            (guild["guild_id"], guild["guild_id"])
        ) as cur:
            war = await cur.fetchone()
    
    if not war:
        await update.message.reply_text("❌ Ta guilde n'est pas en guerre.")
        return
    
    penalty = int(guild["treasury"] * 0.2)
    new_treasury = max(0, guild["treasury"] - penalty)
    winner = war["guild_b"] if war["guild_a"] == guild["guild_id"] else war["guild_a"]
    winner_guild = await get_guild_by_id(winner)
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute("UPDATE guilds SET treasury = ? WHERE guild_id = ?", (new_treasury, guild["guild_id"]))
        await db.execute("UPDATE guild_wars SET status = 'surrendered', winner = ? WHERE war_id = ?", (winner, war["war_id"]))
        await db.execute(
            "UPDATE guilds SET reputation = ?, treasury = treasury + ? WHERE guild_id = ?",
            (min(100, winner_guild["reputation"] + 10), penalty, winner)
        )
        await db.commit()
    await guild_broadcast(guild["guild_id"], f"🏳️ La guilde s'est rendue ! Perte de {fmt(penalty)} de trésorerie.", context.bot)
    await guild_broadcast(winner, f"🏆 Victoire par reddition ! +10 réputation et {fmt(penalty)} récupérés.", context.bot)
    await update.message.reply_text(f"🏳️ Reddition acceptée. Perte de {fmt(penalty)}. La guerre est terminée.")
