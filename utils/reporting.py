# handlers/reporting.py
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, now, add_notification
from utils.decorators import require_registered, admin_only, cooldown
from utils.helpers import fmt_time

# ─────────────────────────────────────────────────────────────────────────────
# Types de reports
# ─────────────────────────────────────────────────────────────────────────────
REPORT_TYPES = {
    "bug": {
        "name": "🐛 Bug / Erreur",
        "emoji": "🐛",
        "priority": 1,
        "auto_response": "Merci d'avoir signalé ce bug ! Notre équipe va l'examiner."
    },
    "suggestion": {
        "name": "💡 Suggestion / Idée",
        "emoji": "💡",
        "priority": 2,
        "auto_response": "Merci pour ta suggestion ! Nous l'étudions avec attention."
    },
    "exploit": {
        "name": "⚠️ Exploit / Triche",
        "emoji": "⚠️",
        "priority": 0,  # priorité haute
        "auto_response": "Merci d'avoir signalé cet exploit. Une enquête va être menée."
    },
    "harassment": {
        "name": "🚫 Harcèlement / Toxicité",
        "emoji": "🚫",
        "priority": 0,
        "auto_response": "Nous prenons ce signalement très au sérieux. Des mesures seront prises."
    },
    "other": {
        "name": "📝 Autre",
        "emoji": "📝",
        "priority": 2,
        "auto_response": "Merci pour ton retour !"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
async def add_report(user_id: int, report_type: str, message: str, target_id: int = None):
    """Ajoute un report en base."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO reports (user_id, report_type, message, target_id, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
        """, (user_id, report_type, message, target_id, now()))
        await db.commit()

async def get_reports(status: str = "pending", limit: int = 50) -> list:
    """Récupère les reports selon leur statut."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT r.*, u.full_name as reporter_name
            FROM reports r
            JOIN users u ON u.user_id = r.user_id
            WHERE r.status = ?
            ORDER BY r.created_at ASC
            LIMIT ?
        """, (status, limit)) as cur:
            return [dict(row) for row in await cur.fetchall()]

