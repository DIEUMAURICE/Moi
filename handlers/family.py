# handlers/family.py
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_field, update_balance, get_marriage, get_top_rich
from utils.decorators import require_registered, require_free
from utils.helpers import fmt, now, get_title, escape_md

# ─────────────────────────────────────────────────────────────────────────────
# Mariage
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
@require_free
async def cmd_mariage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Réponds au message de la personne à qui tu veux te marier.")
        return

    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te marier à toi-même !")
        return

    # Vérifier l'âge minimum (18 ans)
    if u.get("age", 0) < 18:
        await update.message.reply_text("❌ Tu dois avoir au moins 18 ans pour te marier.")
        return

    t_data = await get_user(target.id)
    if t_data.get("age", 0) < 18:
        await update.message.reply_text(f"❌ {escape_md(target.full_name)} doit avoir au moins 18 ans pour se marier.")
        return

    # Vérifier si déjà marié
    marriage = await get_marriage(user.id)
    if marriage:
        await update.message.reply_text("❌ Tu es déjà marié(e) ! Divorce d'abord avec /divorce.")
        return

    target_marriage = await get_marriage(target.id)
    if target_marriage:
        await update.message.reply_text(f"❌ {escape_md(target.full_name)} est déjà marié(e) !")
        return

    # Coût du mariage
    MARRY_COST = 50_000
    if u["balance"] < MARRY_COST:
        await update.message.reply_text(
            f"❌ Le mariage coûte **{fmt(MARRY_COST)}**.\nTon solde : {fmt(u['balance'])}",
            parse_mode="Markdown"
        )
        return

    # Envoyer la demande
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM marriage_requests WHERE to_id=?", (target.id,))
        await db.execute(
            "INSERT INTO marriage_requests (from_id, to_id, created_at) VALUES (?,?,?)",
            (user.id, target.id, now())
        )
        await db.commit()

    await update.message.reply_text(
        f"💍 **Demande en mariage envoyée !**\n\n"
        f"👤 De : {escape_md(user.full_name)}\n"
        f"👤 Vers : {escape_md(target.full_name)}\n"
        f"💰 Frais : {fmt(MARRY_COST)} (déduits au moment de l'acceptation)\n\n"
        f"_{escape_md(target.full_name)}, utilise /acceptermariage pour accepter !_",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_acceptermariage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM marriage_requests WHERE to_id=? ORDER BY created_at DESC LIMIT 1",
            (user.id,)
        ) as cur:
            req = await cur.fetchone()

    if not req:
        await update.message.reply_text("❌ Tu n'as pas de demande en mariage en attente.")
        return

    from_user = await get_user(req["from_id"])
    MARRY_COST = 50_000

    if u["balance"] < MARRY_COST:
        await update.message.reply_text(
            f"❌ Le mariage coûte **{fmt(MARRY_COST)}**.\nTon solde : {fmt(u['balance'])}",
            parse_mode="Markdown"
        )
        return

    await update_balance(user.id, -MARRY_COST)
    await update_balance(from_user["user_id"], -MARRY_COST)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO marriages (user_id, partner_id, married_at, status) VALUES (?,?,?,?)",
            (user.id, req["from_id"], now(), "active")
        )
        await db.execute(
            "INSERT OR REPLACE INTO marriages (user_id, partner_id, married_at, status) VALUES (?,?,?,?)",
            (req["from_id"], user.id, now(), "active")
        )
        await db.execute("DELETE FROM marriage_requests WHERE to_id=?", (user.id,))
        await db.commit()

    await update.message.reply_text(
        f"💒 **Mariage célébré !**\n\n"
        f"💍 {escape_md(from_user['full_name'])} ❤️ {escape_md(user.full_name)}\n"
        f"💰 Frais totaux : {fmt(MARRY_COST * 2)}\n\n"
        f"_Que votre vie commune soit prospère !_\n\n"
        f"💡 Avantages du mariage :\n"
        f"• Bonus de 5% sur les revenus partagés\n"
        f"• Accès à l'arbre généalogique familial\n"
        f"• Possibilité d'adopter ensemble",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_divorce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    marriage = await get_marriage(user.id)

    if not marriage:
        await update.message.reply_text("❌ Tu n'es pas marié(e).")
        return

    partner = await get_user(marriage["partner_id"])
    DIVORCE_COST = 25_000
    u = await get_user(user.id)

    if u["balance"] < DIVORCE_COST:
        await update.message.reply_text(
            f"❌ Le divorce coûte **{fmt(DIVORCE_COST)}**.\nTon solde : {fmt(u['balance'])}",
            parse_mode="Markdown"
        )
        return

    await update_balance(user.id, -DIVORCE_COST)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE marriages SET status='divorced', divorced_at=? WHERE user_id=? OR partner_id=?",
            (now(), user.id, user.id)
        )
        await db.commit()

    await update.message.reply_text(
        f"💔 **Divorce prononcé...**\n\n"
        f"Tu divorces de **{escape_md(partner['full_name'])}**.\n"
        f"💰 Frais : {fmt(DIVORCE_COST)}\n\n"
        f"_Une page se tourne. Vers de nouveaux horizons !_",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Arbre généalogique et famille
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
async def cmd_arbre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT f.*, fm.role FROM family_members fm JOIN family f ON f.family_id=fm.family_id WHERE fm.user_id=?",
            (user.id,)
        ) as cur:
            fam_row = await cur.fetchone()

    marriage = await get_marriage(user.id)
    u = await get_user(user.id)

    text = f"🌳 **Arbre généalogique de {escape_md(user.full_name)}**\n\n"

    if marriage:
        partner = await get_user(marriage["partner_id"])
        text += f"💍 Conjoint(e) : **{escape_md(partner['full_name'])}**\n"
        text += f"   💰 {fmt(partner['balance'])} | {get_title(partner['balance'])}\n\n"

    # Enfants adoptés
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT u.full_name, u.balance FROM adoptions a JOIN users u ON u.user_id=a.child_id WHERE a.parent_id=?",
            (user.id,)
        ) as cur:
            children = await cur.fetchall()
        async with db.execute(
            "SELECT u.full_name, u.balance FROM adoptions a JOIN users u ON u.user_id=a.parent_id WHERE a.child_id=?",
            (user.id,)
        ) as cur:
            parents = await cur.fetchall()

    if children:
        text += "👶 **Enfants adoptés :**\n"
        for c in children:
            text += f"  • {escape_md(c[0])} — {fmt(c[1])}\n"
        text += "\n"
    if parents:
        text += "👨‍👩 **Parents adoptifs :**\n"
        for p in parents:
            text += f"  • {escape_md(p[0])} — {fmt(p[1])}\n"
        text += "\n"

    if fam_row:
        text += f"👨‍👩‍👧 **Clan familial :** **{escape_md(fam_row['name'])}**\n"
        text += f"📋 Ton rôle : {escape_md(fam_row['role'])}\n\n"

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT u.full_name, u.balance, fm.role
                FROM family_members fm JOIN users u ON u.user_id=fm.user_id
                WHERE fm.family_id=?
            """, (fam_row["family_id"],)) as cur:
                members = await cur.fetchall()

        text += "👥 **Membres :**\n"
        for m in members:
            text += f"  • **{escape_md(m[0])}** ({escape_md(m[2])}) — {fmt(m[1])}\n"
    else:
        text += "_Tu n'as pas de clan familial.\n/creerfamille [nom] pour en créer un !_"

    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
async def cmd_famille(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les informations du clan familial (alias de /arbre)."""
    await cmd_arbre(update, context)


