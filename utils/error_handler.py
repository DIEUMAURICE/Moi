# utils/error_handler.py
import traceback
import logging
from telegram import Update
from telegram.ext import ContextTypes
from config import ADMIN_IDS

logger = logging.getLogger(__name__)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log l'erreur et notifie les admins."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)
    
    # Notifier les admins
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"❌ **Erreur dans le bot**\n\n"
                f"Update: {update}\n"
                f"Error: {context.error}\n\n"
                f"```\n{tb_string[:3500]}\n```",
                parse_mode="Markdown"
            )
        except:
            pass