async def get_user_reports(user_id: int, limit: int = 20) -> list:
    """Récupère les reports d'un utilisateur."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM reports
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit)) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def has_recent_target_report(user_id: int, target_id: int, seconds: int = 3600) -> bool:
    """Vérifie si l'utilisateur a déjà signalé cette cible récemment."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT 1
            FROM reports
            WHERE user_id = ? AND target_id = ? AND created_at >= ?
            LIMIT 1
            """,
            (user_id, target_id, now() - seconds)
        ) as cur:
            return await cur.fetchone() is not None

async def update_report_status(report_id: int, status: str, admin_response: str = None):
    """Met à jour le statut d'un report."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE reports
            SET status = ?, admin_response = ?, resolved_at = ?
            WHERE id = ?
        """, (status, admin_response, now(), report_id))
        await db.commit()

async def get_report_stats() -> dict:
    """Récupère les statistiques des reports."""
    async with aiosqlite.connect(DB_PATH) as db:
        stats = {}
        for status in ["pending", "in_progress", "resolved", "rejected"]:
            async with db.execute("SELECT COUNT(*) FROM reports WHERE status = ?", (status,)) as cur:
                stats[status] = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM reports WHERE created_at > ?", (now() - 86400 * 7,)) as cur:
            stats["last_7_days"] = (await cur.fetchone())[0]
        return stats

# ─────────────────────────────────────────────────────────────────────────────
# Commandes utilisateur
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@cooldown("report_cooldown", 300, "⏳ Tu as déjà envoyé un signalement récemment. Attends 5 minutes.")
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envoie un signalement (bug, suggestion, exploit)."""
    user = update.effective_user
    
    # Si un type a été sélectionné via callback, on le stocke dans context.user_data
    temp_type = context.user_data.get("temp_report_type")
    if temp_type and temp_type in REPORT_TYPES:
        # L'utilisateur a déjà choisi un type, on attend le message
        if not context.args:
            await update.message.reply_text(
                f"{REPORT_TYPES[temp_type]['emoji']} **{REPORT_TYPES[temp_type]['name']}**\n\n"
                "Envoie maintenant le détail de ton signalement en une seule ligne :\n"
                f"`/report {temp_type} [ton message]`\n\n"
                "Exemple : `/report bug Le bouton 'travailler' ne fonctionne pas`",
                parse_mode="Markdown"
            )
            return
        else:
            # L'utilisateur a tapé /report avec le message directement, on ignore le premier argument si c'est le type
            # car il a déjà été choisi. On prend tout le message.
            message = " ".join(context.args)
            if not message:
                await update.message.reply_text("❌ Tu dois fournir un message.")
                return
            report_type = temp_type
            # Nettoyer le stockage
            del context.user_data["temp_report_type"]
            await add_report(user.id, report_type, message)
            response = REPORT_TYPES[report_type]["auto_response"]
            await update.message.reply_text(
                f"{REPORT_TYPES[report_type]['emoji']} **Signalement envoyé !**\n\n"
                f"{response}\n\n"
                f"_ID: #(auto)_\n"
                f"Tu peux suivre l'état de ton signalement avec `/myreports`.",
                parse_mode="Markdown"
            )
            return
    
    # Sinon, pas de type temporaire : afficher les options
    if not context.args:
        keyboard = []
        for rt, data in REPORT_TYPES.items():
            keyboard.append([InlineKeyboardButton(data["emoji"] + " " + data["name"], callback_data=f"report_type_{rt}")])
        keyboard.append([InlineKeyboardButton("❌ Annuler", callback_data="report_cancel")])
        
        await update.message.reply_text(
            "📋 **Signaler un problème ou proposer une idée**\n\n"
            "Choisis le type de signalement :",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return
    
    # Si des arguments sont fournis directement (format: /report type message)
    report_type = context.args[0].lower()
    if report_type not in REPORT_TYPES:
        await update.message.reply_text(f"❌ Type invalide. Types : {', '.join(REPORT_TYPES.keys())}")
        return
    
    message = " ".join(context.args[1:])
    if not message:
        await update.message.reply_text("❌ Tu dois fournir un message.")
        return
    
    await add_report(user.id, report_type, message)
    response = REPORT_TYPES[report_type]["auto_response"]
    await update.message.reply_text(
        f"{REPORT_TYPES[report_type]['emoji']} **Signalement envoyé !**\n\n"
        f"{response}\n\n"
        f"_ID: #(auto)_\n"
        f"Tu peux suivre l'état de ton signalement avec `/myreports`.",
        parse_mode="Markdown"
    )

@require_registered
async def cmd_report_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Signale un autre joueur (harcèlement, triche, etc.)."""
    user = update.effective_user
    
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "❌ Réponds au message du joueur à signaler.\n\n"
            "Usage : `/report_user [raison]` (en répondant au message du joueur)"
        )
        return
    
    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te signaler toi-même.")
        return
    if await has_recent_target_report(user.id, target.id):
        await update.message.reply_text(
            "⏳ Tu as déjà signalé ce joueur récemment.\n"
            "Attends 1 heure avant d'envoyer un nouveau signalement contre la même cible."
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ Tu dois fournir une raison.\n\n"
            "Usage : `/report_user [raison]`\n"
            "Ex: `/report_user Triche confirmée - vitesses anormales`"
        )
        return
    
    reason = " ".join(context.args)
    await add_report(user.id, "harassment" if "harcè" in reason.lower() or "insulte" in reason.lower() else "exploit", reason, target.id)
    
    await update.message.reply_text(
        f"⚠️ **Signalement envoyé contre {target.full_name}**\n\n"
        f"Raison : {reason}\n\n"
        f"L'équipe de modération va examiner la situation.",
        parse_mode="Markdown"
    )

@require_registered
async def cmd_myreports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche l'historique des signalements de l'utilisateur."""
    user = update.effective_user
    reports = await get_user_reports(user.id, 20)
    
    if not reports:
        await update.message.reply_text("📋 Tu n'as envoyé aucun signalement.")
        return
    
    status_emojis = {
        "pending": "⏳",
        "in_progress": "🔨",
        "resolved": "✅",
        "rejected": "❌"
    }
    
    text = "📋 **Mes signalements**\n\n"
    for r in reports:
        data = REPORT_TYPES.get(r["report_type"], {"name": r["report_type"], "emoji": "📝"})
        status_emoji = status_emojis.get(r["status"], "❓")
        date = fmt_time(r["created_at"])
        text += f"{data['emoji']} **{data['name']}** {status_emoji}\n"
        text += f"📅 {date}\n"
        text += f"📝 {r['message'][:100]}\n"
        if r["admin_response"]:
            text += f"💬 Réponse : {r['admin_response'][:100]}\n"
        text += "\n"
    
    await update.message.reply_text(text[:4000], parse_mode="Markdown")

# ─────────────────────────────────────────────────────────────────────────────
# Commandes admin
# ─────────────────────────────────────────────────────────────────────────────
@admin_only
async def cmd_reports_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche la liste des signalements en attente."""
    reports = await get_reports("pending", 20)
    
    if not reports:
        await update.message.reply_text("📋 Aucun signalement en attente.")
        return
    
    text = "📋 **Signalements en attente**\n\n"
    for r in reports:
        data = REPORT_TYPES.get(r["report_type"], {"name": r["report_type"], "emoji": "📝"})
        date = fmt_time(r["created_at"])
        text += f"**ID #{r['id']}** | {data['emoji']} {data['name']}\n"
        text += f"👤 Par : {r['reporter_name']}\n"
        text += f"📅 {date}\n"
        text += f"📝 {r['message'][:150]}\n"
        if r["target_id"]:
            text += f"🎯 Cible : ID {r['target_id']}\n"
        text += "\n"
    
    keyboard = []
    for r in reports[:5]:
        keyboard.append([InlineKeyboardButton(f"📌 #{r['id']} - {r['report_type']}", callback_data=f"admin_report_{r['id']}")])
    keyboard.append([InlineKeyboardButton("📊 Statistiques", callback_data="admin_report_stats")])
    
    await update.message.reply_text(
        text[:3500],
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
    )

@admin_only
async def cmd_report_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Traite un signalement (répondre, changer statut)."""
    if not context.args:
        await update.message.reply_text(
            "Usage : `/report_handle [id] [status] [réponse optionnelle]`\n\n"
            "Statuts : pending, in_progress, resolved, rejected\n"
            "Ex: `/report_handle 42 resolved Merci pour ton signalement, nous avons corrigé le bug.`"
        )
        return
    
    try:
        report_id = int(context.args[0])
        status = context.args[1].lower()
        response = " ".join(context.args[2:]) if len(context.args) > 2 else None
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Format invalide.")
        return
    
    if status not in ["pending", "in_progress", "resolved", "rejected"]:
        await update.message.reply_text("❌ Statut invalide.")
        return
    
    await update_report_status(report_id, status, response)
    await update.message.reply_text(f"✅ Signalement #{report_id} mis à jour : **{status}**")
    
    # Notifier l'utilisateur
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, message FROM reports WHERE id = ?", (report_id,)) as cur:
            report = await cur.fetchone()
    if report and response:
        # Utiliser la fonction add_notification de database.py (2 arguments)
        await add_notification(
            report[0],
            f"📢 Mise à jour de votre signalement #{report_id}\nStatut : **{status}**\n{response}"
        )

@admin_only
async def cmd_reports_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les statistiques des signalements."""
    stats = await get_report_stats()
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT report_type, COUNT(*) as count
            FROM reports
            GROUP BY report_type
            ORDER BY count DESC
        """) as cur:
            by_type = await cur.fetchall()
    
    text = "📊 **Statistiques des signalements**\n\n"
    text += f"⏳ En attente : {stats['pending']}\n"
    text += f"🔨 En cours : {stats['in_progress']}\n"
    text += f"✅ Résolus : {stats['resolved']}\n"
    text += f"❌ Rejetés : {stats['rejected']}\n"
    text += f"📅 7 derniers jours : {stats['last_7_days']}\n\n"
    text += "**Par type :**\n"
    for rt, count in by_type:
        data = REPORT_TYPES.get(rt, {"name": rt, "emoji": "📝"})
        text += f"{data['emoji']} {data['name']} : {count}\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")

