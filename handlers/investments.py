import random
import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import (
    DB_PATH, get_user, update_balance, increment_field,
    get_portfolio, get_market_price, update_market_price
)
from utils.decorators import require_registered, cooldown, require_free
from utils.helpers import fmt, now, parse_amount
from config import ASSETS, SEASON_EFFECTS
from handlers.missions import update_mission_progress


def _find_asset(name: str) -> dict | None:
    for a in ASSETS:
        if a["name"].lower() == name.lower():
            return a
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (communs avec autres modules)
# ─────────────────────────────────────────────────────────────────────────────
async def get_city_multipliers(user_id: int) -> dict:
    u = await get_user(user_id)
    city = u.get("location", "Paris")  # ← CORRECTION : 'location' au lieu de 'city'
    from config import CITIES
    return CITIES.get(city, {"realestate_mult": 1.0, "vehicle_mult": 1.0, "salary_mult": 1.0, "market_mult": 1.0, "crime_mult": 1.0})


async def get_season_effect():
    # On utilise directement la date système pour éviter la dépendance circulaire
    import datetime
    m = datetime.datetime.utcnow().month
    if m in (12, 1, 2):
        season = "hiver"
    elif m in (3, 4, 5):
        season = "printemps"
    elif m in (6, 7, 8):
        season = "été"
    else:
        season = "automne"
    return SEASON_EFFECTS.get(season, {})


@require_registered
async def cmd_marche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    city_mult = await get_city_multipliers(user.id)
    market_mult = city_mult.get("market_mult", 1.0)
    season_effect = await get_season_effect()
    season_market_mult = season_effect.get("market_mult", 1.0)

    text = "📈 **Marché Boursier**\n\n"
    for a in ASSETS:
        global_price = await get_market_price(a["name"])
        base = a["price"]
        # Prix local pour l'affichage (n'affecte pas le marché global)
        local_price = int(global_price * market_mult * season_market_mult)
        change = ((global_price - base) / base) * 100
        arrow = "📈" if change >= 0 else "📉"
        text += (
            f"{a['emoji']} **{a['name']}**\n"
            f"  🌍 Prix mondial : {fmt(int(global_price))} {arrow} {change:+.1f}%\n"
            f"  🏙️ Prix local : {fmt(local_price)}\n"
            f"  📊 Volatilité : {int(a['volatility'] * 100)}%\n\n"
        )
    text += "_/acheteraction [nom] [quantité] — acheter (au prix local)_\n"
    text += "_/vendreaction [nom] [quantité] — vendre (au prix local)_\n"
    text += "_/portefeuille — voir tes investissements_"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
@require_free
@cooldown("buy_cooldown", 5, "⏳ Attends quelques secondes avant de passer un autre ordre.")
async def cmd_acheteraction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    city_mult = await get_city_multipliers(user.id)
    market_mult = city_mult.get("market_mult", 1.0)
    season_effect = await get_season_effect()
    season_market_mult = season_effect.get("market_mult", 1.0)

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage : /acheteraction [nom actif] [quantité]\n"
            "Ex: /acheteraction CryptoX 10\n"
            "/marche pour voir les prix"
        )
        return

    try:
        qty = float(context.args[-1])
        asset_name = " ".join(context.args[:-1])
    except ValueError:
        await update.message.reply_text("❌ Quantité invalide.")
        return

    if qty <= 0:
        await update.message.reply_text("❌ La quantité doit être positive.")
        return

    asset = _find_asset(asset_name)
    if not asset:
        await update.message.reply_text(f"❌ Actif inconnu : {asset_name}\n/marche pour voir la liste.")
        return

    global_price = await get_market_price(asset["name"])
    local_price = int(global_price * market_mult * season_market_mult)
    total_cost = int(local_price * qty)
    if total_cost < 1000:
        await update.message.reply_text("❌ Montant minimum d'achat : 1 000 coins.")
        return

    if u["balance"] < total_cost:
        await update.message.reply_text(
            f"❌ Fonds insuffisants !\n"
            f"💰 Coût total (prix local) : {fmt(total_cost)}\n"
            f"💵 Ton solde : {fmt(u['balance'])}"
        )
        return

    await update_balance(user.id, -total_cost)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM investments WHERE user_id=? AND asset_name=?",
            (user.id, asset["name"])
        ) as cur:
            existing = await cur.fetchone()

        if existing:
            old_qty = existing[3]
            old_avg = existing[4]
            new_qty = old_qty + qty
            new_avg = (old_qty * old_avg + qty * local_price) / new_qty
            await db.execute(
                "UPDATE investments SET quantity=?, avg_price=? WHERE user_id=? AND asset_name=?",
                (new_qty, new_avg, user.id, asset["name"])
            )
        else:
            await db.execute(
                "INSERT INTO investments (user_id, asset_name, quantity, avg_price, bought_at) VALUES (?,?,?,?,?)",
                (user.id, asset["name"], qty, local_price, now())
            )
        await db.commit()

    # Impact marché (basé sur le prix global)
    if total_cost > 1_000_000:
        bump = min(0.05, total_cost / 100_000_000)
        await update_market_price(asset["name"], global_price * (1 + bump))

    await increment_field(user.id, "xp", 50)
    # Mise à jour de la mission "Investir en bourse"
    await update_mission_progress(user.id, "invest", 1)

    await update.message.reply_text(
        f"✅ **Achat exécuté !**\n\n"
        f"{asset['emoji']} **{asset['name']}**\n"
        f"📊 Quantité : {qty:.3f}\n"
        f"💰 Prix unitaire (local) : {fmt(local_price)}\n"
        f"💵 Total payé : {fmt(total_cost)}\n\n"
        f"📈 Ton portefeuille s'enrichit !",
        parse_mode="Markdown"
    )


