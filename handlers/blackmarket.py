# handlers/blackmarket.py
import random
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, update_field, increment_field
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, now, fmt_time
from config import BLACK_MARKET_ITEMS, HACK_TARGETS
from handlers.competitions import on_crime_success, on_xp_gain


@require_registered
@require_free
async def cmd_noir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    text = "🛒 **Marché Noir**\n\n"
    text += "_Sois prudent — toutes les transactions ici sont illégales._\n\n"
    for item, data in BLACK_MARKET_ITEMS.items():
        text += (
            f"{data['emoji']} **{item}**\n"
            f"  💰 Prix : {fmt(data['price'])}\n"
            f"  ⚠️ Risque arrestation : {int(data['risk'] * 100)}%\n"
            f"  🌟 Impact karma : {data['karma']}\n\n"
        )
    text += "_/acheternoir [article] pour acheter_"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
@require_free
async def cmd_acheter_noir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    # Vérifier que le joueur n'est pas déjà en prison
    if u.get("prison_until", 0) > now():
        remaining = u["prison_until"] - now()
        await update.message.reply_text(
            f"⛓️ Tu es en prison pour encore {fmt_time(remaining)} !\n"
            "_Attends ta libération pour trafiquer._"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage : /acheternoir [article]\n"
            f"Articles disponibles : {', '.join(BLACK_MARKET_ITEMS.keys())}"
        )
        return

    item_name = " ".join(context.args).lower()
    matched = None
    for item in BLACK_MARKET_ITEMS:
        if item.lower() == item_name:
            matched = item
            break
    if not matched:
        # Recherche partielle
        for item in BLACK_MARKET_ITEMS:
            if any(word in item.lower() for word in item_name.split()):
                matched = item
                break
    if not matched:
        await update.message.reply_text(
            f"❌ Article inconnu. Disponibles : {', '.join(BLACK_MARKET_ITEMS.keys())}"
        )
        return

    data = BLACK_MARKET_ITEMS[matched]

    if u["balance"] < data["price"]:
        await update.message.reply_text(
            f"❌ Fonds insuffisants !\n"
            f"💰 Prix : {fmt(data['price'])}\n"
            f"💵 Ton solde : {fmt(u['balance'])}"
        )
        return

    # Débiter l'argent et ajuster le karma
    await update_balance(user.id, -data["price"])
    await increment_field(user.id, "karma", data["karma"])

    # Log de la transaction
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO blackmarket_log (user_id, item_name, price, success, timestamp) VALUES (?,?,?,1,?)",
            (user.id, matched, data["price"], now())
        )
        await db.commit()

    # Risque d'arrestation (basé sur la compétence Discrétion)
    from database import get_skill
    discretion = await get_skill(user.id, "Discrétion")
    arrest_prob = max(0.02, min(0.95, data["risk"] - discretion * 0.02))

    if random.random() < arrest_prob:
        jail_time = 3 * 3600
        await update_field(user.id, "prison_until", now() + jail_time)
        await increment_field(user.id, "crimes_done")
        await update.message.reply_text(
            f"🚨 **ARRESTATION !**\n\n"
            f"La police t'a surpris en train d'acheter **{matched}** !\n\n"
            f"💸 Argent perdu : {fmt(data['price'])}\n"
            f"⛓️ Prison : 3 heures\n\n"
            f"_/caution pour sortir plus tôt_",
            parse_mode="Markdown"
        )
    else:
        # ─────────────────────────────────────────────────────────────
        # Ajout à l'inventaire via la table `items` (item_id)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            # 1. Chercher ou créer l'item dans la table items
            async with db.execute("SELECT item_id FROM items WHERE name = ?", (matched,)) as cur:
                row = await cur.fetchone()
            if row:
                item_id = row["item_id"]
            else:
                # Créer un nouvel item (type "blackmarket", rareté "rare" par défaut)
                await db.execute("""
                    INSERT INTO items (name, type, rarity, value, effect_type, effect_value, emoji, description)
                    VALUES (?, 'blackmarket', 'rare', ?, NULL, 0, ?, ?)
                """, (matched, data["price"], data["emoji"], f"Article du marché noir : {matched}"))
                async with db.execute("SELECT last_insert_rowid()") as cur2:
                    item_id = (await cur2.fetchone())[0]

            # 2. Ajouter à l'inventaire du joueur
            async with db.execute(
                "SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?",
                (user.id, item_id)
            ) as cur3:
                existing = await cur3.fetchone()
            if existing:
                await db.execute(
                    "UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_id = ?",
                    (user.id, item_id)
                )
            else:
                await db.execute("""
                    INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at)
                    VALUES (?, ?, 'blackmarket', ?, 1, ?)
                """, (user.id, item_id, matched, now()))
            await db.commit()
        # ─────────────────────────────────────────────────────────────

        # Hook compétition pour crime réussi
        await on_crime_success(user.id)

        await update.message.reply_text(
            f"✅ **Transaction réussie !**\n\n"
            f"{data['emoji']} **{matched}** acquis discrètement.\n"
            f"💰 Prix payé : {fmt(data['price'])}\n"
            f"🌟 Karma : {data['karma']:+d}\n\n"
            f"_Méfie-toi des indics..._",
            parse_mode="Markdown"
        )


