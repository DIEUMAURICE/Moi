# handlers/realestate.py — Version améliorée (pagination, robustesse, factorisation)
import aiosqlite
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, update_field, get_properties, add_notification, now
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt, parse_amount, fmt_time, escape_html
from utils.pagination import paginate_lines
from config import PROPERTIES, CITIES, SEASON_EFFECTS

MAX_PROPERTIES = 20

# ==================== HELPERS MULTIPLICATEURS ====================
async def get_city_multipliers(user_id: int) -> dict:
    """Retourne les multiplicateurs de la ville du joueur."""
    u = await get_user(user_id)
    city = u.get("location", "Paris")
    return CITIES.get(city, {"realestate_mult": 1.0, "vehicle_mult": 1.0, "salary_mult": 1.0,
                             "market_mult": 1.0, "crime_mult": 1.0})

async def get_season_multiplier() -> float:
    """Retourne le multiplicateur saisonnier pour l'immobilier (ex: été plus actif)."""
    try:
        from handlers.calendar import get_calendar
        cal = await get_calendar()
        season = cal["season"]
        fx = SEASON_EFFECTS.get(season, {})
        # Par exemple, le marché immobilier est plus actif au printemps
        return fx.get("garden_mult", 1.0)  # temporaire, à ajuster selon besoin
    except ImportError:
        return 1.0

# ==================== COMMANDES PRINCIPALES ====================
@require_registered
@require_free
async def cmd_proprietes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    mult = await get_city_multipliers(user.id)
    season_mult = await get_season_multiplier()

    text = "🏠 <b>Marché Immobilier</b>\n\n"
    for ptype, data in PROPERTIES.items():
        price = int(data['price'] * mult.get("realestate_mult", 1.0) * season_mult)
        rent = int(data['rent'] * mult.get("realestate_mult", 1.0))
        maint = int(data['maint'] * mult.get("realestate_mult", 1.0))
        net_month = rent - maint
        net_year = net_month * 12
        yield_annual = (net_year / price) * 100 if price > 0 else 0
        text += (
            f"{data['emoji']} <b>{ptype}</b>\n"
            f"  💰 Prix : {fmt(price)}\n"
            f"  🏦 Hypothèque (20%) : {fmt(int(price * 0.20))}\n"
            f"  💵 Loyer : {fmt(rent)}/mois\n"
            f"  🔧 Entretien : {fmt(maint)}/mois\n"
            f"  📈 Rendement net : {fmt(net_month)}/mois ({yield_annual:.1f}% annuel)\n\n"
        )
    text += "<i>/acheter [type] — acheter une propriété</i>\n"
    text += "<i>/hypotheque [type] — acheter avec un crédit (20% d'apport)</i>\n"
    text += "<i>/mesbiens — voir tes propriétés</i>\n"
    text += "<i>/proposer_location [numéro] @locataire [loyer] — proposer une location</i>"
    await update.message.reply_text(text, parse_mode="HTML")

@require_registered
@require_free
@cooldown("buy_property", 10, "⏳ Attends un instant avant d'acheter à nouveau.")
async def cmd_acheter_bien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    mult = await get_city_multipliers(user.id)

    if not context.args:
        await update.message.reply_text("Usage : /acheter [type de bien]\nEx: /acheter Studio\n\n/proprietes pour voir la liste")
        return

    ptype = " ".join(context.args).title()
    matched = None
    for p in PROPERTIES:
        if p.lower() == ptype.lower():
            matched = p
            break
    if not matched:
        await update.message.reply_text(f"❌ Type de bien inconnu. /proprietes pour voir la liste.")
        return

    data = PROPERTIES[matched]
    price = int(data["price"] * mult.get("realestate_mult", 1.0))
    props = await get_properties(user.id)
    if len(props) >= MAX_PROPERTIES:
        await update.message.reply_text(f"❌ Tu as déjà {MAX_PROPERTIES} biens, maximum atteint.")
        return

    if u["balance"] < price:
        await update.message.reply_text(
            f"❌ Fonds insuffisants !\n💰 Prix : {fmt(price)}\n💵 Ton solde : {fmt(u['balance'])}\n\n"
            f"💡 Pense à l'hypothèque : /hypotheque [type] (paye 20% maintenant)",
            parse_mode="HTML"
        )
        return

    # Nom par défaut ou personnalisé
    if len(context.args) > 1:
        raw_name = " ".join(context.args[1:])
        name = raw_name[:50]  # limiter la longueur
    else:
        name = f"{matched} de {user.full_name}"

    await update_balance(user.id, -price)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO properties (user_id, prop_type, name, purchased_at, condition, last_maintenance) VALUES (?,?,?,?,100,?)",
            (user.id, matched, name, now(), now())
        )
        await db.commit()

    net_rent = int((data['rent'] - data['maint']) * mult.get("realestate_mult", 1.0))
    await update.message.reply_text(
        f"🏠 <b>Propriété achetée !</b>\n\n"
        f"{data['emoji']} <b>{matched}</b>\n"
        f"💰 Prix payé : {fmt(price)}\n"
        f"💵 Loyer potentiel : {fmt(int(data['rent'] * mult.get('realestate_mult', 1.0)))}/mois\n"
        f"🔧 Entretien : {fmt(int(data['maint'] * mult.get('realestate_mult', 1.0)))}/mois\n"
        f"📈 Rendement net mensuel : {fmt(net_rent)}\n\n"
        f"👉 /proposer_location [numéro] @locataire [loyer] — mettre en location\n"
        f"👉 /mesbiens — voir toutes tes propriétés",
        parse_mode="HTML"
    )

