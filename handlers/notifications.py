# handlers/notifications.py
import asyncio
import html
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, now
from utils.decorators import require_registered, admin_only
from utils.helpers import fmt_time

# ─────────────────────────────────────────────────────────────────────────────
# Types de notifications
# ─────────────────────────────────────────────────────────────────────────────
NOTIF_TYPES = {
    "info": "ℹ️",
    "success": "✅",
    "warning": "⚠️",
    "error": "❌",
    "event": "🌍",
    "reward": "🎁",
    "achievement": "🏆",
    "competition": "🏅",
    "guild": "🏰",
    "crime": "🔫",
    "economy": "💰",
    "social": "📱"
}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers (file d'attente)
# ─────────────────────────────────────────────────────────────────────────────
async def queue_notification(user_id: int, title: str, message: str, notif_type: str = "info", delay: int = 0):
    """Ajoute une notification à la file d'attente (différée)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO notifications (user_id, title, message, type, delay, created_at, sent)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (user_id, title, message, notif_type, delay, now()))
        await db.commit()

async def queue_broadcast_notification(title: str, message: str, notif_type: str = "info", delay: int = 0):
    """Ajoute une notification pour TOUS les utilisateurs (file d'attente)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE registered = 1 AND banned = 0") as cur:
            users = await cur.fetchall()
        for user in users:
            await db.execute("""
                INSERT INTO notifications (user_id, title, message, type, delay, created_at, sent)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            """, (user[0], title, message, notif_type, delay, now()))
        await db.commit()

async def get_pending_notifications(user_id: int, limit: int = 50) -> list:
    """Récupère les notifications non envoyées d'un utilisateur."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM notifications
            WHERE user_id = ? AND sent = 0 AND (delay = 0 OR created_at + delay < ?)
            ORDER BY created_at ASC
            LIMIT ?
        """, (user_id, now(), limit)) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def mark_notification_sent(notif_id: int):
    """Marque une notification comme envoyée."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE notifications SET sent = 1, sent_at = ? WHERE id = ?", (now(), notif_id))
        await db.commit()

async def get_notification_history(user_id: int, limit: int = 20) -> list:
    """Récupère l'historique des notifications envoyées."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM notifications
            WHERE user_id = ? AND sent = 1
            ORDER BY sent_at DESC
            LIMIT ?
        """, (user_id, limit)) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def clear_old_notifications(days: int = 30):
    """Supprime les notifications de plus de X jours."""
    cutoff = now() - (days * 86400)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM notifications WHERE sent = 1 AND sent_at < ?", (cutoff,))
        await db.commit()

# ─────────────────────────────────────────────────────────────────────────────
# Envoi périodique des notifications (à appeler dans le scheduler)
# ─────────────────────────────────────────────────────────────────────────────
async def process_notifications(bot):
    """Envoie les notifications en attente (appelé régulièrement)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Récupérer tous les utilisateurs ayant des notifications en attente
        async with db.execute("""
            SELECT DISTINCT user_id FROM notifications
            WHERE sent = 0 AND (delay = 0 OR created_at + delay < ?)
            LIMIT 100
        """, (now(),)) as cur:
            users = await cur.fetchall()
    
    for user_row in users:
        user_id = user_row["user_id"]
        pending = await get_pending_notifications(user_id, 5)  # Max 5 par envoi
        if not pending:
            continue
        
        # Grouper les notifications
        text = "📬 **Tes notifications**\n\n"
        for notif in pending[:5]:
            emoji = NOTIF_TYPES.get(notif["type"], "📌")
            text += f"{emoji} **{notif['title']}**\n{notif['message']}\n\n"
            await mark_notification_sent(notif["id"])
        
        if len(pending) > 5:
            text += f"_... et {len(pending) - 5} autres notifications_"
        
        try:
            await bot.send_message(user_id, text, parse_mode="Markdown")
        except Exception:
            pass  # Ignorer les erreurs (blocage, utilisateur bloqué, etc.)

# ─────────────────────────────────────────────────────────────────────────────
# Commandes utilisateur
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
async def cmd_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT title, message, created_at FROM notifications WHERE user_id=? AND sent=1 ORDER BY created_at DESC LIMIT 20",
            (user.id,)
        ) as cur:
            notifs = await cur.fetchall()
    if not notifs:
        await update.message.reply_text("📭 Aucune notification.")
        return
    text = "📬 <b>Notifications</b>\n\n"
    for n in notifs:
        title = html.escape(n["title"])
        msg = html.escape(n["message"])
        text += f"• <b>{title}</b> : {msg}\n"
        if len(text) > 4000:
            break
    await update.message.reply_text(text[:4000], parse_mode="HTML")