@require_registered
@require_free
@cooldown("sell_cooldown", 5, "⏳ Attends quelques secondes avant de passer un autre ordre.")
async def cmd_vendreaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    city_mult = await get_city_multipliers(user.id)
    market_mult = city_mult.get("market_mult", 1.0)
    season_effect = await get_season_effect()
    season_market_mult = season_effect.get("market_mult", 1.0)

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage : /vendreaction [nom actif] [quantité ou 'tout']\n"
            "Ex: /vendreaction CryptoX 5"
        )
        return

    try:
        qty_str = context.args[-1].lower()
        asset_name = " ".join(context.args[:-1])
    except Exception:
        await update.message.reply_text("❌ Format invalide.")
        return

    asset = _find_asset(asset_name)
    if not asset:
        await update.message.reply_text(f"❌ Actif inconnu. /marche pour voir la liste.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM investments WHERE user_id=? AND asset_name=?",
            (user.id, asset["name"])
        ) as cur:
            inv = await cur.fetchone()

    if not inv or inv["quantity"] <= 0:
        await update.message.reply_text(f"❌ Tu ne possèdes pas de {asset['name']}.")
        return

    if qty_str in ("tout", "all"):
        qty = inv["quantity"]
    else:
        try:
            qty = float(qty_str)
        except ValueError:
            await update.message.reply_text("❌ Quantité invalide.")
            return

    if qty <= 0:
        await update.message.reply_text("❌ La quantité doit être positive.")
        return

    if qty > inv["quantity"]:
        await update.message.reply_text(f"❌ Tu ne possèdes que {inv['quantity']:.3f} {asset['name']}.")
        return

    global_price = await get_market_price(asset["name"])
    local_price = int(global_price * market_mult * season_market_mult)
    total_revenue = int(local_price * qty)
    profit = int((local_price - inv["avg_price"]) * qty)
    profit_pct = ((local_price - inv["avg_price"]) / inv["avg_price"]) * 100

    new_qty = inv["quantity"] - qty
    await update_balance(user.id, total_revenue)

    async with aiosqlite.connect(DB_PATH) as db:
        if new_qty <= 0:
            await db.execute(
                "DELETE FROM investments WHERE user_id=? AND asset_name=?",
                (user.id, asset["name"])
            )
        else:
            await db.execute(
                "UPDATE investments SET quantity=? WHERE user_id=? AND asset_name=?",
                (new_qty, user.id, asset["name"])
            )
        await db.commit()

    # Impact marché (basé sur le prix global)
    if total_revenue > 1_000_000:
        drop = min(0.05, total_revenue / 100_000_000)
        await update_market_price(asset["name"], global_price * (1 - drop))

    await increment_field(user.id, "xp", 50)

    profit_text = f"📈 +{fmt(profit)} ({profit_pct:+.1f}%)" if profit >= 0 else f"📉 {fmt(profit)} ({profit_pct:+.1f}%)"

    await update.message.reply_text(
        f"✅ **Vente exécutée !**\n\n"
        f"{asset['emoji']} **{asset['name']}**\n"
        f"📊 Quantité vendue : {qty:.3f}\n"
        f"💰 Prix de vente (local) : {fmt(local_price)}\n"
        f"💵 Revenu total : {fmt(total_revenue)}\n"
        f"📈 Plus-value : {profit_text}\n\n"
        f"{'🎉 Belle performance !' if profit > 0 else '😢 Vente à perte...' if profit < 0 else '😐 Équilibre.'}",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_portefeuille(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    portfolio = await get_portfolio(user.id)
    city_mult = await get_city_multipliers(user.id)
    market_mult = city_mult.get("market_mult", 1.0)
    season_effect = await get_season_effect()
    season_market_mult = season_effect.get("market_mult", 1.0)

    if not portfolio:
        await update.message.reply_text(
            "📊 Ton portefeuille est vide.\n"
            "/marche pour voir les actifs disponibles."
        )
        return

    text = "📊 **Ton portefeuille d'investissement**\n\n"
    total_value = 0
    total_invested = 0

    for inv in portfolio:
        asset = _find_asset(inv["asset_name"])
        emoji = asset["emoji"] if asset else "📈"
        global_price = await get_market_price(inv["asset_name"])
        local_price = int(global_price * market_mult * season_market_mult)
        current_value = int(local_price * inv["quantity"])
        invested = int(inv["avg_price"] * inv["quantity"])
        pnl = current_value - invested
        pnl_pct = ((local_price - inv["avg_price"]) / inv["avg_price"]) * 100

        total_value += current_value
        total_invested += invested

        trend = "📈" if pnl >= 0 else "📉"
        text += (
            f"{emoji} **{inv['asset_name']}**\n"
            f"  Qté : {inv['quantity']:.3f} | Prix moy : {fmt(int(inv['avg_price']))}\n"
            f"  Valeur actuelle (locale) : {fmt(current_value)}\n"
            f"  {trend} P&L : {'+' if pnl >= 0 else ''}{fmt(pnl)} ({pnl_pct:+.1f}%)\n\n"
        )

    total_pnl = total_value - total_invested
    total_pnl_pct = ((total_value - total_invested) / max(1, total_invested)) * 100

    text += (
        f"━━━━━━━━━━━━\n"
        f"💎 **Valeur totale (locale) : {fmt(total_value)}**\n"
        f"💰 Investi : {fmt(total_invested)}\n"
        f"{'📈' if total_pnl >= 0 else '📉'} P&L total : {'+' if total_pnl >= 0 else ''}{fmt(total_pnl)} ({total_pnl_pct:+.1f}%)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def fluctuate_market():
    """Called by scheduler to update market prices."""
    for asset in ASSETS:
        price = await get_market_price(asset["name"])
        volatility = asset["volatility"]
        change = random.gauss(0, volatility * 0.1)
        new_price = max(asset["price"] * 0.1, price * (1 + change))
        await update_market_price(asset["name"], round(new_price, 2))


@require_registered
async def cmd_historique(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show price history for a given asset."""
    if not context.args:
        names = ", ".join(a["name"] for a in ASSETS)
        await update.message.reply_text(
            f"📊 **Historique des prix**\n\nUsage : /historique [actif]\n\nActifs disponibles : {names}"
        )
        return

    name = " ".join(context.args)
    asset = _find_asset(name)
    if not asset:
        await update.message.reply_text(f"❌ Actif introuvable : {name}")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT price, recorded_at FROM price_history WHERE asset_name=? ORDER BY recorded_at DESC LIMIT 20",
            (asset["name"],)
        ) as cur:
            rows = await cur.fetchall()

    from datetime import datetime
    current = await get_market_price(asset["name"])
    text = f"📊 **Historique — {asset['name']}**\n\nPrix actuel (mondial) : **{fmt(current)}**\n\n"

    if not rows:
        text += "_Aucun historique disponible pour le moment._"
    else:
        for r in rows[:10]:
            dt = datetime.fromtimestamp(r["recorded_at"]).strftime("%d/%m %H:%M")
            text += f"• [{dt}] {fmt(r['price'])}\n"

    base = asset["price"]
    change = ((current - base) / base) * 100
    text += f"\n📉 Base d'origine : {fmt(base)}\n{'📈' if change >= 0 else '📉'} Variation totale : {change:+.1f}%"
    await update.message.reply_text(text, parse_mode="Markdown")