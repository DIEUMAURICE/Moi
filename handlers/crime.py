# handlers/crime.py
import random
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, update_field, increment_field
from utils.decorators import require_registered, require_free
from utils.helpers import fmt, fmt_time, now, roll_success, parse_amount
from config import CRIMES, BAIL_COST_RATIO, LAWYER_COST
from handlers.competitions import on_crime_success
from handlers.missions import update_mission_progress
from handlers.vehicles import get_active_vehicle   # Nouvel import pour le véhicule actif

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

# ─────────────────────────────────────────────────────────────────────────────
# Commandes criminelles
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
@require_free
async def cmd_crimes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🔫 **Crimes disponibles**\n\n"
    text += "_Chaque crime est un risque — tu pourrais aller en prison !_\n\n"
    for crime, data in CRIMES.items():
        min_r, max_r = data["reward"]
        text += (
            f"💀 **{crime}**\n"
            f"  💰 Gain : {fmt(min_r)} – {fmt(max_r)}\n"
            f"  ✅ Chance : {int(data['success'] * 100)}%\n"
            f"  ⛓️ Prison si pris : {fmt_time(data['jail'])}\n"
            f"  🌟 Impact karma : {data['karma']}\n\n"
        )
    text += "_/commettre [crime] pour tenter le coup_"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