@require_registered
@require_free
async def cmd_creer_famille(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Créer un clan familial."""
    user = update.effective_user
    u = await get_user(user.id)

    if not context.args:
        await update.message.reply_text("Usage : /creerfamille [nom du clan]")
        return

    name = " ".join(context.args)[:40]
    FAMILY_COST = 30_000

    if u["balance"] < FAMILY_COST:
        await update.message.reply_text(
            f"❌ Créer un clan coûte **{fmt(FAMILY_COST)}**.\nSolde : {fmt(u['balance'])}",
            parse_mode="Markdown"
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT family_id FROM family_members WHERE user_id=?", (user.id,)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Tu es déjà membre d'un clan.")
                return
        async with db.execute("SELECT family_id FROM family WHERE LOWER(name)=LOWER(?)", (name,)) as cur:
            if await cur.fetchone():
                await update.message.reply_text(f"❌ Le nom **{escape_md(name)}** est déjà pris.", parse_mode="Markdown")
                return

        await db.execute(
            "INSERT INTO family (name, founder_id, created_at) VALUES (?,?,?)",
            (name, user.id, now())
        )
        async with db.execute("SELECT last_insert_rowid()") as cur:
            fam_id = (await cur.fetchone())[0]
        await db.execute(
            "INSERT INTO family_members (family_id, user_id, role, joined_at) VALUES (?,?,'Fondateur',?)",
            (fam_id, user.id, now())
        )
        await db.commit()

    await update_balance(user.id, -FAMILY_COST)
    await update.message.reply_text(
        f"👨‍👩‍👧 **Clan fondé !**\n\n"
        f"🏡 Nom : **{escape_md(name)}**\n"
        f"👑 Fondateur : {escape_md(user.full_name)}\n"
        f"💰 Coût : {fmt(FAMILY_COST)}\n\n"
        f"_Invite des membres avec /inviterfamille @joueur_",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_inviterfamille(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inviter un joueur à rejoindre son clan familial."""
    user = update.effective_user

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Réponds au message du joueur à inviter.")
        return

    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas t'inviter toi-même.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT f.family_id, f.name FROM family_members fm JOIN family f ON f.family_id=fm.family_id WHERE fm.user_id=?",
            (user.id,)
        ) as cur:
            family = await cur.fetchone()

    if not family:
        await update.message.reply_text("❌ Tu n'es dans aucun clan. /creerfamille pour en créer un.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT family_id FROM family_members WHERE user_id=?", (target.id,)
        ) as cur:
            if await cur.fetchone():
                await update.message.reply_text(f"❌ {escape_md(target.full_name)} est déjà dans un clan.")
                return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO family_invites (family_id, invited_id, invited_by, created_at) VALUES (?,?,?,?)",
            (family[0], target.id, user.id, now())
        )
        await db.commit()

    await update.message.reply_text(
        f"📨 Invitation envoyée à **{escape_md(target.full_name)}** pour rejoindre **{escape_md(family[1])}** !\n"
        f"Il doit utiliser `/rejoindrefamille {escape_md(family[1])}` pour accepter.",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_rejoindrefamille(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rejoindre un clan sur invitation."""
    user = update.effective_user

    if not context.args:
        await update.message.reply_text("Usage : /rejoindrefamille [nom du clan]")
        return

    family_name = " ".join(context.args)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT f.family_id, f.name FROM family f WHERE LOWER(f.name)=LOWER(?)",
            (family_name,)
        ) as cur:
            family = await cur.fetchone()

    if not family:
        await update.message.reply_text(f"❌ Clan **{escape_md(family_name)}** introuvable.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM family_invites WHERE family_id=? AND invited_id=?",
            (family["family_id"], user.id)
        ) as cur:
            invite = await cur.fetchone()

    if not invite:
        await update.message.reply_text(f"❌ Tu n'as pas d'invitation pour **{escape_md(family['name'])}**.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO family_members (family_id, user_id, role, joined_at) VALUES (?,?,'Membre',?)",
            (family["family_id"], user.id, now())
        )
        await db.execute("DELETE FROM family_invites WHERE family_id=? AND invited_id=?", (family["family_id"], user.id))
        await db.commit()

    await update.message.reply_text(
        f"🎉 Bienvenue dans le clan **{escape_md(family['name'])}** !\n"
        f"Utilise /famille pour voir tes nouveaux compagnons.",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_quitterfamille(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quitter son clan familial."""
    user = update.effective_user

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT f.family_id, f.name, fm.role FROM family_members fm JOIN family f ON f.family_id=fm.family_id WHERE fm.user_id=?",
            (user.id,)
        ) as cur:
            member = await cur.fetchone()

    if not member:
        await update.message.reply_text("❌ Tu n'es dans aucun clan.")
        return

    family_id, family_name, role = member

    if role == "Fondateur":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM family_members WHERE family_id=?", (family_id,)
            ) as cur:
                count = (await cur.fetchone())[0]
        if count > 1:
            await update.message.reply_text(
                "❌ Tu es le fondateur. Tu ne peux pas quitter sans dissoudre ou transférer.\n"
                "Utilise `/transfertclan @joueur` pour passer le leadership.\n"
                "Ou `/dissoudrefamille` pour dissoudre le clan."
            )
            return
        else:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM family WHERE family_id=?", (family_id,))
                await db.execute("DELETE FROM family_members WHERE family_id=?", (family_id,))
                await db.commit()
            await update.message.reply_text(f"💀 Le clan **{escape_md(family_name)}** a été dissous car tu étais le dernier membre.")
            return
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM family_members WHERE user_id=? AND family_id=?", (user.id, family_id))
            await db.commit()
        await update.message.reply_text(f"👋 Tu as quitté le clan **{escape_md(family_name)}**.", parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# Amis
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
@require_free
async def cmd_ami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Réponds au message du joueur à ajouter en ami.")
        return

    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas t'ajouter toi-même en ami !")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO friendships (user_id, friend_id, since) VALUES (?,?,?)",
            (user.id, target.id, now())
        )
        await db.commit()

    await update.message.reply_text(
        f"👫 **{escape_md(target.full_name)} ajouté(e) en ami !**\n"
        f"_Prenez soin l'un de l'autre dans ce monde._",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_mesamis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.full_name, u.balance, f.since
            FROM friendships f JOIN users u ON u.user_id=f.friend_id
            WHERE f.user_id=?
        """, (user.id,)) as cur:
            friends = await cur.fetchall()

    if not friends:
        await update.message.reply_text(
            "👥 Tu n'as aucun ami.\n"
            "Réponds au message de qqn et utilise /ami pour l'ajouter."
        )
        return

    text = f"👥 **Tes amis ({len(friends)})**\n\n"
    for f in friends:
        text += f"• **{escape_md(f['full_name'])}** — {fmt(f['balance'])} | {get_title(f['balance'])}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# Classement général
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await get_top_rich(20)
    medals = ["🥇", "🥈", "🥉"] + ["💰"] * 17
    text = "🏆 **CLASSEMENT GÉNÉRAL**\n\n"
    for i, r in enumerate(rows):
        name = r.get("full_name") or r.get("username") or f"Joueur#{r['user_id']}"
        name_escaped = escape_md(name)
        title = get_title(r["balance"])
        text += f"{medals[i]} **{name_escaped}** {title}\n   {fmt(r['balance'])}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# Adoption
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
@require_free
async def cmd_adopter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Réponds au message du joueur que tu veux adopter.")
        return

    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas t'adopter toi-même !")
        return

    if u.get("age", 0) < 25:
        await update.message.reply_text("❌ Tu dois avoir au moins 25 ans pour adopter.")
        return

    t_data = await get_user(target.id)
    if t_data.get("age", 0) >= 20:
        await update.message.reply_text("❌ Tu ne peux adopter qu'un mineur (moins de 20 ans).")
        return

    ADOPTION_COST = 50_000
    if u["balance"] < ADOPTION_COST:
        await update.message.reply_text(
            f"❌ L'adoption coûte **{fmt(ADOPTION_COST)}**.\nSolde actuel : {fmt(u['balance'])}",
            parse_mode="Markdown"
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM adoptions WHERE child_id=?", (target.id,)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Ce joueur a déjà été adopté.")
                return
        async with db.execute("SELECT COUNT(*) FROM adoptions WHERE parent_id=?", (user.id,)) as cur:
            count = (await cur.fetchone())[0]
        if count >= 5:
            await update.message.reply_text("❌ Tu ne peux adopter que 5 enfants maximum.")
            return

        await db.execute(
            "INSERT INTO adoptions (parent_id, child_id, adopted_at) VALUES (?,?,?)",
            (user.id, target.id, now())
        )
        await db.commit()

    await update_balance(user.id, -ADOPTION_COST)
    await update.message.reply_text(
        f"👨‍👩‍👧 **Adoption réussie !**\n\n"
        f"Tu as adopté **{escape_md(target.full_name)}** !\n"
        f"💰 Frais d'adoption : {fmt(ADOPTION_COST)}\n\n"
        f"_Vous êtes maintenant liés dans l'arbre généalogique._\n"
        f"Consulte /arbre pour voir ta famille.",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Transfert de leadership de clan
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
@require_free
async def cmd_transfertclan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Réponds au message du joueur à qui tu veux transférer le leadership.")
        return

    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te transférer à toi-même.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT f.family_id, f.name FROM family f WHERE f.founder_id=?",
            (user.id,)
        ) as cur:
            family = await cur.fetchone()

        if not family:
            await update.message.reply_text("❌ Tu n'es pas le fondateur d'un clan.")
            return

        async with db.execute(
            "SELECT * FROM family_members WHERE family_id=? AND user_id=?",
            (family[0], target.id)
        ) as cur:
            if not await cur.fetchone():
                await update.message.reply_text(f"❌ {escape_md(target.full_name)} n'est pas membre de ton clan.")
                return

        await db.execute("UPDATE family SET founder_id=? WHERE family_id=?", (target.id, family[0]))
        await db.execute("UPDATE family_members SET role='Fondateur' WHERE family_id=? AND user_id=?", (family[0], target.id))
        await db.execute("UPDATE family_members SET role='Ex-fondateur' WHERE family_id=? AND user_id=?", (family[0], user.id))
        await db.commit()

    await update.message.reply_text(
        f"👑 **Transfert de leadership**\n\n"
        f"**{escape_md(target.full_name)}** est maintenant le fondateur du clan **{escape_md(family[1])}**.\n"
        f"Tu deviens 'Ex-fondateur'.",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_dissoudrefamille(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT f.family_id, f.name FROM family f WHERE f.founder_id=?",
            (user.id,)
        ) as cur:
            family = await cur.fetchone()

    if not family:
        await update.message.reply_text("❌ Tu n'es pas le fondateur d'un clan.")
        return

    if not context.args or context.args[0].lower() != "confirmer":
        await update.message.reply_text(
            f"⚠️ **DANGER : Dissolution du clan {escape_md(family[1])}**\n\n"
            f"Cette action est IRRÉVERSIBLE !\n"
            f"Pour confirmer : /dissoudrefamille confirmer"
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM family WHERE family_id=?", (family[0],))
        await db.execute("DELETE FROM family_members WHERE family_id=?", (family[0],))
        await db.execute("DELETE FROM family_invites WHERE family_id=?", (family[0],))
        await db.commit()

    await update.message.reply_text(f"💀 Le clan **{escape_md(family[1])}** a été dissous.", parse_mode="Markdown")