@require_registered
@require_free
@cooldown("mortgage", 10, "⏳ Attends un instant avant d'utiliser l'hypothèque.")
async def cmd_hypotheque(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    mult = await get_city_multipliers(user.id)

    if not context.args:
        text = "🏦 <b>Achat immobilier avec hypothèque</b>\n\nPaye seulement 20% maintenant, le reste sur 30 ans.\n\n"
        for ptype, data in PROPERTIES.items():
            price = int(data["price"] * mult.get("realestate_mult", 1.0))
            down = int(price * 0.20)
            monthly = int(price * 0.80 / 360)
            text += f"{data['emoji']} <b>{ptype}</b> — Apport : {fmt(down)} | Mensualité : {fmt(monthly)}\n"
        text += "\n<i>/hypotheque [type] pour acheter</i>"
        await update.message.reply_text(text, parse_mode="HTML")
        return

    ptype = " ".join(context.args).title()
    matched = None
    for p in PROPERTIES:
        if p.lower() == ptype.lower():
            matched = p
            break
    if not matched:
        await update.message.reply_text("❌ Type de bien inconnu.")
        return

    data = PROPERTIES[matched]
    price = int(data["price"] * mult.get("realestate_mult", 1.0))
    down = int(price * 0.20)
    mortgage = price - down
    monthly = int(mortgage / 360)

    props = await get_properties(user.id)
    if len(props) >= MAX_PROPERTIES:
        await update.message.reply_text(f"❌ Tu as déjà {MAX_PROPERTIES} biens, maximum atteint.")
        return

    if u["balance"] < down:
        await update.message.reply_text(f"❌ Apport insuffisant !\n💰 Apport requis (20%) : {fmt(down)}\n💵 Ton solde : {fmt(u['balance'])}", parse_mode="HTML")
        return

    await update_balance(user.id, -down)

    if len(context.args) > 1:
        name = " ".join(context.args[1:])[:50]
    else:
        name = f"{matched} (hypothèque)"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO properties (user_id, prop_type, name, purchased_at, mortgage, mortgage_due, condition, last_maintenance) VALUES (?,?,?,?,?,?,100,?)",
            (user.id, matched, name, now(), mortgage, now() + 360 * 86400, now())
        )
        await db.commit()

    await update.message.reply_text(
        f"🏦 <b>Hypothèque accordée !</b>\n\n"
        f"{data['emoji']} <b>{matched}</b> acheté !\n"
        f"💰 Apport versé : {fmt(down)}\n"
        f"💳 Hypothèque restante : {fmt(mortgage)}\n"
        f"📅 Mensualité : {fmt(monthly)}/mois\n"
        f"⏳ Durée : 30 ans\n\n"
        f"⚠️ Les mensualités sont prélevées automatiquement chaque mois.",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_mesbiens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche la liste des propriétés du joueur avec pagination."""
    user = update.effective_user
    props = await get_properties(user.id)
    mult = await get_city_multipliers(user.id)
    mult_val = mult.get("realestate_mult", 1.0)

    if not props:
        await update.message.reply_text("🏠 Tu ne possèdes aucune propriété.\n/proprietes pour voir le marché.")
        return

    lines = []
    total_value = 0
    total_rent = 0
    for i, p in enumerate(props, 1):
        data = PROPERTIES.get(p["prop_type"], {})
        val = int(data.get("price", 0) * mult_val)
        rent = int(data.get("rent", 0) * mult_val)
        rented = "✅ Louée" if p.get("rented_to") else "❌ Vacante"
        condition = p.get("condition", 100)
        cond_bar = "█" * (condition // 10) + "░" * (10 - condition // 10)
        mortgage_text = f"\n  💳 Hypothèque : {fmt(p['mortgage'])}" if p.get("mortgage", 0) > 0 else ""
        total_value += val
        total_rent += rent if p.get("rented_to") else 0

        line = (f"#{i} {data.get('emoji', '🏠')} <b>{p['prop_type']}</b> — {escape_html(p.get('name', ''))}\n"
                f"  💰 Valeur : {fmt(val)} | {rented}{mortgage_text}\n"
                f"  🏚️ État : <code>{cond_bar}</code> {condition}%\n"
                f"  💵 Loyer : {fmt(rent)}/mois")
        last_maint = p.get("last_maintenance", 0)
        if last_maint:
            ago = fmt_time(now() - last_maint)
            line += f"\n  🔧 Dernier entretien : il y a {ago}"
        lines.append(line)

    # En-tête et pied de page
    header = f"🏠 <b>Tes propriétés</b> (total: {len(props)})\n\n"
    footer = (f"\n📊 <b>Valeur totale : {fmt(total_value)}</b>\n"
              f"💵 Revenus locatifs actifs : {fmt(total_rent)}/mois\n"
              "<i>/entretenir [numéro] pour maintenir l'état\n"
              "/proposer_location [numéro] @locataire [loyer]</i>")

    # Pagination (20 lignes par page)
    page = 1
    if context.args and context.args[0].isdigit():
        page = int(context.args[0])
    text, markup = await paginate_lines(lines, page, per_page=15, header=header, footer=footer)
    if markup:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode="HTML")

# ==================== LOCATION SÉCURISÉE ====================
@require_registered
@require_free
@cooldown("propose_rental", 10, "⏳ Attends un instant.")
async def cmd_proposer_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    props = await get_properties(user.id)

    if len(context.args) < 3:
        await update.message.reply_text("Usage : /proposer_location [numéro] @locataire [loyer]")
        return

    try:
        prop_num = int(context.args[0]) - 1
        prop = props[prop_num]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Numéro de propriété invalide. Utilise /mesbiens pour voir la liste.")
        return

    tenant_mention = context.args[1]
    tenant_id = None
    if tenant_mention.startswith("@"):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT user_id FROM users WHERE username=?", (tenant_mention[1:],)) as cur:
                row = await cur.fetchone()
                if row: tenant_id = row[0]
    else:
        try:
            tenant_id = int(tenant_mention)
        except:
            pass
    if not tenant_id:
        await update.message.reply_text("❌ Locataire introuvable. Utilise son @username.")
        return

    try:
        rent = int(context.args[2])
        if rent <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Loyer invalide (doit être un nombre positif).")
        return

    if prop.get("rented_to"):
        await update.message.reply_text("❌ Cette propriété est déjà louée.")
        return

    if tenant_id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te louer ta propre propriété.")
        return

    tenant = await get_user(tenant_id)
    if not tenant.get("registered"):
        await update.message.reply_text("❌ Ce joueur n'est pas enregistré.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO rental_proposals (property_id, owner_id, tenant_id, rent, proposed_at)
            VALUES (?, ?, ?, ?, ?)
        """, (prop["prop_id"], user.id, tenant_id, rent, now()))
        proposal_id = cursor.lastrowid
        await db.commit()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accepter", callback_data=f"rent_accept_{proposal_id}"),
         InlineKeyboardButton("❌ Refuser", callback_data=f"rent_refuse_{proposal_id}")]
    ])
    try:
        await context.bot.send_message(
            tenant_id,
            f"🏠 **Proposition de location**\n\n"
            f"De : {escape_html(user.full_name)}\n"
            f"Bien : {prop['prop_type']} — {escape_html(prop.get('name', ''))}\n"
            f"💰 Loyer : {fmt(rent)}/mois\n\n"
            f"Souhaites-tu louer ce bien ?",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await update.message.reply_text(f"📨 Proposition envoyée à {escape_html(tenant['full_name'])}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Impossible d'envoyer la proposition : {e}")

async def rental_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    _, action, proposal_id = data.split("_")
    proposal_id = int(proposal_id)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM rental_proposals WHERE id = ?", (proposal_id,)) as cur:
            prop = await cur.fetchone()
        if not prop or prop["status"] != "pending":
            await query.edit_message_text("❌ Cette proposition a expiré ou n'existe plus.")
            return

        if prop["tenant_id"] != query.from_user.id:
            await query.answer("Ce n'est pas pour toi.", show_alert=True)
            return

        if action == "refuse":
            await db.execute("UPDATE rental_proposals SET status = 'refused' WHERE id = ?", (proposal_id,))
            await db.commit()
            await query.edit_message_text("❌ Location refusée.")
            await add_notification(prop["owner_id"], f"🏠 {escape_html(query.from_user.full_name)} a refusé votre proposition de location pour le bien #{prop['property_id']}.")
            return

        # Acceptation
        async with db.execute("SELECT rented_to FROM properties WHERE prop_id = ?", (prop["property_id"],)) as cur2:
            prop_row = await cur2.fetchone()
        if prop_row and prop_row["rented_to"]:
            await query.edit_message_text("❌ Cette propriété a déjà été louée.")
            return

        await db.execute("""
            INSERT INTO rental_agreements (property_id, owner_id, tenant_id, rent, start_date)
            VALUES (?, ?, ?, ?, ?)
        """, (prop["property_id"], prop["owner_id"], prop["tenant_id"], prop["rent"], now()))
        await db.execute("UPDATE properties SET rented_to = ? WHERE prop_id = ?", (prop["tenant_id"], prop["property_id"]))
        await db.execute("UPDATE rental_proposals SET status = 'accepted' WHERE id = ?", (proposal_id,))
        await db.commit()

    await query.edit_message_text(
        f"✅ **Location signée !**\n\n"
        f"🏠 Bien #{prop['property_id']}\n"
        f"💰 Loyer : {fmt(prop['rent'])}/mois\n"
        f"_Les loyers seront prélevés automatiquement chaque mois._"
    )
    try:
        await context.bot.send_message(
            prop["owner_id"],
            f"🏠 **Location acceptée !**\n\n"
            f"Votre bien #{prop['property_id']} est maintenant loué à {escape_html(query.from_user.full_name)} pour {fmt(prop['rent'])}/mois.",
            parse_mode="HTML"
        )
    except Exception:
        pass

@require_registered
async def cmd_meslocations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT ra.id, p.prop_type, p.name, ra.rent, ra.start_date
            FROM rental_agreements ra
            JOIN properties p ON p.prop_id = ra.property_id
            WHERE ra.tenant_id = ? AND ra.status = 'active'
        """, (user.id,)) as cur:
            rentals = await cur.fetchall()
    if not rentals:
        await update.message.reply_text("🏠 Tu ne loues aucun logement.")
        return
    text = "🏠 <b>Tes locations</b>\n\n"
    for r in rentals:
        text += f"📍 <b>{r['prop_type']}</b> — {escape_html(r['name'])}\n"
        text += f"💰 Loyer : {fmt(r['rent'])}/mois\n"
        elapsed = now() - r['start_date']
        since_str = "aujourd'hui" if elapsed < 86400 else fmt_time(elapsed)
        text += f"📅 Depuis : {since_str}\n"
        text += f"👉 /quitter_logement {r['id']} pour résilier\n\n"
    await update.message.reply_text(text, parse_mode="HTML")

@require_registered
@require_free
async def cmd_quitter_logement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /quitter_logement [id_contrat]")
        return
    try:
        contract_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT ra.property_id, ra.owner_id
            FROM rental_agreements ra
            WHERE ra.id = ? AND ra.tenant_id = ? AND ra.status = 'active'
        """, (contract_id, user.id)) as cur:
            rental = await cur.fetchone()
        if not rental:
            await update.message.reply_text("❌ Contrat de location introuvable.")
            return
        property_id, owner_id = rental
        await db.execute("UPDATE rental_agreements SET status = 'terminated', end_date = ? WHERE id = ?", (now(), contract_id))
        await db.execute("UPDATE properties SET rented_to = 0 WHERE prop_id = ?", (property_id,))
        await db.commit()
        await add_notification(owner_id, f"🏠 {escape_html(user.full_name)} a quitté le logement #{property_id}. Le bien est à nouveau disponible.")
    await update.message.reply_text("👋 Tu as quitté ton logement. Les loyers ne seront plus prélevés.")

