# handlers/casino.py — Casino interactif complet (1xBet style)
import random
import asyncio
import time
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, update_field, increment_field
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, now, parse_amount
from config import (
    CASINO_MIN_BET, CASINO_MAX_BET, ROULETTE_NUMBERS,
    TIME_MULTIPLIER, CASINO_OPEN_HOUR, CASINO_CLOSE_HOUR
)
from handlers.missions import update_mission_progress
# ─────────────────────────────────────────────────────────────────────────────
# Gestion du temps de jeu et horaires du casino
# ─────────────────────────────────────────────────────────────────────────────

def get_game_time() -> tuple[int, int, int]:
    real_seconds = int(time.time())
    game_seconds = real_seconds * TIME_MULTIPLIER
    day_seconds = game_seconds % 86_400
    hours = day_seconds // 3600
    minutes = (day_seconds % 3600) // 60
    return day_seconds, hours, minutes

def is_casino_open() -> bool:
    _, hours, _ = get_game_time()
    if CASINO_OPEN_HOUR <= hours < 24:
        return True
    if 0 <= hours < CASINO_CLOSE_HOUR:
        return True
    return False

def get_casino_status_text() -> str:
    _, hours, minutes = get_game_time()
    game_time_str = f"{hours:02d}:{minutes:02d}"
    open_time = f"{CASINO_OPEN_HOUR:02d}:00"
    close_time = f"{CASINO_CLOSE_HOUR:02d}:00"

    if is_casino_open():
        current_seconds = hours * 3600 + minutes * 60
        close_seconds = CASINO_CLOSE_HOUR * 3600
        if close_seconds <= current_seconds:
            close_seconds += 86400
        remaining_game_seconds = close_seconds - current_seconds
        remaining_real_seconds = remaining_game_seconds / TIME_MULTIPLIER
        remaining_real_minutes = int(remaining_real_seconds // 60)
        if remaining_real_minutes < 1:
            remaining_real_minutes = 1
        status = f"🟢 **OUVERT**\nFerme dans environ **{remaining_real_minutes} min** (heure réelle)."
    else:
        current_seconds = hours * 3600 + minutes * 60
        open_seconds = CASINO_OPEN_HOUR * 3600
        if open_seconds <= current_seconds:
            open_seconds += 86400
        remaining_game_seconds = open_seconds - current_seconds
        remaining_real_seconds = remaining_game_seconds / TIME_MULTIPLIER
        remaining_real_minutes = int(remaining_real_seconds // 60)
        if remaining_real_minutes < 1:
            remaining_real_minutes = 1
        status = f"🔴 **FERMÉ**\nOuvre dans environ **{remaining_real_minutes} min** (heure réelle)."

    return (
        f"🎰 **Casino**\n"
        f"Heure de jeu : **{game_time_str}**\n"
        f"Horaires : **{open_time} → {close_time}** (heure du jeu)\n"
        f"{status}"
    )

@require_registered
async def cmd_casino(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_casino_status_text(), parse_mode="Markdown")

# ─────────────────────────────────────────────────────────────────────────────
# Helpers communs
# ─────────────────────────────────────────────────────────────────────────────

async def add_random_item(user_id: int, min_rarity: str = "common", max_rarity: str = "legendary"):
    rarity_order = ["common", "rare", "epic", "legendary"]
    min_idx = rarity_order.index(min_rarity)
    max_idx = rarity_order.index(max_rarity)
    possible = [r for r in rarity_order if min_idx <= rarity_order.index(r) <= max_idx]
    chosen_rarity = random.choice(possible)
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT item_id, name, emoji, rarity FROM items WHERE rarity = ? ORDER BY RANDOM() LIMIT 1",
            (chosen_rarity,)
        ) as cur:
            item = await cur.fetchone()
        if not item:
            return None
        
        async with db.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item["item_id"])
        ) as cur2:
            existing = await cur2.fetchone()
        if existing:
            await db.execute(
                "UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_id = ?",
                (user_id, item["item_id"])
            )
        else:
            await db.execute("""
                INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, item["item_id"], "loot", item["name"], 1, now()))
        await db.commit()
    return f"{item['emoji']} {item['name']} ({item['rarity']})"

def validate_bet(u: dict, args: list) -> tuple[int | None, str | None]:
    if not args:
        return None, f"Usage : commande [mise]\nMise min : {fmt(CASINO_MIN_BET)} | Max : {fmt(CASINO_MAX_BET)}"
    amount = parse_amount(args[0], u["balance"])
    if not amount or amount < CASINO_MIN_BET:
        return None, f"❌ Mise minimum : {fmt(CASINO_MIN_BET)}"
    if amount > CASINO_MAX_BET:
        return None, f"❌ Mise maximum : {fmt(CASINO_MAX_BET)}"
    if amount > u["balance"]:
        return None, f"❌ Fonds insuffisants ! Solde : {fmt(u['balance'])}"
    return amount, None

async def track_casino(user_id: int, bet: int, win: int):
    await increment_field(user_id, "casino_total_bet", bet)
    if win > 0:
        await increment_field(user_id, "casino_total_win", win)
        # ✅ Mise à jour de la mission "Gagner 30K en jeux"
        await update_mission_progress(user_id, "casino_win", win)

# ─────────────────────────────────────────────────────────────────────────────
# Helper Blackjack
# ─────────────────────────────────────────────────────────────────────────────
def card() -> str:
    suits = ["♠", "♥", "♦", "♣"]
    ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    return random.choice(ranks) + random.choice(suits)

def hand_value(hand: list) -> int:
    total = 0
    aces = 0
    for c in hand:
        r = c[:-1]
        if r in ["J", "Q", "K"]:
            total += 10
        elif r == "A":
            total += 11
            aces += 1
        else:
            total += int(r)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total

# ─────────────────────────────────────────────────────────────────────────────
# Dictionnaire pour stocker les parties en cours (callbacks)
# ─────────────────────────────────────────────────────────────────────────────
active_games = {}  # clé: (user_id, message_id)

# ─────────────────────────────────────────────────────────────────────────────
# 1. SLOTS (interactif)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_casino_open():
        await update.message.reply_text(get_casino_status_text(), parse_mode="Markdown")
        return

    user = update.effective_user
    u = await get_user(user.id)
    amount, err = validate_bet(u, context.args)
    if err:
        await update.message.reply_text(err)
        return

    await update_balance(user.id, -amount)

    keyboard = [[InlineKeyboardButton("🎰 SPIN", callback_data=f"slots_spin:{user.id}:{amount}")]]
    msg = await update.message.reply_text(
        f"🎰 **Machine à Sous**\n\n"
        f"💰 Mise : {fmt(amount)}\n"
        f"Appuie sur SPIN pour lancer les rouleaux.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    active_games[(user.id, msg.message_id)] = {
        "game": "slots",
        "amount": amount,
        "user_id": user.id,
        "message_id": msg.message_id,
        "chat_id": msg.chat_id,
    }

async def slots_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split(":")
    if data[0] != "slots_spin":
        return
    user_id = int(data[1])
    amount = int(data[2])

    if query.from_user.id != user_id:
        await query.answer("Ce n'est pas ton jeu !", show_alert=True)
        return

    game_key = (user_id, query.message.message_id)
    game = active_games.get(game_key)
    if not game:
        await query.answer("Partie expirée.", show_alert=True)
        return

    SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "🔔", "💎", "7️⃣", "🌟", "🍀", "💰"]
    WEIGHTS = [20, 20, 15, 15, 10, 5, 5, 3, 4, 3]
    reels = [random.choices(SYMBOLS, weights=WEIGHTS)[0] for _ in range(5)]

    win = 0
    unique = set(reels)
    if len(unique) == 1:
        mult = 50
    elif reels[0] == reels[1] == reels[2]:
        mult = 10
    elif len(unique) == 2 and max(reels.count(s) for s in unique) >= 4:
        mult = 8
    elif len(unique) <= 2:
        mult = 5
    elif len(unique) == 3:
        mult = 2
    elif "💎" in reels or "7️⃣" in reels:
        mult = 1
    else:
        mult = 0

    win = int(amount * mult)
    if win > 0:
        await update_balance(user_id, win)
    await track_casino(user_id, amount, win)

    loot_msg = ""
    if win > 0 and random.random() < 0.2:
        item = await add_random_item(user_id, "common", "epic")
        if item:
            loot_msg = f"\n\n🎁 Tu as gagné un item : {item} !"

    result_text = " | ".join(reels)
    if mult >= 50:
        msg = "🎉 **JACKPOT ABSOLU !!!**"
    elif mult >= 10:
        msg = "🎉 **JACKPOT !!!**"
    elif mult >= 5:
        msg = "🎉 **Gros gain !**"
    elif mult >= 2:
        msg = "✅ **Petit gain !**"
    elif mult == 1:
        msg = "💫 **Mise récupérée !**"
    else:
        msg = "❌ **Perdu !**"

    await query.edit_message_text(
        f"🎰 **Machine à Sous**\n\n"
        f"[ {result_text} ]\n\n"
        f"{msg}\n"
        f"💰 Mise : {fmt(amount)} | "
        f"{'💵 Gain : ' + fmt(win) if win > 0 else '💸 Perdu : ' + fmt(amount)}\n"
        f"💵 Solde : {fmt((await get_user(user_id))['balance'])}{loot_msg}",
        parse_mode="Markdown",
        reply_markup=None
    )
    active_games.pop(game_key, None)

# ─────────────────────────────────────────────────────────────────────────────
# 2. BLACKJACK (interactif)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_blackjack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_casino_open():
        await update.message.reply_text(get_casino_status_text(), parse_mode="Markdown")
        return

    user = update.effective_user
    u = await get_user(user.id)
    amount, err = validate_bet(u, context.args)
    if err:
        await update.message.reply_text(err)
        return

    player = [card(), card()]
    dealer = [card(), card()]
    pv = hand_value(player)

    if pv == 21:
        await update_balance(user.id, -amount)
        win = int(amount * 1.5)
        await update_balance(user.id, win)
        await track_casino(user.id, amount, win)
        loot_msg = ""
        if random.random() < 0.3:
            item = await add_random_item(user.id, "rare", "legendary")
            if item:
                loot_msg = f"\n\n🎁 Tu as gagné un item : {item} !"
        await update.message.reply_text(
            f"🃏 **BLACKJACK !** Naturel !\n\n"
            f"Ton jeu : {' '.join(player)} = 21\n"
            f"Croupier : {dealer[0]} ?\n\n"
            f"🎉 **+{fmt(win)} (x1.5)**{loot_msg}",
            parse_mode="Markdown"
        )
        return

    await update_balance(user.id, -amount)

    keyboard = [
        [InlineKeyboardButton("🃏 Tirer", callback_data=f"bj_hit:{user.id}:{amount}:{','.join(player)}:{','.join(dealer)}"),
         InlineKeyboardButton("✋ Rester", callback_data=f"bj_stand:{user.id}:{amount}:{','.join(player)}:{','.join(dealer)}"),
         InlineKeyboardButton("💥 Doubler", callback_data=f"bj_double:{user.id}:{amount}:{','.join(player)}:{','.join(dealer)}")]
    ]

    await update.message.reply_text(
        f"🃏 **Blackjack — Mise : {fmt(amount)}**\n\n"
        f"Ton jeu : {' '.join(player)} = {pv}\n"
        f"Croupier : {dealer[0]} ?\n\n"
        f"Que fais-tu ?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def bj_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[0]
    user_id = int(parts[1])
    amount = int(parts[2])
    player = parts[3].split(",")
    dealer_hand = parts[4].split(",")

    if query.from_user.id != user_id:
        await query.answer("Ce n'est pas ton jeu !", show_alert=True)
        return

    u = await get_user(user_id)

    if action == "bj_hit":
        player.append(card())
        pv = hand_value(player)
        if pv > 21:
            await query.edit_message_text(
                f"🃏 **Blackjack — Bust !**\n\n"
                f"Ton jeu : {' '.join(player)} = {pv}\n\n"
                f"❌ Tu dépasses 21 ! Perdu {fmt(amount)}.",
                parse_mode="Markdown"
            )
            return

        keyboard = [
            [InlineKeyboardButton("🃏 Tirer", callback_data=f"bj_hit:{user_id}:{amount}:{','.join(player)}:{','.join(dealer_hand)}"),
             InlineKeyboardButton("✋ Rester", callback_data=f"bj_stand:{user_id}:{amount}:{','.join(player)}:{','.join(dealer_hand)}")]
        ]
        await query.edit_message_text(
            f"🃏 **Blackjack**\n\nTon jeu : {' '.join(player)} = {pv}\nCroupier : {dealer_hand[0]} ?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif action in ("bj_stand", "bj_double"):
        if action == "bj_double":
            if u["balance"] < amount:
                await query.answer("Fonds insuffisants pour doubler !", show_alert=True)
                return
            await update_balance(user_id, -amount)
            amount *= 2
            player.append(card())

        pv = hand_value(player)
        while hand_value(dealer_hand) < 17:
            dealer_hand.append(card())
        dv = hand_value(dealer_hand)

        result = ""
        if pv > 21:
            result = "❌ Bust ! Perdu."
            win = 0
        elif dv > 21 or pv > dv:
            result = "✅ Tu gagnes !"
            win = amount * 2
            await update_balance(user_id, win)
        elif pv == dv:
            result = "🤝 Égalité !"
            win = amount
            await update_balance(user_id, win)
        else:
            result = "❌ Le croupier gagne."
            win = 0

        await track_casino(user_id, amount, win)

        loot_msg = ""
        if win > 0 and random.random() < 0.25:
            item = await add_random_item(user_id, "common", "epic")
            if item:
                loot_msg = f"\n\n🎁 Tu as gagné un item : {item} !"

        await query.edit_message_text(
            f"🃏 **Blackjack — Résultat**\n\n"
            f"Ton jeu : {' '.join(player)} = {pv}\n"
            f"Croupier : {' '.join(dealer_hand)} = {dv}\n\n"
            f"{result}\n"
            f"{'💵 Gain : ' + fmt(win) if win > 0 else '💸 Perdu : ' + fmt(amount)}{loot_msg}",
            parse_mode="Markdown"
        )

# ─────────────────────────────────────────────────────────────────────────────
# 3. ROULETTE (interactive)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_casino_open():
        await update.message.reply_text(get_casino_status_text(), parse_mode="Markdown")
        return

    user = update.effective_user
    u = await get_user(user.id)
    amount, err = validate_bet(u, context.args)
    if err:
        await update.message.reply_text(err)
        return

    await update_balance(user.id, -amount)

    keyboard = [
        [
            InlineKeyboardButton("🔴 Rouge", callback_data=f"roulette_bet:{user.id}:{amount}:rouge"),
            InlineKeyboardButton("⚫ Noir", callback_data=f"roulette_bet:{user.id}:{amount}:noir"),
        ],
        [
            InlineKeyboardButton("🟢 Pair", callback_data=f"roulette_bet:{user.id}:{amount}:pair"),
            InlineKeyboardButton("🟣 Impair", callback_data=f"roulette_bet:{user.id}:{amount}:impair"),
        ],
        [
            InlineKeyboardButton("1-18", callback_data=f"roulette_bet:{user.id}:{amount}:1-18"),
            InlineKeyboardButton("19-36", callback_data=f"roulette_bet:{user.id}:{amount}:19-36"),
        ],
        [
            InlineKeyboardButton("1-12", callback_data=f"roulette_bet:{user.id}:{amount}:1-12"),
            InlineKeyboardButton("13-24", callback_data=f"roulette_bet:{user.id}:{amount}:13-24"),
            InlineKeyboardButton("25-36", callback_data=f"roulette_bet:{user.id}:{amount}:25-36"),
        ],
        [InlineKeyboardButton("🎯 Numéro (0-36)", callback_data=f"roulette_number:{user.id}:{amount}")],
    ]
    msg = await update.message.reply_text(
        f"🎡 **Roulette**\n\n"
        f"💰 Mise : {fmt(amount)}\n"
        f"Choisis ton pari, puis appuie sur LANCER.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    active_games[(user.id, msg.message_id)] = {
        "game": "roulette",
        "amount": amount,
        "user_id": user.id,
        "message_id": msg.message_id,
        "chat_id": msg.chat_id,
        "bet_type": None,
    }

async def roulette_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split(":")
    action = data[0]
    user_id = int(data[1])
    amount = int(data[2])

    if query.from_user.id != user_id:
        await query.answer("Ce n'est pas ton jeu !", show_alert=True)
        return

    game_key = (user_id, query.message.message_id)
    game = active_games.get(game_key)
    if not game:
        await query.answer("Partie expirée.", show_alert=True)
        return

    if action == "roulette_bet":
        bet_type = data[3]
        game["bet_type"] = bet_type
        keyboard = [[InlineKeyboardButton("🎡 LANCER", callback_data=f"roulette_spin:{user_id}:{amount}")]]
        await query.edit_message_text(
            f"🎡 **Roulette**\n\n"
            f"💰 Mise : {fmt(amount)}\n"
            f"Pari choisi : **{bet_type}**\n\n"
            f"Appuie sur LANCER pour faire tourner la roue.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif action == "roulette_number":
        await query.edit_message_text(
            f"🎡 **Roulette**\n\n"
            f"💰 Mise : {fmt(amount)}\n"
            f"✏️ Envoie le numéro (0-36) dans la conversation.\n\n"
            f"_(le bot attend ta réponse)_",
            parse_mode="Markdown",
            reply_markup=None
        )
        game["awaiting_number"] = True
        # On utilise le contexte utilisateur pour savoir qu'on attend un numéro pour ce joueur
        context.user_data["roulette_awaiting"] = True
        context.user_data["roulette_game_key"] = game_key
        context.user_data["roulette_amount"] = amount

    elif action == "roulette_spin":
        bet_type = game.get("bet_type")
        if not bet_type:
            await query.answer("Choisis d'abord un pari !", show_alert=True)
            return

        spin = random.randint(0, 36)
        RED = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
        spin_color = "🔴" if spin in RED else ("⚫" if spin != 0 else "🟢")

        win = 0
        if bet_type in ("rouge", "red"):
            win = amount * 2 if spin in RED else 0
        elif bet_type in ("noir", "black"):
            win = amount * 2 if spin not in RED and spin != 0 else 0
        elif bet_type in ("pair", "even"):
            win = amount * 2 if spin != 0 and spin % 2 == 0 else 0
        elif bet_type in ("impair", "odd"):
            win = amount * 2 if spin % 2 == 1 else 0
        elif bet_type in ("1-18", "1_18"):
            win = amount * 2 if 1 <= spin <= 18 else 0
        elif bet_type in ("19-36", "19_36"):
            win = amount * 2 if 19 <= spin <= 36 else 0
        elif bet_type in ("1-12", "1_12"):
            win = amount * 3 if 1 <= spin <= 12 else 0
        elif bet_type in ("13-24", "13_24"):
            win = amount * 3 if 13 <= spin <= 24 else 0
        elif bet_type in ("25-36", "25_36"):
            win = amount * 3 if 25 <= spin <= 36 else 0
        else:
            pass

        if win > 0:
            await update_balance(user_id, win)
        await track_casino(user_id, amount, win)

        loot_msg = ""
        if win > 0 and random.random() < 0.15:
            item = await add_random_item(user_id, "common", "rare")
            if item:
                loot_msg = f"\n\n🎁 Tu as gagné un item : {item} !"

        await query.edit_message_text(
            f"🎡 **Roulette**\n\n"
            f"La bille tombe sur : **{spin_color} {spin}**\n\n"
            f"Ton pari : {bet_type}\n"
            f"{'🎉 **GAGNÉ !**' if win > 0 else '❌ **Perdu !**'}\n"
            f"{'💵 Gain : ' + fmt(win) if win > 0 else '💸 Perdu : ' + fmt(amount)}\n"
            f"💵 Solde : {fmt((await get_user(user_id))['balance'])}{loot_msg}",
            parse_mode="Markdown",
            reply_markup=None
        )
        active_games.pop(game_key, None)

async def roulette_number_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Vérifier si on attend vraiment un numéro pour ce joueur
    if not context.user_data.get("roulette_awaiting"):
        return

    game_key = context.user_data.get("roulette_game_key")
    if not game_key:
        return

    game = active_games.get(game_key)
    if not game or game["user_id"] != user.id:
        # Nettoyer le contexte si la partie est terminée
        context.user_data.pop("roulette_awaiting", None)
        context.user_data.pop("roulette_game_key", None)
        return

    try:
        num = int(update.message.text.strip())
        if not (0 <= num <= 36):
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Numéro invalide. Envoie un nombre entre 0 et 36.")
        return

    amount = context.user_data.get("roulette_amount") or game["amount"]

    # Tirage
    spin = random.randint(0, 36)
    RED = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
    spin_color = "🔴" if spin in RED else ("⚫" if spin != 0 else "🟢")
    win = amount * 36 if spin == num else 0

    if win > 0:
        await update_balance(user.id, win)
    await track_casino(user.id, amount, win)

    loot_msg = ""
    if win > 0 and random.random() < 0.15:
        item = await add_random_item(user.id, "common", "rare")
        if item:
            loot_msg = f"\n\n🎁 Tu as gagné un item : {item} !"

    try:
        await context.bot.edit_message_text(
            f"🎡 **Roulette**\n\n"
            f"La bille tombe sur : **{spin_color} {spin}**\n\n"
            f"Ton pari : numéro {num}\n"
            f"{'🎉 **GAGNÉ !**' if win > 0 else '❌ **Perdu !**'}\n"
            f"{'💵 Gain : ' + fmt(win) if win > 0 else '💸 Perdu : ' + fmt(amount)}\n"
            f"💵 Solde : {fmt((await get_user(user.id))['balance'])}{loot_msg}",
            parse_mode="Markdown",
            chat_id=game["chat_id"],
            message_id=game["message_id"],
            reply_markup=None
        )
    except Exception:
        await update.message.reply_text("❌ Impossible de mettre à jour le message.")

    # Nettoyage
    active_games.pop(game_key, None)
    context.user_data.pop("roulette_awaiting", None)
    context.user_data.pop("roulette_game_key", None)
    context.user_data.pop("roulette_amount", None)

# ─────────────────────────────────────────────────────────────────────────────
# 4. CRASH (interactif)
# ─────────────────────────────────────────────────────────────────────────────
active_crash_games = {}

@require_registered
@require_free
async def cmd_crash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_casino_open():
        await update.message.reply_text(get_casino_status_text(), parse_mode="Markdown")
        return

    user = update.effective_user
    u = await get_user(user.id)
    amount, err = validate_bet(u, context.args)
    if err:
        await update.message.reply_text(err)
        return

    r = random.random()
    if r < 0.01:
        crash_point = random.uniform(50, 100)
    elif r < 0.05:
        crash_point = random.uniform(10, 50)
    elif r < 0.15:
        crash_point = random.uniform(3, 10)
    elif r < 0.40:
        crash_point = random.uniform(1.5, 3)
    else:
        crash_point = random.uniform(1.0, 1.5)
    crash_point = round(crash_point, 2)

    await update_balance(user.id, -amount)

    game_data = {
        "user_id": user.id,
        "amount": amount,
        "crash_point": crash_point,
        "current_mult": 1.0,
        "running": True,
        "message_id": None,
        "chat_id": update.effective_chat.id
    }

    keyboard = [[InlineKeyboardButton("💸 ENCAISSER", callback_data=f"crash_cashout:{user.id}:{amount}")]]

    msg = await update.message.reply_text(
        f"🚀 **CRASH GAME**\n\n"
        f"💰 Mise : **{fmt(amount)}**\n"
        f"📈 Multiplicateur : **1.00x**\n"
        f"💵 Gain potentiel : **{fmt(amount)}**\n\n"
        f"⚠️ Le crash peut arriver à tout moment !\n"
        f"👇 Appuie sur ENCAISSER avant le crash !",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    game_data["message_id"] = msg.message_id
    game_data["chat_id"] = msg.chat_id
    active_crash_games[(user.id, msg.message_id)] = game_data

    async def run_crash_game():
        mult = 1.0
        step = 0.05
        interval = 0.15

        while game_data["running"] and mult < crash_point:
            mult += step
            game_data["current_mult"] = mult

            if int(mult * 20) % 5 == 0 or mult >= crash_point:
                try:
                    potential_win = int(amount * mult)
                    new_text = (
                        f"🚀 **CRASH GAME**\n\n"
                        f"💰 Mise : **{fmt(amount)}**\n"
                        f"📈 Multiplicateur : **{mult:.2f}x**\n"
                        f"💵 Gain potentiel : **{fmt(potential_win)}**\n\n"
                        f"⚠️ Le crash peut arriver à tout moment !\n"
                        f"👇 Appuie sur ENCAISSER avant le crash !"
                    )
                    await context.bot.edit_message_text(
                        new_text,
                        chat_id=game_data["chat_id"],
                        message_id=game_data["message_id"],
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                except Exception:
                    pass
            await asyncio.sleep(interval)

        if game_data["running"]:
            game_data["running"] = False
            try:
                await context.bot.edit_message_text(
                    f"💥 **CRASH !**\n\n"
                    f"💰 Mise : {fmt(amount)}\n"
                    f"📈 Dernier multiplicateur : **{mult:.2f}x**\n"
                    f"❌ Tu n'as pas encaissé à temps.\n"
                    f"💸 Perte : **{fmt(amount)}**\n\n"
                    f"👉 /crash pour rejouer",
                    chat_id=game_data["chat_id"],
                    message_id=game_data["message_id"],
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        active_crash_games.pop((user.id, msg.message_id), None)

    asyncio.create_task(run_crash_game())

async def crash_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    user_id = int(parts[1])
    amount = int(parts[2])

    if query.from_user.id != user_id:
        await query.answer("Ce n'est pas ton jeu !", show_alert=True)
        return

    game_key = (user_id, query.message.message_id)
    game_data = active_crash_games.get(game_key)

    if game_data is None:
        await query.answer("Le jeu est déjà terminé !", show_alert=True)
        return

    if not game_data["running"]:
        await query.answer("Le jeu a déjà crashé !", show_alert=True)
        return

    game_data["running"] = False
    current_mult = game_data["current_mult"]
    win = int(amount * current_mult)

    await update_balance(user_id, win)
    await track_casino(user_id, amount, win)

    profit = win - amount
    if profit > 0:
        profit_text = f"✅ Profit : **+{fmt(profit)}**"
    elif profit < 0:
        profit_text = f"❌ Perte : **{fmt(profit)}**"
    else:
        profit_text = "🤝 Équilibre"

    loot_msg = ""
    if win > amount and random.random() < 0.2:
        item = await add_random_item(user_id, "rare", "legendary")
        if item:
            loot_msg = f"\n\n🎁 Tu as gagné un item : {item} !"

    await query.edit_message_text(
        f"💸 **ENCAISSEMENT RÉUSSI !**\n\n"
        f"💰 Mise : {fmt(amount)}\n"
        f"📈 Multiplicateur : **{current_mult:.2f}x**\n"
        f"💵 Gain : **{fmt(win)}**\n"
        f"{profit_text}{loot_msg}\n\n"
        f"👉 /crash pour rejouer",
        parse_mode="Markdown"
    )

    active_crash_games.pop(game_key, None)

# ─────────────────────────────────────────────────────────────────────────────
# 5. POKER (vidéo poker automatique)
# ─────────────────────────────────────────────────────────────────────────────
def hand_rank(hand):
    rank_vals = {"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":10,"J":11,"Q":12,"K":13,"A":14}
    vals = sorted([rank_vals.get(c[:-1], 0) for c in hand], reverse=True)
    suits_h = [c[-1] for c in hand]
    flush = len(set(suits_h)) == 1
    straight = False
    if vals == [14, 5, 4, 3, 2]:
        straight = True
        vals = [5, 4, 3, 2, 1]
    else:
        straight = vals == list(range(vals[0], vals[0] - 5, -1))
    counts = sorted([vals.count(v) for v in set(vals)], reverse=True)

    if flush and straight:
        return 8, "Quinte flush"
    if counts[0] == 4:
        return 7, "Carré"
    if counts[:2] == [3, 2]:
        return 6, "Full House"
    if flush:
        return 5, "Couleur"
    if straight:
        return 4, "Suite"
    if counts[0] == 3:
        return 3, "Brelan"
    if counts[:2] == [2, 2]:
        return 2, "Double paire"
    if counts[0] == 2:
        return 1, "Paire"
    return 0, "Hauteur"

@require_registered
@require_free
async def cmd_poker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_casino_open():
        await update.message.reply_text(get_casino_status_text(), parse_mode="Markdown")
        return

    user = update.effective_user
    u = await get_user(user.id)
    amount, err = validate_bet(u, context.args)
    if err:
        await update.message.reply_text(err)
        return

    suits = ["♠", "♥", "♦", "♣"]
    ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
    deck = [r + s for r in ranks for s in suits]
    random.shuffle(deck)

    player_hand = deck[:5]
    dealer_hand = deck[5:10]

    p_rank, p_name = hand_rank(player_hand)
    d_rank, d_name = hand_rank(dealer_hand)

    mult_map = {8: 50, 7: 25, 6: 10, 5: 6, 4: 4, 3: 3, 2: 2, 1: 1, 0: 0}

    if p_rank > d_rank:
        mult = mult_map.get(p_rank, 1)
        win = int(amount * mult)
        await update_balance(user.id, win - amount)
        result = f"🎉 **Tu gagnes !** x{mult}\n💵 Gain : {fmt(win)}"
    elif p_rank == d_rank:
        await update_balance(user.id, 0)
        win = amount
        result = f"🤝 **Égalité !**\n💵 Récupéré : {fmt(amount)}"
    else:
        win = 0
        await update_balance(user.id, -amount)
        result = f"❌ **Perdu !**\n💸 Perdu : {fmt(amount)}"

    await track_casino(user.id, amount, win)

    loot_msg = ""
    if win > amount and random.random() < 0.3:
        item = await add_random_item(user.id, "rare", "legendary")
        if item:
            loot_msg = f"\n\n🎁 Tu as gagné un item : {item} !"

    await update.message.reply_text(
        f"♠️ **Poker — Video Poker**\n\n"
        f"Ta main : {' '.join(player_hand)}\n"
        f"🎴 {p_name}\n\n"
        f"Croupier : {' '.join(dealer_hand)}\n"
        f"🎴 {d_name}\n\n"
        f"{result}\n"
        f"💵 Solde : {fmt(u['balance'] + (win - amount))}{loot_msg}",
        parse_mode="Markdown"
    )

# ─────────────────────────────────────────────────────────────────────────────
# 6. MINES (interactif)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
@cooldown("casino_last", 10, "⏳ Attends 10 secondes avant de rejouer.")
async def cmd_mines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_casino_open():
        await update.message.reply_text(get_casino_status_text(), parse_mode="Markdown")
        return

    user = update.effective_user
    u = await get_user(user.id)
    amount, err = validate_bet(u, context.args)
    if err:
        await update.message.reply_text(err)
        return

    MAX_BET = 200_000
    if amount > MAX_BET:
        await update.message.reply_text(f"❌ Mise maximale pour Mines : {fmt(MAX_BET)}")
        return

    await update_balance(user.id, -amount)

    mines_count = 5
    grid_size = 25
    mines = set(random.sample(range(grid_size), mines_count))
    revealed = set()
    multiplier = 1.0

    def build_keyboard():
        buttons = []
        for i in range(5):
            row = []
            for j in range(5):
                idx = i*5 + j
                if idx in revealed:
                    text = "💣" if idx in mines else "💎"
                else:
                    text = "⬛"
                callback = f"mines_click:{user.id}:{amount}:{idx}"
                row.append(InlineKeyboardButton(text, callback_data=callback))
            buttons.append(row)
        buttons.append([InlineKeyboardButton("💰 ENCAISSER", callback_data=f"mines_cashout:{user.id}:{amount}")])
        return InlineKeyboardMarkup(buttons)

    msg = await update.message.reply_text(
        f"💣 **MINES**\n\n"
        f"💰 Mise : {fmt(amount)}\n"
        f"💎 Cases sûres révélées : {len(revealed)}\n"
        f"📈 Multiplicateur actuel : {multiplier:.2f}x\n"
        f"💵 Gain potentiel : {fmt(int(amount * multiplier))}\n\n"
        f"Clique sur une case pour la révéler, ou encaisse !",
        parse_mode="Markdown",
        reply_markup=build_keyboard()
    )

    active_games[(user.id, msg.message_id)] = {
        "game": "mines",
        "amount": amount,
        "user_id": user.id,
        "message_id": msg.message_id,
        "chat_id": msg.chat_id,
        "mines": mines,
        "revealed": revealed,
        "multiplier": multiplier,
        "game_over": False,
    }

def build_mines_keyboard(game):
    buttons = []
    for i in range(5):
        row = []
        for j in range(5):
            idx = i*5 + j
            if idx in game["revealed"]:
                text = "💣" if idx in game["mines"] else "💎"
            else:
                text = "⬛"
            callback = f"mines_click:{game['user_id']}:{game['amount']}:{idx}"
            row.append(InlineKeyboardButton(text, callback_data=callback))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("💰 ENCAISSER", callback_data=f"mines_cashout:{game['user_id']}:{game['amount']}")])
    return InlineKeyboardMarkup(buttons)

async def mines_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split(":")
    action = data[0]
    user_id = int(data[1])
    amount = int(data[2])

    if query.from_user.id != user_id:
        await query.answer("Ce n'est pas ton jeu !", show_alert=True)
        return

    game_key = (user_id, query.message.message_id)
    game = active_games.get(game_key)
    if not game or game.get("game_over"):
        await query.answer("Partie terminée.", show_alert=True)
        return

    if action == "mines_click":
        idx = int(data[3])
        if idx in game["revealed"]:
            await query.answer("Case déjà révélée.", show_alert=True)
            return

        game["revealed"].add(idx)
        if idx in game["mines"]:
            game["game_over"] = True
            await track_casino(user_id, amount, 0)
            await query.edit_message_text(
                f"💣 **MINE !**\n\n"
                f"💥 Tu as cliqué sur une mine !\n"
                f"💸 Perdu : {fmt(amount)}\n"
                f"💵 Solde : {fmt((await get_user(user_id))['balance'])}",
                parse_mode="Markdown",
                reply_markup=None
            )
            active_games.pop(game_key, None)
            return
        else:
            game["multiplier"] += 0.05
            multiplier = game["multiplier"]
            potential_win = int(amount * multiplier)
            keyboard = build_mines_keyboard(game)
            await query.edit_message_text(
                f"💣 **MINES**\n\n"
                f"💰 Mise : {fmt(amount)}\n"
                f"💎 Cases sûres révélées : {len(game['revealed'])}\n"
                f"📈 Multiplicateur actuel : {multiplier:.2f}x\n"
                f"💵 Gain potentiel : {fmt(potential_win)}\n\n"
                f"Clique sur une case pour continuer, ou encaisse !",
                parse_mode="Markdown",
                reply_markup=keyboard
            )

    elif action == "mines_cashout":
        multiplier = game["multiplier"]
        win = int(amount * multiplier)
        await update_balance(user_id, win)
        await track_casino(user_id, amount, win)

        loot_msg = ""
        if win > amount and random.random() < 0.1:
            item = await add_random_item(user_id, "common", "rare")
            if item:
                loot_msg = f"\n\n🎁 Tu as gagné un item : {item} !"

        game["game_over"] = True
        await query.edit_message_text(
            f"💰 **ENCAISSEMENT RÉUSSI !**\n\n"
            f"💰 Mise : {fmt(amount)}\n"
            f"📈 Multiplicateur : {multiplier:.2f}x\n"
            f"💵 Gain : {fmt(win)}\n"
            f"💵 Solde : {fmt((await get_user(user_id))['balance'])}{loot_msg}",
            parse_mode="Markdown",
            reply_markup=None
        )
        active_games.pop(game_key, None)

# ─────────────────────────────────────────────────────────────────────────────
# 7. PMU (interactif)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_pmu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_casino_open():
        await update.message.reply_text(get_casino_status_text(), parse_mode="Markdown")
        return

    user = update.effective_user
    u = await get_user(user.id)
    amount, err = validate_bet(u, context.args)
    if err:
        await update.message.reply_text(err)
        return

    await update_balance(user.id, -amount)

    horses = [
        {"name": "Éclair Noir", "odds": 2.5, "emoji": "🐴", "weight": 30},
        {"name": "Tonnerre",    "odds": 4.0, "emoji": "🐎", "weight": 20},
        {"name": "Vent du Nord","odds": 6.0, "emoji": "🏇", "weight": 15},
        {"name": "Galaxie",     "odds": 10.0,"emoji": "⭐", "weight": 15},
        {"name": "Fantôme",     "odds": 15.0,"emoji": "👻", "weight": 12},
        {"name": "Miracle",     "odds": 25.0,"emoji": "🎯", "weight": 8},
    ]

    keyboard = []
    for i, h in enumerate(horses):
        keyboard.append([InlineKeyboardButton(
            f"{h['emoji']} {h['name']} (x{h['odds']})",
            callback_data=f"pmu_bet:{user.id}:{amount}:{i}"
        )])
    keyboard.append([InlineKeyboardButton("🚀 LANCER LA COURSE", callback_data=f"pmu_run:{user.id}:{amount}")])

    msg = await update.message.reply_text(
        f"🏇 **Paris Hippiques**\n\n"
        f"💰 Mise : {fmt(amount)}\n"
        f"Choisis ton cheval, puis lance la course.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    active_games[(user.id, msg.message_id)] = {
        "game": "pmu",
        "amount": amount,
        "user_id": user.id,
        "message_id": msg.message_id,
        "chat_id": msg.chat_id,
        "chosen": None,
        "horses": horses,
    }

async def pmu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split(":")
    action = data[0]
    user_id = int(data[1])
    amount = int(data[2])

    if query.from_user.id != user_id:
        await query.answer("Ce n'est pas ton jeu !", show_alert=True)
        return

    game_key = (user_id, query.message.message_id)
    game = active_games.get(game_key)
    if not game:
        await query.answer("Partie expirée.", show_alert=True)
        return

    if action == "pmu_bet":
        idx = int(data[3])
        game["chosen"] = idx
        horse = game["horses"][idx]
        await query.edit_message_text(
            f"🏇 **Paris Hippiques**\n\n"
            f"💰 Mise : {fmt(amount)}\n"
            f"Cheval choisi : {horse['emoji']} {horse['name']} (cote {horse['odds']}x)\n\n"
            f"Appuie sur LANCER LA COURSE pour voir le résultat.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 LANCER LA COURSE", callback_data=f"pmu_run:{user_id}:{amount}")]])
        )

    elif action == "pmu_run":
        chosen_idx = game.get("chosen")
        if chosen_idx is None:
            await query.answer("Choisis d'abord un cheval !", show_alert=True)
            return

        horses = game["horses"]
        remaining = list(range(len(horses)))
        ranking = []
        while remaining:
            weights = [horses[i]["weight"] for i in remaining]
            chosen = random.choices(remaining, weights=weights)[0]
            ranking.append(chosen)
            remaining.remove(chosen)

        winner_idx = ranking[0]
        winner = horses[winner_idx]

        if winner_idx == chosen_idx:
            win = int(amount * horses[chosen_idx]["odds"])
            await update_balance(user_id, win)
            result = f"🎉 **TON CHEVAL GAGNE !**\n💵 Gain : {fmt(win)} (x{horses[chosen_idx]['odds']})"
            loot_msg = ""
            if random.random() < 0.2:
                item = await add_random_item(user_id, "common", "epic")
                if item:
                    loot_msg = f"\n\n🎁 Tu as gagné un item : {item} !"
        else:
            win = 0
            result = f"❌ **{winner['emoji']} {winner['name']} gagne !**\n💸 Perdu : {fmt(amount)}"
            loot_msg = ""

        await track_casino(user_id, amount, win)

        podium = "\n".join(f"{i+1}. {horses[pos]['emoji']} {horses[pos]['name']}" for i, pos in enumerate(ranking[:3]))
        await query.edit_message_text(
            f"🏇 **Course terminée !**\n\n"
            f"🏆 **Podium :**\n{podium}\n\n"
            f"{result}\n"
            f"💵 Solde : {fmt((await get_user(user_id))['balance'])}{loot_msg}",
            parse_mode="Markdown",
            reply_markup=None
        )
        active_games.pop(game_key, None)

# ─────────────────────────────────────────────────────────────────────────────
# Export des fonctions pour bot.py
# ─────────────────────────────────────────────────────────────────────────────
__all__ = [
    "cmd_casino",
    "cmd_slots", "cmd_blackjack", "cmd_roulette", "cmd_crash",
    "cmd_poker", "cmd_mines", "cmd_pmu",
    "bj_callback", "crash_callback",
    "slots_callback", "roulette_callback", "mines_callback", "pmu_callback",
    "roulette_number_input",
]