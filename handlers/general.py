"""
handlers/general.py — LIFESIM ULTRA V2
═══════════════════════════════════════════════════════════════════════
Commandes générales : /start, /menu, /aide, /guide, /nouveautes, /about
Refonte esthétique complète avec menu interactif boutons et liens sociaux.
Ajout Phase 0 : /shop (menu boutique unifié)
Ajout Phase 1 : commandes du marché joueur dans /aide et /menu
Mise à jour sociale avancée (liberté totale + multijoueur)
Mise à jour politique avancée (élections, partis, lois, référendums, destitution)
Version interactive du casino, fusions avec accord, produits à effets, négociation de contrats.
Téléphone intégré (hub central) avec messagerie privée.
Ajout Véhicules 2.0 : système de garage, stats (vitesse, cargo, luxe), état, carburant, réparation, refuel.
"""
from datetime import datetime
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes

from database import (
    DB_PATH, get_user, update_field, increment_field, update_balance,
    get_active_event, add_life_journal
)
from utils.decorators import require_registered
from utils.helpers import (
    fmt, now, get_level, lifestyle_score, life_state_label,
    wealth_class, karma_label, random_quote, xp_progress, escape_html
)
from utils.aesthetics import (
    card, stat_card, alert, age_stage,
)
from utils.simulation import apply_passive_simulation, current_season, SEASON_EFFECTS
from config import START_PHOTO_PATH
from handlers.missions import update_mission_progress

BOT_PUBLIC_LINK = "https://t.me/MYLIFEYOURLIFESIM_bot"
REFERRAL_JOIN_REFERRER = 15_000
REFERRAL_JOIN_REFERRED = 7_500
REFERRAL_JOIN_COINS = 15
REFERRAL_ACTIVATION_REFERRER = 40_000
REFERRAL_ACTIVATION_REFERRED = 20_000
REFERRAL_ACTIVATION_COINS = 30


async def _notify_user(bot, user_id: int, text: str):
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception:
        pass


