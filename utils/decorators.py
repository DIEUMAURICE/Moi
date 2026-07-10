import functools
from telegram import Update
from telegram.ext import ContextTypes
from database import get_user
from utils.helpers import now, fmt_time, cooldown_remaining
from config import ADMIN_IDS
from utils.simulation import apply_passive_simulation
from utils.ratelimit import rate_limit_middleware


async def _reply(update: Update, text: str, parse_mode: str | None = None):
    if update.message:
        await update.message.reply_text(text, parse_mode=parse_mode)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, parse_mode=parse_mode)


def require_registered(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user:
            return
        u = await get_user(user.id, user.username or "", user.full_name or "")
        if u.get("banned"):
            await _reply(update, "🚫 Tu as été banni du jeu.")
            return
        if not u.get("registered"):
            await _reply(
                update,
                "<b>Bienvenue dans LifeSim Ultra</b>\n"
                "Ton personnage existe, mais sa vie n'a pas encore commencé.\n"
                "Tape <code>/start</code> pour lancer ta simulation et débloquer toutes les actions.",
                parse_mode="HTML",
            )
            return

        sim_result = await apply_passive_simulation(user.id)
        if sim_result.get("alert"):
            await _reply(update, sim_result["alert"], parse_mode="HTML")
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_free(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        u = await get_user(user.id)
        # Exemption pour les admins et le god mode
        if user.id in ADMIN_IDS or u.get("god_mode"):
            return await func(update, context, *args, **kwargs)
        t = now()
        if u.get("prison_until", 0) > t:
            rem = u["prison_until"] - t
            await _reply(
                update,
                f"⛓️ Tu es en prison ! Libération dans <b>{fmt_time(rem)}</b>\n"
                f"💰 Caution : /caution | ⚖️ Tribunal : /tribunal",
                parse_mode="HTML"
            )
            return
        if u.get("hospital_until", 0) > t:
            rem = u["hospital_until"] - t
            await _reply(
                update,
                f"🏥 Tu es à l'hôpital ! Sortie dans <b>{fmt_time(rem)}</b>",
                parse_mode="HTML"
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def cooldown(field: str, duration: int, error_msg: str = None):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user = update.effective_user
            # Exemption pour les admins et le god mode
            u = await get_user(user.id)
            if user.id in ADMIN_IDS or u.get("god_mode"):
                return await func(update, context, *args, **kwargs)
            rem = cooldown_remaining(u.get(field, 0), duration)
            if rem > 0:
                msg = error_msg or f"⏳ Cooldown ! Attends encore <b>{fmt_time(rem)}</b>."
                await _reply(update, msg, parse_mode="HTML")
                return
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator


def admin_only(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        u = await get_user(user.id)
        if user.id not in ADMIN_IDS and not u.get("god_mode"):
            await _reply(update, "🚫 Accès réservé aux admins.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_min_balance(amount: int):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user = update.effective_user
            u = await get_user(user.id)
            if user.id in ADMIN_IDS or u.get("god_mode"):
                return await func(update, context, *args, **kwargs)
            if u["balance"] < amount:
                from utils.helpers import fmt
                await _reply(
                    update,
                    f"❌ Fonds insuffisants. Il te faut au moins <b>{fmt(amount)}</b>.\n"
                    f"💰 Ton solde : {fmt(u['balance'])}",
                    parse_mode="HTML"
                )
                return
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator


def rate_limited(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not await rate_limit_middleware(update, context):
            return
        return await func(update, context, *args, **kwargs)
    return wrapper
