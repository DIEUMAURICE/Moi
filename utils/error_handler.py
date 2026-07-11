"""
utils/error_handler.py — Gestionnaire d'erreurs global
Branchement dans bot.py :
    from utils.error_handler import error_handler
    app.add_error_handler(error_handler)
"""
import html
import logging
import traceback

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import ADMIN_IDS

logger = logging.getLogger(__name__)

USER_ERROR_MESSAGE = (
    "😬 Oups, une erreur est survenue de mon côté. "
    "L'incident a été signalé. Réessaie dans un instant."
)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception non gérée :", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(USER_ERROR_MESSAGE)
        except Exception:
            logger.exception("Impossible d'envoyer le message d'erreur à l'utilisateur")

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    user = update.effective_user if isinstance(update, Update) else None
    header = "⚠️ <b>Erreur dans le bot</b>\n"
    if user:
        header += f"👤 {user.id} (@{user.username or '—'})\n"

    message = f"{header}<pre>{html.escape(tb_string[-3500:])}</pre>"

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=message, parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception("Impossible d'alerter l'admin %s", admin_id)