async def process_referral_progress(user_id: int, bot=None):
    """Active la seconde récompense quand le filleul progresse vraiment."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT r.referrer_id, u.level, u.total_earned, u.full_name, u.referral_activated
            FROM referrals r
            JOIN users u ON u.user_id = r.referred_id
            WHERE r.referred_id=? AND r.activated_at=0
        """, (user_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        if row["referral_activated"]:
            return False
        if row["level"] < 3 or row["total_earned"] < 25_000:
            return False

        current = now()
        await db.execute("UPDATE referrals SET activated_at=? WHERE referred_id=?", (current, user_id))
        await db.execute(
            "UPDATE users SET referral_activated=1, social_coins=COALESCE(social_coins,0)+?, referral_rewards=referral_rewards+? WHERE user_id=?",
            (REFERRAL_ACTIVATION_COINS, REFERRAL_ACTIVATION_REFERRED, user_id)
        )
        await db.execute(
            "UPDATE users SET social_coins=COALESCE(social_coins,0)+?, referral_rewards=referral_rewards+? WHERE user_id=?",
            (REFERRAL_ACTIVATION_COINS, REFERRAL_ACTIVATION_REFERRER, row["referrer_id"])
        )
        await db.commit()

    await update_balance(user_id, REFERRAL_ACTIVATION_REFERRED)
    await update_balance(row["referrer_id"], REFERRAL_ACTIVATION_REFERRER)
    await add_life_journal(
        user_id, "parrainage",
        f"🎁 Récompense d'activation de parrainage reçue : {fmt(REFERRAL_ACTIVATION_REFERRED)} + {REFERRAL_ACTIVATION_COINS} SocialCoins.",
        severity="success"
    )
    await add_life_journal(
        row["referrer_id"], "parrainage",
        f"👥 Ton filleul {escape_html(row['full_name'])} s'est bien lancé : {fmt(REFERRAL_ACTIVATION_REFERRER)} + {REFERRAL_ACTIVATION_COINS} SocialCoins.",
        severity="success"
    )
    if bot:
        await _notify_user(
            bot,
            row["referrer_id"],
            f"🎉 <b>Ton filleul progresse vraiment !</b>\n"
            f"{escape_html(row['full_name'])} a validé son activation.\n"
            f"💰 Récompense : <b>{fmt(REFERRAL_ACTIVATION_REFERRER)}</b>\n"
            f"💎 Bonus : <b>{REFERRAL_ACTIVATION_COINS}</b> SocialCoins"
        )
        await _notify_user(
            bot,
            user_id,
            f"🎁 <b>Parrainage activé</b>\n"
            f"Tu as débloqué la récompense de progression.\n"
            f"💰 Gain : <b>{fmt(REFERRAL_ACTIVATION_REFERRED)}</b>\n"
            f"💎 Bonus : <b>{REFERRAL_ACTIVATION_COINS}</b> SocialCoins"
        )
    return True


async def _handle_referral_start(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    if not context.args:
        return ""
    token = context.args[0].strip()
    if not token.startswith("ref_"):
        return ""
    code = token[4:]
    if not code.isdigit():
        return ""

    referrer_id = int(code)
    if referrer_id == user_id:
        return ""

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT referred_by FROM users WHERE user_id=?", (user_id,)) as cur:
            current_user = await cur.fetchone()
        if not current_user or (current_user["referred_by"] or 0):
            return ""

        async with db.execute(
            "SELECT user_id, full_name, registered FROM users WHERE user_id=?",
            (referrer_id,)
        ) as cur:
            referrer = await cur.fetchone()
        if not referrer or not referrer["registered"]:
            return ""

        await db.execute("UPDATE users SET referred_by=? WHERE user_id=?", (referrer_id, user_id))
        await db.execute(
            "INSERT OR IGNORE INTO referrals(referrer_id, referred_id, created_at, starter_rewarded, activated_at) VALUES(?,?,?,?,0)",
            (referrer_id, user_id, now(), 1)
        )
        await db.execute(
            "UPDATE users SET referral_count=referral_count+1, social_coins=COALESCE(social_coins,0)+?, referral_rewards=referral_rewards+? WHERE user_id=?",
            (REFERRAL_JOIN_COINS, REFERRAL_JOIN_REFERRER, referrer_id)
        )
        await db.execute(
            "UPDATE users SET social_coins=COALESCE(social_coins,0)+?, referral_rewards=referral_rewards+? WHERE user_id=?",
            (REFERRAL_JOIN_COINS, REFERRAL_JOIN_REFERRED, user_id)
        )
        await db.commit()
        referrer_name = referrer["full_name"] or f"Joueur {referrer_id}"

    await update_balance(referrer_id, REFERRAL_JOIN_REFERRER)
    await update_balance(user_id, REFERRAL_JOIN_REFERRED)
    await add_life_journal(
        user_id, "parrainage",
        f"🤝 Tu as rejoint le jeu via {escape_html(referrer_name)} : {fmt(REFERRAL_JOIN_REFERRED)} + {REFERRAL_JOIN_COINS} SocialCoins.",
        severity="success"
    )
    await add_life_journal(
        referrer_id, "parrainage",
        f"👥 Un nouveau filleul a rejoint le jeu : {fmt(REFERRAL_JOIN_REFERRER)} + {REFERRAL_JOIN_COINS} SocialCoins.",
        severity="success"
    )
    await _notify_user(
        context.bot,
        referrer_id,
        f"🎉 <b>Nouveau filleul !</b>\n"
        f"Un joueur vient de rejoindre grâce à ton lien.\n"
        f"💰 Récompense immédiate : <b>{fmt(REFERRAL_JOIN_REFERRER)}</b>\n"
        f"💎 Bonus : <b>{REFERRAL_JOIN_COINS}</b> SocialCoins"
    )
    return (
        f"🤝 <b>Parrainage activé</b>\n"
        f"Parrain : <b>{escape_html(referrer_name)}</b>\n"
        f"🎁 Bonus de départ : <b>{fmt(REFERRAL_JOIN_REFERRED)}</b>\n"
        f"💎 Bonus : <b>{REFERRAL_JOIN_COINS}</b> SocialCoins\n"
        f"🚀 Une autre récompense se débloquera quand tu auras vraiment lancé ta partie."
    )


# ═══════════════════════════════════════════════════════════════════
#                              /start
# ═══════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id, user.username or "", user.full_name or "")

    if u.get("registered"):
        # Joueur de retour
        sim = await apply_passive_simulation(user.id)
        await process_referral_progress(user.id, context.bot)
        u = await get_user(user.id, user.username or "", user.full_name or "")
        score = lifestyle_score(u)
        lvl, xp_in, xp_for = xp_progress(u.get("xp", 0))
        age = u.get("age", 20)
        age_label, age_icon = age_stage(age)
        season = current_season()
        s_fx = SEASON_EFFECTS[season]
        await update_mission_progress(user.id, "login", 1)

        if sim.get("alert"):
            await update.message.reply_text(sim["alert"], parse_mode="HTML")

        body = [
            f"{age_icon} <b>{age} ans</b> · {age_label}",
            f"{s_fx['icon']} Saison : <b>{season.capitalize()}</b>",
            "",
            f"🧬 État global : <b>{life_state_label(score)}</b>  ({score}/100)",
            "",
            stat_card("Santé",   u.get("health", 100),   emoji="❤️"),
            stat_card("Énergie", u.get("energy", 100),   emoji="⚡"),
            stat_card("Faim",    u.get("hunger", 100),   emoji="🍽️"),
            stat_card("Bonheur", u.get("happiness", 100), emoji="😊"),
            stat_card("Stress",  u.get("stress", 0),     emoji="😰", inverted=True),
            "",
            f"💰 Solde : <b>{fmt(u['balance'])}</b>   ·   {wealth_class(u['balance'])}",
            f"⭐ Niveau <b>{lvl}</b>  ·  XP : {xp_in}/{xp_for}",
            f"🌟 Karma : <b>{karma_label(u.get('karma', 0))}</b>",
            f"💼 {escape_html(u.get('job') or 'Sans emploi')}  ·  🎓 {escape_html(u.get('diplome') or 'Aucun diplôme')}",
        ]

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 Menu principal", callback_data="menu_main"),
             InlineKeyboardButton("🧬 Vie", callback_data="menu_life")],
            [InlineKeyboardButton("💼 Économie", callback_data="menu_economy"),
             InlineKeyboardButton("🎓 Carrière", callback_data="menu_career")],
            [InlineKeyboardButton("👥 Multijoueur", callback_data="menu_mp"),
             InlineKeyboardButton("🏛️ Société", callback_data="menu_society")],
            [InlineKeyboardButton("🏢 Entreprises", callback_data="menu_companies"),
             InlineKeyboardButton("🎲 Loisirs", callback_data="menu_fun")],
            [InlineKeyboardButton("📜 Profil", callback_data="menu_profile"),
             InlineKeyboardButton("❓ Aide", callback_data="menu_help")],
            [InlineKeyboardButton("👥 Groupe de jeu", url="https://t.me/MYLIFEYOURLIFESIM"),
             InlineKeyboardButton("📢 Mises à jour", url="https://t.me/GameFrench")],
            [InlineKeyboardButton("🛠️ Support", url="https://t.me/GameFrenchSupport"),
             InlineKeyboardButton("👨‍💻 Développeur", callback_data="dev_info"),
             InlineKeyboardButton("👑 Créateur", callback_data="creator_info")],
            [InlineKeyboardButton("🤖 Bot officiel", url="https://t.me/MYLIFEYOURLIFESIM_bot")],
        ])

        texte = card(
            f"Bon retour, {escape_html(user.full_name)}",
            body,
            footer=random_quote(),
            icon="🌟",
            style="thick",
        )

        try:
            with open(START_PHOTO_PATH, 'rb') as photo_file:
                await update.message.reply_photo(
                    photo=InputFile(photo_file),
                    caption=f"Bon retour, {escape_html(user.full_name)} 👋",
                    parse_mode="HTML",
                    reply_markup=kb
                )
                await update.message.reply_text(texte, parse_mode="HTML")
        except FileNotFoundError:
            await update.message.reply_text(texte, parse_mode="HTML", reply_markup=kb)

        await update_field(user.id, "last_seen", now())
        return

    # ─── PREMIÈRE FOIS ──────────────────────────────────────────
    defaults = {
        "registered": 1,
        "created_at": now(),
        "last_seen": now(),
        "last_life_tick": now(),
        "balance": 10_000,
        "health": 100, "energy": 100, "happiness": 100, "hunger": 100,
        "stress": 0, "karma": 0, "age": 18,
        "job": "Livreur", "diplome": "",
        "level": 1, "xp": 0,
    }
    for field, value in defaults.items():
        await update_field(user.id, field, value)

    referral_text = await _handle_referral_start(update, context, user.id)

    await add_life_journal(
        user.id, "naissance",
        f"🎂 Naissance d'un nouveau destin : {escape_html(user.full_name)}, 18 ans, 10 000$ en poche, Livreur.",
        severity="success",
    )

    intro_body = [
        f"🎂 Tu nais à <b>18 ans</b> dans une grande métropole.",
        "💼 Premier job : <b>Livreur</b>.",
        "💰 Capital de départ : <b>10 000$</b>.",
        "",
        "<b>🌍 Ce qui t'attend dans cette version</b>",
        "  ▸ Marché joueur d'items : acheter, vendre et utiliser des objets",
        "  ▸ Entreprises plus complètes avec audit, produits à effets, fusions avec accord, contrats négociables",
        "  ▸ Casino interactif (boutons SPIN, LANCER, grille cliquable) – ouvert la nuit (17h→5h)",
        "  ▸ Escouades coopératives et raids contre boss",
        "  ▸ Réseaux sociaux, communautés, tendances et SocialCoins",
        "  ▸ Parrainage avec lien personnel et récompenses progressives",
        "  ▸ 📱 Téléphone intégré avec calendrier, assistant, banque, inventaire, marché, messagerie et plus !",
        "  ▸ 🏎️ Système de véhicules 2.0 (garage, statistiques, entretien, carburant)",
        "",
        "<b>🚀 Pour commencer</b>",
        "  • <code>/menu</code> ─ voir les grands systèmes du jeu",
        "  • <code>/nouveautes</code> ─ lire les changements récents",
        "  • <code>/guide</code> ─ comprendre les règles importantes",
        "  • <code>/travailler</code> ─ gagner ton premier salaire",
        "  • <code>/vie</code> ─ voir ton état complet",
        "  • <code>/parrainage</code> ─ inviter tes amis et gagner des bonus",
        "  • <code>/phone</code> ─ ton hub central dans le jeu",
    ]
    if referral_text:
        intro_body.extend(["", referral_text])

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Lancer mon aventure", callback_data="menu_main")],
        [InlineKeyboardButton("🆕 Voir les nouveautés", callback_data="menu_help")],
        [InlineKeyboardButton("👥 Groupe de jeu", url="https://t.me/MYLIFEYOURLIFESIM"),
         InlineKeyboardButton("📢 Mises à jour", url="https://t.me/GameFrench")],
        [InlineKeyboardButton("🛠️ Support", url="https://t.me/GameFrenchSupport"),
         InlineKeyboardButton("👨‍💻 Développeur", callback_data="dev_info"),
         InlineKeyboardButton("👑 Créateur", callback_data="creator_info")],
    ])

    texte = card(
        f"Bienvenue dans LifeSim Ultra, {escape_html(user.full_name)}",
        intro_body,
        footer="Chaque choix forge ton destin. Tape /menu pour entrer dans le jeu.",
        icon="🌟",
        style="thick",
    )

    try:
        with open(START_PHOTO_PATH, 'rb') as photo_file:
            await update.message.reply_photo(
                photo=InputFile(photo_file),
                caption=f"Bienvenue, {escape_html(user.full_name)} 🚀",
                parse_mode="HTML",
                reply_markup=kb
            )
            await update.message.reply_text(texte, parse_mode="HTML")
    except FileNotFoundError:
        await update.message.reply_text(texte, parse_mode="HTML", reply_markup=kb)

    await update_field(user.id, "last_seen", now())
    return