@require_registered
@require_free
async def cmd_hack_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    from database import get_skill
    tech_skill = await get_skill(user.id, "Technique")

    text = "💻 **Cibles de Hacking**\n\n"
    for target, data in HACK_TARGETS.items():
        min_r, max_r = data["reward"]
        locked = tech_skill < data["skill_need"]
        locked_line = f"  🔒 Besoin de Niv.{data['skill_need']} Technique\n" if locked else ""
        text += (
            f"{'🔒' if locked else '🖥️'} **{target}**\n"
            f"  💰 Gain : {fmt(min_r)} – {fmt(max_r)}\n"
            f"  ✅ Chance : {int(data['success'] * 100)}%\n"
            f"  🧠 Technique requise : Niv.{data['skill_need']}\n"
            f"{locked_line}\n"
        )
    text += "_/hacker [cible] pour attaquer_"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
@require_free
@cooldown("last_hack", 3600, "⏳ Attends 1h avant de refaire un hack.")
async def cmd_hacker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not context.args:
        await update.message.reply_text(
            "Usage : /hacker [cible]\n"
            "/hacktargets pour voir les cibles disponibles."
        )
        return

    target_name = " ".join(context.args).lower()
    matched = None
    for t in HACK_TARGETS:
        if t.lower() == target_name:
            matched = t
            break
    if not matched:
        for t in HACK_TARGETS:
            if any(word in t.lower() for word in target_name.split()):
                matched = t
                break
    if not matched:
        await update.message.reply_text(
            f"❌ Cible inconnue. Cibles : {', '.join(HACK_TARGETS.keys())}"
        )
        return

    data = HACK_TARGETS[matched]
    from database import get_skill
    tech_skill = await get_skill(user.id, "Technique")

    if tech_skill < data["skill_need"]:
        await update.message.reply_text(
            f"🔒 Compétence insuffisante !\n"
            f"🧠 Technique requise : Niv.{data['skill_need']}\n"
            f"🧠 Ton niveau : {tech_skill}\n"
            f"👉 /formation Technique pour progresser"
        )
        return

    await increment_field(user.id, "hack_attempts")

    skill_bonus = tech_skill * 0.02
    success_prob = min(0.95, data["success"] + skill_bonus)

    success = random.random() < success_prob

    if success:
        min_r, max_r = data["reward"]
        reward = random.randint(min_r, max_r)
        await update_balance(user.id, reward)
        await increment_field(user.id, "karma", -10)
        xp_gain = 200
        await increment_field(user.id, "xp", xp_gain)
        await on_xp_gain(user.id, xp_gain)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO hack_log (hacker_id, target_type, success, reward, timestamp) VALUES (?,?,1,?,?)",
                (user.id, matched, reward, now())
            )
            await db.commit()

        await update_field(user.id, "last_hack", now())

        hack_flavors = [
            "Tu accèdes au système en moins de 10 secondes... 💻",
            "Firewall contourné ! Données exfiltrées avec succès. 🔓",
            "L'IA de sécurité ne t'a pas vu venir... 🤖",
            "3 couches de chiffrement... jeu d'enfant pour toi. 🔐",
        ]
        await update.message.reply_text(
            f"💻 **HACK RÉUSSI : {matched}**\n\n"
            f"_{random.choice(hack_flavors)}_\n\n"
            f"💰 Butin : **{fmt(reward)}**\n"
            f"🌟 Karma : -10\n"
            f"✨ +{xp_gain} XP",
            parse_mode="Markdown"
        )
    else:
        jail_time = random.choice([3600, 7200, 14400])
        await update_field(user.id, "prison_until", now() + jail_time)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO hack_log (hacker_id, target_type, success, timestamp) VALUES (?,?,0,?)",
                (user.id, matched, now())
            )
            await db.commit()

        await update.message.reply_text(
            f"❌ **HACK ÉCHOUÉ : {matched}**\n\n"
            f"_L'IA de sécurité t'a tracé et la police a débarqué !_\n\n"
            f"⛓️ Prison : {fmt_time(jail_time)}\n"
            f"💡 Améliore ta compétence Technique pour réussir !",
            parse_mode="Markdown"
        )