# ==================== ANCIENNE COMMANDE /louer (redirection) ====================
@require_registered
@require_free
async def cmd_louer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ La commande <code>/louer</code> a été remplacée par <code>/proposer_location</code> pour plus de sécurité.\n\n"
        "👉 <b>Nouvelle utilisation :</b>\n"
        "<code>/proposer_location [numéro] @locataire [loyer]</code>\n\n"
        "Le locataire doit ensuite <b>accepter</b> la proposition via un bouton.\n"
        "Il peut voir ses locations avec <code>/meslocations</code> et quitter à tout moment avec <code>/quitter_logement</code>.\n\n"
        "<i>Cette nouvelle méthode empêche la location forcée et permet au locataire de gérer ses baux.</i>",
        parse_mode="HTML"
    )

# ==================== VENTE ET ENTRETIEN ====================
@require_registered
@require_free
@cooldown("sell_property", 10, "⏳ Attends un instant avant de vendre à nouveau.")
async def cmd_vendre_bien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    props = await get_properties(user.id)
    mult = await get_city_multipliers(user.id)
    mult_val = mult.get("realestate_mult", 1.0)

    if not context.args:
        await update.message.reply_text("Usage : /vendre [numéro] (/mesbiens pour voir la liste)")
        return

    try:
        prop_num = int(context.args[0]) - 1
        prop = props[prop_num]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Numéro invalide.")
        return

    data = PROPERTIES.get(prop["prop_type"], {})
    condition = prop.get("condition", 100)
    base_price = data.get("price", 0) * mult_val
    condition_factor = condition / 100
    sell_price = int(base_price * 0.85 * condition_factor)

    if prop.get("mortgage", 0) > 0:
        if sell_price < prop["mortgage"]:
            await update.message.reply_text(
                f"❌ La vente ne couvrirait pas ton hypothèque !\n"
                f"Valeur nette : {fmt(sell_price)}\n"
                f"Hypothèque due : {fmt(prop['mortgage'])}\n"
                f"Rembourse d'abord l'hypothèque ou améliore l'état du bien.",
                parse_mode="HTML"
            )
            return
        sell_price -= prop["mortgage"]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM properties WHERE prop_id=?", (prop["prop_id"],))
        await db.commit()

    await update_balance(user.id, sell_price)
    await update.message.reply_text(
        f"✅ <b>Propriété vendue !</b>\n\n"
        f"{data.get('emoji','🏠')} {prop['prop_type']} — {escape_html(prop.get('name', ''))}\n"
        f"💰 Prix de vente : {fmt(sell_price)}\n"
        f"🏚️ État au moment de la vente : {condition}%\n"
        f"<i>(-15% frais d'agence)</i>",
        parse_mode="HTML"
    )

