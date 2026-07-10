# handlers/arena.py
import random
import asyncio
import aiosqlite
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, increment_field, now, add_notification
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, parse_amount, get_level
from handlers.competitions import on_arena_win
from handlers.progression import apply_ranked_match_result, format_ranked_result_note

logger = logging.getLogger(__name__)

# Dictionnaires
pending_challenges = {}
active_quizzes = {}
rps_choices = {}
coinflip_choices = {}

# ─────────────────────────────────────────────────────────────────────────────
# Helper pour ajouter un item aléatoire (identique à casino.py)
# ─────────────────────────────────────────────────────────────────────────────
async def add_random_item(user_id: int, min_rarity: str = "common", max_rarity: str = "legendary"):
    """Ajoute un item aléatoire à l'inventaire du joueur. Retourne le nom de l'item ou None."""
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

# Jeux disponibles
PVP_GAMES = {
    "combat": {
        "name": "⚔️ Combat",
        "desc": "Combat 1v1 basé sur Force, Agilité et niveau",
        "min_bet": 500,
    },
    "pierre_papier_ciseaux": {
        "name": "✊ Pierre - Papier - Ciseaux",
        "desc": "Jeu classique, le gagnant remporte la mise",
        "min_bet": 100,
    },
    "pile_face": {
        "name": "🪙 Pile ou Face",
        "desc": "50/50, le gagnant remporte la mise x2",
        "min_bet": 100,
    },
    "des": {
        "name": "🎲 Dés",
        "desc": "Celui qui fait le plus grand nombre gagne",
        "min_bet": 200,
    },
    "course": {
        "name": "🏃 Course",
        "desc": "Course aléatoire, bonus de vitesse",
        "min_bet": 300,
    },
    "quiz": {
        "name": "❓ Quiz",
        "desc": "Question aléatoire, bonus d'intelligence",
        "min_bet": 500,
    }
}

