# handlers/social.py — LIFESIM ULTRA V2
# Module social ultime : liberté totale, interactions multijoueur, accords volontaires.
# Aucune narration imposée – seuls des chiffres et des confirmations d'action.
# Ajout du bonus luxe pour les gains sociaux

import random
import re
import json
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import DB_PATH, get_user, update_balance, increment_field, update_field, now
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, fmt_duration
from config import (
    SOCIAL_PLATFORMS, SOCIAL_COLLAB_COST, SOCIAL_TRANSFER_TAX,
    SOCIAL_TREND_COST, SOCIAL_TREND_DURATION, SOCIAL_TREND_MULTIPLIER,
    SOCIAL_STORY_DURATION, SOCIAL_MIN_FOLLOWERS_FOR_LIVE,
    SOCIAL_COIN_NAME, SOCIAL_COIN_STARTING_BALANCE, ADMIN_IDS
)
from handlers.competitions import on_social_gain
from handlers.vehicles import get_active_vehicle  # Import pour le bonus luxe


# ============================================================================
# 1. COMMANDES DE BASE (poster, story, live, analytics)
# ============================================================================

@require_registered
@require_free
async def cmd_plateformes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liste les plateformes et les commandes disponibles."""
    text = "📱 <b>Plateformes sociales</b>\n\n" + "\n".join(f"• {p}" for p in SOCIAL_PLATFORMS)
    text += "\n\n📤 /poster [plateforme] [message libre]"
    text += "\n📸 /story [plateforme] [message libre]"
    text += "\n🎥 /live [plateforme] [durée_h]"
    text += "\n🤝 /collab @utilisateur [--type live|giveaway|cross] [--duree h]"
    text += "\n💰 /vendre_followers @utilisateur [quantité] [prix]"
    text += "\n⭐ /noter @utilisateur +1|-1"
    text += "\n🏆 /classement_social"
    text += "\n🏷️ /lancer_tendance #hashtag [mise]"
    text += "\n📊 /analytiques [plateforme] [--periode 7j]"
    text += "\n👥 /creer_communaute [nom]"
    text += "\n🔄 /partager @utilisateur"
    text += "\n💎 /socialcoins"
    text += "\n🎁 /donner_socialcoins @utilisateur [montant]"
    text += "\n📋 /mesabonnes"
    await update.message.reply_text(text, parse_mode="HTML")


@require_registered
@require_free
@cooldown("post_cooldown", 7200)
async def cmd_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Publie un message libre. Réponse factuelle."""
    if len(context.args) < 2:
        await update.message.reply_text(f"Usage : /poster [plateforme] [message libre]\nPlateformes : {', '.join(SOCIAL_PLATFORMS)}")
        return
    platform = context.args[0]
    if platform not in SOCIAL_PLATFORMS:
        await update.message.reply_text("Plateforme inconnue.")
        return
    user = update.effective_user
    u = await get_user(user.id)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM social_media WHERE user_id=? AND platform=?", (user.id, platform))
        acc = await cursor.fetchone()
        acc = dict(acc) if acc else None

    if acc and (now() - acc.get("last_post", 0)) < 7200:
        rem = 7200 - (now() - acc["last_post"])
        await update.message.reply_text(f"⏳ Déjà posté récemment. Attends {fmt_duration(rem)}.")
        return
    if not acc:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO social_media (user_id, platform, followers, posts, revenue_per_day, last_post) VALUES (?,?,0,0,0,0)",
                (user.id, platform)
            )
            await db.commit()
        acc = {"followers": 0, "posts": 0, "revenue_per_day": 0, "last_post": 0}

    current_followers = acc["followers"]
    creativity = await get_skill(user.id, "Créativité")
    charisma = await get_skill(user.id, "Charisme")
    base_gain = random.randint(10, 200)
    skill_mult = 1 + (creativity + charisma) * 0.05
    follower_gain = int(base_gain * skill_mult)
    karma = u.get("karma", 0)
    if karma > 0:
        follower_gain = int(follower_gain * (1 + min(0.5, karma/1000)))
    trend_mult = await get_pending_trend_multiplier(user.id)
    if trend_mult > 1.0:
        follower_gain = int(follower_gain * trend_mult)
        await clear_pending_trend(user.id)
    from handlers.events import get_event_effect
    event_effect = await get_event_effect()
    if event_effect.get("social_boost", 1.0) > 1.0:
        follower_gain = int(follower_gain * event_effect["social_boost"])
    collab_mult, collab_count = await get_active_collab_bonus(user.id)
    community_mult, community_name, community_size = await get_community_bonus(user.id)
    engagement_mult = await get_platform_engagement_multiplier(user.id, platform)
    rating_mult = await get_social_rating_multiplier(user.id)
    follower_gain = int(follower_gain * collab_mult * community_mult * engagement_mult * rating_mult)
    
    # ─── BONUS LUXE DU VÉHICULE (si luxe > 80) ───
    active_vehicle = await get_active_vehicle(user.id)
    luxe_bonus_msg = ""
    if active_vehicle:
        vehicle_luxe = active_vehicle.get("luxury", 0)
        if vehicle_luxe > 80:
            luxe_bonus = 1 + (vehicle_luxe - 80) / 100  # +0% à +20%
            follower_gain = int(follower_gain * luxe_bonus)
            luxe_bonus_msg = f"\n🚗 Bonus luxe (véhicule) : x{luxe_bonus:.2f}"
        elif vehicle_luxe > 0:
            luxe_bonus_msg = f"\n🚗 Luxe du véhicule : {vehicle_luxe}/100 (seuil bonus : 80)"
    else:
        luxe_bonus_msg = "\n🚫 Aucun véhicule actif - bonus luxe non disponible"
    
    viral = random.random() < min(0.24, 0.03 + charisma * 0.01 + collab_count * 0.03)
    viral_mult = round(random.uniform(1.8, 3.0), 2) if viral else 1.0
    follower_gain = max(1, int(follower_gain * viral_mult))
    coin_gain = max(1, follower_gain // 250)

    new_followers = current_followers + follower_gain
    if new_followers >= 1_000_000:
        daily_rev = int(new_followers * 0.001)
    elif new_followers >= 100_000:
        daily_rev = int(new_followers * 0.0005)
    elif new_followers >= 10_000:
        daily_rev = int(new_followers * 0.0002)
    else:
        daily_rev = 0

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE social_media SET followers=?, posts=posts+1, revenue_per_day=?, last_post=? WHERE user_id=? AND platform=?",
            (new_followers, daily_rev, now(), user.id, platform)
        )
        await db.commit()
    await update_field(user.id, "social_followers", u.get("social_followers", 0) + follower_gain)
    await increment_field(user.id, "xp", 40)
    await give_social_coins(user.id, coin_gain)
    await add_social_history(user.id, platform, "post", follower_gain)
    await check_social_badges(user.id)
    await on_social_gain(user.id, follower_gain)

    msg = f"📱 Posté sur {platform}\n👥 +{follower_gain:,} abonnés\n👥 Total : {new_followers:,}"
    if daily_rev > 0:
        msg += f"\n💰 Revenu quotidien : {fmt(daily_rev)}"
    if collab_count:
        msg += f"\n🤝 Bonus collabs : x{collab_mult:.2f}"
    if community_name:
        msg += f"\n👥 Communauté {community_name} : x{community_mult:.2f} ({community_size} membres)"
    if engagement_mult > 1.0:
        msg += f"\n🔥 Engagement : x{engagement_mult:.2f}"
    if viral:
        msg += f"\n🚀 Post viral : x{viral_mult}"
    msg += luxe_bonus_msg
    msg += f"\n💎 +{coin_gain} {SOCIAL_COIN_NAME}"
    msg += f"\n✨ +40 XP"
    await update.message.reply_text(msg, parse_mode="Markdown")