@require_registered
@require_free
@cooldown("maintain_property", 86400, "⏳ Tu ne peux entretenir qu'une fois par jour.")
async def cmd_entretenir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    props = await get_properties(user.id)

    if not context.args:
        await update.message.reply_text("Usage : /entretenir [numéro] (/mesbiens pour voir la liste)")
        return

    try:
        prop_num = int(context.args[0]) - 1
        prop = props[prop_num]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Numéro invalide.")
        return

    condition = prop.get("condition", 100)
    if condition >= 100:
        await update.message.reply_text(f"✅ Cette propriété est déjà en excellent état.")
        return

    base_price = PROPERTIES.get(prop["prop_type"], {}).get("price", 0)
    mult = await get_city_multipliers(user.id)
    price = int(base_price * mult.get("realestate_mult", 1.0))
    cost = int(price * 0.01)

    u = await get_user(user.id)
    if u["balance"] < cost:
        await update.message.reply_text(f"❌ Fonds insuffisants pour l'entretien. Coût : {fmt(cost)}", parse_mode="HTML")
        return

    await update_balance(user.id, -cost)
    new_condition = min(100, condition + 20)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE properties SET condition=?, last_maintenance=? WHERE prop_id=?", (new_condition, now(), prop["prop_id"]))
        await db.commit()

    await update.message.reply_text(
        f"🔧 <b>Entretien effectué !</b>\n\n"
        f"🏠 {prop['prop_type']} — {escape_html(prop.get('name', ''))}\n"
        f"🏚️ État : {condition}% → {new_condition}%\n"
        f"💰 Coût : {fmt(cost)}\n"
        f"_Pense à entretenir régulièrement pour éviter la dégradation accélérée._",
        parse_mode="HTML"
    )