# ─────────────────────────────────────────────────────────────────────────────
# Callbacks
# ─────────────────────────────────────────────────────────────────────────────
async def report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère les callbacks des signalements."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    
    if data.startswith("report_type_"):
        report_type = data.replace("report_type_", "")
        # Stocker temporairement en context.user_data
        context.user_data["temp_report_type"] = report_type
        await query.edit_message_text(
            f"{REPORT_TYPES[report_type]['emoji']} **{REPORT_TYPES[report_type]['name']}**\n\n"
            "Envoie maintenant le détail de ton signalement :\n"
            f"`/report {report_type} [ton message]`\n\n"
            "Exemple : `/report bug Le bouton 'travailler' ne fonctionne pas`",
            parse_mode="Markdown"
        )
    
    elif data == "report_cancel":
        await query.edit_message_text("❌ Signalement annulé.")
    
    elif data.startswith("admin_report_"):
        report_id = int(data.replace("admin_report_", ""))
        context.user_data["temp_report_id"] = report_id
        await query.edit_message_text(
            f"📌 **Signalement #{report_id}**\n\n"
            "Actions disponibles :\n"
            "- `/report_handle [id] pending` – remettre en attente\n"
            "- `/report_handle [id] in_progress` – marquer en cours\n"
            "- `/report_handle [id] resolved [réponse]` – résoudre\n"
            "- `/report_handle [id] rejected [raison]` – rejeter\n\n"
            "Exemple : `/report_handle 42 resolved Merci !`",
            parse_mode="Markdown"
        )
    
    elif data == "admin_report_stats":
        stats = await get_report_stats()
        await query.edit_message_text(
            f"📊 **Statistiques**\n\n"
            f"En attente : {stats['pending']}\n"
            f"En cours : {stats['in_progress']}\n"
            f"Résolus : {stats['resolved']}\n"
            f"Rejetés : {stats['rejected']}\n"
            f"7 derniers jours : {stats['last_7_days']}",
            parse_mode="Markdown"
        )