@require_registered
@require_free
@cooldown("story_cooldown", 1800)
async def cmd_story(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Publie une story (disparaît après 24h, gain réduit)."""
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /story [plateforme] [message]")
        return
    platform = context.args[0]
    if platform not in SOCIAL_PLATFORMS:
        await update.message.reply_text("Plateforme inconnue.")
        return
    user = update.effective_user
    gain = await compute_follower_gain_simple(user.id, platform, factor=0.4)
    collab_mult, collab_count = await get_active_collab_bonus(user.id)
    community_mult, community_name, community_size = await get_community_bonus(user.id)
    engagement_mult = await get_platform_engagement_multiplier(user.id, platform)
    gain = max(1, int(gain * collab_mult * community_mult * engagement_mult))
    
    # ─── BONUS LUXE DU VÉHICULE (si luxe > 80) ───
    active_vehicle = await get_active_vehicle(user.id)
    luxe_bonus_msg = ""
    if active_vehicle:
        vehicle_luxe = active_vehicle.get("luxury", 0)
        if vehicle_luxe > 80:
            luxe_bonus = 1 + (vehicle_luxe - 80) / 100
            gain = int(gain * luxe_bonus)
            luxe_bonus_msg = f"\n🚗 Bonus luxe : x{luxe_bonus:.2f}"
    
    coin_gain = max(1, gain // 400)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO social_stories (user_id, platform, content, created_at, expires_at) VALUES (?,?,?,?,?)",
            (user.id, platform, "story", now(), now() + SOCIAL_STORY_DURATION)
        )
        await db.commit()
    await add_social_history(user.id, platform, "story", gain)
    await update_field(user.id, "social_followers", (await get_user(user.id)).get("social_followers", 0) + gain)
    await increment_field(user.id, "xp", 15)
    await on_social_gain(user.id, gain)
    await give_social_coins(user.id, coin_gain)
    await set_engagement_boost(user.id, platform, 1.15, 3600)
    
    text = f"📸 Story publiée sur {platform}\n👥 +{gain} abonnés\n💎 +{coin_gain} {SOCIAL_COIN_NAME}\n✨ +15 XP"
    if collab_count:
        text += f"\n🤝 Bonus collabs : x{collab_mult:.2f}"
    if community_name:
        text += f"\n👥 Communauté {community_name} : x{community_mult:.2f} ({community_size} membres)"
    if engagement_mult > 1.0:
        text += f"\n🔥 Engagement : x{engagement_mult:.2f}"
    if luxe_bonus_msg:
        text += f"\n{luxe_bonus_msg}"
    await update.message.reply_text(text)


@require_registered
@require_free
@cooldown("live_cooldown", 43200)
async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lance un live (autres joueurs peuvent donner)."""
    if len(context.args) < 1:
        await update.message.reply_text("Usage : /live [plateforme] [durée_h] (max 4h)")
        return
    platform = context.args[0]
    duration_h = 1
    if len(context.args) > 1:
        try:
            duration_h = min(4, int(context.args[1]))
        except:
            pass
    if platform not in SOCIAL_PLATFORMS:
        await update.message.reply_text("Plateforme inconnue.")
        return
    user = update.effective_user
    total_followers = await get_total_followers(user.id)
    if total_followers < SOCIAL_MIN_FOLLOWERS_FOR_LIVE:
        await update.message.reply_text(f"❌ Il faut au moins {SOCIAL_MIN_FOLLOWERS_FOR_LIVE} abonnés pour faire un live.")
        return
    collab_mult, collab_count = await get_active_collab_bonus(user.id)
    community_mult, community_name, community_size = await get_community_bonus(user.id)
    gain = int((total_followers * 0.01 * duration_h + random.randint(10, 500)) * collab_mult * community_mult)
    
    # ─── BONUS LUXE DU VÉHICULE (si luxe > 80) ───
    active_vehicle = await get_active_vehicle(user.id)
    luxe_bonus_msg = ""
    if active_vehicle:
        vehicle_luxe = active_vehicle.get("luxury", 0)
        if vehicle_luxe > 80:
            luxe_bonus = 1 + (vehicle_luxe - 80) / 100
            gain = int(gain * luxe_bonus)
            luxe_bonus_msg = f"\n🚗 Bonus luxe : x{luxe_bonus:.2f}"
    
    coin_gain = max(2, gain // 300)
    await add_social_history(user.id, platform, "live", gain)
    await update_field(user.id, "social_followers", (await get_user(user.id)).get("social_followers", 0) + gain)
    await give_social_coins(user.id, coin_gain)
    await on_social_gain(user.id, gain)
    live_id = await register_live(user.id, platform, duration_h)
    
    text = (
        f"🎥 Live démarré sur {platform} pour {duration_h}h\n"
        f"👥 Abonnés gagnés : ~{gain}\n"
        f"💎 +{coin_gain} {SOCIAL_COIN_NAME}\n"
        f"💸 Dons possibles : /donner_live {live_id} <montant>"
    )
    if collab_count:
        text += f"\n🤝 Bonus collabs : x{collab_mult:.2f}"
    if community_name:
        text += f"\n👥 Communauté {community_name} : x{community_mult:.2f} ({community_size} membres)"
    if luxe_bonus_msg:
        text += f"\n{luxe_bonus_msg}"
    await update.message.reply_text(text)


@require_registered
@require_free
async def cmd_donner_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Donne de l'argent à un streameur en live."""
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /donner_live <live_id> <montant>")
        return
    try:
        live_id = int(context.args[0])
        amount = int(context.args[1])
        if amount <= 0:
            raise ValueError
    except:
        await update.message.reply_text("Montant invalide.")
        return
    donor = update.effective_user
    donor_data = await get_user(donor.id)
    if donor_data["balance"] < amount:
        await update.message.reply_text("Solde insuffisant.")
        return
    live = await get_live_info(live_id)
    if not live or live["ends_at"] < now():
        await update.message.reply_text("Ce live n'est plus actif.")
        return
    await update_balance(donor.id, -amount)
    await update_balance(live["user_id"], amount)
    bonus_followers = max(1, amount // 1500)
    coin_gain = max(1, amount // 5000)
    await add_followers(live["user_id"], bonus_followers, "live_tip")
    await give_social_coins(live["user_id"], coin_gain)
    try:
        await context.bot.send_message(
            live["user_id"],
            f"💸 {donor.full_name} t'a envoyé {fmt(amount)} pendant ton live.\n"
            f"👥 +{bonus_followers} abonnés bonus\n"
            f"💎 +{coin_gain} {SOCIAL_COIN_NAME}"
        )
    except Exception:
        pass
    await update.message.reply_text(
        f"💸 Don de {fmt(amount)} effectué.\n"
        f"Le streameur gagne aussi 👥 +{bonus_followers} et 💎 +{coin_gain} {SOCIAL_COIN_NAME}."
    )


@require_registered
async def cmd_analytiques(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche l'historique des gains (chiffres bruts)."""
    user = update.effective_user
    days = 7
    platform = None
    for arg in context.args:
        if arg.startswith("--periode"):
            try:
                days = int(arg.split("=")[1])
            except:
                pass
        elif arg in SOCIAL_PLATFORMS:
            platform = arg
    async with aiosqlite.connect(DB_PATH) as db:
        cutoff = now() - days * 86400
        if platform:
            cursor = await db.execute(
                "SELECT date(timestamp, 'unixepoch') as day, SUM(gain) as gain FROM social_history WHERE user_id=? AND platform=? AND timestamp > ? GROUP BY day ORDER BY day DESC LIMIT ?",
                (user.id, platform, cutoff, days)
            )
        else:
            cursor = await db.execute(
                "SELECT date(timestamp, 'unixepoch') as day, SUM(gain) as gain FROM social_history WHERE user_id=? AND timestamp > ? GROUP BY day ORDER BY day DESC LIMIT ?",
                (user.id, cutoff, days)
            )
        rows = await cursor.fetchall()
    if not rows:
        await update.message.reply_text("Aucune donnée pour la période demandée.")
        return
    lines = [f"📈 Évolution des abonnés (derniers {days} jours)"]
    for row in rows:
        lines.append(f"{row[0]} : +{row[1]} abonnés")
    await update.message.reply_text("\n".join(lines))


# ============================================================================
# 2. COMMANDES MULTIJOUEUR AVEC ACCORD
# ============================================================================

@require_registered
@require_free
async def cmd_collab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Propose une collaboration à un autre joueur (nécessite acceptation)."""
    if not update.message.reply_to_message:
        await update.message.reply_text("Répondez au message du joueur avec qui collaborer.")
        return
    target = update.message.reply_to_message.from_user
    if target.id == update.effective_user.id:
        await update.message.reply_text("Impossible de collaborer avec soi-même.")
        return
    collab_type = "live"
    duration = 24
    for i, arg in enumerate(context.args):
        if arg == "--type" and i+1 < len(context.args):
            if context.args[i+1] in ("live", "giveaway", "cross"):
                collab_type = context.args[i+1]
        if arg == "--duree" and i+1 < len(context.args):
            try:
                duration = min(72, int(context.args[i+1]))
            except:
                pass
    u = await get_user(update.effective_user.id)
    if u["balance"] < SOCIAL_COLLAB_COST:
        await update.message.reply_text(f"Solde insuffisant, besoin de {fmt(SOCIAL_COLLAB_COST)}.")
        return
    request_id = await create_collab_request(update.effective_user.id, target.id, collab_type, duration)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accepter", callback_data=f"collab_accept_{request_id}"),
         InlineKeyboardButton("❌ Refuser", callback_data=f"collab_refuse_{request_id}")]
    ])
    await context.bot.send_message(
        target.id,
        f"🤝 {update.effective_user.full_name} veut collaborer avec vous.\nType : {collab_type}\nDurée : {duration}h",
        reply_markup=keyboard
    )
    await update.message.reply_text("Demande de collaboration envoyée.")


@require_registered
@require_free
async def cmd_vendre_followers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Propose une vente de followers (nécessite acceptation de l'acheteur)."""
    if len(context.args) < 3:
        await update.message.reply_text("Usage : /vendre_followers @utilisateur <quantité> <prix_total>")
        return
    target_mention = context.args[0]
    try:
        quantity = int(context.args[1])
        price = int(context.args[2])
        if quantity <= 0 or price <= 0:
            raise ValueError
    except:
        await update.message.reply_text("Quantité et prix doivent être des entiers positifs.")
        return
    user = update.effective_user
    total_followers = await get_total_followers(user.id)
    if total_followers < quantity:
        await update.message.reply_text(f"Vous n'avez que {total_followers} abonnés.")
        return
    target_id = await user_id_from_mention(target_mention)
    if not target_id:
        await update.message.reply_text("Utilisateur introuvable.")
        return
    offer_id = await create_follower_offer(user.id, target_id, quantity, price)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Acheter", callback_data=f"buy_followers_{offer_id}"),
         InlineKeyboardButton("❌ Refuser", callback_data=f"refuse_followers_{offer_id}")]
    ])
    await context.bot.send_message(
        target_id,
        f"💵 Offre de {update.effective_user.full_name} : {quantity} abonnés pour {fmt(price)}.\nTaxe incluse.",
        reply_markup=keyboard
    )
    await update.message.reply_text("Offre envoyée. En attente d'acceptation.")


@require_registered
@require_free
@cooldown("social_rating_cooldown", 3600)
async def cmd_noter_social(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Note un autre joueur sur son influence (+1 ou -1)."""
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /noter @utilisateur +1|-1")
        return
    target_mention = context.args[0]
    try:
        delta = int(context.args[1])
        if delta not in (-1, 1):
            raise ValueError
    except:
        await update.message.reply_text("Note doit être +1 ou -1.")
        return
    target_id = await user_id_from_mention(target_mention)
    if not target_id or target_id == update.effective_user.id:
        await update.message.reply_text("Utilisateur invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT delta FROM social_reputation WHERE user_id=? AND rater_id=?",
            (target_id, update.effective_user.id)
        )
        previous = await cursor.fetchone()
        diff = delta - (previous[0] if previous else 0)
        await db.execute(
            "INSERT OR REPLACE INTO social_reputation (user_id, rater_id, delta, created_at) VALUES (?,?,?,?)",
            (target_id, update.effective_user.id, delta, now())
        )
        await db.execute("UPDATE users SET social_rating = social_rating + ? WHERE user_id=?", (diff, target_id))
        await db.commit()
    await update.message.reply_text(f"⭐ Note {delta:+d} pour {target_mention}")


@require_registered
async def cmd_classement_social(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Top influenceurs (nombre total d'abonnés)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, username, full_name, social_followers FROM users WHERE social_followers > 0 ORDER BY social_followers DESC LIMIT 10"
        )
        rows = await cursor.fetchall()
    if not rows:
        await update.message.reply_text("Aucun influenceur pour l'instant.")
        return
    lines = ["<b>🏆 TOP INFLUENCEURS</b>"]
    for i, (uid, username, fullname, followers) in enumerate(rows, 1):
        name = username or fullname or f"Joueur {uid}"
        import html
        name = html.escape(name)
        lines.append(f"{i}. {name} — 👥 {followers:,} abonnés")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ============================================================================
# 3. COMMUNAUTÉS (groupes sociaux)
# ============================================================================

@require_registered
@require_free
@cooldown("create_community_cooldown", 86400)
async def cmd_creer_communaute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Crée une communauté (nécessite 1000 SocialCoins)."""
    if len(context.args) < 1:
        await update.message.reply_text("Usage : /creer_communaute <nom>")
        return
    name = " ".join(context.args)
    user = update.effective_user
    coins = await get_social_coins(user.id)
    if coins < 1000:
        await update.message.reply_text(f"❌ Il faut 1000 {SOCIAL_COIN_NAME}.")
        return
    await spend_social_coins(user.id, 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO social_communities (name, owner_id, created_at) VALUES (?,?,?)", (name, user.id, now()))
        cursor = await db.execute("SELECT last_insert_rowid()")
        community_id = (await cursor.fetchone())[0]
        await db.execute(
            "INSERT INTO social_community_members (community_id, user_id, role, joined_at) VALUES (?,?,?,?)",
            (community_id, user.id, "owner", now())
        )
        await db.commit()
    await update.message.reply_text(
        f"👥 Communauté **{name}** créée (ID {community_id}).\n"
        f"Invitez des membres avec /inviter_communaute {community_id} @utilisateur\n"
        f"Consultez-la avec /communaute"
    )


@require_registered
@require_free
async def cmd_inviter_communaute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Invite un joueur à rejoindre une communauté."""
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /inviter_communaute <id> @utilisateur")
        return
    try:
        community_id = int(context.args[0])
    except:
        await update.message.reply_text("ID invalide.")
        return
    target_mention = context.args[1]
    target_id = await user_id_from_mention(target_mention)
    if not target_id:
        await update.message.reply_text("Utilisateur introuvable.")
        return
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT role FROM social_community_members WHERE community_id=? AND user_id=?", (community_id, user.id))
        row = await cursor.fetchone()
        if not row or row[0] not in ('owner', 'admin'):
            await update.message.reply_text("Vous n'êtes pas autorisé à inviter dans cette communauté.")
            return
        await db.execute(
            "INSERT INTO social_community_invites (community_id, invited_id, inviter_id, created_at) VALUES (?,?,?,?)",
            (community_id, target_id, user.id, now())
        )
        await db.commit()
    await update.message.reply_text(f"Invitation envoyée à {target_mention}.")
    try:
        await context.bot.send_message(
            target_id,
            f"👥 Invitation à rejoindre la communauté #{community_id}.\n"
            f"Utilise /rejoindre_communaute {community_id} pour accepter."
        )
    except Exception:
        pass


@require_registered
async def cmd_communaute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche la communauté du joueur ou les plus grosses communautés."""
    user = update.effective_user
    community = await get_user_community(user.id)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if community:
            async with db.execute("""
                SELECT u.full_name, m.role
                FROM social_community_members m
                JOIN users u ON u.user_id = m.user_id
                WHERE m.community_id=?
                ORDER BY CASE WHEN m.role='owner' THEN 0 WHEN m.role='admin' THEN 1 ELSE 2 END, u.full_name
                LIMIT 12
            """, (community["community_id"],)) as cur:
                members = await cur.fetchall()
            lines = [
                f"👥 **{community['name']}**",
                f"ID : {community['community_id']}",
                f"Membres : {community['member_count']}",
                "",
                "Participants :",
            ]
            for m in members:
                badge = "👑" if m["role"] == "owner" else ("🛡️" if m["role"] == "admin" else "•")
                lines.append(f"{badge} {m['full_name']} — {m['role']}")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            return

        async with db.execute("""
            SELECT c.community_id, c.name, COUNT(m.user_id) AS members
            FROM social_communities c
            LEFT JOIN social_community_members m ON m.community_id = c.community_id
            WHERE c.disbanded = 0
            GROUP BY c.community_id
            ORDER BY members DESC, c.created_at ASC
            LIMIT 5
        """) as cur:
            rows = await cur.fetchall()
    if not rows:
        await update.message.reply_text("Aucune communauté pour l'instant. Créez-en une avec /creer_communaute.")
        return
    lines = ["👥 **COMMUNAUTÉS POPULAIRES**"]
    for row in rows:
        lines.append(f"#{row['community_id']} — {row['name']} ({row['members']} membres)")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@require_registered
@require_free
async def cmd_rejoindre_communaute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accepte une invitation à une communauté."""
    if len(context.args) != 1:
        await update.message.reply_text("Usage : /rejoindre_communaute <id>")
        return
    try:
        community_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID invalide.")
        return
    user = update.effective_user
    if await get_user_community(user.id):
        await update.message.reply_text("Vous êtes déjà dans une communauté.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT invite_id FROM social_community_invites WHERE community_id=? AND invited_id=? AND status='pending'",
            (community_id, user.id)
        )
        invite = await cursor.fetchone()
        if not invite:
            await update.message.reply_text("Aucune invitation en attente pour cette communauté.")
            return
        cursor = await db.execute(
            "SELECT name FROM social_communities WHERE community_id=? AND disbanded=0",
            (community_id,)
        )
        community = await cursor.fetchone()
        if not community:
            await update.message.reply_text("Cette communauté n'existe plus.")
            return
        await db.execute(
            "INSERT OR IGNORE INTO social_community_members (community_id, user_id, role, joined_at) VALUES (?,?,?,?)",
            (community_id, user.id, "member", now())
        )
        await db.execute(
            "UPDATE social_community_invites SET status='accepted' WHERE invite_id=?",
            (invite["invite_id"],)
        )
        await db.commit()
    await update.message.reply_text(f"✅ Vous avez rejoint la communauté **{community['name']}**.", parse_mode="Markdown")


# ============================================================================
# 4. PARTAGES ET TENDANCES
# ============================================================================

@require_registered
@require_free
@cooldown("share_cooldown", 3600)
async def cmd_partager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Partage le dernier post d'un autre joueur (gain minime)."""
    if not update.message.reply_to_message:
        await update.message.reply_text("Répondez au message du joueur dont vous voulez partager le post.")
        return
    target = update.message.reply_to_message.from_user
    if target.id == update.effective_user.id:
        await update.message.reply_text("Vous ne pouvez pas vous partager vous-même.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM social_media WHERE user_id=? AND posts>0", (target.id,))
        if not await cursor.fetchone():
            await update.message.reply_text("Ce joueur n'a jamais publié.")
            return
    gain = random.randint(5, 50)
    await add_followers(update.effective_user.id, gain, "share")
    await add_followers(target.id, gain, "share")
    await update.message.reply_text(
        f"🔄 Partage effectué\n👥 +{gain} abonnés pour vous\n👥 +{gain} abonnés pour {target.full_name}"
    )


@require_registered
@require_free
@cooldown("trend_cooldown", 86400)
async def cmd_lancer_tendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lance un hashtag tendance (mise obligatoire)."""
    if len(context.args) < 1:
        await update.message.reply_text("Usage : /lancer_tendance #hashtag [mise]")
        return
    hashtag = context.args[0]
    if not hashtag.startswith("#"):
        hashtag = "#" + hashtag
    bet = SOCIAL_TREND_COST
    if len(context.args) > 1:
        try:
            bet = int(context.args[1])
        except:
            pass
    user = update.effective_user
    u = await get_user(user.id)
    if u["balance"] < bet:
        await update.message.reply_text(f"Solde insuffisant. Besoin de {fmt(bet)}.")
        return
    await update_balance(user.id, -bet)
    expires = now() + SOCIAL_TREND_DURATION
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO social_trends (hashtag, creator_id, multiplier, expires_at) VALUES (?,?,?,?)",
            (hashtag, user.id, SOCIAL_TREND_MULTIPLIER, expires)
        )
        await db.commit()
    await update.message.reply_text(
        f"🏷️ Tendance {hashtag} lancée pour {fmt_duration(SOCIAL_TREND_DURATION)}\n"
        f"Multiplicateur x{SOCIAL_TREND_MULTIPLIER}\nUtilisez /utiliser_tendance {hashtag} avant de poster."
    )


@require_registered
@require_free
async def cmd_utiliser_tendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Utilise une tendance active pour le prochain post."""
    if len(context.args) < 1:
        await update.message.reply_text("Usage : /utiliser_tendance #hashtag")
        return
    hashtag = context.args[0]
    if not hashtag.startswith("#"):
        hashtag = "#" + hashtag
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT multiplier, expires_at FROM social_trends WHERE hashtag=? AND expires_at>?", (hashtag, now()))
        trend = await cursor.fetchone()
        if not trend:
            await update.message.reply_text("Cette tendance n'est pas active.")
            return
        multiplier = trend[0]
        await db.execute(
            "INSERT OR REPLACE INTO social_user_trends (user_id, hashtag, multiplier, used) VALUES (?,?,?,0)",
            (update.effective_user.id, hashtag, multiplier)
        )
        await db.commit()
    await update.message.reply_text(f"🏷️ Tendance {hashtag} activée pour votre prochain post (x{multiplier})")


# ============================================================================
# 5. SOCIALCOINS (économie interne)
# ============================================================================

@require_registered
async def cmd_socialcoins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche son solde de SocialCoins."""
    coins = await get_social_coins(update.effective_user.id)
    await update.message.reply_text(f"💎 {SOCIAL_COIN_NAME} : {coins}")


@require_registered
@require_free
async def cmd_donner_socialcoins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Donne des SocialCoins à un autre joueur."""
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /donner_socialcoins @utilisateur <montant>")
        return
    target_mention = context.args[0]
    try:
        amount = int(context.args[1])
        if amount <= 0:
            raise ValueError
    except:
        await update.message.reply_text("Montant invalide.")
        return
    user = update.effective_user
    coins = await get_social_coins(user.id)
    if coins < amount:
        await update.message.reply_text(f"Solde {SOCIAL_COIN_NAME} insuffisant.")
        return
    target_id = await user_id_from_mention(target_mention)
    if not target_id or target_id == user.id:
        await update.message.reply_text("Utilisateur invalide.")
        return
    await spend_social_coins(user.id, amount)
    await give_social_coins(target_id, amount)
    await update.message.reply_text(f"💸 {amount} {SOCIAL_COIN_NAME} donnés à {target_mention}")


# ============================================================================
# 6. COMMANDES UTILITAIRES (stats, etc.)
# ============================================================================

@require_registered
async def cmd_mesabonnes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les stats sociales (chiffres bruts)."""
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT platform, followers, posts, revenue_per_day FROM social_media WHERE user_id=? ORDER BY followers DESC",
            (user.id,)
        )
        rows = await cursor.fetchall()
    if not rows:
        await update.message.reply_text("Aucune activité sociale.")
        return
    lines = ["📊 **VOS RÉSEAUX**"]
    total_followers = 0
    total_revenue = 0
    for r in rows:
        lines.append(f"{r['platform']} : 👥 {r['followers']:,} | 📝 {r['posts']} | 💰 {fmt(r['revenue_per_day'])}/j")
        total_followers += r['followers']
        total_revenue += r['revenue_per_day']
    lines.append(f"━━━━━━━━━━━━\n👥 Total : {total_followers:,} | 💰 Total : {fmt(total_revenue)}/j")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ============================================================================
# 7. CALLBACK HANDLERS (pour les accords)
# ============================================================================

async def collab_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("collab_accept_"):
        request_id = int(data.split("_")[2])
        await apply_collaboration(request_id)
        await query.edit_message_text("✅ Collaboration acceptée. Effets actifs.")
    elif data.startswith("collab_refuse_"):
        request_id = int(data.split("_")[2])
        await refuse_collaboration(request_id)
        await query.edit_message_text("❌ Collaboration refusée.")


async def buy_followers_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    if parts[0] == "buy":
        offer_id = int(parts[2])
        success = await execute_follower_transfer(offer_id)
        if success:
            await query.edit_message_text("✅ Achat effectué. Les abonnés ont été transférés.")
        else:
            await query.edit_message_text("❌ Échec de l'achat (solde insuffisant ou offre expirée).")
    elif parts[0] == "refuse":
        offer_id = int(parts[2])
        await refuse_follower_offer(offer_id)
        await query.edit_message_text("❌ Offre refusée.")


# ============================================================================
# 8. SONDAGES SOCIAUX
# ============================================================================

@require_registered
@require_free
@cooldown("poll_cooldown", 3600)
async def cmd_sondage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Crée un sondage sur sa page sociale."""
    if len(context.args) < 3:
        await update.message.reply_text('Usage : /sondage "Question" "Option1" "Option2" ["Option3"]')
        return
    args_str = " ".join(context.args)
    matches = re.findall(r'"([^"]*)"', args_str)
    if len(matches) < 3:
        await update.message.reply_text("Mettez chaque élément entre guillemets.")
        return
    question = matches[0]
    options = matches[1:5]
    user = update.effective_user
    poll_id = await create_poll(user.id, question, options)
    lines = [f"📊 Sondage #{poll_id} par {user.full_name}", f"❓ {question}"]
    for i, opt in enumerate(options, 1):
        lines.append(f"{i}. {opt}")
    lines.append("Votez avec /vote <id_sondage> <numéro_option>")
    await update.message.reply_text("\n".join(lines))


@require_registered
@require_free
async def cmd_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Vote pour un sondage social."""
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /vote <id_sondage> <numéro_option>")
        return
    try:
        poll_id = int(context.args[0])
        option_idx = int(context.args[1]) - 1
    except:
        await update.message.reply_text("Arguments invalides.")
        return
    user = update.effective_user
    success = await register_vote(poll_id, user.id, option_idx)
    if not success:
        await update.message.reply_text("Déjà voté ou sondage invalide.")
    else:
        await update.message.reply_text(f"Vote enregistré pour l'option {option_idx+1}.")


@require_registered
@require_free
async def cmd_resultats_sondage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les résultats d'un sondage (réservé au créateur du sondage)."""
    if len(context.args) != 1:
        await update.message.reply_text("Usage : /resultats_sondage [id_sondage]")
        return
    try:
        poll_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, question, options, active FROM social_polls WHERE poll_id = ?",
            (poll_id,)
        )
        poll = await cursor.fetchone()
        if not poll or poll[3] == 0:
            await update.message.reply_text("❌ Sondage introuvable ou expiré.")
            return
        creator_id, question, options_json, active = poll
        options = json.loads(options_json)

        user_id = update.effective_user.id
        if user_id != creator_id and user_id not in ADMIN_IDS:
            await update.message.reply_text("🔒 Seul le créateur du sondage peut voir les résultats.")
            return

        cursor = await db.execute(
            "SELECT option_index, COUNT(*) FROM social_poll_votes WHERE poll_id = ? GROUP BY option_index",
            (poll_id,)
        )
        votes = {idx: count for idx, count in await cursor.fetchall()}
        total = sum(votes.values())

        result_text = f"📊 **Résultats du sondage #{poll_id}**\n"
        result_text += f"❓ {question}\n\n"
        for i, opt in enumerate(options):
            count = votes.get(i, 0)
            percent = (count / total * 100) if total > 0 else 0
            bar = "█" * int(percent // 2) + "░" * (50 - int(percent // 2))
            result_text += f"{i+1}. {opt} : {count} vote(s) ({percent:.1f}%)\n"
            result_text += f"   `{bar}`\n\n"
        result_text += f"*Total : {total} votant(s)*"

        await update.message.reply_text(result_text, parse_mode="Markdown")


# ============================================================================
# 9. REVENUS QUOTIDIENS SOCIAUX (pour le scheduler)
# ============================================================================

async def process_social_revenue():
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT user_id, SUM(revenue_per_day) as total_revenue FROM social_media WHERE revenue_per_day > 0 GROUP BY user_id"
        )
        users = await cursor.fetchall()
        for row in users:
            uid = row["user_id"]
            revenue = row["total_revenue"]
            if revenue > 0:
                await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (revenue, uid))
                await db.execute(
                    "INSERT INTO social_log (user_id, revenue, timestamp) VALUES (?,?,?)",
                    (uid, revenue, now())
                )
        await db.commit()


async def process_social_maintenance():
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        current = now()
        await db.execute("UPDATE social_lives SET active=0 WHERE active=1 AND ends_at<=?", (current,))
        await db.execute("UPDATE social_trends SET active=0 WHERE active=1 AND expires_at<=?", (current,))
        await db.execute("DELETE FROM social_active_collabs WHERE expires_at<=?", (current,))
        await db.execute("UPDATE social_polls SET active=0 WHERE active=1 AND created_at<=?", (current - 172800,))
        await db.commit()


# ============================================================================
# 10. FONCTIONS UTILITAIRES INTERNES (base de données)
# ============================================================================

async def get_skill(user_id: int, skill_name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT level FROM skills WHERE user_id=? AND skill_name=?", (user_id, skill_name))
        row = await cursor.fetchone()
        return row[0] if row else 0


async def compute_follower_gain_simple(user_id: int, platform: str, factor: float = 1.0) -> int:
    u = await get_user(user_id)
    creativity = await get_skill(user_id, "Créativité")
    charisma = await get_skill(user_id, "Charisme")
    base = random.randint(5, 100)
    mult = 1 + (creativity + charisma) * 0.03
    gain = int(base * mult * factor)
    karma = u.get("karma", 0)
    if karma > 0:
        gain = int(gain * (1 + min(0.3, karma / 1000)))
    
    # Bonus luxe du véhicule (si luxe > 80)
    active_vehicle = await get_active_vehicle(user_id)
    if active_vehicle:
        vehicle_luxe = active_vehicle.get("luxury", 0)
        if vehicle_luxe > 80:
            bonus = 1 + (vehicle_luxe - 80) / 100  # +0% à +20%
            gain = int(gain * bonus)
    
    return max(1, gain)


async def get_total_followers(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT SUM(followers) FROM social_media WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row[0] else 0


async def get_user_community(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT c.community_id, c.name, m.role, COUNT(all_members.user_id) AS member_count
            FROM social_community_members m
            JOIN social_communities c ON c.community_id = m.community_id
            LEFT JOIN social_community_members all_members ON all_members.community_id = c.community_id
            WHERE m.user_id=? AND c.disbanded=0
            GROUP BY c.community_id, c.name, m.role
        """, (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_active_collab_bonus(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM social_active_collabs WHERE expires_at>? AND (from_id=? OR to_id=?)",
            (now(), user_id, user_id)
        )
        count = (await cursor.fetchone())[0]
    return 1 + min(0.36, count * 0.12), count


async def get_community_bonus(user_id: int):
    community = await get_user_community(user_id)
    if not community:
        return 1.0, None, 0
    members = int(community.get("member_count", 1) or 1)
    return 1 + min(0.25, members * 0.02), community["name"], members


async def get_platform_engagement_multiplier(user_id: int, platform: str) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT multiplier, expires_at FROM social_engagement_boosts WHERE user_id=? AND platform=?",
            (user_id, platform)
        )
        row = await cursor.fetchone()
        if not row:
            return 1.0
        multiplier, expires_at = row
        if expires_at <= now():
            await db.execute(
                "DELETE FROM social_engagement_boosts WHERE user_id=? AND platform=?",
                (user_id, platform)
            )
            await db.commit()
            return 1.0
        return max(1.0, float(multiplier))


async def get_social_rating_multiplier(user_id: int) -> float:
    user = await get_user(user_id)
    rating = int(user.get("social_rating", 0) or 0)
    if rating >= 0:
        return 1 + min(0.25, rating * 0.01)
    return max(0.85, 1 + rating * 0.01)


async def add_followers(user_id: int, gain: int, source: str):
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        cursor = await db.execute(
            "SELECT platform FROM social_media WHERE user_id=? ORDER BY followers DESC LIMIT 1",
            (user_id,)
        )
        row = await cursor.fetchone()
        if row:
            platform = row[0]
            await db.execute(
                "UPDATE social_media SET followers = followers + ? WHERE user_id=? AND platform=?",
                (gain, user_id, platform)
            )
        else:
            platform = SOCIAL_PLATFORMS[0]
            await db.execute(
                "INSERT INTO social_media (user_id, platform, followers, posts, revenue_per_day, last_post) VALUES (?,?,?,0,0,0)",
                (user_id, platform, gain)
            )
        await db.execute(
            "UPDATE users SET social_followers = social_followers + ? WHERE user_id=?",
            (gain, user_id)
        )
        await db.execute(
            "INSERT INTO social_history (user_id, platform, action_type, gain, timestamp) VALUES (?,?,?,?,?)",
            (user_id, platform, source, gain, now())
        )
        await on_social_gain(user_id, gain)
        await db.commit()


async def add_social_history(user_id: int, platform: str, action: str, gain: int):
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute(
            "INSERT INTO social_history (user_id, platform, action_type, gain, timestamp) VALUES (?,?,?,?,?)",
            (user_id, platform, action, gain, now())
        )
        await db.commit()


async def set_engagement_boost(user_id: int, platform: str, mult: float, duration: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO social_engagement_boosts (user_id, platform, multiplier, expires_at) VALUES (?,?,?,?)",
            (user_id, platform, mult, now() + duration)
        )
        await db.commit()


async def get_pending_trend_multiplier(user_id: int) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT multiplier FROM social_user_trends WHERE user_id=? AND used=0", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else 1.0


async def clear_pending_trend(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE social_user_trends SET used=1 WHERE user_id=?", (user_id,))
        await db.commit()


async def register_live(user_id: int, platform: str, duration_h: int) -> int:
    ends = now() + duration_h * 3600
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO social_lives (user_id, platform, started_at, ends_at) VALUES (?,?,?,?)",
            (user_id, platform, now(), ends)
        )
        await db.commit()
        return cursor.lastrowid


async def get_live_info(live_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, ends_at FROM social_lives WHERE live_id=? AND active=1",
            (live_id,)
        )
        row = await cursor.fetchone()
        if row:
            return {"user_id": row[0], "ends_at": row[1]}
    return None


async def check_social_badges(user_id: int):
    total = await get_total_followers(user_id)
    badges = []
    if total >= 1000:
        badges.append("débutant_influence")
    if total >= 10000:
        badges.append("influenceur_local")
    if total >= 100000:
        badges.append("célébrité")
    if total >= 1000000:
        badges.append("méga_star")
    if badges:
        async with aiosqlite.connect(DB_PATH) as db:
            for badge in badges:
                await db.execute(
                    "INSERT OR IGNORE INTO user_badges (user_id, badge, earned_at) VALUES (?,?,?)",
                    (user_id, badge, now())
                )
            await db.commit()


async def get_social_coins(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT social_coins FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else SOCIAL_COIN_STARTING_BALANCE


async def spend_social_coins(user_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET social_coins = social_coins - ? WHERE user_id=?", (amount, user_id))
        await db.commit()


async def give_social_coins(user_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET social_coins = social_coins + ? WHERE user_id=?", (amount, user_id))
        await db.commit()


async def user_id_from_mention(mention: str) -> int | None:
    username = mention.lstrip('@')
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE username LIKE ?", (f"%{username}%",))
        row = await cursor.fetchone()
        return row[0] if row else None


async def create_collab_request(from_id: int, to_id: int, collab_type: str, duration_h: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO social_collab_requests (from_id, to_id, collab_type, duration_h, created_at) VALUES (?,?,?,?,?)",
            (from_id, to_id, collab_type, duration_h, now())
        )
        await db.commit()
        return cursor.lastrowid


async def apply_collaboration(request_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT from_id, to_id, collab_type, duration_h FROM social_collab_requests WHERE request_id=? AND status='pending'",
            (request_id,)
        )
        req = await cursor.fetchone()
        if not req:
            return
        from_id, to_id, collab_type, duration_h = req
        expires = now() + duration_h * 3600
        await db.execute(
            "INSERT INTO social_active_collabs (from_id, to_id, collab_type, expires_at) VALUES (?,?,?,?)",
            (from_id, to_id, collab_type, expires)
        )
        await db.execute("UPDATE social_collab_requests SET status='accepted' WHERE request_id=?", (request_id,))
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (SOCIAL_COLLAB_COST, from_id))
        await db.commit()
        await add_followers(from_id, 100, "collab")
        await add_followers(to_id, 100, "collab")


async def refuse_collaboration(request_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE social_collab_requests SET status='refused' WHERE request_id=?", (request_id,))
        await db.commit()


async def create_follower_offer(seller_id: int, buyer_id: int, quantity: int, price: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO social_follower_offers (seller_id, buyer_id, quantity, price, created_at) VALUES (?,?,?,?,?)",
            (seller_id, buyer_id, quantity, price, now())
        )
        await db.commit()
        return cursor.lastrowid


async def execute_follower_transfer(offer_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT seller_id, buyer_id, quantity, price, status FROM social_follower_offers WHERE offer_id=?",
            (offer_id,)
        )
        offer = await cursor.fetchone()
        if not offer or offer[4] != 'pending':
            return False
        seller_id, buyer_id, quantity, price = offer[:4]
        cursor2 = await db.execute("SELECT balance FROM users WHERE user_id=?", (buyer_id,))
        buyer_balance = (await cursor2.fetchone())[0]
        if buyer_balance < price:
            return False
        seller_followers = await get_total_followers(seller_id)
        if seller_followers < quantity:
            return False
        tax = int(price * SOCIAL_TRANSFER_TAX)
        net = price - tax
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (price, buyer_id))
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (net, seller_id))
        cursor3 = await db.execute(
            "SELECT platform FROM social_media WHERE user_id=? ORDER BY followers DESC LIMIT 1",
            (seller_id,)
        )
        seller_platform_row = await cursor3.fetchone()
        if not seller_platform_row:
            return False
        seller_platform = seller_platform_row[0]
        await db.execute(
            "UPDATE social_media SET followers = followers - ? WHERE user_id=? AND platform=?",
            (quantity, seller_id, seller_platform)
        )
        cursor4 = await db.execute(
            "SELECT platform FROM social_media WHERE user_id=? ORDER BY followers DESC LIMIT 1",
            (buyer_id,)
        )
        buyer_platform_row = await cursor4.fetchone()
        if buyer_platform_row:
            await db.execute(
                "UPDATE social_media SET followers = followers + ? WHERE user_id=? AND platform=?",
                (quantity, buyer_id, buyer_platform_row[0])
            )
        else:
            await db.execute(
                "INSERT INTO social_media (user_id, platform, followers, posts, revenue_per_day, last_post) VALUES (?,?,?,0,0,0)",
                (buyer_id, SOCIAL_PLATFORMS[0], quantity)
            )
        await db.execute("UPDATE users SET social_followers = social_followers - ? WHERE user_id=?", (quantity, seller_id))
        await db.execute("UPDATE users SET social_followers = social_followers + ? WHERE user_id=?", (quantity, buyer_id))
        await db.execute("UPDATE social_follower_offers SET status='completed' WHERE offer_id=?", (offer_id,))
        await db.commit()
        return True


async def refuse_follower_offer(offer_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE social_follower_offers SET status='refused' WHERE offer_id=?", (offer_id,))
        await db.commit()


async def create_poll(user_id: int, question: str, options: list) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO social_polls (user_id, question, options, created_at) VALUES (?,?,?,?)",
            (user_id, question, json.dumps(options), now())
        )
        await db.commit()
        return cursor.lastrowid


async def register_vote(poll_id: int, user_id: int, option_idx: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM social_poll_votes WHERE poll_id=? AND user_id=?", (poll_id, user_id))
        if await cursor.fetchone():
            return False
        cursor2 = await db.execute("SELECT active FROM social_polls WHERE poll_id=?", (poll_id,))
        poll = await cursor2.fetchone()
        if not poll or poll[0] == 0:
            return False
        await db.execute(
            "INSERT INTO social_poll_votes (poll_id, user_id, option_index) VALUES (?,?,?)",
            (poll_id, user_id, option_idx)
        )
        await db.commit()
        return True