@require_registered
async def cmd_parrainage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    deep_link = f"{BOT_PUBLIC_LINK}?start=ref_{user.id}"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN activated_at > 0 THEN 1 ELSE 0 END) AS activated
            FROM referrals
            WHERE referrer_id=?
        """, (user.id,)) as cur:
            stats = await cur.fetchone()
        async with db.execute("""
            SELECT u.full_name, u.level, r.created_at, r.activated_at
            FROM referrals r
            JOIN users u ON u.user_id = r.referred_id
            WHERE r.referrer_id=?
            ORDER BY r.created_at DESC
            LIMIT 8
        """, (user.id,)) as cur:
            rows = await cur.fetchall()

    u = await get_user(user.id)
    total = stats["total"] or 0
    activated = stats["activated"] or 0
    pending = total - activated

    body = [
        "Invite tes amis avec ce lien personnel :",
        f"<code>{escape_html(deep_link)}</code>",
        "",
        f"👥 Filleuls inscrits : <b>{total}</b>",
        f"✅ Activés : <b>{activated}</b>",
        f"⏳ En attente : <b>{pending}</b>",
        f"💰 Récompenses gagnées : <b>{fmt(u.get('referral_rewards', 0))}</b>",
        "",
        "<b>Récompenses</b>",
        f"• Arrivée d'un filleul : <b>{fmt(REFERRAL_JOIN_REFERRER)}</b> + {REFERRAL_JOIN_COINS} SocialCoins",
        f"• Activation d'un filleul : <b>{fmt(REFERRAL_ACTIVATION_REFERRER)}</b> + {REFERRAL_ACTIVATION_COINS} SocialCoins",
        "",
        "Un filleul est activé quand il atteint le niveau 3 et a gagné au moins 25 000$ au total.",
    ]
    if rows:
        body.extend(["", "<b>Derniers filleuls</b>"])
        for row in rows:
            status = "✅ activé" if row["activated_at"] else "⏳ en cours"
            body.append(f"• <b>{escape_html(row['full_name'])}</b> · niv.{row['level']} · {status}")

    await update.message.reply_text(
        card("🤝 PARRAINAGE", body, icon="🤝", style="thick",
             footer="Partage le lien /start pour faire grandir la communauté."),
        parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════════════════════
#                              /menu
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    event = await get_active_event()
    ev_line = f"⚡ Événement actif : <b>{escape_html(event['name'])}</b>" if event else "🌍 Monde stable"

    body = [
        ev_line,
        "",
        f"💰 Solde : <b>{fmt(u.get('balance', 0))}</b>",
        f"⭐ Niveau : <b>{u.get('level', 1)}</b>   ·   ❤️ Santé : <b>{u.get('health', 100)}%</b>",
        "",
        "<b>🎮 Raccourcis utiles</b>",
        "  /vie ─ état complet de ton personnage",
        "  /phone ─ téléphone (hub central)",
        "  /travailler ─ gagner de l'argent",
        "  /etudes ─ progresser et débloquer de meilleurs jobs",
        "  /market ─ marché joueur d'items",
        "  /crafting ─ fabriquer des objets",
        "  /monentreprise ─ gérer ta boîte",
        "  /escouade ─ coop et raids",
        "  /competition ─ défi hebdomadaire",
        "  /ranked ─ saison compétitive",
        "  /tutorial ─ parcours guidé",
        "  /graphstats ─ évolution richesse / XP",
        "  /garage ─ gérer tes véhicules (nouveau !)",
        "",
        "<b>🆕 Changements récents</b>",
        "  • 🏎️ Système de véhicules 2.0 : statistiques, garage, entretien, carburant",
        "  • 📱 Téléphone intégré avec calendrier, assistant, banque, inventaire, marché, messagerie et plus !",
        "  • Casino interactif (boutons SPIN, LANCER, grille cliquable) – ouvert la nuit",
        "  • Entreprises : produits à effets, fusions avec accord, contrats négociables",
        "  • Escouades, raids et coopération renforcée",
        "  • Réseaux sociaux, tendances et SocialCoins",
        "  • Parrainage avec récompenses progressives",
        "",
        "<b>📚 Pour t'orienter</b>",
        "  /aide ─ liste complète des commandes",
        "  /guide ─ explications détaillées",
        "  /nouveautes ─ résumé des ajouts et changements",
        "  Utilise aussi les boutons ci-dessous pour naviguer par thème.",
    ]
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧬 Vie", callback_data="menu_life"),
         InlineKeyboardButton("💼 Économie", callback_data="menu_economy")],
        [InlineKeyboardButton("🎲 Loisirs", callback_data="menu_fun"),
         InlineKeyboardButton("🎓 Carrière", callback_data="menu_career")],
        [InlineKeyboardButton("👥 Multijoueur", callback_data="menu_mp"),
         InlineKeyboardButton("🏛️ Société", callback_data="menu_society")],
        [InlineKeyboardButton("🏢 Entreprises", callback_data="menu_companies"),
         InlineKeyboardButton("📜 Profil", callback_data="menu_profile")],
        [InlineKeyboardButton("❓ Aide", callback_data="menu_help")],
    ])
    
    await update.message.reply_text(
        card("MENU PRINCIPAL", body, icon="🎮", style="double",
             footer="Menu raccourci pour éviter les messages trop longs. /aide contient la liste complète."),
        parse_mode="HTML",
        reply_markup=kb,
    )


# ═══════════════════════════════════════════════════════════════════
#                              /aide
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_aide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    body = [
        # ============================================================
        # 1. TÉLÉPHONE & HUB CENTRAL
        # ============================================================
        "<b>📱 SMARTPHONE HUB</b>",
        "  /phone — Menu interactif avec toutes les applications",
        "  /phone_event \"titre\" [date] — ajouter un événement perso",
        "  /phone_msg @user [message] — envoyer un message privé",
        "  /status [message] — publier un statut sur VoidGram",
        "",
        # ============================================================
        # 2. SURVIE & SANTÉ
        # ============================================================
        "<b>🧬 SURVIE & SANTÉ</b>",
        "  /vie — tableau de bord complet",
        "  /sante — état de santé détaillé",
        "  /manger — restaurer la faim",
        "  /dormir — restaurer l'énergie",
        "  /gym — fitness (+force)",
        "  /medecin — consulter un médecin",
        "  /hopital — hospitalisation",
        "  /medicaments — acheter des médicaments",
        "  /assurance — souscrire une assurance santé",
        "  /journal — historique de vie",
        "  /routine — actions quotidiennes",
        "",
        # ============================================================
        # 3. ÉCONOMIE & FINANCES
        # ============================================================
        "<b>💰 ÉCONOMIE & FINANCES</b>",
        "  /quotidien — bonus journalier",
        "  /travailler — gagner un salaire",
        "  /metier — changer de métier",
        "  /promotion — évoluer dans son métier",
        "  /compte — voir son compte",
        "  /payer — envoyer de l'argent",
        "  /don — donation (karma)",
        "  /impots — fiscalité",
        "  /banques — liste des banques",
        "  /ouvrir — ouvrir un compte bancaire",
        "  /depot — déposer de l'argent",
        "  /retrait — retirer de l'argent",
        "  /pret — emprunter",
        "  /rembourser — rembourser un prêt",
        "  /mescomptes — voir ses comptes",
        "  /loterie — loterie nationale",
        "  /acheterticket — acheter un ticket",
        "  /mestickets — voir ses tickets",
        "  /tirage — résultats du tirage",
        "  /richesse — classement des riches",
        "",
        # ============================================================
        # 4. INVESTISSEMENTS & PATRIMOINE
        # ============================================================
        "<b>📈 INVESTISSEMENTS & PATRIMOINE</b>",
        "  /marche — marché boursier",
        "  /acheteraction — acheter des actions",
        "  /vendreaction — vendre des actions",
        "  /portefeuille — voir son portefeuille",
        "  /historique — historique des prix",
        "  /proprietes — marché immobilier",
        "  /acheter — acheter une propriété",
        "  /hypotheque — acheter avec crédit",
        "  /mesbiens — voir ses propriétés",
        "  /vendre — vendre une propriété",
        "  /entretenir — entretenir une propriété",
        "  /proposer_location — proposer une location",
        "  /meslocations — voir ses locations",
        "  /quitter_logement — quitter un logement",
        "",
        # ============================================================
        # 5. VÉHICULES
        # ============================================================
        "<b>🏎️ VÉHICULES</b>",
        "  /garage — garage interactif (statistiques, sélection)",
        "  /repair — réparer le véhicule actif",
        "  /refuel — faire le plein de carburant",
        "  /vehicules — catalogue des véhicules",
        "  /acheterv — acheter un véhicule",
        "  /mesvehicules — voir ses véhicules",
        "  /vendrevehicule — vendre un véhicule",
        "  /assurerv — assurer un véhicule",
        "  /acces_vip — vérifier l'accès VIP",
        "  /lieux_vip — liste des lieux VIP",
        "  /cargobonus — bonus de cargo sur les ventes",
        "",
        # ============================================================
        # 6. LUXE & PRESTIGE
        # ============================================================
        "<b>👑 LUXE & PRESTIGE</b>",
        "  /luxe — boutique de luxe",
        "  /acheterLuxe — acheter un article de luxe",
        "  /prestige — voir son prestige",
        "  /classementprestige — classement du prestige",
        "  /prestigelog — historique des achats",
        "",
        # ============================================================
        # 7. MARCHÉ JOUEUR & ITEMS
        # ============================================================
        "<b>🏪 MARCHÉ JOUEUR & ITEMS</b>",
        "  /market — voir les annonces",
        "  /sellitem — mettre un item en vente",
        "  /buyitem — acheter un item",
        "  /cancelitem — annuler une vente",
        "  /useitem — utiliser un item",
        "  /myitems — voir ses annonces",
        "  /inventaire — voir son inventaire",
        "  /shop — boutique unifiée (luxe/noir)",
        "",
        # ============================================================
        # 8. ENTREPRISES
        # ============================================================
        "<b>🏢 ENTREPRISES</b>",
        "  /boites — liste des entreprises",
        "  /creerboite — créer une entreprise",
        "  /monentreprise — gérer son entreprise",
        "  /dissoudreboite — fermer l'entreprise",
        "  /depotboite — déposer en trésorerie",
        "  /retirerboite — retirer de la trésorerie",
        "  /parts — voir les parts",
        "  /acheterparts — acheter des parts",
        "  /vendreparts — vendre des parts",
        "  /dividendes — verser des dividendes",
        "  /rd — investir en R&D",
        "  /produits — gérer les produits",
        "  /creer_produit — créer un produit",
        "  /annonce — publier une offre d'emploi",
        "  /emplois — voir les offres d'emploi",
        "  /postuler — postuler à une offre",
        "  /candidatures — voir les candidatures",
        "  /accepter — accepter un candidat",
        "  /refuser — refuser un candidat",
        "  /nommer — promouvoir un employé",
        "  /setsalaire — ajuster un salaire",
        "  /licencier — licencier un employé",
        "  /former — financer une formation",
        "  /prime — donner une prime",
        "  /proposer_contrat — proposer un contrat B2B",
        "  /repondre_contrat — répondre à un contrat",
        "  /proposer_fusion — proposer une fusion",
        "  /repondre_fusion — répondre à une fusion",
        "",
        # ============================================================
        # 9. SOCIAL & MULTIJOUEUR
        # ============================================================
        "<b>👥 SOCIAL & MULTIJOUEUR</b>",
        "  /mariage — se marier",
        "  /acceptermariage — accepter un mariage",
        "  /divorce — divorcer",
        "  /famille — voir sa famille",
        "  /creerfamille — créer un clan",
        "  /adopter — adopter un joueur",
        "  /ami — ajouter un ami",
        "  /mesamis — voir ses amis",
        "  /guild — voir sa guilde",
        "  /guild_create — créer une guilde",
        "  /guild_invite — inviter à la guilde",
        "  /guild_join — rejoindre une guilde",
        "  /gang — voir son gang",
        "  /creergand — créer un gang",
        "  /rejoindregang — rejoindre un gang",
        "  /escouade — voir son escouade",
        "  /creerescouade — créer une escouade",
        "  /raid — lancer ou attaquer un raid",
        "  /echange — échange sécurisé",
        "  /cadeau — offrir un cadeau",
        "  /salutations — saluer un joueur",
        "  /relations — voir ses relations",
        "  /classements — classements mondiaux",
        "  /leaderboard — classement général",
        "",
        # ============================================================
        # 10. RÉSEAUX SOCIAUX
        # ============================================================
        "<b>🌐 RÉSEAUX SOCIAUX</b>",
        "  /plateformes — liste des plateformes",
        "  /poster — publier un post",
        "  /story — publier une story",
        "  /live — lancer un live",
        "  /donner_live — donner pendant un live",
        "  /collab — proposer une collaboration",
        "  /vendre_followers — vendre des abonnés",
        "  /noter — noter un joueur",
        "  /classement_social — top influenceurs",
        "  /creer_communaute — créer une communauté",
        "  /communaute — voir sa communauté",
        "  /partager — partager un post",
        "  /lancer_tendance — lancer une tendance",
        "  /utiliser_tendance — utiliser une tendance",
        "  /socialcoins — voir ses SocialCoins",
        "  /donner_socialcoins — donner des SocialCoins",
        "  /sondage — créer un sondage",
        "  /vote — voter à un sondage",
        "  /resultats_sondage — voir les résultats",
        "",
        # ============================================================
        # 11. POLITIQUE
        # ============================================================
        "<b>🏛️ POLITIQUE</b>",
        "  /constitution — consulter la constitution",
        "  /modifierconstitution — proposer une modification",
        "  /postes — voir les élus",
        "  /lancerelection — lancer une élection",
        "  /candidater — se présenter",
        "  /voter — voter pour un candidat",
        "  /candidats — voir les candidats",
        "  /elections — liste des élections",
        "  /depouiller — dépouiller une élection",
        "  /monposte — voir son mandat",
        "  /demissionnerposte — démissionner",
        "  /destituer — lancer une motion",
        "  /signer — signer une motion",
        "  /vote_destitution — voter une destitution",
        "  /creerparti — créer un parti",
        "  /partis — liste des partis",
        "  /rejoindreparti — rejoindre un parti",
        "  /quitterparti — quitter un parti",
        "  /proposerloi — proposer une loi",
        "  /voterloi — voter une loi",
        "  /referendum — lancer un référendum",
        "  /votereferendum — voter un référendum",
        "  /nommer — nommer un ministre",
        "  /ministres — liste du gouvernement",
        "",
        # ============================================================
        # 12. LOISIRS & JEUX
        # ============================================================
        "<b>🎲 LOISIRS & JEUX</b>",
        "  /casino — voir l'état du casino",
        "  /slots — machines à sous (SPIN)",
        "  /blackjack — 21 (Tirer/Rester/Doubler)",
        "  /roulette — roulette (pari puis LANCER)",
        "  /crash — crash game (ENCAISSER)",
        "  /poker — vidéo poker",
        "  /mines — démineur 5x5",
        "  /pmu — courses hippiques",
        "  /jardin — voir son jardin",
        "  /planter — planter une plante",
        "  /arroser — arroser une plante",
        "  /recolter — récolter",
        "  /vendrecolte — vendre la récolte",
        "  /defier — défier un joueur",
        "  /defis — voir ses défis",
        "  /classementarene — classement PvP",
        "  /parier — parier sur un combat",
        "",
        # ============================================================
        # 13. CRIME & BLACK MARKET
        # ============================================================
        "<b>⚔️ CRIME & BLACK MARKET</b>",
        "  /crimes — liste des crimes",
        "  /commettre — commettre un crime",
        "  /caution — payer une caution",
        "  /tribunal — passer au tribunal",
        "  /avocat — engager un avocat",
        "  /noir — marché noir",
        "  /acheternoir — acheter au marché noir",
        "  /hacktargets — cibles de hacking",
        "  /hacker — hacker une cible",
        "  /defenses — renforcer ses défenses",
        "",
        # ============================================================
        # 14. PROGRESSION & MISSIONS
        # ============================================================
        "<b>🎯 PROGRESSION & MISSIONS</b>",
        "  /missions — voir ses missions",
        "  /missions_completed — missions terminées",
        "  /achievements — voir ses succès",
        "  /prestige — voir son prestige",
        "  /legacy — système d'héritage",
        "  /reincarnate — se réincarner",
        "  /competition — compétition en cours",
        "  /competition_join — rejoindre la compétition",
        "  /competition_history — historique",
        "  /ranked — saison compétitive",
        "  /ranked_join — rejoindre le ranked",
        "  /ranked_leaderboard — classement ranked",
        "  /tutorial — parcours guidé",
        "  /graphstats — graphiques d'évolution",
        "",
        # ============================================================
        # 15. PROFIL & COMMUNAUTÉ
        # ============================================================
        "<b>📜 PROFIL & COMMUNAUTÉ</b>",
        "  /profil — voir son profil",
        "  /stats — statistiques détaillées",
        "  /badges — voir ses badges",
        "  /bio — modifier sa bio",
        "  /lieu — changer de lieu",
        "  /niveau — progression du niveau",
        "  /titres — voir ses titres",
        "  /historiquetitres — hiérarchie des titres",
        "  /karma — voir son karma",
        "  /topxp — classement XP",
        "  /notifications — voir ses notifications",
        "  /notifications_history — historique",
        "  /notifications_clear — effacer les notifications",
        "  /report — signaler un problème",
        "  /report_user — signaler un joueur",
        "  /myreports — voir ses signalements",
        "",
        # ============================================================
        # 16. VOYAGES
        # ============================================================
        "<b>✈️ VOYAGES</b>",
        "  /destinations — liste des destinations",
        "  /voyager — voyager",
        "  /monstimbre — collection de voyages",
        "",
        # ============================================================
        # 17. CRAFTING
        # ============================================================
        "<b>🛠️ CRAFTING</b>",
        "  /crafting — voir les recettes",
        "  /craft — fabriquer un objet",
        "",
        # ============================================================
        # 18. AUTRES
        # ============================================================
        "<b>📌 AUTRES</b>",
        "  /menu — menu principal",
        "  /guide — guide complet",
        "  /nouveautes — quoi de neuf",
        "  /about — informations du bot",
        "  /shop — boutique unifiée",
        "  /parrainage — parrainer des amis",
    ]

    footer = "Plus de 200 commandes disponibles. Tape /guide pour le tutoriel détaillé."

    # Découpage par groupes de 22 lignes
    chunk_size = 22
    for i in range(0, len(body), chunk_size):
        chunk = body[i:i+chunk_size]
        current_footer = footer if i + chunk_size >= len(body) else None
        text = card("📖 GUIDE DES COMMANDES", chunk, icon="📖", style="thick", footer=current_footer)
        await update.message.reply_text(text, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                              /guide
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    body = [
        "<b>📖 LES BASES</b>",
        "Dans LifeSim, ton personnage vit en temps réel.",
        "Pendant ton absence, le monde évolue : faim ↘, énergie ↗,",
        "événements aléatoires, vieillissement, saisons…",
        "",
        "<b>🎯 LES 5 PILIERS</b>",
        "  ❤️ <b>Santé</b> ─ à 0%, tu meurs et perds ton legacy",
        "  ⚡ <b>Énergie</b> ─ nécessaire pour bosser, étudier, agir",
        "  🍽️ <b>Faim</b> ─ /manger pour la restaurer",
        "  😊 <b>Bonheur</b> ─ booste les chances de succès",
        "  😰 <b>Stress</b> ─ inverse : plus c'est haut, plus c'est mauvais",
        "",
        "<b>💡 STRATÉGIE TYPE</b>",
        "  1. /quotidien chaque jour pour les revenus passifs",
        "  2. /travailler toutes les 4h pour ton salaire",
        "  3. /etudier pour grimper en diplôme = meilleurs jobs",
        "  4. /acheter une propriété ─ revenus locatifs",
        "  5. /marche ─ investis tes économies à long terme",
        "  6. /missions chaque jour pour XP & bonus",
        "",
        "<b>📱 SMARTPHONE HUB</b>",
        "  • /phone ouvre un menu interactif avec toutes les applications.",
        "  • Calendrier : visualise les événements mondiaux et ajoute les tiens.",
        "  • Assistant : reçois des conseils personnalisés.",
        "  • Messagerie : envoie des messages privés avec /phone_msg.",
        "  • Banque, inventaire, marché, profil : tout est accessible.",
        "  • Personnalise ton téléphone (thème, sonnerie) dans Paramètres.",
        "  • /status [message] — publie un statut sur VoidGram visible par tous.",
        "",
        "<b>🏪 MARCHÉ JOUEUR & ITEMS</b>",
        "  • Gagne des items dans les casinos, arènes, crimes, missions.",
        "  • Vends-les avec /sellitem et consulte les annonces avec /market.",
        "  • Achète ce qui t’intéresse avec /buyitem.",
        "  • Utilise les items consommables avec /useitem (santé, énergie, XP, argent).",
        "",
        "<b>🏠 SYSTÈME DE LOCATION SÉCURISÉ</b>",
        "  • /proposer_location [numéro] @locataire [loyer]",
        "  • /meslocations — voir les biens que tu loues",
        "  • /quitter_logement [id_contrat] — résilier un bail",
        "  • Les loyers sont prélevés automatiquement chaque mois.",
        "",
        "<b>🏢 ENTREPRISES RENFORCÉES</b>",
        "  • /auditboite — analyse points forts/risques de ta société.",
        "  • Les produits ont des EFFETS RÉELS (soin, énergie, XP, argent, buff).",
        "  • /proposer_fusion — demander une fusion (avec accord).",
        "  • /negocier_contrat — négocier un contrat B2B avant acceptation.",
        "",
        "<b>🏎️ VÉHICULES 2.0</b>",
        "  • Achetez un véhicule avec /acheterv et consultez votre garage avec /garage.",
        "  • Chaque véhicule a des statistiques :",
        "    - <b>Vitesse</b> : influence la fuite lors des crimes (bonus de 0.3% par point).",
        "    - <b>Cargo</b> : capacité de transport — bonus sur les ventes (/cargobonus).",
        "    - <b>Luxe</b> : détermine le coût d'entretien, le prestige et l'accès VIP.",
        "    - <b>État</b> : se dégrade de 2% par jour. À réparer avec /repair.",
        "    - <b>Carburant</b> : se consomme quotidiennement. À remplir avec /refuel.",
        "  • Le véhicule actif est utilisé pour vos déplacements et vos fuites.",
        "  • Sans véhicule, vos chances de fuite lors d'un crime sont réduites de 30%.",
        "  • /acces_vip — vérifie si tu as accès aux lieux VIP (luxe ≥ 70).",
        "  • /lieux_vip — liste des lieux prestigieux accessibles.",
        "",
        "<b>🎰 CASINO INTERACTIF</b>",
        "  • /slots → bouton SPIN",
        "  • /roulette → pari puis bouton LANCER",
        "  • /mines → grille 5x5 cliquable",
        "  • /pmu → choix du cheval puis LANCER LA COURSE",
        "  • /blackjack → Tirer/Rester/Doubler",
        "  • /crash → ENCAISSER avant le crash",
        "  • Ouvert uniquement la nuit (17h → 5h heure du jeu).",
        "",
        "<b>🌐 RÉSEAUX SOCIAUX (liberté totale !)</b>",
        "  • Tu écris TOUT le contenu — le bot donne les résultats.",
        "  • /poster — publie ce que tu veux (gains d'abonnés).",
        "  • /story — contenu éphémère.",
        "  • /live — streaming, les autres peuvent te faire des dons.",
        "  • /collab — demande de collaboration (avec acceptation).",
        "  • /vendre_followers — vends tes abonnés (avec accord).",
        "  • /noter — donne une réputation à un joueur.",
        "  • /classement_social — top influenceurs.",
        "  • /socialcoins — monnaie sociale indépendante.",
        "  • /sondage — crée un sondage. /vote — vote.",
        "",
        "<b>🤝 PARRAINAGE</b>",
        "  • /parrainage — lien personnel à partager.",
        "  • Récompense immédiate quand un filleul rejoint.",
        "  • Seconde récompense quand il atteint un vrai palier de progression.",
        "",
        "<b>🏛️ POLITIQUE</b>",
        "  • /constitution — cadre fondamental (modifiable par référendum).",
        "  • /lancerelection — créer une élection (coût, karma requis).",
        "  • /candidater — se porter candidat (programme libre).",
        "  • /voter @candidat — voter directement.",
        "  • /depouiller — ferme l’élection, vérifie le quorum, déclare le vainqueur.",
        "  • /destituer — motion de censure : signatures puis vote.",
        "  • /creerparti — créer un parti.",
        "  • /proposerloi — proposer une loi.",
        "  • /referendum — initiative citoyenne directe.",
        "",
        "<b>⚖️ KARMA</b>",
        "  Le karma influence TOUT : succès, salaires, événements.",
        "  Les bonnes actions le montent, les crimes le détruisent.",
        "",
        "<b>🌟 ASTUCES</b>",
        "  ◽ Reviens au moins 1x/jour pour ne pas accumuler de pénalité",
        "  ◽ Dormir restaure énergie, manger restaure faim",
        "  ◽ Le stress > 70% détruit ta santé en silence",
        "  ◽ Les diplômes débloquent les jobs à fort salaire",
        "  ◽ L'argent en banque génère des intérêts",
        "  ◽ Utilise /notifications pour suivre tes interactions",
        "  ◽ Le multi est clé : échanges, défis, alliances",
        "  ◽ Entretiens tes véhicules régulièrement",
        "  ◽ Un véhicule en bon état te sauvera la mise lors des crimes",
        "  ◽ Utilise /phone comme hub central pour tout gérer",
    ]

    footer = "Tape /nouveautes pour voir les dernières features."

    # Découpage par groupes de 22 lignes
    chunk_size = 22
    for i in range(0, len(body), chunk_size):
        chunk = body[i:i+chunk_size]
        current_footer = footer if i + chunk_size >= len(body) else None
        text = card("📖 GUIDE COMPLET LIFESIM ULTRA", chunk, icon="🎓", style="thick", footer=current_footer)
        await update.message.reply_text(text, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                          /nouveautes
# ═══════════════════════════════════════════════════════════════════
async def cmd_nouveautes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    body = [
        "<b>🆕 CE QUI A CHANGÉ DANS LE JEU</b>",
        "",
        "<b>📱 SMARTPHONE HUB v2.0</b>",
        "  • /phone — nouveau menu interactif avec applications",
        "  • 🏦 Wallet : solde, comptes, dépôt/retrait, vente d'items",
        "  • 🏎️ Garage : véhicule actif, stats, progression VIP",
        "  • 🌐 VoidGram : publiez des statuts visibles par tous",
        "  • 📊 Classements : richesse, XP, prestige",
        "  • 📦 Inventaire : consultez et utilisez vos items",
        "  • 🏪 Marché : dernières annonces",
        "  • 👤 Profil : toutes vos infos",
        "  • ⚙️ Paramètres : thème, sonnerie",
        "  • /status [message] — publier un statut sur VoidGram",
        "",
        "<b>🏎️ SYSTÈME DE VÉHICULES 2.0</b>",
        "  • Statistiques : vitesse, cargo, luxe, état, carburant",
        "  • Vitesse : réduit le temps de trajet (-50% max)",
        "  • Cargo : bonus sur les ventes (/cargobonus, +20% max)",
        "  • Luxe : accès VIP (/acces_vip), bonus social (/poster, /live)",
        "  • /garage — gestion interactive",
        "  • /repair — réparation (coût selon dégradation)",
        "  • /refuel — faire le plein (10 coins/unité)",
        "  • /acces_vip — vérification luxe ≥ 70",
        "  • /lieux_vip — liste des lieux prestigieux",
        "",
        "<b>🏢 ENTREPRISES</b>",
        "  • Produits à effets réels (heal/energy/xp/money/buff)",
        "  • Fusions avec accord (/proposer_fusion, /repondre_fusion)",
        "  • Contrats B2B négociables (/negocier_contrat)",
        "  • /auditboite — diagnostic complet de votre société",
        "",
        "<b>🎰 CASINO INTERACTIF</b>",
        "  • Slots (SPIN), Roulette (LANCER), Mines (grille cliquable)",
        "  • PMU (choix cheval + LANCER)",
        "  • Blackjack (Tirer/Rester/Doubler)",
        "  • Crash (ENCAISSER)",
        "  • Ouvert de 17h à 5h (heure du jeu)",
        "",
        "<b>🤝 COOPÉRATION</b>",
        "  • Escouades (/creerescouade, /escouade)",
        "  • Raids contre boss (/raid lancer, /raid attaquer)",
        "  • Chat d'escouade (/chatescouade)",
        "",
        "<b>🌐 RÉSEAUX SOCIAUX</b>",
        "  • Communautés, tendances, partages, collaborations",
        "  • SocialCoins — monnaie sociale indépendante",
        "  • Sondages (/sondage, /vote)",
        "  • Classement social (/classement_social)",
        "",
        "<b>🤝 PARRAINAGE</b>",
        "  • /parrainage — lien personnel",
        "  • Récompense à l'arrivée + récompense de progression",
        "",
        "<b>📌 COMMANDES À ESSAYER</b>",
        "  • <code>/phone</code> — hub central",
        "  • <code>/status</code> — publier un statut",
        "  • <code>/garage</code> — gérer ses véhicules",
        "  • <code>/acces_vip</code> — vérifier l'accès VIP",
        "  • <code>/cargobonus</code> — voir le bonus de cargo",
        "  • <code>/auditboite</code> — analyser son entreprise",
        "  • <code>/proposer_fusion</code> — fusionner avec accord",
        "  • <code>/negocier_contrat</code> — négocier un contrat",
    ]

    footer = "Tape /aide pour la liste des commandes et /guide pour les explications détaillées."

    # Découpage par groupes de 15 lignes
    chunk_size = 15
    for i in range(0, len(body), chunk_size):
        chunk = body[i:i+chunk_size]
        current_footer = footer if i + chunk_size >= len(body) else None
        text = card("QUOI DE NEUF ?", chunk, icon="🆕", style="thick", footer=current_footer)
        await update.message.reply_text(text, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
#                              /about
# ═══════════════════════════════════════════════════════════════════
@require_registered
async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "**🎮 YOUR LIFE MY LIFE (LifeSim)**\n\n"
        "Plonge dans une simulation de vie réaliste :\n"
        "• Gère ta santé, ton travail, tes relations\n"
        "• Construis un empire économique\n"
        "• Interagis avec d'autres joueurs\n"
        "• Deviens un influenceur sur les réseaux sociaux\n\n"
        "**Liens utiles :**\n"
        "👥 [Groupe de jeu](https://t.me/MYLIFEYOURLIFESIM)\n"
        "📢 [Mises à jour Game French](https://t.me/GameFrench)\n"
        "🛠️ [Support](https://t.me/GameFrenchSupport)\n"
        "👨‍💻 Développeur : [NETFLASH DIEU MAURICE](http://t.me/mauridieu)\n"
        "👑 Créateur : [Game French](http://t.me/Sam_Rang_Nation)\n"
        "🤖 [Bot officiel](https://t.me/MYLIFEYOURLIFESIM_bot)"
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════════
#                          /shop (Phase 0)
# ═══════════════════════════════════════════════════════════════════
async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu unifié de la boutique : Luxe ou Marché noir."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Articles de luxe", callback_data="shop_luxe")],
        [InlineKeyboardButton("🕶️ Marché noir", callback_data="shop_noir")],
        [InlineKeyboardButton("❌ Fermer", callback_data="shop_close")]
    ])
    await update.message.reply_text(
        "🛒 **Boutique**\n\nChoisis ta catégorie :",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


# ═══════════════════════════════════════════════════════════════════
#                  Callback du menu boutons
# ═══════════════════════════════════════════════════════════════════
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    raw_key = q.data.replace("menu_", "", 1)
    if raw_key == "hold":
        return
    page = 1
    if raw_key != "main":
        parts = raw_key.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            raw_key, page = parts[0], int(parts[1])
    key = raw_key

    menus = {
        "main": (
            "MENU PRINCIPAL",
            "🎮",
            [
                "📱 /phone — SmartPhone Hub",
                "🧬 /vie — diagnostic complet",
                "💼 /travailler — gagner de l'argent",
                "🎓 /etudes — se former",
                "💰 /quotidien — revenus journaliers",
                "🎯 /missions — objectifs du jour",
                "📜 /profil — ton profil",
                "🏎️ /garage — gérer tes véhicules",
                "📊 /status — publier un statut VoidGram",
            ],
        ),
        "life": (
            "VIE & SURVIE",
            "🧬",
            [
                "❤️ /sante — état de santé",
                "🍽️ /manger — restaurer la faim",
                "🛌 /dormir — restaurer l'énergie",
                "🏋️ /gym — fitness",
                "💊 /medicaments — soigner",
                "🏥 /hopital — urgences",
                "📔 /journal — historique de vie",
                "⚡ /routine — actions quotidiennes",
            ],
        ),
        "economy": (
            "ÉCONOMIE",
            "💰",
            [
                "💼 /travailler — gagner un salaire",
                "🏦 /banques — comptes bancaires",
                "💳 /pret — emprunter",
                "📈 /marche — investir en bourse",
                "💎 /portefeuille — tes actifs",
                "🎟️ /loterie — loterie",
                "📊 /impots — fiscalité",
                "💸 /payer — envoyer de l'argent",
                "🎁 /don — donation",
                "📊 /compte — voir ton compte",
                "🏆 /richesse — classement des riches",
                "🎰 /casino — jeux d'argent",
            ],
        ),
        "career": (
            "CARRIÈRE & FORMATION",
            "🎓",
            [
                "🎓 /etudes — formation",
                "📝 /examen — passer un examen",
                "📚 /reviser — réviser",
                "🏆 /promotion — grimper",
                "💼 /metier — changer de métier",
                "🧠 /competences — voir compétences",
                "🧠 /formation — apprendre compétence",
                "🏢 /boites — liste des entreprises",
                "👑 /monentreprise — gérer mon entreprise",
                "📝 /postuler — candidater",
                "📋 /candidatures — voir candidats",
                "💰 /setsalaire — fixer salaire",
                "🔬 /rd — investir en R&D",
                "📦 /produits — gérer produits",
                "🆕 /creer_produit — créer produit",
                "🤝 /fusion — fusionner (ancienne)",
                "📢 /annonce — publier offre",
                "📄 /proposer_contrat — contrat B2B",
                "🤝 /proposer_fusion — fusion (avec accord)",
                "🎓 /former — former employé",
                "🎁 /prime — donner une prime",
                "⚖️ /licencier — licencier",
                "📊 /parts — actionnariat",
                "💎 /dividendes — verser dividendes",
            ],
        ),
        "companies": (
            "ENTREPRISES",
            "🏢",
            [
                "🏢 /boites — liste",
                "🏭 /creerboite — créer",
                "👑 /monentreprise — gérer",
                "💀 /dissoudreboite — fermer",
                "",
                "💰 FINANCES",
                "  /depotboite — déposer",
                "  /retirerboite — retirer",
                "  /parts — répartition",
                "  /acheterparts — acheter",
                "  /vendreparts — vendre",
                "  /dividendes — verser",
                "  /rd — R&D",
                "",
                "📦 PRODUITS",
                "  /produits — stock",
                "  /creer_produit — créer",
                "  /sellitem — vendre",
                "",
                "👥 GESTION",
                "  /candidatures — candidats",
                "  /accepter — accepter",
                "  /refuser — refuser",
                "  /nommer — promouvoir",
                "  /setsalaire — ajuster",
                "  /licencier — licencier",
                "  /prime — prime",
                "  /annonce — offre d'emploi",
                "",
                "🤝 B2B",
                "  /proposer_contrat — contrat",
                "  /repondre_contrat — répondre",
                "  /negocier_contrat — négocier",
                "  /proposer_fusion — fusion",
                "  /repondre_fusion — répondre",
            ],
        ),
        "vehicles": (
            "VÉHICULES",
            "🏎️",
            [
                "🏎️ /garage — garage interactif",
                "🔧 /repair — réparer",
                "⛽ /refuel — faire le plein",
                "📋 /vehicules — catalogue",
                "💰 /acheterv — acheter",
                "📋 /mesvehicules — mes véhicules",
                "💸 /vendrevehicule — vendre",
                "🛡️ /assurerv — assurer",
                "✨ /acces_vip — accès VIP",
                "🏛️ /lieux_vip — lieux VIP",
                "📦 /cargobonus — bonus cargo",
            ],
        ),
        "fun": (
            "LOISIRS & JEUX",
            "🎲",
            [
                "🎰 /slots — SPIN",
                "🃏 /blackjack — Tirer/Rester",
                "🎡 /roulette — LANCER",
                "💥 /crash — ENCAISSER",
                "♠️ /poker — vidéo",
                "💣 /mines — grille",
                "🐎 /pmu — courses",
                "🌱 /jardin — jardinage",
                "🌱 /planter — planter",
                "💧 /arroser — arroser",
                "🌾 /recolter — récolter",
                "🕒 Casino ouvert 17h→5h",
            ],
        ),
        "society": (
            "SOCIÉTÉ & POLITIQUE",
            "🏛️",
            [
                "📜 /constitution — consulter",
                "✍️ /modifierconstitution — modifier",
                "",
                "🗳️ ÉLECTIONS",
                "  /lancerelection — lancer",
                "  /candidater — se présenter",
                "  /voter — voter",
                "  /candidats — voir",
                "  /elections — liste",
                "  /depouiller — compter",
                "",
                "⚖️ DESTITUTION",
                "  /destituer — lancer",
                "  /signer — signer",
                "  /vote_destitution — voter",
                "",
                "🏛️ PARTIS",
                "  /creerparti — créer",
                "  /partis — liste",
                "  /rejoindreparti — rejoindre",
                "  /quitterparti — quitter",
                "",
                "📜 LOIS",
                "  /proposerloi — proposer",
                "  /voterloi — voter",
                "  /referendum — référendum",
                "  /votereferendum — voter",
                "",
                "⚔️ CRIME",
                "  /crimes — liste",
                "  /commettre — commettre",
                "  /caution — caution",
                "  /tribunal — tribunal",
                "  /avocat — avocat",
                "",
                "👥 GUILDES & GANGS",
                "  /guild — guildes",
                "  /gang — gangs",
            ],
        ),
        "mp": (
            "MULTIJOUEUR",
            "👥",
            [
                "🤝 /echange — échange",
                "🎁 /cadeau — cadeau",
                "💸 /payer — payer",
                "💍 /mariage — marier",
                "🥊 /defier — défier",
                "🤲 /don — donation",
                "📊 /classements — mondiaux",
                "💬 /salutations — saluer",
                "❤️ /relations — relations",
                "👥 /ami — ami",
                "🏆 /leaderboard — classement",
                "📱 /phone_msg — message privé",
                "",
                "🌐 RÉSEAUX SOCIAUX",
                "  📤 /poster — publier",
                "  📸 /story — story",
                "  🎥 /live — live",
                "  🤝 /collab — collaborer",
                "  ⭐ /noter — noter",
                "  🏆 /classement_social — top",
                "  👥 /creer_communaute — communauté",
                "  🏷️ /lancer_tendance — tendance",
                "  💎 /socialcoins — monnaie",
                "  ⁉️ /sondage — sondage",
            ],
        ),
        "profile": (
            "MON PROFIL",
            "📜",
            [
                "📱 /phone — SmartPhone Hub",
                "📜 /profil — vue complète",
                "📊 /stats — stats",
                "🏅 /badges — succès",
                "📦 /inventaire — objets",
                "👑 /titres — titres",
                "✍️ /bio — modifier",
                "📍 /lieu — changer",
                "📩 /notifications — gérer",
                "🌟 /karma — voir",
                "⭐ /niveau — progression",
                "🔮 /legacy — héritage",
                "😇 /reincarnate — réincarnation",
                "✈️ /monstimbre — voyages",
            ],
        ),
        "help": (
            "AIDE",
            "❓",
            [
                "📖 /aide — toutes les commandes",
                "🎓 /guide — tutoriel détaillé",
                "🆕 /nouveautes — quoi de neuf",
                "🚨 /report — signaler un bug",
                "📩 /notifications — gérer notifs",
                "💬 /menu — retour au menu",
            ],
        ),
    }

    title, icon, lines = menus.get(key, menus["main"])

    if key == "main":
        text = card(
            title,
            lines,
            icon=icon,
            style="double",
            footer="Choisis une catégorie ci-dessous pour ouvrir un menu paginé.",
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧬 Vie", callback_data="menu_life"),
             InlineKeyboardButton("💼 Économie", callback_data="menu_economy")],
            [InlineKeyboardButton("🎲 Loisirs", callback_data="menu_fun"),
             InlineKeyboardButton("🎓 Carrière", callback_data="menu_career")],
            [InlineKeyboardButton("👥 Multijoueur", callback_data="menu_mp"),
             InlineKeyboardButton("🏛️ Société", callback_data="menu_society")],
            [InlineKeyboardButton("🏢 Entreprises", callback_data="menu_companies"),
             InlineKeyboardButton("🏎️ Véhicules", callback_data="menu_vehicles")],
            [InlineKeyboardButton("📜 Profil", callback_data="menu_profile"),
             InlineKeyboardButton("❓ Aide", callback_data="menu_help")],
        ])
    else:
        per_page = 12
        total_pages = max(1, (len(lines) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page
        end = start + per_page
        page_lines = lines[start:end]
        footer = f"Page {page}/{total_pages} · ← Retour menu"
        text = card(title, page_lines, icon=icon, style="double", footer=footer)

        keyboard = []
        if total_pages > 1:
            nav = []
            if page > 1:
                nav.append(InlineKeyboardButton("◀️", callback_data=f"menu_{key}_{page-1}"))
            nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="menu_hold"))
            if page < total_pages:
                nav.append(InlineKeyboardButton("▶️", callback_data=f"menu_{key}_{page+1}"))
            keyboard.append(nav)
        keyboard.append([InlineKeyboardButton("← Retour menu", callback_data="menu_main")])
        markup = InlineKeyboardMarkup(keyboard)

    try:
        if q.message.text:
            await q.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=markup,
            )
        else:
            await context.bot.send_message(
                chat_id=q.message.chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup
            )
            await q.message.delete()
    except Exception:
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
        try:
            await q.message.delete()
        except:
            pass


# ═══════════════════════════════════════════════════════════════════
#                Callbacks pour informations
# ═══════════════════════════════════════════════════════════════════
async def dev_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = (
        "👨‍💻 **Développeur**\n\n"
        "Le cerveau derrière LifeSim :\n"
        "**[NETFLASH DIEU MAURICE](http://t.me/mauridieu)**\n\n"
        "_Passionné par la simulation de vie et les RPG textuels._"
    )
    await q.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)
    try:
        await q.message.delete()
    except Exception:
        pass

async def creator_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = (
        "👑 **Créateur**\n\n"
        "L’univers de LifeSim a été imaginé par un membre de :\n"
        "**[Game French](http://t.me/Sam_Rang_Nation)**\n\n"
        "_Merci de faire vivre ce monde._"
    )
    await q.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)
    try:
        await q.message.delete()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#                Callback pour la boutique /shop
# ═══════════════════════════════════════════════════════════════════
async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "shop_luxe":
        await q.edit_message_text(
            "👑 <b>Boutique luxe</b>\n"
            "Utilise <code>/luxe</code> pour voir les articles premium.\n"
            "Utilise <code>/acheterLuxe [id]</code> pour acheter un article.",
            parse_mode="HTML"
        )
    elif q.data == "shop_noir":
        await q.edit_message_text(
            "🕶️ <b>Marché noir</b>\n"
            "Utilise <code>/noir</code> pour voir les offres illégales.\n"
            "Utilise <code>/acheternoir [id]</code> pour acheter un article.",
            parse_mode="HTML"
        )
    elif q.data == "shop_close":
        await q.message.delete()
    else:
        await q.edit_message_text("❌ Option invalide.")


# ═══════════════════════════════════════════════════════════════════
#                      Commande inconnue
# ═══════════════════════════════════════════════════════════════════
async def cmd_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    cmd = update.message.text.split()[0]
    await update.message.reply_text(
        alert("warning",
              f"La commande <code>{escape_html(cmd)}</code> n'existe pas.\n"
              f"Tape /aide pour la liste complète."),
        parse_mode="HTML",
    )