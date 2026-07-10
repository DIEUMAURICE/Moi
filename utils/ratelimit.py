# utils/ratelimit.py
import time
from collections import defaultdict
from typing import Tuple, Optional
from telegram import Update
from telegram.ext import ContextTypes

# Structure : user_id -> liste de timestamps des commandes récentes
_cmd_history = defaultdict(list)

# Limite : 30 commandes par tranche de 60 secondes
RATE_LIMIT = 30
RATE_WINDOW = 60  # secondes

def is_rate_limited(user_id: int) -> Tuple[bool, Optional[int]]:
    """
    Vérifie si l'utilisateur dépasse la limite.
    Retourne (True, seconds_to_wait) si limité, sinon (False, None).
    """
    now_ts = int(time.time())
    # Nettoyer les entrées hors fenêtre
    _cmd_history[user_id] = [ts for ts in _cmd_history[user_id] if ts > now_ts - RATE_WINDOW]
    if len(_cmd_history[user_id]) >= RATE_LIMIT:
        # Calculer le temps d'attente jusqu'à ce que la plus ancienne expire
        oldest = min(_cmd_history[user_id])
        wait = RATE_WINDOW - (now_ts - oldest)
        return True, max(1, wait)
    _cmd_history[user_id].append(now_ts)
    return False, None

async def rate_limit_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Middleware à appeler au début de chaque handler.
    Retourne True si la commande doit être exécutée, False si elle doit être ignorée.
    """
    if not update.effective_user:
        return True
    user_id = update.effective_user.id
    limited, wait = is_rate_limited(user_id)
    if limited:
        if update.message:
            await update.message.reply_text(
                f"⏳ **Trop de commandes !**\n"
                f"Attends encore **{wait} secondes** avant de réutiliser le bot.",
                parse_mode="Markdown"
            )
        return False
    return True