def escape_html(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

# ----------------------------------------------------------------------
# Commande /defier
# ----------------------------------------------------------------------
@require_registered
@require_free
async def cmd_defier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not update.message.reply_to_message:
        games_list = "\n".join([f"• **{g['name']}** : {g['desc']} (min {fmt(g['min_bet'])})" for g in PVP_GAMES.values()])
        await update.message.reply_text(
            f"🎮 **Défier un joueur**\n\n"
            f"Pour défier quelqu'un, réponds à son message avec :\n"
            f"`/defier [jeu] [mise]`\n\n"
            f"**Jeux disponibles :**\n{games_list}\n\n"
            f"Exemple : `/defier combat 1000`",
            parse_mode="Markdown"
        )
        return

    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te défier toi-même !")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage : `/defier [jeu] [mise]`\n"
            f"Jeux : {', '.join(PVP_GAMES.keys())}\n"
            f"Ex: `/defier combat 1000`"
        )
        return

    game_name = context.args[0].lower()
    if game_name not in PVP_GAMES:
        await update.message.reply_text(f"❌ Jeu inconnu. Jeux : {', '.join(PVP_GAMES.keys())}")
        return

    game = PVP_GAMES[game_name]
    amount = parse_amount(context.args[1], u["balance"])
    if not amount or amount < game["min_bet"]:
        await update.message.reply_text(f"❌ Mise minimale pour {game['name']} : {fmt(game['min_bet'])}")
        return
    if amount > u["balance"]:
        await update.message.reply_text(f"❌ Fonds insuffisants ! Solde : {fmt(u['balance'])}")
        return

    t_data = await get_user(target.id)
    if not t_data or not t_data.get("registered"):
        await update.message.reply_text("❌ Ce joueur n'est pas enregistré.")
        return
    if t_data.get("banned"):
        await update.message.reply_text("❌ Ce joueur est banni.")
        return

    challenge_id = f"{user.id}:{target.id}:{int(now())}"
    pending_challenges[challenge_id] = {
        "challenger_id": user.id,
        "challenger_name": user.full_name,
        "target_id": target.id,
        "target_name": target.full_name,
        "game": game_name,
        "bet": amount,
        "created_at": now(),
        "status": "pending"
    }

    keyboard = [[
        InlineKeyboardButton("✅ Accepter", callback_data=f"challenge_accept|{challenge_id}"),
        InlineKeyboardButton("❌ Refuser", callback_data=f"challenge_refuse|{challenge_id}")
    ]]
    await update.message.reply_text(
        f"🎮 **DÉFI ENVOYÉ !**\n\n"
        f"👤 De : {user.full_name}\n"
        f"👤 Vers : {target.full_name}\n"
        f"🎲 Jeu : {game['name']}\n"
        f"💰 Mise : {fmt(amount)}\n\n"
        f"_En attente de la réponse de {target.full_name}..._",
        parse_mode="Markdown"
    )
    await context.bot.send_message(
        target.id,
        f"🎮 **NOUVEAU DÉFI !**\n\n"
        f"👤 **{user.full_name}** te défie à **{game['name']}** !\n"
        f"💰 Mise : {fmt(amount)}\n\n"
        f"Souhaites-tu relever le défi ?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ----------------------------------------------------------------------
# Callback de gestion des défis
# ----------------------------------------------------------------------
async def challenge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if "|" not in data:
        await query.edit_message_text("❌ Format invalide.")
        return
    action_part, challenge_id = data.split("|", 1)
    if "accept" in action_part:
        action = "accept"
    elif "refuse" in action_part:
        action = "refuse"
    else:
        await query.edit_message_text("❌ Action inconnue.")
        return

    challenge = pending_challenges.get(challenge_id)
    if not challenge:
        await query.edit_message_text("❌ Ce défi a expiré ou n'existe plus.")
        return

    user_id = query.from_user.id
    if action == "refuse":
        if user_id != challenge["target_id"]:
            await query.answer("Ce n'est pas ton défi !", show_alert=True)
            return
        await query.edit_message_text(f"❌ **Défi refusé**\n\n{challenge['target_name']} a refusé.")
        await context.bot.send_message(challenge["challenger_id"], f"❌ **Défi refusé**\n\n{challenge['target_name']} a refusé.")
        del pending_challenges[challenge_id]
        return

    if action == "accept":
        if user_id != challenge["target_id"]:
            await query.answer("Ce n'est pas ton défi !", show_alert=True)
            return
        challenger = await get_user(challenge["challenger_id"])
        if challenger["balance"] < challenge["bet"]:
            await query.edit_message_text(f"❌ {challenge['challenger_name']} n'a plus assez d'argent.")
            await context.bot.send_message(challenge["challenger_id"], f"❌ Défi annulé : solde insuffisant.")
            del pending_challenges[challenge_id]
            return
        target = await get_user(challenge["target_id"])
        if target["balance"] < challenge["bet"]:
            await query.edit_message_text(f"❌ Tu n'as pas assez d'argent.")
            del pending_challenges[challenge_id]
            return

        await update_balance(challenge["challenger_id"], -challenge["bet"])
        await update_balance(challenge["target_id"], -challenge["bet"])
        challenge["status"] = "accepted"

        game_name = challenge["game"]
        await query.edit_message_text(
            f"✅ **Défi accepté !**\n\n"
            f"🎮 Jeu : {PVP_GAMES[game_name]['name']}\n"
            f"💰 Mise : {fmt(challenge['bet'])} (x2 pour le vainqueur)\n\n"
            f"_Début du jeu..._",
            parse_mode="Markdown"
        )

        try:
            if game_name == "combat":
                await start_combat_game(context, challenge)
            elif game_name == "pierre_papier_ciseaux":
                await start_rps_game(context, challenge)
            elif game_name == "pile_face":
                await start_coinflip_game(context, challenge)
            elif game_name == "des":
                await start_dice_game(context, challenge)
            elif game_name == "course":
                await start_race_game(context, challenge)
            elif game_name == "quiz":
                await start_quiz_game(context, challenge)
        except Exception as e:
            logger.error(f"Erreur jeu {game_name}: {e}")
            await context.bot.send_message(challenge["challenger_id"], f"❌ Erreur: {str(e)[:100]}")
            await context.bot.send_message(challenge["target_id"], f"❌ Erreur: {str(e)[:100]}")
            await update_balance(challenge["challenger_id"], challenge["bet"])
            await update_balance(challenge["target_id"], challenge["bet"])
            if challenge_id in pending_challenges:
                del pending_challenges[challenge_id]

# ----------------------------------------------------------------------
# JEU 1 : COMBAT (avec loot pour le vainqueur)
# ----------------------------------------------------------------------
async def start_combat_game(context, challenge):
    from database import get_skill
    c_force = await get_skill(challenge["challenger_id"], "Force") or 0
    t_force = await get_skill(challenge["target_id"], "Force") or 0
    c_agi = await get_skill(challenge["challenger_id"], "Agilité") or 0
    t_agi = await get_skill(challenge["target_id"], "Agilité") or 0
    c_end = await get_skill(challenge["challenger_id"], "Endurance") or 0
    t_end = await get_skill(challenge["target_id"], "Endurance") or 0

    challenger = await get_user(challenge["challenger_id"])
    target = await get_user(challenge["target_id"])
    c_power = random.randint(40, 100) + c_force*5 + c_agi*3 + c_end*2 + get_level(challenger.get("xp",0))*3
    t_power = random.randint(40, 100) + t_force*5 + t_agi*3 + t_end*2 + get_level(target.get("xp",0))*3

    c_hp, t_hp = 100, 100
    logs = []
    for rd in range(1,4):
        c_dmg = random.randint(10, max(10, c_power//3))
        t_dmg = random.randint(10, max(10, t_power//3))
        t_hp -= c_dmg
        c_hp -= t_dmg
        logs.append(f"• Round {rd} : {challenge['challenger_name']} inflige {c_dmg}, subit {t_dmg}")
        if c_hp <= 0 or t_hp <= 0:
            break

    winner_id = challenge["challenger_id"] if c_hp > t_hp else challenge["target_id"]
    winner_name = challenge["challenger_name"] if c_hp > t_hp else challenge["target_name"]
    loser_id = challenge["target_id"] if winner_id == challenge["challenger_id"] else challenge["challenger_id"]
    prize = challenge["bet"] * 2

    await update_balance(winner_id, prize)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET arena_wins = COALESCE(arena_wins,0) + 1 WHERE user_id = ?", (winner_id,))
        await db.execute("UPDATE users SET arena_losses = COALESCE(arena_losses,0) + 1 WHERE user_id = ?", (loser_id,))
        await db.execute("UPDATE users SET xp = xp + 100 WHERE user_id = ?", (winner_id,))
        await db.execute("UPDATE users SET xp = xp + 50 WHERE user_id = ?", (loser_id,))
        await db.commit()
    await on_arena_win(winner_id)
    ranked_result = await apply_ranked_match_result(winner_id, loser_id, source="combat")
    ranked_note = format_ranked_result_note(ranked_result)

    # Ajout de loot pour le vainqueur (20% de chance)
    loot_msg = ""
    if random.random() < 0.2:
        item = await add_random_item(winner_id, "common", "epic")
        if item:
            loot_msg = f"\n\n🎁 Tu as gagné un item : {item} !"

    text = (
        f"⚔️ **COMBAT**\n\n{chr(10).join(logs)}\n\n"
        f"❤️ HP final : {challenge['challenger_name']}: {max(0,int(c_hp))}% | {challenge['target_name']}: {max(0,int(t_hp))}%\n\n"
        f"🏆 **{winner_name} remporte le combat !**\n💰 Gain : {fmt(prize)} (+100 XP){ranked_note}{loot_msg}"
    )
    await context.bot.send_message(challenge["challenger_id"], text, parse_mode="Markdown")
    await context.bot.send_message(challenge["target_id"], text, parse_mode="Markdown")

# ----------------------------------------------------------------------
# JEU 2 : PIERRE-PAPIER-CISEAUX (avec loot)
# ----------------------------------------------------------------------
async def start_rps_game(context, challenge):
    challenge_id = f"{challenge['challenger_id']}:{challenge['target_id']}:{int(challenge['created_at'])}"
    keyboard = [
        [InlineKeyboardButton("✊ Pierre", callback_data=f"rps|{challenge_id}|pierre")],
        [InlineKeyboardButton("📄 Papier", callback_data=f"rps|{challenge_id}|papier")],
        [InlineKeyboardButton("✂️ Ciseaux", callback_data=f"rps|{challenge_id}|ciseaux")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = f"✊ **Pierre - Papier - Ciseaux**\n\n💰 Mise : {fmt(challenge['bet'])}\n👤 Adversaire : {challenge['target_name']}\n\nChoisis ton coup :"
    await context.bot.send_message(challenge["challenger_id"], msg, reply_markup=reply_markup, parse_mode="Markdown")
    await context.bot.send_message(challenge["target_id"], msg, reply_markup=reply_markup, parse_mode="Markdown")

async def rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("|")
    if len(parts) != 3:
        await query.answer("Format invalide.", show_alert=True)
        return
    _, challenge_id, player_choice = parts
    challenge = pending_challenges.get(challenge_id)
    if not challenge:
        await query.edit_message_text("❌ Défi expiré.")
        return
    user_id = query.from_user.id
    if user_id not in (challenge["challenger_id"], challenge["target_id"]):
        await query.answer("Tu ne participes pas.", show_alert=True)
        return

    global rps_choices
    if challenge_id not in rps_choices:
        rps_choices[challenge_id] = {}
    rps_choices[challenge_id][user_id] = player_choice
    await query.edit_message_text(f"✅ Choix enregistré ({player_choice}) ! En attente...")

    if len(rps_choices[challenge_id]) == 2:
        c_choice = rps_choices[challenge_id].get(challenge["challenger_id"])
        t_choice = rps_choices[challenge_id].get(challenge["target_id"])
        rules = {"pierre": {"ciseaux": "gagne", "papier": "perd"},
                 "papier": {"pierre": "gagne", "ciseaux": "perd"},
                 "ciseaux": {"papier": "gagne", "pierre": "perd"}}
        if c_choice == t_choice:
            winner_id = None
            result_text = "🤝 Égalité, remboursement."
            prize = challenge["bet"]
        elif rules[c_choice].get(t_choice) == "gagne":
            winner_id = challenge["challenger_id"]
            result_text = f"🏆 {challenge['challenger_name']} gagne !"
            prize = challenge["bet"] * 2
        else:
            winner_id = challenge["target_id"]
            result_text = f"🏆 {challenge['target_name']} gagne !"
            prize = challenge["bet"] * 2

        emojis = {"pierre":"✊","papier":"📄","ciseaux":"✂️"}
        if winner_id:
            await update_balance(winner_id, prize)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE users SET xp = xp + 50 WHERE user_id = ?", (winner_id,))
                await db.execute("UPDATE users SET xp = xp + 25 WHERE user_id = ?", (challenge["target_id"] if winner_id==challenge["challenger_id"] else challenge["challenger_id"],))
                await db.commit()
            await on_arena_win(winner_id)
            loser_id = challenge["target_id"] if winner_id == challenge["challenger_id"] else challenge["challenger_id"]
            ranked_result = await apply_ranked_match_result(winner_id, loser_id, source="pierre_papier_ciseaux")
            ranked_note = format_ranked_result_note(ranked_result)
            loot_msg = ""
            if random.random() < 0.2:
                item = await add_random_item(winner_id, "common", "rare")
                if item:
                    loot_msg = f"\n\n🎁 Item gagné : {item} !"
        else:
            await update_balance(challenge["challenger_id"], challenge["bet"])
            await update_balance(challenge["target_id"], challenge["bet"])
            ranked_result = await apply_ranked_match_result(
                challenge["challenger_id"],
                challenge["target_id"],
                is_draw=True,
                source="pierre_papier_ciseaux",
            )
            ranked_note = format_ranked_result_note(ranked_result)
            loot_msg = ""

        text = (
            f"✊ **Résultat**\n\n"
            f"{challenge['challenger_name']} : {emojis[c_choice]}\n"
            f"{challenge['target_name']} : {emojis[t_choice]}\n\n"
            f"{result_text}\n💰 {('Gain '+fmt(prize)) if winner_id else ('Remboursement '+fmt(prize))}{ranked_note}{loot_msg}"
        )
        await context.bot.send_message(challenge["challenger_id"], text, parse_mode="Markdown")
        await context.bot.send_message(challenge["target_id"], text, parse_mode="Markdown")
        del rps_choices[challenge_id]
        if challenge_id in pending_challenges:
            del pending_challenges[challenge_id]

# ----------------------------------------------------------------------
# JEU 3 : PILE OU FACE (avec loot)
# ----------------------------------------------------------------------
async def start_coinflip_game(context, challenge):
    challenge_id = f"{challenge['challenger_id']}:{challenge['target_id']}:{int(challenge['created_at'])}"
    keyboard = [
        [InlineKeyboardButton("🪙 Pile", callback_data=f"coinflip|{challenge_id}|pile")],
        [InlineKeyboardButton("🪙 Face", callback_data=f"coinflip|{challenge_id}|face")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = f"🪙 **Pile ou Face**\n\n💰 Mise : {fmt(challenge['bet'])}\n👤 Adversaire : {challenge['target_name']}\n\nChoisis ton camp :"
    await context.bot.send_message(challenge["challenger_id"], msg, reply_markup=reply_markup, parse_mode="Markdown")
    await context.bot.send_message(challenge["target_id"], msg, reply_markup=reply_markup, parse_mode="Markdown")

async def coinflip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("|")
    if len(parts) != 3:
        await query.answer("Format invalide.", show_alert=True)
        return
    _, challenge_id, choice = parts
    challenge = pending_challenges.get(challenge_id)
    if not challenge:
        await query.edit_message_text("❌ Défi expiré.")
        return
    user_id = query.from_user.id
    if user_id not in (challenge["challenger_id"], challenge["target_id"]):
        await query.answer("Tu ne participes pas.", show_alert=True)
        return

    global coinflip_choices
    if challenge_id not in coinflip_choices:
        coinflip_choices[challenge_id] = {}
    coinflip_choices[challenge_id][user_id] = choice
    await query.edit_message_text(f"✅ Choix enregistré ({choice.upper()}) ! En attente de l'adversaire...")

    if len(coinflip_choices[challenge_id]) == 2:
        c_choice = coinflip_choices[challenge_id].get(challenge["challenger_id"])
        t_choice = coinflip_choices[challenge_id].get(challenge["target_id"])
        result = random.choice(["pile", "face"])
        total_bet = challenge["bet"] * 2

        if c_choice == result and t_choice == result:
            await update_balance(challenge["challenger_id"], challenge["bet"])
            await update_balance(challenge["target_id"], challenge["bet"])
            ranked_result = await apply_ranked_match_result(
                challenge["challenger_id"],
                challenge["target_id"],
                is_draw=True,
                source="pile_face",
            )
            ranked_note = format_ranked_result_note(ranked_result)
            result_text = "🤝 **Égalité parfaite !** Les deux ont choisi la bonne face. Remboursement."
            gain_msg = ""
            loot_msg = ""
        elif c_choice == result:
            await update_balance(challenge["challenger_id"], total_bet)
            await update_balance(challenge["target_id"], 0)
            await increment_field(challenge["challenger_id"], "xp", 30)
            await on_arena_win(challenge["challenger_id"])
            ranked_result = await apply_ranked_match_result(
                challenge["challenger_id"],
                challenge["target_id"],
                source="pile_face",
            )
            ranked_note = format_ranked_result_note(ranked_result)
            result_text = f"🏆 **{challenge['challenger_name']} gagne !**"
            gain_msg = f"💰 Gain : {fmt(total_bet)}"
            loot_msg = ""
            if random.random() < 0.2:
                item = await add_random_item(challenge["challenger_id"], "common", "rare")
                if item:
                    loot_msg = f"\n\n🎁 Item gagné : {item} !"
        elif t_choice == result:
            await update_balance(challenge["target_id"], total_bet)
            await update_balance(challenge["challenger_id"], 0)
            await increment_field(challenge["target_id"], "xp", 30)
            await on_arena_win(challenge["target_id"])
            ranked_result = await apply_ranked_match_result(
                challenge["target_id"],
                challenge["challenger_id"],
                source="pile_face",
            )
            ranked_note = format_ranked_result_note(ranked_result)
            result_text = f"🏆 **{challenge['target_name']} gagne !**"
            gain_msg = f"💰 Gain : {fmt(total_bet)}"
            loot_msg = ""
            if random.random() < 0.2:
                item = await add_random_item(challenge["target_id"], "common", "rare")
                if item:
                    loot_msg = f"\n\n🎁 Item gagné : {item} !"
        else:
            result_text = "💀 **Personne n'a gagné !** Les deux perdent leur mise."
            gain_msg = "💸 Mise perdue pour les deux."
            ranked_note = ""
            loot_msg = ""

        text = (
            f"🪙 **Pile ou Face**\n\n"
            f"🎲 Résultat : **{result.upper()}**\n"
            f"👤 {challenge['challenger_name']} a choisi : {c_choice.upper()}\n"
            f"👤 {challenge['target_name']} a choisi : {t_choice.upper()}\n\n"
            f"{result_text}\n{gain_msg}{ranked_note}{loot_msg}"
        )
        await context.bot.send_message(challenge["challenger_id"], text, parse_mode="Markdown")
        await context.bot.send_message(challenge["target_id"], text, parse_mode="Markdown")

        del coinflip_choices[challenge_id]
        if challenge_id in pending_challenges:
            del pending_challenges[challenge_id]

# ----------------------------------------------------------------------
# JEU 4 : DÉS (avec loot)
# ----------------------------------------------------------------------
async def start_dice_game(context, challenge):
    from database import get_skill
    c_roll = random.randint(1,6)
    t_roll = random.randint(1,6)
    c_intel = await get_skill(challenge["challenger_id"], "Intelligence") or 0
    t_intel = await get_skill(challenge["target_id"], "Intelligence") or 0
    c_total = c_roll + (c_intel//5)
    t_total = t_roll + (t_intel//5)

    if c_total > t_total:
        winner_id = challenge["challenger_id"]
        winner_name = challenge["challenger_name"]
    elif t_total > c_total:
        winner_id = challenge["target_id"]
        winner_name = challenge["target_name"]
    else:
        winner_id = None
        winner_name = None

    prize = challenge["bet"] * 2 if winner_id else challenge["bet"]
    if winner_id:
        await update_balance(winner_id, prize)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET xp = xp + 40 WHERE user_id = ?", (winner_id,))
            await db.commit()
        await on_arena_win(winner_id)
        loser_id = challenge["target_id"] if winner_id == challenge["challenger_id"] else challenge["challenger_id"]
        ranked_result = await apply_ranked_match_result(winner_id, loser_id, source="des")
        ranked_note = format_ranked_result_note(ranked_result)
        loot_msg = ""
        if random.random() < 0.2:
            item = await add_random_item(winner_id, "common", "rare")
            if item:
                loot_msg = f"\n\n🎁 Item gagné : {item} !"
    else:
        await update_balance(challenge["challenger_id"], challenge["bet"])
        await update_balance(challenge["target_id"], challenge["bet"])
        ranked_result = await apply_ranked_match_result(
            challenge["challenger_id"],
            challenge["target_id"],
            is_draw=True,
            source="des",
        )
        ranked_note = format_ranked_result_note(ranked_result)
        loot_msg = ""

    text = (
        f"🎲 **Dés**\n\n"
        f"{challenge['challenger_name']} : {c_roll} (Intel +{c_intel//5}) = {c_total}\n"
        f"{challenge['target_name']} : {t_roll} (Intel +{t_intel//5}) = {t_total}\n\n"
        f"{'🏆 '+winner_name+' gagne !' if winner_id else '🤝 Égalité, remboursement'}\n"
        f"💰 {'Gain '+fmt(prize) if winner_id else 'Remboursement '+fmt(prize)}{ranked_note}{loot_msg}"
    )
    await context.bot.send_message(challenge["challenger_id"], text, parse_mode="Markdown")
    await context.bot.send_message(challenge["target_id"], text, parse_mode="Markdown")
    challenge_id = f"{challenge['challenger_id']}:{challenge['target_id']}:{int(challenge['created_at'])}"
    if challenge_id in pending_challenges:
        del pending_challenges[challenge_id]

# ----------------------------------------------------------------------
# JEU 5 : COURSE (avec loot)
# ----------------------------------------------------------------------
async def start_race_game(context, challenge):
    from database import get_skill
    c_agi = await get_skill(challenge["challenger_id"], "Agilité") or 0
    t_agi = await get_skill(challenge["target_id"], "Agilité") or 0
    c_end = await get_skill(challenge["challenger_id"], "Endurance") or 0
    t_end = await get_skill(challenge["target_id"], "Endurance") or 0
    c_speed = random.randint(20,80) + c_agi*4 + c_end*2
    t_speed = random.randint(20,80) + t_agi*4 + t_end*2
    winner_id = challenge["challenger_id"] if c_speed > t_speed else challenge["target_id"]
    winner_name = challenge["challenger_name"] if c_speed > t_speed else challenge["target_name"]
    prize = challenge["bet"] * 2
    await update_balance(winner_id, prize)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET xp = xp + 60 WHERE user_id = ?", (winner_id,))
        await db.commit()
    await on_arena_win(winner_id)
    loser_id = challenge["target_id"] if winner_id == challenge["challenger_id"] else challenge["challenger_id"]
    ranked_result = await apply_ranked_match_result(winner_id, loser_id, source="course")
    ranked_note = format_ranked_result_note(ranked_result)
    loot_msg = ""
    if random.random() < 0.2:
        item = await add_random_item(winner_id, "common", "rare")
        if item:
            loot_msg = f"\n\n🎁 Item gagné : {item} !"
    text = (
        f"🏃 **Course**\n\n"
        f"{challenge['challenger_name']} : {c_speed} km/h\n"
        f"{challenge['target_name']} : {t_speed} km/h\n\n"
        f"🏆 **{winner_name} remporte la course !**\n💰 Gain : {fmt(prize)} (+60 XP){ranked_note}{loot_msg}"
    )
    await context.bot.send_message(challenge["challenger_id"], text, parse_mode="Markdown")
    await context.bot.send_message(challenge["target_id"], text, parse_mode="Markdown")
    challenge_id = f"{challenge['challenger_id']}:{challenge['target_id']}:{int(challenge['created_at'])}"
    if challenge_id in pending_challenges:
        del pending_challenges[challenge_id]

# ----------------------------------------------------------------------
# JEU 6 : QUIZ (avec loot)
# ----------------------------------------------------------------------
QUESTIONS = [
    {"question": "Quelle est la capitale de la France ?", "answer": "paris", "options": ["paris","londres","berlin","madrid"]},
    {"question": "Combien font 7 x 8 ?", "answer": "56", "options": ["48","56","64","72"]},
    {"question": "Qui a peint la Joconde ?", "answer": "leonard de vinci", "options": ["van gogh","picasso","leonard de vinci","monet"]},
    {"question": "Quel est le plus grand océan ?", "answer": "pacifique", "options": ["atlantique","indien","pacifique","arctique"]},
    {"question": "Année de la Révolution française ?", "answer": "1789", "options": ["1776","1789","1799","1804"]},
    {"question": "Auteur des Misérables ?", "answer": "victor hugo", "options": ["zola","hugo","flaubert","balzac"]},
    {"question": "Planète la plus proche du soleil ?", "answer": "mercure", "options": ["vénus","terre","mercure","mars"]},
    {"question": "Joueurs dans une équipe de foot ?", "answer": "11", "options": ["9","10","11","12"]},
]

async def start_quiz_game(context, challenge):
    from database import get_skill
    c_intel = await get_skill(challenge["challenger_id"], "Intelligence") or 0
    t_intel = await get_skill(challenge["target_id"], "Intelligence") or 0
    q = random.choice(QUESTIONS)
    quiz_id = f"quiz_{challenge['challenger_id']}_{challenge['target_id']}_{int(now())}"
    original_challenge_id = f"{challenge['challenger_id']}:{challenge['target_id']}:{int(challenge['created_at'])}"
    active_quizzes[quiz_id] = {
        "question": q,
        "correct_answer": q["answer"],
        "challenger_id": challenge["challenger_id"],
        "target_id": challenge["target_id"],
        "challenger_name": challenge["challenger_name"],
        "target_name": challenge["target_name"],
        "bet": challenge["bet"],
        "answered": False,
        "winner_id": None,
        "original_challenge_id": original_challenge_id
    }
    keyboard = [[InlineKeyboardButton(opt.upper(), callback_data=f"quiz_answer|{quiz_id}|{opt.lower()}")] for opt in q["options"]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"❓ **QUIZ**\n\n📝 {q['question']}\n\n💰 Mise : {fmt(challenge['bet'])} (x2)\n"
        f"⚡ Premier à répondre gagne !\n📚 Intel: {challenge['challenger_name']} +{c_intel} | {challenge['target_name']} +{t_intel}"
    )
    await context.bot.send_message(challenge["challenger_id"], text, reply_markup=reply_markup, parse_mode="Markdown")
    await context.bot.send_message(challenge["target_id"], text, reply_markup=reply_markup, parse_mode="Markdown")
    asyncio.create_task(quiz_timeout(context, quiz_id))

async def quiz_timeout(context, quiz_id):
    await asyncio.sleep(60)
    game = active_quizzes.get(quiz_id)
    if game and not game["answered"]:
        game["answered"] = True
        await update_balance(game["challenger_id"], game["bet"])
        await update_balance(game["target_id"], game["bet"])
        timeout_text = f"⏰ Quiz expiré ! Mise remboursée : {fmt(game['bet'])}"
        await context.bot.send_message(game["challenger_id"], timeout_text, parse_mode="Markdown")
        await context.bot.send_message(game["target_id"], timeout_text, parse_mode="Markdown")
        if game.get("original_challenge_id") in pending_challenges:
            del pending_challenges[game["original_challenge_id"]]
        del active_quizzes[quiz_id]

async def quiz_answer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("|")
    if len(parts) != 3:
        await query.answer("Format invalide.", show_alert=True)
        return
    _, quiz_id, answer = parts
    answer = answer.lower()
    user_id = query.from_user.id
    game = active_quizzes.get(quiz_id)
    if not game:
        await query.answer("Quiz expiré.", show_alert=True)
        return
    if user_id not in (game["challenger_id"], game["target_id"]):
        await query.answer("Tu ne participes pas.", show_alert=True)
        return
    if game["answered"]:
        await query.answer("Déjà terminé.", show_alert=True)
        return
    if answer == game["correct_answer"]:
        game["answered"] = True
        game["winner_id"] = user_id
        winner_name = game["challenger_name"] if user_id == game["challenger_id"] else game["target_name"]
        loser_id = game["target_id"] if user_id == game["challenger_id"] else game["challenger_id"]
        prize = game["bet"] * 2
        await update_balance(user_id, prize)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET xp = xp + 75 WHERE user_id = ?", (user_id,))
            await db.execute("UPDATE users SET xp = xp + 25 WHERE user_id = ?", (loser_id,))
            await db.commit()
        await on_arena_win(user_id)
        ranked_result = await apply_ranked_match_result(user_id, loser_id, source="quiz")
        ranked_note = format_ranked_result_note(ranked_result)
        loot_msg = ""
        if random.random() < 0.25:
            item = await add_random_item(user_id, "common", "epic")
            if item:
                loot_msg = f"\n\n🎁 Item gagné : {item} !"
        result_text = f"❓ **QUIZ - RÉPONSE CORRECTE !**\n\n✅ {winner_name} a gagné !\n📝 Réponse : {game['correct_answer']}\n💰 Gain : {fmt(prize)}{ranked_note}{loot_msg}"
        await query.edit_message_text(result_text, parse_mode="Markdown")
        await context.bot.send_message(loser_id, result_text, parse_mode="Markdown")
        if game.get("original_challenge_id") in pending_challenges:
            del pending_challenges[game["original_challenge_id"]]
        del active_quizzes[quiz_id]
    else:
        await query.answer(f"Mauvaise réponse ! La bonne était {game['correct_answer']}", show_alert=True)

# ----------------------------------------------------------------------
# Autres commandes : /defis, /classementarene, /parier
# ----------------------------------------------------------------------
@require_registered
async def cmd_defis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    my = [ch for ch in pending_challenges.values() if ch["target_id"] == user_id and ch["status"] == "pending"]
    if not my:
        await update.message.reply_text("📭 Aucun défi en attente.")
        return
    text = "🎮 **Défis en attente**\n\n"
    for ch in my:
        text += f"• De **{ch['challenger_name']}** - {PVP_GAMES[ch['game']]['name']} - Mise {fmt(ch['bet'])}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

@require_registered
async def cmd_classement_arene(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, full_name, arena_wins, arena_losses FROM users ORDER BY arena_wins DESC LIMIT 15") as cur:
            rows = await cur.fetchall()
    medals = ["🥇","🥈","🥉"] + ["⚔️"]*12
    text = "⚔️ <b>Classement de l'Arène</b>\n\n"
    for i, r in enumerate(rows):
        wr = int(r["arena_wins"] / max(1, r["arena_wins"]+r["arena_losses"]) * 100)
        name = escape_html(r['full_name'])
        text += f"{medals[i]} <b>{name}</b> — {r['arena_wins']}W/{r['arena_losses']}L ({wr}%)\n"
    await update.message.reply_text(text, parse_mode="HTML") 

@require_registered
@require_free
@cooldown("parier_cooldown", 10, "⏳ Attends 10 secondes avant de parier à nouveau.")
async def cmd_parier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    if not update.message.reply_to_message or not context.args:
        await update.message.reply_text("🎰 **Paris sur combats**\n\nRéponds à un message avec /parier [montant]")
        return
    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas parier sur toi-même.")
        return
    amount = parse_amount(context.args[0])
    if not amount or amount < 500:
        await update.message.reply_text("❌ Mise minimale : 500 coins.")
        return
    MAX_BET = 1_000_000
    if amount > MAX_BET:
        await update.message.reply_text(f"❌ Mise maximale pour parier : {fmt(MAX_BET)}")
        return
    if u["balance"] < amount:
        await update.message.reply_text("❌ Solde insuffisant.")
        return
    t_data = await get_user(target.id)
    if not t_data or not t_data.get("registered"):
        await update.message.reply_text("❌ Ce joueur n'est pas enregistré.")
        return
    
    # 50% de chance de gagner
    if random.random() < 0.5:
        gain = int(amount * 1.8)   # gain x1.8 (avantage maison 10%)
        await update_balance(user.id, gain)
        await update.message.reply_text(f"🎰 **PARI GAGNÉ !**\n\n💰 Gain : {fmt(gain)}")
    else:
        await update_balance(user.id, -amount)
        await update.message.reply_text(f"🎰 **PARI PERDU !**\n\n💸 Perte : {fmt(amount)}")