@require_free
async def cmd_commettre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    last_crime = u.get("last_crime", 0)
    if now() - last_crime < 60:
        remaining = 60 - (now() - last_crime)
        await update.message.reply_text(f"⏳ Calme-toi ! Attends {fmt_time(remaining)} avant un autre crime.")
        return

    if not context.args:
        await update.message.reply_text("Usage : /commettre [crime]\n/crimes pour voir la liste")
        return

    crime_name = " ".join(context.args).title()
    matched = None
    for c in CRIMES:
        if c.lower() == crime_name.lower():
            matched = c
            break
    if not matched:
        await update.message.reply_text("❌ Crime inconnu. /crimes pour voir la liste.")
        return

    data = CRIMES[matched]

    from database import get_skill
    discretion = await get_skill(user.id, "Discrétion")
    agility = await get_skill(user.id, "Agilité")
    skill_bonus = (discretion * 0.02) + (agility * 0.01)
    karma_bonus = max(-0.1, min(0.1, u.get("karma", 0) / 1000))

    gang_bonus = 0
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT g.reputation FROM gang_members gm JOIN gangs g ON g.gang_id=gm.gang_id WHERE gm.user_id=?",
            (user.id,)
        ) as cur:
            gang_row = await cur.fetchone()
        if gang_row:
            gang_bonus = (gang_row[0] - 50) / 200

    success = roll_success(data["success"], skill_bonus + gang_bonus, karma_bonus)

    await update_field(user.id, "last_crime", now())
    await increment_field(user.id, "crimes_done")

    # Récupérer le véhicule actif pour la fuite
    active_vehicle = await get_active_vehicle(user.id)
    vehicle_speed = active_vehicle.get("speed", 0) if active_vehicle else 0

    # Variable pour déterminer si le joueur finit en prison
    prison_time = 0
    arrested = False

    if success:
        min_r, max_r = data["reward"]
        reward = random.randint(min_r, max_r)
        await update_balance(user.id, reward)
        await increment_field(user.id, "karma", data["karma"])
        await increment_field(user.id, "crimes_success")
        await increment_field(user.id, "xp", 150)
        await on_crime_success(user.id)
        await update_mission_progress(user.id, "crime", 1)

        # === PHASE DE FUITE ===
        if active_vehicle:
            # Bonus de vitesse : 0.3% par point de vitesse, max 30% (de 0.70 à 1.00)
            escape_base = 0.70 + (vehicle_speed * 0.3 / 100)  # 0.70 + (vitesse * 0.003)
        else:
            # Sans véhicule, pénalité de 30% (0.70 - 0.30 = 0.40)
            escape_base = 0.40

        # Bonus supplémentaire de karma (max +5%)
        karma_escape_bonus = (u.get("karma", 0) / 1000) * 0.1
        escape_chance = min(0.95, escape_base + karma_escape_bonus)

        if random.random() > escape_chance:
            # Le joueur est pris après le crime (peine réduite de moitié)
            prison_time = data["jail"] // 2
            await update_field(user.id, "prison_until", now() + prison_time)
            arrested = True
            # On loggue l'échec de fuite comme un échec du crime (mais il a eu le butin)
            async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
                await db.execute(
                    "INSERT INTO crime_log (user_id, crime_type, success, reward, jail_time, timestamp) VALUES (?,?,1,?,?,?)",
                    (user.id, matched, reward, prison_time, now())
                )
                await db.commit()
        else:
            # Fuite réussie, pas de prison
            async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
                await db.execute(
                    "INSERT INTO crime_log (user_id, crime_type, success, reward, timestamp) VALUES (?,?,1,?,?)",
                    (user.id, matched, reward, now())
                )
                await db.commit()

        # Ajout de loot (20% de chance)
        loot_msg = ""
        if random.random() < 0.2:
            if matched in ("Hold-up banque", "Cybercriminalité"):
                item = await add_random_item(user.id, "rare", "legendary")
            elif matched in ("Braquage", "Trafic drogue"):
                item = await add_random_item(user.id, "common", "epic")
            else:
                item = await add_random_item(user.id, "common", "rare")
            if item:
                loot_msg = f"\n\n🎁 Tu as trouvé un objet : {item} !"

        crime_flavor = {
            "Pickpocket":      "Tu glisses ta main dans une poche... Jackpot ! 🤫",
            "Vol à l'étalage": "Tu sors du magasin sans te faire remarquer 😏",
            "Cambriolage":     "La maison était vide — parfait ! 🏠💨",
            "Arnaque":         "L'idiot a mordu à l'hameçon ! 🎣",
            "Braquage":        "Le coffre était plein ! Tu fuis dans une voiture volée 🚗💨",
            "Trafic drogue":   "La marchandise est livrée sans accroc 💊",
            "Cybercriminalité":"Les virements ont été effectués avant que l'alerte sonne 💻",
            "Hold-up banque":  "Police aux trousses mais tu t'en sors ! La légende commence... 🏦🔫",
        }

        if arrested:
            await update.message.reply_text(
                f"✅ **Crime réussi : {matched}**\n\n"
                f"_{crime_flavor.get(matched, 'Mission accomplie !')}_\n\n"
                f"💰 Butin : **{fmt(reward)}**\n"
                f"🌟 Karma : {data['karma']:+d}\n"
                f"✨ +150 XP{loot_msg}\n\n"
                f"🚨 **Mais tu es pris en fuite !**\n"
                f"⛓️ Prison pour {fmt_time(prison_time)}.\n"
                f"💡 /caution pour sortir plus tôt.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"✅ **Crime réussi : {matched}**\n\n"
                f"_{crime_flavor.get(matched, 'Mission accomplie !')}_\n\n"
                f"💰 Butin : **{fmt(reward)}**\n"
                f"🌟 Karma : {data['karma']:+d}\n"
                f"✨ +150 XP{loot_msg}",
                parse_mode="Markdown"
            )

    else:
        # Crime échoué
        prison_time = data["jail"]
        await update_field(user.id, "prison_until", now() + prison_time)

        async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
            await db.execute(
                "INSERT INTO crime_log (user_id, crime_type, success, jail_time, timestamp) VALUES (?,?,0,?,?)",
                (user.id, matched, prison_time, now())
            )
            await db.commit()

        fails = [
            "La police t'attendait... Tu te fais menotter 👮",
            "Un témoin a tout vu et appelé le 17 ! 📞",
            "Une alarme silencieuse te trahit 🚨",
            "Ton complice t'a dénoncé ! 🐀",
            "Tu as laissé des empreintes partout 🔍",
            "Un passant t'a filmé en direct 📱",
        ]

        caution = int((data["reward"][0] + data["reward"][1]) / 2 * BAIL_COST_RATIO)

        await update.message.reply_text(
            f"❌ **Crime échoué : {matched}**\n\n"
            f"_{random.choice(fails)}_\n\n"
            f"⛓️ Tu es en prison pour **{fmt_time(prison_time)}** !\n\n"
            f"💡 Options :\n"
            f"• /caution — payer {fmt(caution)} pour sortir\n"
            f"• /tribunal — passer devant le juge (risqué)\n"
            f"• /avocat — engager un avocat ({fmt(LAWYER_COST)})",
            parse_mode="Markdown"
        )