@require_registered
@require_free
async def cmd_defenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Renforce tes défenses contre les hackers."""
    user = update.effective_user
    u = await get_user(user.id)

    DEFENSE_TIERS = [
        {"name": "Pare-feu basique",    "cost": 10_000,  "bonus": 10, "level": 1},
        {"name": "VPN renforcé",        "cost": 50_000,  "bonus": 20, "level": 2},
        {"name": "IDS/IPS",             "cost": 200_000, "bonus": 35, "level": 3},
        {"name": "Bunker numérique",    "cost": 750_000, "bonus": 55, "level": 4},
        {"name": "IA défensive",        "cost": 5_000_000,"bonus": 80, "level": 5},
    ]

    current_def = u.get("defense_level", 0)

    if not context.args:
        text = "🛡️ **Défenses numériques**\n\n"
        text += f"Ton niveau actuel : **{current_def}**\n\n"
        text += "**Niveaux disponibles :**\n"
        for t in DEFENSE_TIERS:
            status = "✅" if current_def >= t["level"] else ("👉" if current_def == t["level"] - 1 else "🔒")
            text += f"{status} Niv.{t['level']} — **{t['name']}** — {fmt(t['cost'])} — -{t['bonus']}% risques hacks\n"
        text += "\n_/defenses [niveau] pour acheter_"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    try:
        lvl = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Spécifie un numéro de niveau (1-5).")
        return

    tier = next((t for t in DEFENSE_TIERS if t["level"] == lvl), None)
    if not tier:
        await update.message.reply_text("❌ Niveau invalide (1-5).")
        return
    if current_def >= lvl:
        await update.message.reply_text(f"✅ Tu as déjà le niveau {lvl} ou supérieur !")
        return
    if lvl > current_def + 1:
        await update.message.reply_text(f"❌ Tu dois d'abord acheter le niveau {current_def + 1}.")
        return
    if u["balance"] < tier["cost"]:
        await update.message.reply_text(
            f"❌ Fonds insuffisants. Coût : {fmt(tier['cost'])}\n"
            f"Solde : {fmt(u['balance'])}"
        )
        return

    await update_balance(user.id, -tier["cost"])
    await update_field(user.id, "defense_level", lvl)

    await update.message.reply_text(
        f"🛡️ **Défense améliorée !**\n\n"
        f"Niveau {lvl} — **{tier['name']}**\n"
        f"💰 Coût : {fmt(tier['cost'])}\n"
        f"🔐 Réduction des hacks entrants : **-{tier['bonus']}%**\n\n"
        f"_Les hackers auront du mal à te cibler !_",
        parse_mode="Markdown"
    )