@require_registered
async def cmd_notifications_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche l'historique des notifications."""
    user = update.effective_user
    history = await get_notification_history(user.id, 20)
    
    if not history:
        await update.message.reply_text("📜 Aucun historique de notifications.")
        return
    
    text = "📜 <b>Historique des notifications</b>\n\n"
    for notif in history:
        emoji = NOTIF_TYPES.get(notif["type"], "📌")
        title = html.escape(notif["title"])
        message = html.escape(notif["message"])
        sent_at = fmt_time(notif["sent_at"])
        text += f"{emoji} <b>{title}</b>\n{message}\n<i>[{sent_at}]</i>\n\n"
    
    await update.message.reply_text(text[:4000], parse_mode="HTML")

@require_registered
async def cmd_notifications_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Supprime toutes les notifications en attente."""
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM notifications WHERE user_id = ? AND sent = 0", (user.id,))
        await db.commit()
    await update.message.reply_text("🗑️ Toutes tes notifications en attente ont été supprimées.")

# ─────────────────────────────────────────────────────────────────────────────
# Commandes admin
# ─────────────────────────────────────────────────────────────────────────────
@admin_only
async def cmd_broadcast_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envoie une notification à tous les utilisateurs (file d'attente)."""
    if not context.args:
        await update.message.reply_text(
            "Usage : `/broadcast_notify [titre] | [message]`\n"
            "Ex: `/broadcast_notify Maintenance | Le serveur redémarre dans 10 minutes`"
        )
        return
    
    args = " ".join(context.args)
    if " | " not in args:
        await update.message.reply_text("❌ Format invalide. Utilise `titre | message`")
        return
    
    title, message = args.split(" | ", 1)
    await queue_broadcast_notification(title[:50], message[:500], "info")
    await update.message.reply_text(f"✅ Notification mise en file d'attente pour tous les utilisateurs :\n**{title}**\n{message}", parse_mode="Markdown")

@admin_only
async def cmd_notify_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envoie une notification à un utilisateur spécifique (file d'attente)."""
    if not update.message.reply_to_message or len(context.args) < 2:
        await update.message.reply_text(
            "Usage : Répondre au message de l'utilisateur avec `/notify_user [titre] | [message]`"
        )
        return
    
    target = update.message.reply_to_message.from_user
    args = " ".join(context.args)
    if " | " not in args:
        await update.message.reply_text("❌ Format invalide. Utilise `titre | message`")
        return
    
    title, message = args.split(" | ", 1)
    await queue_notification(target.id, title[:50], message[:500], "info")
    await update.message.reply_text(f"✅ Notification mise en file d'attente pour {target.full_name} :\n**{title}**\n{message}", parse_mode="Markdown")

@admin_only
async def cmd_notifications_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les statistiques des notifications."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM notifications WHERE sent = 0") as cur:
            pending = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM notifications WHERE sent = 1") as cur:
            sent = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM notifications WHERE sent = 0") as cur:
            users_pending = (await cur.fetchone())[0]
    
    await update.message.reply_text(
        f"📊 **Statistiques des notifications**\n\n"
        f"⏳ En attente : {pending}\n"
        f"✅ Envoyées : {sent}\n"
        f"👥 Utilisateurs avec notifications : {users_pending}",
        parse_mode="Markdown"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Hooks pratiques pour les autres modules (utilisent la file d'attente)
# ─────────────────────────────────────────────────────────────────────────────
async def notify_achievement(user_id: int, achievement_name: str):
    await queue_notification(user_id, "Nouveau succès !", f"Tu as débloqué le succès **{achievement_name}** !", "achievement")

async def notify_competition_result(user_id: int, rank: int, reward: str):
    if rank == 1:
        title = "🥇 Victoire en compétition !"
    elif rank == 2:
        title = "🥈 Deuxième place en compétition"
    elif rank == 3:
        title = "🥉 Troisième place en compétition"
    else:
        title = "Compétition terminée"
    await queue_notification(user_id, title, f"Tu as terminé #{rank} et gagné {reward} !", "competition")

async def notify_guild_event(user_id: int, guild_name: str, event: str):
    await queue_notification(user_id, f"🏰 {guild_name}", event, "guild")

async def notify_economy(user_id: int, title: str, message: str):
    await queue_notification(user_id, title, message, "economy")

async def notify_crime(user_id: int, success: bool, reward: int = 0, jail_time: int = 0):
    if success:
        await queue_notification(user_id, "🔫 Crime réussi", f"Tu as gagné {reward} coins !", "crime")
    else:
        await queue_notification(user_id, "🔫 Crime échoué", f"Tu as été arrêté pour {fmt_time(jail_time)}.", "crime")