@require_registered
async def cmd_caution(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    prison_time = u.get("prison_until", 0)
    if prison_time <= now():
        await update.message.reply_text("✅ Tu n'es pas en prison !")
        return

    remaining = prison_time - now()
    caution = int(remaining / 3600 * 5_000)

    if u["balance"] < caution:
        await update.message.reply_text(
            f"❌ Caution insuffisante !\n"
            f"💰 Caution requise : {fmt(caution)}\n"
            f"💵 Ton solde : {fmt(u['balance'])}\n\n"
            f"⏳ Temps restant : {fmt_time(remaining)}\n"
            f"Ou tente ta chance au /tribunal"
        )
        return

    await update_balance(user.id, -caution)
    await update_field(user.id, "prison_until", 0)

    await update.message.reply_text(
        f"🔓 **Liberté recouvrée !**\n\n"
        f"💰 Caution payée : {fmt(caution)}\n"
        f"_Tu sors par la porte de derrière..._\n\n"
        f"⚠️ Évite les crimes pendant quelques temps !",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_tribunal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    prison_time = u.get("prison_until", 0)
    if prison_time <= now():
        await update.message.reply_text("✅ Tu n'es pas en prison !")
        return

    remaining = prison_time - now()
    karma = u.get("karma", 0)
    if karma >= 100:
        acquit_chance = 0.60
    elif karma >= 0:
        acquit_chance = 0.40
    elif karma >= -100:
        acquit_chance = 0.25
    else:
        acquit_chance = 0.10

    roll = random.random()

    if roll < acquit_chance:
        await update_field(user.id, "prison_until", 0)
        await increment_field(user.id, "karma", 5)
        await update.message.reply_text(
            f"⚖️ **ACQUITTÉ !**\n\n"
            f"_Le juge a reconnu ton bon comportement._\n\n"
            f"🎉 Tu es libre !\n"
            f"🌟 +5 Karma",
            parse_mode="Markdown"
        )
    elif roll < acquit_chance + 0.3:
        new_time = now() + remaining // 2
        await update_field(user.id, "prison_until", new_time)
        await update.message.reply_text(
            f"⚖️ **Peine réduite !**\n\n"
            f"_Le juge est clément._\n\n"
            f"⏳ Peine réduite de moitié : {fmt_time(remaining // 2)}",
            parse_mode="Markdown"
        )
    else:
        extra = random.randint(1, 3) * 3600
        new_time = prison_time + extra
        fine = random.randint(10_000, 100_000)
        await update_field(user.id, "prison_until", new_time)
        if u["balance"] >= fine:
            await update_balance(user.id, -fine)
        await increment_field(user.id, "karma", -10)

        await update.message.reply_text(
            f"⚖️ **CONDAMNÉ !**\n\n"
            f"_Le juge était de mauvaise humeur..._\n\n"
            f"⛓️ Peine allongée de {fmt_time(extra)}\n"
            f"💸 Amende : {fmt(fine)}\n"
            f"🌟 Karma : -10",
            parse_mode="Markdown"
        )


@require_registered
async def cmd_avocat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    prison_time = u.get("prison_until", 0)
    if prison_time <= now():
        await update.message.reply_text("✅ Tu n'es pas en prison. Pas besoin d'avocat.")
        return

    if u["balance"] < LAWYER_COST:
        await update.message.reply_text(
            f"❌ Avocat coûte {fmt(LAWYER_COST)}.\n"
            f"💵 Ton solde : {fmt(u['balance'])}"
        )
        return

    await update_balance(user.id, -LAWYER_COST)
    remaining = prison_time - now()

    if random.random() < 0.70:
        new_time = now() + remaining // 3
        await update_field(user.id, "prison_until", new_time)
        await update.message.reply_text(
            f"👨‍⚖️ **Avocat intervenu !**\n\n"
            f"_Maître Leblanc plaide brillamment._\n\n"
            f"✅ Peine réduite à {fmt_time(remaining // 3)} !\n"
            f"💰 Honoraires : {fmt(LAWYER_COST)}",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"👨‍⚖️ **L'avocat a échoué...**\n\n"
            f"_Maître Leblanc n'a pas convaincu le juge._\n\n"
            f"❌ Peine inchangée : {fmt_time(remaining)}\n"
            f"💰 Honoraires perdus : {fmt(LAWYER_COST)}",
            parse_mode="Markdown"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Commandes des gangs (inchangées)
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
@require_free
async def cmd_gang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT g.*, gm.role FROM gang_members gm
            JOIN gangs g ON g.gang_id=gm.gang_id WHERE gm.user_id=?
        """, (user.id,)) as cur:
            gang = await cur.fetchone()

    if not gang:
        await update.message.reply_text(
            "🔫 Tu n'es dans aucun gang.\n\n"
            "👉 /creergand [nom] — créer ton propre gang\n"
            "👉 /rejoindregang [nom] — rejoindre un gang"
        )
        return

    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM gang_members WHERE gang_id=?", (gang["gang_id"],)
        ) as cur:
            count = (await cur.fetchone())[0]

    await update.message.reply_text(
        f"🔫 **Gang : {gang['name']}**\n\n"
        f"👤 Ton rôle : {gang['role']}\n"
        f"👥 Membres : {count}\n"
        f"💰 Caisse : {fmt(gang['treasury'])}\n"
        f"⭐ Réputation : {gang['reputation']}\n\n"
        f"👉 /ganginfo {gang['name']} — détails\n"
        f"👉 /gangactions — actions de gang\n"
        f"👉 /quittergang — quitter le gang",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_creergand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Créer un nouveau gang."""
    user = update.effective_user
    u = await get_user(user.id)

    if not context.args:
        await update.message.reply_text("Usage : /creergand [nom du gang]")
        return

    name = " ".join(context.args)[:40]
    GANG_CREATION_COST = 50_000

    if u["balance"] < GANG_CREATION_COST:
        await update.message.reply_text(
            f"❌ Créer un gang coûte **{fmt(GANG_CREATION_COST)}**.\n"
            f"Ton solde : {fmt(u['balance'])}",
            parse_mode="Markdown"
        )
        return

    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT g.name FROM gangs g JOIN gang_members gm ON g.gang_id=gm.gang_id WHERE gm.user_id=?",
            (user.id,)
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            await update.message.reply_text(
                f"❌ Tu es déjà membre du gang **{existing[0]}**.\n"
                f"Quitte-le avant d'en créer un nouveau.",
                parse_mode="Markdown"
            )
            return

        async with db.execute("SELECT gang_id FROM gangs WHERE LOWER(name)=LOWER(?)", (name,)) as cur:
            if await cur.fetchone():
                await update.message.reply_text(f"❌ Le nom **{name}** est déjà pris.", parse_mode="Markdown")
                return

        await db.execute(
            "INSERT INTO gangs (name, founder_id, treasury, reputation, created_at) VALUES (?,?,0,0,?)",
            (name, user.id, now())
        )
        async with db.execute("SELECT last_insert_rowid()") as cur:
            gang_id = (await cur.fetchone())[0]
        await db.execute(
            "INSERT INTO gang_members (gang_id, user_id, role, joined_at) VALUES (?,?,'Chef',?)",
            (gang_id, user.id, now())
        )
        await db.commit()

    await update_balance(user.id, -GANG_CREATION_COST)

    await update.message.reply_text(
        f"🔫 **Gang créé avec succès !**\n\n"
        f"🏷️ Nom : **{name}**\n"
        f"👑 Chef : {user.full_name}\n"
        f"💰 Coût : {fmt(GANG_CREATION_COST)}\n\n"
        f"_Recrute des membres et bâtis ton empire criminel !_",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_rejoindregang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rejoindre un gang existant (sur invitation ou demande)."""
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /rejoindregang [nom du gang]")
        return

    name = " ".join(context.args)

    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute("SELECT gang_id, founder_id FROM gangs WHERE LOWER(name)=LOWER(?)", (name,)) as cur:
            gang = await cur.fetchone()
        if not gang:
            await update.message.reply_text(f"❌ Gang **{name}** introuvable.")
            return

        async with db.execute("SELECT * FROM gang_members WHERE user_id=?", (user.id,)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Tu es déjà dans un gang ! Quitte-le d'abord.")
                return

        await db.execute(
            "INSERT INTO gang_members (gang_id, user_id, role, joined_at) VALUES (?,?,'Soldat',?)",
            (gang[0], user.id, now())
        )
        await db.commit()

    await update.message.reply_text(
        f"✅ Tu as rejoint le gang **{name}** !\n\n"
        f"Utilise /gang pour voir tes infos.",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_quittergang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quitter son gang actuel."""
    user = update.effective_user

    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT g.gang_id, g.name, gm.role FROM gang_members gm JOIN gangs g ON g.gang_id=gm.gang_id WHERE gm.user_id=?",
            (user.id,)
        ) as cur:
            member = await cur.fetchone()
        if not member:
            await update.message.reply_text("❌ Tu n'es dans aucun gang.")
            return

        gang_id, gang_name, role = member
        if role == "Chef":
            async with db.execute("SELECT COUNT(*) FROM gang_members WHERE gang_id=?", (gang_id,)) as cur:
                count = (await cur.fetchone())[0]
            if count > 1:
                await update.message.reply_text(
                    "❌ Tu es le chef ! Tu ne peux pas quitter le gang sans le dissoudre.\n"
                    "Pour transférer le leadership, utilise `/transfertchef @utilisateur`\n"
                    "Pour dissoudre, `/dissoudregang`."
                )
                return
            else:
                # Dernier membre, dissolution automatique
                await db.execute("DELETE FROM gangs WHERE gang_id=?", (gang_id,))
                await db.execute("DELETE FROM gang_members WHERE gang_id=?", (gang_id,))
                await db.commit()
                await update.message.reply_text(
                    f"💀 Le gang **{gang_name}** a été dissous car tu étais le dernier membre.",
                    parse_mode="Markdown"
                )
                return
        else:
            await db.execute("DELETE FROM gang_members WHERE user_id=? AND gang_id=?", (user.id, gang_id))
            await db.commit()
            await update.message.reply_text(
                f"👋 Tu as quitté le gang **{gang_name}**.",
                parse_mode="Markdown"
            )


@require_registered
@require_free
async def cmd_dissoudregang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dissoudre le gang (chef uniquement). Supprime le gang et tous ses membres."""
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT g.gang_id, g.name, g.treasury, gm.role FROM gang_members gm JOIN gangs g ON g.gang_id=gm.gang_id WHERE gm.user_id=?",
            (user.id,)
        ) as cur:
            gang = await cur.fetchone()
        if not gang:
            await update.message.reply_text("❌ Tu n'es dans aucun gang.")
            return
        gang_id, gang_name, treasury, role = gang
        if role != "Chef":
            await update.message.reply_text("❌ Seul le chef peut dissoudre le gang.")
            return

        # Supprimer toutes les entrées du gang
        await db.execute("DELETE FROM gang_members WHERE gang_id=?", (gang_id,))
        await db.execute("DELETE FROM gangs WHERE gang_id=?", (gang_id,))
        # Optionnel : reverser la trésorerie au chef
        if treasury > 0:
            await update_balance(user.id, treasury)
            msg_remb = f"\n💰 La trésorerie de {fmt(treasury)} a été reversée dans ton compte."
        else:
            msg_remb = ""
        await db.commit()

    await update.message.reply_text(
        f"💀 **Gang {gang_name} dissous** par son chef.{msg_remb}\n"
        f"_Les membres ont été libérés de leurs obligations._",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_ganginfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Afficher les détails d'un gang (public)."""
    if not context.args:
        await update.message.reply_text("Usage : /ganginfo [nom du gang]")
        return

    name = " ".join(context.args)
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM gangs WHERE LOWER(name)=LOWER(?)", (name,)) as cur:
            gang = await cur.fetchone()
        if not gang:
            await update.message.reply_text(f"❌ Gang **{name}** introuvable.")
            return

        async with db.execute("SELECT COUNT(*) FROM gang_members WHERE gang_id=?", (gang["gang_id"],)) as cur:
            count = (await cur.fetchone())[0]

        async with db.execute("""
            SELECT u.full_name, gm.role, gm.joined_at
            FROM gang_members gm JOIN users u ON u.user_id=gm.user_id
            WHERE gm.gang_id=?
        """, (gang["gang_id"],)) as cur:
            members = await cur.fetchall()

    role_order = {"Chef": 0, "Lieutenant": 1, "Soldat": 2}
    members_sorted = sorted(members, key=lambda m: role_order.get(m["role"], 3))

    text = f"🔫 **Gang : {gang['name']}**\n\n"
    text += f"👑 Fondateur : `{gang['founder_id']}`\n"
    text += f"💰 Caisse : {fmt(gang['treasury'])}\n"
    text += f"⭐ Réputation : {gang['reputation']} / 100\n"
    text += f"👥 Membres : {count}\n\n"
    text += "**Membres :**\n"
    for m in members_sorted[:15]:
        text += f"  • {m['full_name']} — {m['role']}\n"
    if count > 15:
        text += f"  _... et {count - 15} autres_"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
@require_free
async def cmd_gangactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu des actions de gang."""
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT g.*, gm.role FROM gang_members gm JOIN gangs g ON g.gang_id=gm.gang_id WHERE gm.user_id=?",
            (user.id,)
        ) as cur:
            gang = await cur.fetchone()
    if not gang:
        await update.message.reply_text("❌ Tu n'es dans aucun gang.")
        return

    role = gang[5]  # role
    text = f"🔫 **Actions du gang — {gang[1]}**\n\n"
    text += f"💰 Caisse : {fmt(gang[3])}\n"
    text += f"⭐ Réputation : {gang[4]}\n\n"

    if role in ("Chef", "Lieutenant"):
        text += "**Gestion :**\n"
        text += "• `/gangcaisse depot 10000` — déposer de l'argent\n"
        text += "• `/gangcaisse retrait 5000` — retirer (chef uniquement)\n"
        text += "• `/transfertchef @utilisateur` — passer le leadership\n"
        text += "• `/gangupgrade` — améliorer le gang (réputation)\n\n"
    text += "**Actions criminelles :**\n"
    text += "• `/ganghold` — hold-up collectif\n"
    text += "• `/gangclassement` — classement des gangs\n"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
@require_free
async def cmd_gangcaisse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Déposer ou retirer de l'argent de la caisse du gang."""
    user = update.effective_user
    u = await get_user(user.id)

    if len(context.args) < 2:
        await update.message.reply_text("Usage : /gangcaisse [depot|retrait] [montant]")
        return

    action = context.args[0].lower()
    amount = parse_amount(context.args[1], u["balance"] if action == "depot" else None)
    if not amount or amount <= 0:
        await update.message.reply_text("❌ Montant invalide.")
        return

    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT g.gang_id, g.treasury, gm.role FROM gang_members gm JOIN gangs g ON g.gang_id=gm.gang_id WHERE gm.user_id=?",
            (user.id,)
        ) as cur:
            gang = await cur.fetchone()
        if not gang:
            await update.message.reply_text("❌ Tu n'es dans aucun gang.")
            return

        gang_id, treasury, role = gang
        if action == "depot":
            if amount > u["balance"]:
                await update.message.reply_text("❌ Fonds insuffisants.")
                return
            await update_balance(user.id, -amount)
            await db.execute("UPDATE gangs SET treasury=treasury+? WHERE gang_id=?", (amount, gang_id))
            await db.commit()
            await update.message.reply_text(
                f"🏦 Dépôt de {fmt(amount)} dans la caisse du gang.\n"
                f"💰 Nouvelle caisse : {fmt(treasury + amount)}",
                parse_mode="Markdown"
            )
        elif action == "retrait":
            if role != "Chef":
                await update.message.reply_text("❌ Seul le chef peut retirer de l'argent.")
                return
            if amount > treasury:
                await update.message.reply_text("❌ Montant supérieur à la caisse.")
                return
            await db.execute("UPDATE gangs SET treasury=treasury-? WHERE gang_id=?", (amount, gang_id))
            await update_balance(user.id, amount)
            await db.commit()
            await update.message.reply_text(
                f"🏦 Retrait de {fmt(amount)} de la caisse du gang.\n"
                f"💰 Nouvelle caisse : {fmt(treasury - amount)}",
                parse_mode="Markdown"
            )


@require_registered
@require_free
async def cmd_transfertchef(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transférer le leadership du gang à un autre membre."""
    user = update.effective_user
    if not update.message.reply_to_message:
        await update.message.reply_text("Usage : répondre au message du nouveau chef avec /transfertchef")
        return

    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te transférer à toi-même.")
        return

    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT g.gang_id, g.name, gm.role FROM gang_members gm JOIN gangs g ON g.gang_id=gm.gang_id WHERE gm.user_id=?",
            (user.id,)
        ) as cur:
            my_gang = await cur.fetchone()
        if not my_gang or my_gang[2] != "Chef":
            await update.message.reply_text("❌ Tu n'es pas le chef d'un gang.")
            return

        gang_id, gang_name, _ = my_gang
        async with db.execute(
            "SELECT * FROM gang_members WHERE gang_id=? AND user_id=?", (gang_id, target.id)
        ) as cur:
            if not await cur.fetchone():
                await update.message.reply_text(f"❌ {target.full_name} n'est pas dans ton gang.")
                return

        await db.execute(
            "UPDATE gang_members SET role='Chef' WHERE gang_id=? AND user_id=?", (gang_id, target.id)
        )
        await db.execute(
            "UPDATE gang_members SET role='Soldat' WHERE gang_id=? AND user_id=?", (gang_id, user.id)
        )
        await db.commit()

    await update.message.reply_text(
        f"👑 **{target.full_name}** est maintenant le chef de **{gang_name}**.\n"
        f"Tu es passé au rang de Soldat.",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_ganghold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hold-up collectif : tous les membres gagnent argent et réputation."""
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT g.gang_id, g.name, g.treasury, g.reputation FROM gang_members gm JOIN gangs g ON g.gang_id=gm.gang_id WHERE gm.user_id=?",
            (user.id,)
        ) as cur:
            gang = await cur.fetchone()
    if not gang:
        await update.message.reply_text("❌ Tu n'es dans aucun gang.")
        return

    gang_id, gang_name, treasury, rep = gang
    last_hold = await get_gang_last_hold(gang_id)
    if now() - last_hold < 3600:
        remaining = 3600 - (now() - last_hold)
        await update.message.reply_text(f"⏳ Hold-up déjà effectué récemment. Prochain dans {fmt_time(remaining)}.")
        return

    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db2:
        async with db2.execute("SELECT user_id FROM gang_members WHERE gang_id=?", (gang_id,)) as cur:
            members = [row[0] for row in await cur.fetchall()]

    success_chance = min(0.95, 0.3 + rep / 200)
    if random.random() > success_chance:
        loss = int(treasury * 0.1) if treasury > 0 else 0
        new_rep = max(0, rep - 10)
        await update_gang_field(gang_id, "reputation", new_rep)
        if loss > 0:
            await update_gang_field(gang_id, "treasury", treasury - loss)
        await update_gang_last_hold(gang_id, now())
        await update.message.reply_text(
            f"💥 **Hold-up échoué pour {gang_name} !**\n\n"
            f"La police a déjoué le plan.\n"
            f"⭐ Réputation : -10\n"
            f"{'💰 Perte : ' + fmt(loss) if loss else ''}",
            parse_mode="Markdown"
        )
        return

    base_reward = random.randint(50_000, 200_000)
    rep_gain = random.randint(5, 15)
    per_member = base_reward // len(members) if members else 0
    for uid in members:
        await update_balance(uid, per_member)
    await update_gang_field(gang_id, "treasury", treasury + base_reward)
    await update_gang_field(gang_id, "reputation", min(100, rep + rep_gain))
    await update_gang_last_hold(gang_id, now())

    await update.message.reply_text(
        f"💰 **Hold-up réussi !**\n\n"
        f"Le gang **{gang_name}** a braqué une banque !\n"
        f"👥 {len(members)} membres ont reçu {fmt(per_member)} chacun.\n"
        f"🏦 Caisse du gang : +{fmt(base_reward)}\n"
        f"⭐ Réputation : +{rep_gain}\n\n"
        f"_La légende du gang grandit..._",
        parse_mode="Markdown"
    )


async def get_gang_last_hold(gang_id: int) -> int:
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute("SELECT last_hold FROM gangs WHERE gang_id=?", (gang_id,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def update_gang_last_hold(gang_id: int, timestamp: int):
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute("UPDATE gangs SET last_hold=? WHERE gang_id=?", (timestamp, gang_id))
        await db.commit()


async def update_gang_field(gang_id: int, field: str, value):
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(f"UPDATE gangs SET {field}=? WHERE gang_id=?", (value, gang_id))
        await db.commit()


@require_registered
@require_free
async def cmd_gangupgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Améliorer la réputation du gang avec de l'argent."""
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT g.gang_id, g.name, g.treasury, g.reputation, gm.role FROM gang_members gm JOIN gangs g ON g.gang_id=gm.gang_id WHERE gm.user_id=?",
            (user.id,)
        ) as cur:
            gang = await cur.fetchone()
    if not gang or gang[4] not in ("Chef", "Lieutenant"):
        await update.message.reply_text("❌ Seuls le chef et les lieutenants peuvent améliorer le gang.")
        return

    gang_id, name, treasury, rep, _ = gang
    cost = 100_000
    if treasury < cost:
        await update.message.reply_text(f"❌ Caisse insuffisante. Coût : {fmt(cost)} | Caisse : {fmt(treasury)}")
        return

    new_rep = min(100, rep + 10)
    await update_gang_field(gang_id, "treasury", treasury - cost)
    await update_gang_field(gang_id, "reputation", new_rep)

    await update.message.reply_text(
        f"⭐ **Gang {name} amélioré !**\n\n"
        f"💰 Coût : {fmt(cost)}\n"
        f"⭐ Réputation : {rep} → {new_rep}\n"
        f"🏦 Nouvelle caisse : {fmt(treasury - cost)}",
        parse_mode="Markdown"
    )


@require_registered
@require_free
async def cmd_gangclassement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Classement des gangs par réputation et trésorerie."""
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT name, reputation, treasury, (SELECT COUNT(*) FROM gang_members WHERE gang_id=gangs.gang_id) as membres
            FROM gangs ORDER BY reputation DESC, treasury DESC LIMIT 15
        """) as cur:
            gangs = await cur.fetchall()

    if not gangs:
        await update.message.reply_text("📋 Aucun gang enregistré.")
        return

    text = "🏆 **Classement des gangs**\n\n"
    medals = ["🥇", "🥈", "🥉"] + ["⭐"] * 12
    for i, g in enumerate(gangs):
        text += f"{medals[i]} **{g['name']}**\n"
        text += f"   ⭐ {g['reputation']} | 💰 {fmt(g['treasury'])} | 👥 {g['membres']}\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance (à appeler depuis le scheduler)
# ─────────────────────────────────────────────────────────────────────────────

async def process_gang_maintenance():
    """Frais quotidiens des gangs (5% de la trésorerie ou baisse de réputation)."""
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute("SELECT gang_id, treasury, reputation FROM gangs") as cur:
            gangs = await cur.fetchall()

    for gang_id, treasury, rep in gangs:
        cost = int(treasury * 0.05) if treasury > 0 else 0
        if cost > 0 and treasury >= cost:
            await update_gang_field(gang_id, "treasury", treasury - cost)
        else:
            new_rep = max(0, rep - 5)
            await update_gang_field(gang_id, "reputation", new_rep)