# ==================== NOUVELLE COMMANDE : INFOS SUR UN BIEN ====================
@require_registered
async def cmd_propriete_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les détails complets d'une propriété (par numéro)."""
    user = update.effective_user
    props = await get_properties(user.id)
    if not context.args:
        await update.message.reply_text("Usage : /propriete_info [numéro] (voir /mesbiens pour les numéros)")
        return
    try:
        prop_num = int(context.args[0]) - 1
        prop = props[prop_num]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Numéro invalide.")
        return

    mult = await get_city_multipliers(user.id)
    mult_val = mult.get("realestate_mult", 1.0)
    data = PROPERTIES.get(prop["prop_type"], {})
    price = int(data.get("price", 0) * mult_val)
    rent = int(data.get("rent", 0) * mult_val)
    maint = int(data.get("maint", 0) * mult_val)
    condition = prop.get("condition", 100)
    cond_bar = "█" * (condition // 10) + "░" * (10 - condition // 10)
    last_maint = prop.get("last_maintenance", 0)
    mortgage = prop.get("mortgage", 0)
    rented_to = prop.get("rented_to", 0)

    text = (
        f"🏠 <b>Propriété #{prop_num+1}</b>\n\n"
        f"{data.get('emoji', '🏠')} <b>{prop['prop_type']}</b> — {escape_html(prop.get('name', ''))}\n"
        f"💰 Valeur actuelle : {fmt(price)}\n"
        f"💵 Loyer de base : {fmt(rent)}/mois\n"
        f"🔧 Entretien mensuel : {fmt(maint)}\n"
        f"🏚️ État : <code>{cond_bar}</code> {condition}%\n"
    )
    if last_maint:
        ago = fmt_time(now() - last_maint)
        text += f"🔧 Dernier entretien : il y a {ago}\n"
    if mortgage > 0:
        text += f"💳 Hypothèque restante : {fmt(mortgage)}\n"
    if rented_to:
        text += f"✅ <b>Louée à</b> : {rented_to}\n"
    else:
        text += f"❌ <b>Vacante</b>\n"

    await update.message.reply_text(text, parse_mode="HTML")

# ==================== MAINTENANCE (appelée par scheduler) ====================
async def process_realestate_maintenance():
    """Détérioration naturelle, gestion des hypothèques, notifications."""
    from config import PROPERTIES
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1. Détérioration naturelle légère (tous les jours)
        await db.execute("UPDATE properties SET condition = MAX(0, condition - 2) WHERE condition > 0")

        # 2. Dégradation accélérée pour les biens sans entretien depuis plus de 30 jours
        cutoff = now() - 30 * 86400
        async with db.execute("SELECT * FROM properties WHERE last_maintenance < ? AND condition > 0", (cutoff,)) as cur:
            neglected = await cur.fetchall()
        for prop in neglected:
            deg = random.randint(5, 15)
            new_cond = max(0, prop["condition"] - deg)
            await db.execute("UPDATE properties SET condition = ? WHERE prop_id = ?", (new_cond, prop["prop_id"]))
            # Notifier le propriétaire
            if new_cond < prop["condition"]:
                await add_notification(prop["user_id"], f"🏚️ Votre bien {prop['prop_type']} se dégrade faute d'entretien (état {new_cond}%).")

        # 3. Gestion des hypothèques
        async with db.execute("SELECT * FROM properties WHERE mortgage > 0 AND mortgage_due > 0") as cur:
            props = [dict(p) for p in await cur.fetchall()]
        for prop in props:
            mortgage = prop["mortgage"]
            if mortgage <= 0:
                continue
            monthly = int(mortgage / 360) if mortgage > 0 else 0
            if monthly <= 0:
                continue
            owner = await get_user(prop["user_id"])
            if owner["balance"] >= monthly:
                await update_balance(prop["user_id"], -monthly)
                new_mortgage = mortgage - monthly
                if new_mortgage <= 0:
                    await db.execute("UPDATE properties SET mortgage=0, mortgage_due=0 WHERE prop_id=?", (prop["prop_id"],))
                else:
                    await db.execute("UPDATE properties SET mortgage=? WHERE prop_id=?", (new_mortgage, prop["prop_id"]))
            else:
                # Pénalité : dégradation supplémentaire
                await db.execute("UPDATE properties SET condition = MAX(0, condition - 10) WHERE prop_id=?", (prop["prop_id"],))
                await add_notification(prop["user_id"], f"⚠️ Impayé sur votre hypothèque ! L'état de votre bien se dégrade.")

        await db.commit()