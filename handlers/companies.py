# handlers/companies.py — Module Entreprises refondu
import aiosqlite
import random
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import (
    DB_PATH, get_user, update_balance, get_company_by_name, get_company_by_id,
    get_user_company, get_all_companies, log_company_action, increment_field,
    now, add_notification, DB_TIMEOUT
)
from utils.decorators import require_registered, require_free
from utils.helpers import fmt, parse_amount, escape_md, escape_html
from utils.aesthetics import card, alert, section, rich_bar
from config import SECTORS, COMPANY_CREATION_COST, PRODUCT_EFFECTS

# ─────────────────────────────────────────────────────────────────────────────
# Helper : enlever les guillemets
# ─────────────────────────────────────────────────────────────────────────────
def strip_quotes(s: str) -> str:
    """Enlève les guillemets au début et à la fin d'une chaîne."""
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    return s

# ─────────────────────────────────────────────────────────────────────────────
# Helper : ajouter/retirer item de l'inventaire
# ─────────────────────────────────────────────────────────────────────────────
async def _add_item_to_inventory(user_id: int, item_id: int, quantity: int = 1, item_type: str = "product", db=None):
    if db is None:
        async with aiosqlite.connect(DB_PATH) as db:
            return await _add_item_to_inventory(user_id, item_id, quantity, item_type, db)
    async with db.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?", (user_id, item_id)) as cur:
        existing = await cur.fetchone()
    if existing:
        await db.execute("UPDATE inventory SET quantity = quantity + ? WHERE user_id = ? AND item_id = ?", (quantity, user_id, item_id))
    else:
        async with db.execute("SELECT name FROM items WHERE item_id = ?", (item_id,)) as cur2:
            name_row = await cur2.fetchone()
        item_name = name_row[0] if name_row else "Item"
        await db.execute("""
            INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, item_id, item_type, item_name, quantity, now()))
    return True

async def _remove_item_from_inventory(user_id: int, item_id: int, quantity: int = 1, db=None) -> bool:
    if db is None:
        async with aiosqlite.connect(DB_PATH) as db:
            return await _remove_item_from_inventory(user_id, item_id, quantity, db)
    async with db.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?", (user_id, item_id)) as cur:
        row = await cur.fetchone()
    if not row or row[0] < quantity:
        return False
    new_qty = row[0] - quantity
    if new_qty <= 0:
        await db.execute("DELETE FROM inventory WHERE user_id = ? AND item_id = ?", (user_id, item_id))
    else:
        await db.execute("UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND item_id = ?", (quantity, user_id, item_id))
    return True

# ─────────────────────────────────────────────────────────────────────────────
# Helper : pagination
# ─────────────────────────────────────────────────────────────────────────────
async def paginate_items(items: list, page: int, per_page: int = 5, title: str = ""):
    total_pages = (len(items) + per_page - 1) // per_page
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    end = start + per_page
    page_items = items[start:end]
    text = title + "\n\n" + "\n".join(page_items)
    keyboard = []
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"company_page_{page-1}"))
        nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"company_page_{page+1}"))
        keyboard.append(nav)
    return text, InlineKeyboardMarkup(keyboard) if keyboard else None

# ─────────────────────────────────────────────────────────────────────────────
# Fonctions utilitaires pour les parts
# ─────────────────────────────────────────────────────────────────────────────
async def get_user_shares(user_id: int, company_id: int) -> int:
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        async with db.execute("SELECT shares FROM company_shares WHERE user_id=? AND company_id=?", (user_id, company_id)) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0

async def update_company_field(company_id: int, field: str, value):
    allowed_fields = {"treasury", "reputation", "level", "rd_level", "overhead"}
    if field not in allowed_fields:
        raise ValueError("Champ non autorisé")
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute(f"UPDATE companies SET {field}=? WHERE company_id=?", (value, company_id))
        await db.commit()

async def get_company_field(company_id: int, field: str):
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        async with db.execute(f"SELECT {field} FROM companies WHERE company_id=?", (company_id,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None

# ─────────────────────────────────────────────────────────────────────────────
# Fonctions d'affichage (company view)
# ─────────────────────────────────────────────────────────────────────────────
def _company_stage(level: int) -> str:
    if level >= 15:
        return "Empire mondial"
    if level >= 11:
        return "Groupe dominant"
    if level >= 8:
        return "Scale-up"
    if level >= 5:
        return "Entreprise installée"
    if level >= 3:
        return "Startup ambitieuse"
    return "Jeune pousse"

def _company_targets(level: int) -> dict:
    return {
        "treasury": 150_000 * max(1, level),
        "reputation": min(95, 45 + level * 4),
        "rd_level": max(1, level // 2),
        "employees": min(12, 1 + level),
        "products": min(6, 1 + level // 2),
    }

def _progress_mark(current: int, target: int) -> str:
    return "✅" if current >= target else "⬜"

async def _company_metrics(company: dict) -> dict:
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) AS n, COALESCE(AVG(activity_score),0) AS avg_activity "
            "FROM company_members WHERE company_id=?",
            (company["company_id"],)
        ) as cur:
            team = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM company_products WHERE company_id=?", (company["company_id"],)) as cur:
            product_count = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM company_ads WHERE company_id=?", (company["company_id"],)) as cur:
            ad_count = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM company_applications WHERE company_id=? AND status='pending'",
            (company["company_id"],)
        ) as cur:
            pending_apps = (await cur.fetchone())[0]
        # Contrats actifs (type accepted)
        async with db.execute(
            "SELECT COUNT(*) FROM contracts WHERE (from_company=? OR to_company=?) AND status='accepted' AND end_date > ?",
            (company["company_id"], company["company_id"], now())
        ) as cur:
            contract_count = (await cur.fetchone())[0]
        # Contrats en négociation
        async with db.execute(
            "SELECT COUNT(*) FROM contracts WHERE (from_company=? OR to_company=?) AND status='negotiating'",
            (company["company_id"], company["company_id"])
        ) as cur:
            negotiating_contracts = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*), COALESCE(SUM(shares), 0) FROM company_shares WHERE company_id=?",
            (company["company_id"],)
        ) as cur:
            shareholders_count, total_shares = await cur.fetchone()
        async with db.execute("""
            SELECT name, price, sales, revenue
            FROM company_products
            WHERE company_id=?
            ORDER BY revenue DESC, sales DESC, price DESC
            LIMIT 5
        """, (company["company_id"],)) as cur:
            top_products = [dict(r) for r in await cur.fetchall()]
        async with db.execute("""
            SELECT u.full_name, cm.poste, cm.activity_score, cm.base_salary
            FROM company_members cm
            JOIN users u ON u.user_id=cm.user_id
            WHERE cm.company_id=?
            ORDER BY cm.activity_score DESC, cm.base_salary DESC
            LIMIT 5
        """, (company["company_id"],)) as cur:
            top_members = [dict(r) for r in await cur.fetchall()]
        async with db.execute("""
            SELECT action, details, timestamp
            FROM company_logs
            WHERE company_id=?
            ORDER BY timestamp DESC
            LIMIT 4
        """, (company["company_id"],)) as cur:
            logs = [dict(r) for r in await cur.fetchall()]

    revenue_total = sum(p["revenue"] for p in top_products)  # historique, plus utilisé
    growth_score = min(
        100,
        company["level"] * 6
        + company["reputation"] // 2
        + company.get("rd_level", 0) * 7
        + int((team["avg_activity"] or 0) * 1.5)
        + product_count * 6
        + contract_count * 8
    )
    health_score = min(
        100,
        max(
            5,
            int(
                min(35, company["treasury"] / 25_000)
                + min(30, company["reputation"] * 0.3)
                + min(20, team["n"] * 2.5)
                + min(15, company.get("rd_level", 0) * 3)
            )
        ),
    )

    return {
        "employees": team["n"],
        "avg_activity": int(team["avg_activity"] or 0),
        "products": product_count,
        "ads": ad_count,
        "pending_apps": pending_apps,
        "contracts": contract_count,
        "negotiating_contracts": negotiating_contracts,
        "shareholders": shareholders_count,
        "total_shares": total_shares or 100,
        "top_products": top_products,
        "top_members": top_members,
        "logs": logs,
        "revenue_total": revenue_total,
        "growth_score": growth_score,
        "health_score": health_score,
    }

def _company_buttons(company_id: int, is_manager: bool = False):
    rows = [[
        InlineKeyboardButton("🏢 Vue", callback_data=f"company_page_overview_{company_id}"),
        InlineKeyboardButton("🧠 Audit", callback_data=f"company_page_audit_{company_id}"),
    ], [
        InlineKeyboardButton("📦 Produits", callback_data=f"company_page_products_{company_id}"),
        InlineKeyboardButton("👥 Équipe", callback_data=f"company_page_team_{company_id}"),
    ]]
    if is_manager:
        rows.append([
            InlineKeyboardButton("💼 Gestion", callback_data=f"company_page_manage_{company_id}"),
            InlineKeyboardButton("💰 Finance", callback_data=f"company_page_finance_{company_id}"),
        ])
    return InlineKeyboardMarkup(rows)

def _company_recommendations(company: dict, metrics: dict) -> list[str]:
    recommendations = []
    runway_needed = max(50_000, metrics["employees"] * max(1, company.get("overhead", 1000)) * 3)
    if company["treasury"] < runway_needed:
        recommendations.append(f"Renforcer la trésorerie jusqu'à <b>{fmt(runway_needed)}</b> minimum.")
    if metrics["products"] == 0:
        recommendations.append("Créer un produit pour générer des revenus via le marché.")
    if metrics["employees"] < max(2, company["level"]):
        recommendations.append("Recruter pour soutenir la croissance et augmenter l'activité.")
    if company.get("rd_level", 0) < max(1, company["level"] // 2):
        recommendations.append("Investir en R&D pour améliorer la qualité des produits.")
    if company["reputation"] < 60:
        recommendations.append("Remonter la réputation avec une équipe active et une offre plus crédible.")
    if metrics["pending_apps"] > 0:
        recommendations.append("Traiter les candidatures en attente pour ne pas rater des talents.")
    if metrics["contracts"] == 0 and company["level"] >= 4:
        recommendations.append("Signer un contrat B2B pour stabiliser les revenus.")
    return recommendations[:4] or ["Entreprise équilibrée. Continue à élargir le catalogue et la structure."]

async def _render_company_view(company: dict, viewer_company: dict | None, view: str = "overview"):
    metrics = await _company_metrics(company)
    is_manager = (
        viewer_company is not None
        and viewer_company.get("company_id") == company["company_id"]
        and viewer_company.get("poste") in ("PDG", "Directeur")
    )
    targets = _company_targets(company["level"] + 1)
    buttons = _company_buttons(company["company_id"], is_manager)
    owner = await get_user(company["owner_id"])

    if view == "products":
        body = [
            f"Catalogue : <b>{metrics['products']}</b> produit(s)",
            f"Revenu cumulé suivi : <b>{fmt(metrics['revenue_total'])}</b>",
            "",
        ]
        if metrics["top_products"]:
            for product in metrics["top_products"]:
                # Récupérer l'effet du produit depuis items
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT effect_type, effect_value FROM items WHERE name=?", (product["name"],)) as cur:
                        item_row = await cur.fetchone()
                effect_str = f" (effet: {item_row[0] if item_row else 'Aucun'})" if item_row else ""
                body.append(
                    f"📦 <b>{escape_html(product['name'])}</b> · prix {fmt(product['price'])} · "
                    f"ventes {product['sales']} · CA {fmt(product['revenue'])}{effect_str}"
                )
        else:
            body.append("Aucun produit enregistré. Utilise <code>/creer_produit Nom effet valeur quantité</code> pour lancer le catalogue.")
        body += ["", "Les produits ne sont plus vendus automatiquement. Vous devez les vendre via le marché."]
        return card("📦 Catalogue entreprise", body, icon="📦", style="thick"), buttons

    if view == "team":
        body = [
            f"👥 Effectif : <b>{metrics['employees']}</b>",
            f"⚡ Activité moyenne : <b>{metrics['avg_activity']}</b>",
            f"📨 Candidatures en attente : <b>{metrics['pending_apps']}</b>",
            "",
        ]
        if metrics["top_members"]:
            for member in metrics["top_members"]:
                body.append(
                    f"👤 <b>{escape_html(member['full_name'])}</b> · {escape_html(member['poste'])} · "
                    f"activité {member['activity_score']} · salaire {fmt(member['base_salary'])}"
                )
        else:
            body.append("Aucun employé actif.")
        return card("👥 Équipe", body, icon="👥", style="thick"), buttons

    if view == "finance":
        share_value = max(
            10,
            (company["treasury"] // 1000) * (50 + company["reputation"]) // 50 + company.get("rd_level", 0) * 100,
        )
        market_cap = share_value * max(1, metrics["total_shares"])
        body = [
            f"💰 Trésorerie : <b>{fmt(company['treasury'])}</b>",
            f"💸 Frais généraux : <b>{fmt(company.get('overhead', 1000))}</b> / employé",
            f"📈 Valeur par part : <b>{fmt(share_value)}</b>",
            f"🏦 Valorisation interne : <b>{fmt(market_cap)}</b>",
            f"🤝 Contrats actifs : <b>{metrics['contracts']}</b>",
            f"📝 Contrats en négociation : <b>{metrics['negotiating_contracts']}</b>",
            f"👤 Actionnaires : <b>{metrics['shareholders']}</b>",
            "",
            f"Momentum : <code>{rich_bar(metrics['growth_score'], 100, 12)}</code> <b>{metrics['growth_score']}%</b>",
        ]
        return card("💰 Finance", body, icon="💰", style="thick"), buttons

    if view == "manage":
        body = [
            f"PDG : <b>{escape_html(owner['full_name'])}</b>",
            f"Réputation : <b>{company['reputation']}/100</b>",
            f"Niveau : <b>{company['level']}</b> · phase <b>{_company_stage(company['level'])}</b>",
            "",
            "<b>Raccourcis utiles</b>",
            "<code>/annonce poste salaire</code>",
            "<code>/candidatures</code>",
            "<code>/rd montant</code>",
            "<code>/creer_produit nom effet valeur quantité</code>",
            "<code>/proposer_contrat cible montant durée type</code>",
            "<code>/proposer_fusion cible</code>",
        ]
        return card("💼 Gestion", body, icon="💼", style="thick"), buttons

    if view == "audit":
        body = [
            f"Phase actuelle : <b>{_company_stage(company['level'])}</b>",
            f"Santé : <code>{rich_bar(metrics['health_score'], 100, 10)}</code> <b>{metrics['health_score']}%</b>",
            f"Croissance : <code>{rich_bar(metrics['growth_score'], 100, 10)}</code> <b>{metrics['growth_score']}%</b>",
            "",
            "<b>Prochain cap</b>",
            f"{_progress_mark(company['treasury'], targets['treasury'])} Trésorerie {fmt(company['treasury'])} / {fmt(targets['treasury'])}",
            f"{_progress_mark(company['reputation'], targets['reputation'])} Réputation {company['reputation']} / {targets['reputation']}",
            f"{_progress_mark(company.get('rd_level', 0), targets['rd_level'])} R&D {company.get('rd_level', 0)} / {targets['rd_level']}",
            f"{_progress_mark(metrics['employees'], targets['employees'])} Employés {metrics['employees']} / {targets['employees']}",
            f"{_progress_mark(metrics['products'], targets['products'])} Produits {metrics['products']} / {targets['products']}",
            "",
            "<b>Priorités</b>",
        ]
        body.extend(f"• {tip}" for tip in _company_recommendations(company, metrics))
        return card("🧠 Audit entreprise", body, icon="🧠", style="thick"), buttons

    # Overview
    body = [
        f"🏭 Secteur : <b>{escape_html(company['sector'])}</b>",
        f"👑 PDG : <b>{escape_html(owner['full_name'])}</b>",
        f"🏆 Niveau : <b>{company['level']}</b> · <b>{_company_stage(company['level'])}</b>",
        f"⭐ Réputation : <b>{company['reputation']}/100</b>",
        f"💰 Trésorerie : <b>{fmt(company['treasury'])}</b>",
        f"🔬 R&D : <b>{company.get('rd_level', 0)}</b>",
        "",
        f"👥 Effectif : <b>{metrics['employees']}</b> · activité moyenne <b>{metrics['avg_activity']}</b>",
        f"📦 Produits : <b>{metrics['products']}</b> · 🤝 contrats actifs : <b>{metrics['contracts']}</b>",
        f"📨 Candidatures : <b>{metrics['pending_apps']}</b> · 📣 annonces : <b>{metrics['ads']}</b>",
        "",
        f"Santé : <code>{rich_bar(metrics['health_score'], 100, 12)}</code> <b>{metrics['health_score']}%</b>",
        f"Croissance : <code>{rich_bar(metrics['growth_score'], 100, 12)}</code> <b>{metrics['growth_score']}%</b>",
    ]
    if metrics["logs"]:
        body += ["", "<b>Derniers mouvements</b>"]
        for log in metrics["logs"][:3]:
            body.append(f"• <b>{escape_html(log['action'])}</b> · {escape_html(log['details'])}")
    return card(f"🏢 {escape_html(company['name'])}", body, icon="🏢", style="thick"), buttons

# ─────────────────────────────────────────────────────────────────────────────
# Commandes principales (conservées ou légèrement adaptées)
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
@require_free
async def cmd_boites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sector = " ".join(context.args) if context.args else None
    companies = await get_all_companies(sector)
    if not companies:
        s_text = f" dans le secteur {sector}" if sector else ""
        await update.message.reply_text(alert("info", f"Aucune entreprise trouvée{s_text}."), parse_mode="HTML")
        return
    by_sector = {}
    for c in companies:
        by_sector.setdefault(c["sector"], []).append(c)
    lines = []
    for sec, comps in by_sector.items():
        lines.append(f"<b>{escape_html(sec)}</b>")
        for c in comps[:5]:
            lines.append(
                f"• <b>{escape_html(c['name'])}</b> · {fmt(c['treasury'])} · niv.{c['level']} · rep {c['reputation']}/100"
            )
        if len(comps) > 5:
            lines.append(f"<i>(+{len(comps) - 5} autres)</i>")
        lines.append("")
    sectors_text = " | ".join(SECTORS)
    lines.append(f"Secteurs : <i>{escape_html(sectors_text)}</i>")
    lines.append("Commande : <code>/boites [secteur]</code> pour filtrer")
    await update.message.reply_text(
        card("🏢 Marché des entreprises", lines, icon="🏢", style="thick"),
        parse_mode="HTML"
    )

@require_registered
@require_free
async def cmd_infoboite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(alert("info", "Usage : <code>/infoboite [nom entreprise]</code>"), parse_mode="HTML")
        return
    name = " ".join(context.args)
    c = await get_company_by_name(name)
    if not c:
        await update.message.reply_text(alert("error", "Entreprise introuvable."), parse_mode="HTML")
        return
    viewer_company = await get_user_company(update.effective_user.id)
    text, reply_markup = await _render_company_view(c, viewer_company, "overview")
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

@require_registered
@require_free
async def cmd_employes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if context.args:
        c = await get_company_by_name(" ".join(context.args))
    else:
        c = await get_user_company(user.id)
    if not c:
        await update.message.reply_text("❌ Entreprise introuvable ou tu n'es pas employé.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.full_name, cm.poste, cm.base_salary, cm.activity_score, cm.joined_at
            FROM company_members cm JOIN users u ON u.user_id=cm.user_id
            WHERE cm.company_id=? ORDER BY cm.activity_score DESC
        """, (c["company_id"],)) as cur:
            members = [dict(m) for m in await cur.fetchall()]
    if not members:
        await update.message.reply_text("👥 Aucun employé.")
        return
    page = 1
    if context.args and context.args[-1].isdigit():
        page = int(context.args[-1])
    lines = []
    for m in members:
        lines.append(
            f"👤 **{escape_md(m['full_name'])}** — {escape_md(m['poste'])}\n"
            f"  💰 {fmt(m['base_salary'])}/paie | ⚡ {m['activity_score']} pts"
        )
    title = f"👥 **{escape_md(c['name'])} — Équipe**"
    text, reply_markup = await paginate_items(lines, page, 5, title)
    if reply_markup:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

@require_registered
@require_free
async def cmd_postuler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if len(context.args) < 1:
        await update.message.reply_text("Usage : /postuler [nom entreprise] [salaire souhaité]")
        return
    name = " ".join(context.args[:-1]) if len(context.args) > 1 else context.args[0]
    desired_salary = 0
    if len(context.args) > 1 and context.args[-1].isdigit():
        desired_salary = int(context.args[-1])
    c = await get_company_by_name(name)
    if not c:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return
    if await get_user_company(user.id):
        await update.message.reply_text("❌ Tu travailles déjà dans une entreprise.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        async with db.execute(
            "SELECT * FROM company_applications WHERE user_id=? AND company_id=? AND status='pending'",
            (user.id, c["company_id"])
        ) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Candidature déjà en attente.")
                return
        await db.execute(
            "INSERT INTO company_applications (user_id, company_id, desired_salary, applied_at) VALUES (?,?,?,?)",
            (user.id, c["company_id"], desired_salary, now())
        )
        await db.commit()
    salary_text = f" avec prétention salariale de {fmt(desired_salary)}" if desired_salary else ""
    await update.message.reply_text(
        f"✅ Candidature envoyée à **{escape_md(c['name'])}**{salary_text} !\n"
        f"_Le PDG va examiner ta candidature._",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_demissionner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company:
        await update.message.reply_text("❌ Tu ne travailles dans aucune entreprise.")
        return
    if company["owner_id"] == user.id:
        await update.message.reply_text("❌ Tu es PDG ! Utilise /dissoudreboite pour fermer.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute("DELETE FROM company_members WHERE user_id=? AND company_id=?", (user.id, company["company_id"]))
        await db.execute("DELETE FROM company_shares WHERE user_id=? AND company_id=?", (user.id, company["company_id"]))
        await db.commit()
    owner = await get_user(company["owner_id"])
    await add_notification(owner["user_id"], f"📢 {user.full_name} a démissionné de **{escape_md(company['name'])}**.")
    await log_company_action(company["company_id"], "démission", user.id, user.full_name)
    await update.message.reply_text(f"👋 Tu as démissionné de **{escape_md(company['name'])}**.", parse_mode="Markdown")

@require_registered
@require_free
async def cmd_monentreprise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company:
        await update.message.reply_text(
            alert("error", "Tu ne travailles dans aucune entreprise. Utilise <code>/emplois</code> ou <code>/postuler [nom]</code>."),
            parse_mode="HTML"
        )
        return
    bonus = int(company.get("base_salary", 0) * 0.1 * company.get("activity_score", 0))
    text, reply_markup = await _render_company_view(company, company, "overview")
    extra = section(
        "Mon poste",
        [
            f"👔 Poste : <b>{escape_html(company['poste'])}</b>",
            f"💰 Salaire de base : <b>{fmt(company.get('base_salary', 0))}</b> / paie",
            f"⚡ Bonus activité : <b>+{fmt(bonus)}</b> ({company.get('activity_score', 0)} pts)",
            f"💎 Salaire estimé : <b>{fmt(company.get('base_salary', 0) + bonus)}</b>",
        ],
        icon="👔"
    )
    await update.message.reply_text(f"{text}\n{extra}", parse_mode="HTML", reply_markup=reply_markup)

@require_registered
@require_free
async def cmd_auditboite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        company = await get_company_by_name(" ".join(context.args))
    else:
        company = await get_user_company(update.effective_user.id)
    if not company:
        await update.message.reply_text(
            alert("error", "Entreprise introuvable. Utilise <code>/auditboite [nom]</code> ou rejoins une entreprise."),
            parse_mode="HTML"
        )
        return
    viewer_company = await get_user_company(update.effective_user.id)
    text, reply_markup = await _render_company_view(company, viewer_company, "audit")
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

@require_registered
@require_free
async def cmd_candidatures(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["poste"] not in ("PDG", "Directeur"):
        await update.message.reply_text("❌ Réservé aux PDG et Directeurs.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT ca.app_id, u.full_name, u.user_id, u.diplome, u.karma, ca.desired_salary, ca.applied_at
            FROM company_applications ca JOIN users u ON u.user_id=ca.user_id
            WHERE ca.company_id=? AND ca.status='pending'
        """, (company["company_id"],)) as cur:
            apps = [dict(a) for a in await cur.fetchall()]
    if not apps:
        await update.message.reply_text("📋 Aucune candidature en attente.")
        return
    page = 1
    if context.args and context.args[-1].isdigit():
        page = int(context.args[-1])
    lines = []
    for a in apps:
        lines.append(
            f"#**{a['app_id']}** {escape_md(a['full_name'])}\n"
            f"  🎓 {escape_md(a['diplome'] or 'Sans diplôme')} | 🌟 Karma: {a['karma']} | 💰 Prétention: {fmt(a['desired_salary'])}"
        )
    title = f"📋 **Candidatures — {escape_md(company['name'])}**"
    text, reply_markup = await paginate_items(lines, page, 5, title)
    if reply_markup:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown")
    await update.message.reply_text("_/accepter [id] [salaire] | /refuser [id] | /negocier [id] [proposition]_", parse_mode="Markdown")

@require_registered
@require_free
async def cmd_accepter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["poste"] not in ("PDG", "Directeur"):
        await update.message.reply_text("❌ Réservé aux PDG et Directeurs.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /accepter [id candidature] [salaire proposé]")
        return
    try:
        app_id = int(context.args[0])
        salary = int(context.args[1])
        if salary < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ ID ou salaire invalide.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM company_applications WHERE app_id=? AND company_id=? AND status='pending'",
            (app_id, company["company_id"])
        ) as cur:
            app = await cur.fetchone()
        if not app:
            await update.message.reply_text("❌ Candidature introuvable.")
            return
        await db.execute("UPDATE company_applications SET status='accepted' WHERE app_id=?", (app_id,))
        await db.execute("""
            INSERT OR REPLACE INTO company_members (user_id, company_id, poste, base_salary, joined_at, activity_score)
            VALUES (?,?,?,?,?,0)
        """, (app["user_id"], company["company_id"], "Employé", salary, now()))
        await db.commit()
    candidate = await get_user(app["user_id"])
    await log_company_action(company["company_id"], "recrutement", user.id, f"{candidate['full_name']} salaire {fmt(salary)}")
    await add_notification(app["user_id"], f"🎉 Votre candidature pour **{escape_md(company['name'])}** a été acceptée ! Salaire : {fmt(salary)}. Utilisez `/monentreprise` pour voir votre poste.")
    await update.message.reply_text(f"✅ Candidature #{app_id} acceptée ! Nouvel employé au salaire de {fmt(salary)}.")

@require_registered
@require_free
async def cmd_negocier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["poste"] not in ("PDG", "Directeur"):
        await update.message.reply_text("❌ Réservé aux PDG et Directeurs.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /negocier [id candidature] [salaire proposé]")
        return
    try:
        app_id = int(context.args[0])
        proposed_salary = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ ID ou salaire invalide.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        async with db.execute(
            "SELECT user_id, desired_salary FROM company_applications WHERE app_id=? AND company_id=? AND status='pending'",
            (app_id, company["company_id"])
        ) as cur:
            app = await cur.fetchone()
        if not app:
            await update.message.reply_text("❌ Candidature introuvable.")
            return
        await db.execute("UPDATE company_applications SET desired_salary=?, status='negotiating' WHERE app_id=?",
                         (proposed_salary, app_id))
        await db.commit()
    await add_notification(app[0], f"🏢 **{escape_md(company['name'])}** vous propose un salaire de {fmt(proposed_salary)}. Utilisez `/repondre_offre {app_id} accepter/refuser`.")
    await update.message.reply_text(f"📨 Contre-offre envoyée. En attente de réponse.")

@require_registered
@require_free
async def cmd_repondre_offre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /repondre_offre [id candidature] [accepter/refuser]")
        return
    try:
        app_id = int(context.args[0])
        decision = context.args[1].lower()
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        async with db.execute(
            "SELECT company_id, desired_salary, status FROM company_applications WHERE app_id=? AND user_id=?",
            (app_id, user.id)
        ) as cur:
            app = await cur.fetchone()
        if not app or app[2] != "negotiating":
            await update.message.reply_text("❌ Aucune offre en négociation trouvée.")
            return
        company_id = app[0]
        async with db.execute("SELECT owner_id FROM companies WHERE company_id=?", (company_id,)) as cur2:
            owner_row = await cur2.fetchone()
        owner_id = owner_row[0] if owner_row else None
        if decision == "accepter":
            salary = app[1]
            await db.execute("UPDATE company_applications SET status='accepted' WHERE app_id=?", (app_id,))
            await db.execute("""
                INSERT OR REPLACE INTO company_members (user_id, company_id, poste, base_salary, joined_at, activity_score)
                VALUES (?,?,?,?,?,0)
            """, (user.id, company_id, "Employé", salary, now()))
            await db.commit()
            await log_company_action(company_id, "recrutement", user.id, f"{user.full_name} salaire {fmt(salary)}")
            await update.message.reply_text(f"✅ Offre acceptée ! Tu rejoins l'entreprise avec un salaire de {fmt(salary)}.")
            if owner_id:
                await add_notification(owner_id, f"📢 {user.full_name} a accepté votre offre pour un salaire de {fmt(salary)}.")
        else:
            await db.execute("UPDATE company_applications SET status='refused' WHERE app_id=?", (app_id,))
            await db.commit()
            await update.message.reply_text("❌ Offre refusée.")
            if owner_id:
                await add_notification(owner_id, f"📢 {user.full_name} a refusé votre offre.")

@require_registered
@require_free
async def cmd_refuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["poste"] not in ("PDG", "Directeur"):
        await update.message.reply_text("❌ Réservé aux PDG et Directeurs.")
        return
    if not context.args:
        await update.message.reply_text("Usage : /refuser [id candidature]")
        return
    try:
        app_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        async with db.execute("SELECT user_id FROM company_applications WHERE app_id=? AND company_id=?", (app_id, company["company_id"])) as cur:
            candidat = await cur.fetchone()
        await db.execute(
            "UPDATE company_applications SET status='refused' WHERE app_id=? AND company_id=?",
            (app_id, company["company_id"])
        )
        await db.commit()
    if candidat:
        await add_notification(candidat[0], f"❌ Votre candidature pour **{escape_md(company['name'])}** a été refusée.")
    await update.message.reply_text(f"❌ Candidature #{app_id} refusée.")

@require_registered
@require_free
async def cmd_nommer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Réservé au PDG.")
        return
    if not update.message.reply_to_message or len(context.args) < 2:
        await update.message.reply_text(
            f"Usage : /nommer [salaire] [poste] (en répondant à l'employé)\n"
            f"Ex: /nommer 7500 Directeur"
        )
        return
    target = update.message.reply_to_message.from_user
    try:
        salary = int(context.args[0])
        if salary < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Salaire invalide.")
        return
    poste = " ".join(context.args[1:]) or "Employé"
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute(
            "UPDATE company_members SET poste=?, base_salary=? WHERE user_id=? AND company_id=?",
            (poste, salary, target.id, company["company_id"])
        )
        await db.commit()
    await log_company_action(company["company_id"], "promotion", user.id, f"{target.full_name} → {poste} ({fmt(salary)})")
    await add_notification(target.id, f"👔 Félicitations ! Vous êtes promu(e) **{poste}** avec un salaire de {fmt(salary)} dans **{escape_md(company['name'])}**.")
    await update.message.reply_text(
        f"👔 **{escape_md(target.full_name)}** promu(e) au poste de **{escape_md(poste)}** !\n"
        f"💰 Nouveau salaire : {fmt(salary)}/paie",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_setsalaire(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Réservé au PDG.")
        return
    if not update.message.reply_to_message or len(context.args) != 1:
        await update.message.reply_text("Usage : /setsalaire [montant] (en répondant à l'employé)")
        return
    target = update.message.reply_to_message.from_user
    try:
        new_salary = int(context.args[0])
        if new_salary < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute(
            "UPDATE company_members SET base_salary=? WHERE user_id=? AND company_id=?",
            (new_salary, target.id, company["company_id"])
        )
        await db.commit()
    await log_company_action(company["company_id"], "ajustement_salaire", user.id, f"{target.full_name} → {fmt(new_salary)}")
    await add_notification(target.id, f"💰 Votre salaire chez **{escape_md(company['name'])}** a été modifié : {fmt(new_salary)}/paie.")
    await update.message.reply_text(f"💰 Salaire de {escape_md(target.full_name)} modifié à {fmt(new_salary)}/paie.")

@require_registered
@require_free
async def cmd_licencier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["poste"] not in ("PDG", "Directeur"):
        await update.message.reply_text("❌ Réservé aux PDG et Directeurs.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Réponds au message de l'employé à licencier.")
        return
    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te licencier toi-même !")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        async with db.execute(
            "SELECT base_salary FROM company_members WHERE user_id=? AND company_id=?",
            (target.id, company["company_id"])
        ) as cur:
            emp = await cur.fetchone()
    if not emp:
        await update.message.reply_text("❌ Cet employé ne travaille pas dans ton entreprise.")
        return
    severance = emp[0] * 2
    can_pay = company["treasury"] >= severance
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute("DELETE FROM company_members WHERE user_id=? AND company_id=?", (target.id, company["company_id"]))
        await db.execute("DELETE FROM company_shares WHERE user_id=? AND company_id=?", (target.id, company["company_id"]))
        if can_pay:
            await db.execute("UPDATE companies SET treasury=treasury-? WHERE company_id=?", (severance, company["company_id"]))
        await db.commit()
    if can_pay:
        await update_balance(target.id, severance)
        await add_notification(target.id, f"⚠️ Vous avez été licencié(e) de **{escape_md(company['name'])}**. Indemnités : {fmt(severance)}.")
    else:
        await add_notification(target.id, f"⚠️ Vous avez été licencié(e) de **{escape_md(company['name'])}** sans indemnités (trésorerie insuffisante).")
    await log_company_action(company["company_id"], "licenciement", user.id, target.full_name)
    await update.message.reply_text(
        f"🔨 **{escape_md(target.full_name)}** licencié(e).\n"
        f"{'💰 Indemnité versée : ' + fmt(severance) if can_pay else '⚠️ Trésorerie insuffisante pour les indemnités.'}",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_creerboite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    if not context.args or len(context.args) < 2:
        sectors_text = ", ".join(SECTORS)
        await update.message.reply_text(
            f"🏢 **Créer une entreprise**\n\n"
            f"💰 Coût de création : {fmt(COMPANY_CREATION_COST)}\n\n"
            f"Usage : /creerboite [nom] [secteur]\n"
            f"Secteurs : {sectors_text}"
        )
        return
    sector = context.args[-1].capitalize()
    name = " ".join(context.args[:-1])
    matched_sector = None
    for s in SECTORS:
        if s.lower() == sector.lower():
            matched_sector = s
            break
    if not matched_sector:
        await update.message.reply_text(f"❌ Secteur inconnu. Choix : {', '.join(SECTORS)}")
        return
    if u["balance"] < COMPANY_CREATION_COST:
        await update.message.reply_text(
            f"❌ Fonds insuffisants !\n"
            f"💰 Coût : {fmt(COMPANY_CREATION_COST)}\n"
            f"💵 Ton solde : {fmt(u['balance'])}"
        )
        return
    # Vérifier si une entreprise (même dissoute) porte déjà ce nom
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT company_id, dissolved FROM companies WHERE name = ?", (name,)) as cur:
            existing = await cur.fetchone()
        if existing:
            if existing["dissolved"] == 0:
                await update.message.reply_text(f"❌ Une entreprise nommée '{escape_md(name)}' existe déjà et n'est pas dissoute !")
                return
            else:
                # Supprimer l'ancienne dissoute pour éviter conflit
                await db.execute("DELETE FROM companies WHERE company_id = ?", (existing["company_id"],))
                await db.execute("DELETE FROM company_members WHERE company_id = ?", (existing["company_id"],))
                await db.execute("DELETE FROM company_shares WHERE company_id = ?", (existing["company_id"],))
                await db.execute("DELETE FROM company_applications WHERE company_id = ?", (existing["company_id"],))
                await db.execute("DELETE FROM company_logs WHERE company_id = ?", (existing["company_id"],))
                await db.execute("DELETE FROM company_products WHERE company_id = ?", (existing["company_id"],))
                await db.execute("DELETE FROM company_ads WHERE company_id = ?", (existing["company_id"],))
                await db.execute("DELETE FROM company_invitations WHERE company_id = ?", (existing["company_id"],))
                await db.commit()
    existing_company = await get_user_company(user.id)
    if existing_company and existing_company["owner_id"] == user.id:
        await update.message.reply_text("❌ Tu diriges déjà une entreprise !")
        return
    await update_balance(user.id, -COMPANY_CREATION_COST)
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute(
            "INSERT INTO companies (name, owner_id, sector, treasury, reputation, level, created_at, overhead, rd_level) VALUES (?,?,?,0,1,1,?,1000,0)",
            (name, user.id, matched_sector, now())
        )
        async with db.execute("SELECT last_insert_rowid()") as cur:
            company_id = (await cur.fetchone())[0]
        await db.execute(
            "INSERT INTO company_members (user_id, company_id, poste, base_salary, joined_at) VALUES (?,?,?,?,?)",
            (user.id, company_id, "PDG", 0, now())
        )
        await db.execute(
            "INSERT INTO company_shares (user_id, company_id, shares) VALUES (?,?,?)",
            (user.id, company_id, 100)
        )
        await db.commit()
    await update.message.reply_text(
        f"🏢 **{escape_md(name)} fondée !**\n\n"
        f"🏭 Secteur : {matched_sector}\n"
        f"💰 Fonds utilisés : {fmt(COMPANY_CREATION_COST)}\n"
        f"👑 Tu es PDG !\n"
        f"⭐ Réputation initiale : 50/100\n\n"
        f"📋 **Prochaines étapes :**\n"
        f"• /candidatures — voir les candidats\n"
        f"• /depotboite montant — alimenter la trésorerie\n"
        f"• /retirerboite montant — retirer de la trésorerie\n"
        f"• /setsalaire — ajuster les salaires\n"
        f"• /versersalaires — verser les salaires\n"
        f"• /acheterparts — investir dans ton entreprise",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_dissoudreboite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Réservé au PDG.")
        return
    if not context.args or context.args[0].lower() != "confirmer":
        await update.message.reply_text(
            f"⚠️ **DANGER : Dissolution de {escape_md(company['name'])}**\n\n"
            f"Cette action est IRRÉVERSIBLE !\n"
            f"💰 Trésorerie récupérée : {fmt(company['treasury'] // 2)} (50%)\n\n"
            f"Pour confirmer : /dissoudreboite confirmer"
        )
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        async with db.execute("SELECT user_id FROM company_members WHERE company_id=?", (company["company_id"],)) as cur:
            members = await cur.fetchall()
    treasury_return = company["treasury"] // 2
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute("UPDATE companies SET dissolved=1 WHERE company_id=?", (company["company_id"],))
        await db.execute("DELETE FROM company_members WHERE company_id=?", (company["company_id"],))
        await db.execute("DELETE FROM company_shares WHERE company_id=?", (company["company_id"],))
        await db.commit()
    if treasury_return > 0:
        await update_balance(user.id, treasury_return)
    for (uid,) in members:
        if uid != user.id:
            await add_notification(uid, f"💀 L'entreprise **{escape_md(company['name'])}** a été dissoute par le PDG.")
    await update.message.reply_text(
        f"💀 **{escape_md(company['name'])} a été dissoute.**\n\n"
        f"💰 Récupéré : {fmt(treasury_return)}\n"
        f"_Une fin d'ère..._"
    )

@require_registered
@require_free
async def cmd_depotboite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    company = await get_user_company(user.id)
    if not company or company["poste"] not in ("PDG", "Directeur"):
        await update.message.reply_text("❌ Réservé aux PDG et Directeurs.")
        return
    if not context.args:
        await update.message.reply_text("Usage : /depotboite montant")
        return
    amount = parse_amount(context.args[0], u["balance"])
    if not amount or amount <= 0 or amount > u["balance"]:
        await update.message.reply_text("❌ Montant invalide.")
        return
    await update_balance(user.id, -amount)
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute("UPDATE companies SET treasury=treasury+? WHERE company_id=?",
                         (amount, company["company_id"]))
        await db.commit()
    await log_company_action(company["company_id"], "dépôt", user.id, fmt(amount))
    await update.message.reply_text(
        f"🏦 **Dépôt en trésorerie : {fmt(amount)}**\n"
        f"🏢 Nouvelle trésorerie : {fmt(company['treasury'] + amount)}",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_retirerboite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut retirer de la trésorerie.")
        return
    if not context.args:
        await update.message.reply_text("Usage : /retirerboite montant")
        return
    amount = parse_amount(context.args[0], company["treasury"])
    if not amount or amount <= 0 or amount > company["treasury"]:
        await update.message.reply_text("❌ Montant invalide ou supérieur à la trésorerie.")
        return
    await update_balance(user.id, amount)
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute("UPDATE companies SET treasury=treasury-? WHERE company_id=?",
                         (amount, company["company_id"]))
        await db.commit()
    await log_company_action(company["company_id"], "retrait", user.id, fmt(amount))
    await update.message.reply_text(
        f"🏦 **Retrait de trésorerie : {fmt(amount)}**\n"
        f"🏢 Nouvelle trésorerie : {fmt(company['treasury'] - amount)}",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_renommer_entreprise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut renommer l'entreprise.")
        return
    if not context.args:
        await update.message.reply_text("Usage : /renommer_entreprise [nouveau nom]\nCoût : 20 000 coins")
        return
    new_name = " ".join(context.args)
    cost = 20000
    if company["treasury"] < cost:
        await update.message.reply_text(f"❌ Trésorerie insuffisante. Coût : {fmt(cost)}")
        return
    existing = await get_company_by_name(new_name)
    if existing and existing["company_id"] != company["company_id"]:
        await update.message.reply_text(f"❌ Une entreprise nommée '{escape_md(new_name)}' existe déjà.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE companies SET treasury = treasury - ?, name = ? WHERE company_id = ?",
                         (cost, new_name, company["company_id"]))
        await db.commit()
    await log_company_action(company["company_id"], "renommer", user.id, f"{company['name']} → {new_name}")
    await update.message.reply_text(f"✅ Entreprise renommée **{escape_md(new_name)}** (coût : {fmt(cost)}).")

@require_registered
@require_free
async def cmd_changer_secteur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut changer le secteur.")
        return
    if not context.args:
        await update.message.reply_text(f"Usage : /changer_secteur [secteur]\nSecteurs : {', '.join(SECTORS)}\nCoût : 50 000 coins")
        return
    new_sector = context.args[0].capitalize()
    if new_sector not in SECTORS:
        await update.message.reply_text(f"❌ Secteur invalide. Choix : {', '.join(SECTORS)}")
        return
    cost = 50000
    if company["treasury"] < cost:
        await update.message.reply_text(f"❌ Trésorerie insuffisante. Coût : {fmt(cost)}")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE companies SET treasury = treasury - ?, sector = ? WHERE company_id = ?",
                         (cost, new_sector, company["company_id"]))
        await db.commit()
    await log_company_action(company["company_id"], "changer_secteur", user.id, f"{company['sector']} → {new_sector}")
    await update.message.reply_text(f"✅ Secteur changé à **{new_sector}** (coût : {fmt(cost)}).")

@require_registered
@require_free
async def cmd_versersalaires(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Réservé au PDG.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT cm.user_id, cm.base_salary, cm.activity_score, u.full_name
            FROM company_members cm JOIN users u ON u.user_id=cm.user_id
            WHERE cm.company_id=? AND cm.user_id != ?
        """, (company["company_id"], user.id)) as cur:
            members = [dict(m) for m in await cur.fetchall()]
    if not members:
        await update.message.reply_text("👥 Aucun employé à payer.")
        return
    total = sum(int(m["base_salary"] * (1 + m["activity_score"] * 0.1)) for m in members)
    if company["treasury"] < total:
        await update.message.reply_text(
            f"❌ Trésorerie insuffisante !\n"
            f"💰 Salaires dus : {fmt(total)}\n"
            f"🏦 Trésorerie : {fmt(company['treasury'])}\n\n"
            f"👉 /depotboite montant pour alimenter"
        )
        return
    paid = []
    for m in members:
        amount = int(m["base_salary"] * (1 + m["activity_score"] * 0.1))
        await update_balance(m["user_id"], amount)
        paid.append(f"  • {escape_md(m['full_name'])}: {fmt(amount)}")
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute("UPDATE companies SET treasury=treasury-? WHERE company_id=?",
                         (total, company["company_id"]))
        await db.execute("UPDATE company_members SET activity_score=0 WHERE company_id=?",
                         (company["company_id"],))
        await db.commit()
    await log_company_action(company["company_id"], "salaires", user.id, fmt(total))
    await update.message.reply_text(
        f"💸 **Salaires versés !**\n\n"
        + "\n".join(paid) + "\n\n"
        f"💰 **Total : {fmt(total)}**\n"
        f"🏦 Trésorerie restante : {fmt(company['treasury'] - total)}",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_logsboite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company:
        await update.message.reply_text("❌ Tu n'as pas d'entreprise.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT cl.action, COALESCE(u.full_name, 'Système') as full_name, cl.details, cl.timestamp
            FROM company_logs cl LEFT JOIN users u ON u.user_id=cl.actor_id
            WHERE cl.company_id=? ORDER BY cl.timestamp DESC LIMIT 20
        """, (company["company_id"],)) as cur:
            logs = await cur.fetchall()
    if not logs:
        await update.message.reply_text("📋 Aucun log disponible.")
        return
    text = f"📋 **Logs — {escape_md(company['name'])}**\n\n"
    from datetime import datetime
    for l in logs:
        ts = datetime.fromtimestamp(l["timestamp"]).strftime("%d/%m %H:%M")
        text += f"[{ts}] **{escape_md(l['action'])}** par {escape_md(l['full_name'])}: {escape_md(l['details'])}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

@require_registered
@require_free
async def cmd_classement_boites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    companies = await get_all_companies()
    companies_sorted = sorted(companies, key=lambda c: c["treasury"], reverse=True)[:10]
    text = "🏆 **Classement des entreprises**\n\n"
    medals = ["🥇", "🥈", "🥉"] + ["🏢"] * 7
    for i, c in enumerate(companies_sorted):
        async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
            async with db.execute("SELECT COUNT(*) FROM company_members WHERE company_id=?", (c["company_id"],)) as cur:
                emp_count = (await cur.fetchone())[0]
        text += f"{medals[i]} **{escape_md(c['name'])}** ({c['sector']})\n  💰 {fmt(c['treasury'])} | 👥 {emp_count} employés\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

@require_registered
@require_free
async def cmd_parts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company:
        await update.message.reply_text("❌ Tu n'es pas dans une entreprise.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT u.full_name, cs.shares FROM company_shares cs JOIN users u ON u.user_id=cs.user_id WHERE cs.company_id=? ORDER BY cs.shares DESC",
            (company["company_id"],)
        ) as cur:
            shareholders = await cur.fetchall()
        async with db.execute(
            "SELECT COUNT(*) FROM company_members WHERE company_id=?", (company["company_id"],)
        ) as cur:
            emp_count = (await cur.fetchone())[0]
    total_shares = sum(s["shares"] for s in shareholders) or 1
    rd_level = company.get("rd_level", 0)
    share_value = max(10, (company["treasury"] // 1000) * (50 + company["reputation"]) // 50 + rd_level * 100)
    market_cap = total_shares * share_value
    text = f"📊 **Parts — {escape_md(company['name'])}**\n\n"
    text += f"💰 Trésorerie : {fmt(company['treasury'])}\n"
    text += f"👥 Employés : {emp_count}\n"
    text += f"⭐ Réputation : {company['reputation']}/100\n"
    text += f"🔬 R&D : Niveau {rd_level}\n\n"
    text += f"**Valorisation estimée**\n"
    text += f"  • Valeur par part : {fmt(share_value)}\n"
    text += f"  • Capitalisation : {fmt(market_cap)}\n\n"
    text += f"**Actionnaires**\n"
    for s in shareholders[:10]:
        pct = s["shares"] / total_shares * 100
        text += f"  • {escape_md(s['full_name'])} : {s['shares']} parts ({pct:.1f}%)\n"
    if len(shareholders) > 10:
        text += f"  _... et {len(shareholders)-10} autres_"
    await update.message.reply_text(text, parse_mode="Markdown")

# ─────────────────────────────────────────────────────────────────────────────
# PRODUITS AVEC EFFETS (nouvelle version corrigée)
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
@require_free
async def cmd_produits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le catalogue entreprise et ses produits."""
    user = update.effective_user
    company = await get_user_company(user.id)

    if not company:
        await update.message.reply_text(
            "❌ Tu n'es dans aucune entreprise.\n"
            "Crée ou rejoins une entreprise pour accéder à ses produits."
        )
        return

    # Vérification directe dans la table companies pour obtenir le owner_id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT owner_id FROM companies WHERE company_id = ?", (company["company_id"],)) as cur:
            row = await cur.fetchone()
            owner_id = row["owner_id"] if row else None

    # Message de diagnostic si l'utilisateur n'est pas propriétaire
    if owner_id != user.id:
        owner = await get_user(owner_id) if owner_id else None
        owner_name = escape_html(owner["full_name"]) if owner else "Inconnu"
        await update.message.reply_text(
            f"❌ Seul le PDG peut voir les produits de l'entreprise.\n"
            f"PDG actuel : {owner_name} (ID: {owner_id})\n"
            f"Ton ID : {user.id}\n\n"
            "⚠️ Si tu penses être le PDG, utilise `/transfert_entreprise` pour récupérer la propriété "
            "ou contacte un administrateur.\n"
            f"Vérifie aussi avec `/monentreprise` pour voir le propriétaire affiché."
        )
        return

    # ─── Affichage du catalogue (requête corrigée) ───
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # CORRECTION : remplacer SUM(i.quantity) par SUM(i2.quantity)
        async with db.execute("""
            SELECT cp.name, cp.price, cp.sales, cp.revenue,
                   COALESCE(SUM(i2.quantity), 0) AS stock,
                   i.effect_type, i.effect_value, i.emoji, i.description
            FROM company_products cp
            LEFT JOIN items i ON i.name = cp.name
            LEFT JOIN inventory i2 ON i2.item_id = i.item_id AND i2.user_id = ?
            WHERE cp.company_id=?
            GROUP BY cp.id, cp.name, cp.price, cp.sales, cp.revenue, i.effect_type, i.effect_value, i.emoji, i.description
            ORDER BY cp.revenue DESC, cp.sales DESC, cp.name ASC
        """, (user.id, company["company_id"])) as cur:
            products = [dict(r) for r in await cur.fetchall()]

    if not products:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM company_products WHERE company_id=?", (company["company_id"],)) as cur2:
                count = (await cur2.fetchone())[0]
        if count == 0:
            await update.message.reply_text(
                card(
                    "📦 Catalogue vide",
                    [
                        "Aucun produit actif.",
                        "",
                        "<code>/creer_produit nom effet valeur quantité</code> pour lancer une gamme",
                        "Effets : heal, energy, xp, money, buff",
                    ],
                    icon="📦", style="round"
                ),
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                card(
                    "📦 Catalogue vide (stock épuisé)",
                    [
                        "Tous les produits sont en rupture de stock.",
                        "Utilise /creer_produit pour en produire de nouveaux.",
                    ],
                    icon="📦", style="round"
                ),
                parse_mode="HTML"
            )
        return

    body = []
    for product in products[:12]:
        effect_info = f" ({product.get('effect_type', 'aucun')} {product.get('effect_value', '')})" if product.get('effect_type') else ""
        emoji = product.get('emoji', '📦')
        desc = product.get('description', '')
        if desc:
            desc = f" — {desc[:30]}{'...' if len(desc) > 30 else ''}"
        body.append(
            f"{emoji} <b>{escape_html(product['name'])}</b> · stock <b>{product['stock']}</b> · "
            f"prix {fmt(product['price'])} · ventes {product['sales']} · CA {fmt(product['revenue'])}{effect_info}{desc}"
        )
    body += [
        "",
        "Commandes utiles :",
        "<code>/creer_produit [nom] [effet] [valeur] [quantité]</code>",
        "<code>/setprix [nom] [prix]</code>",
        "<code>/sellitem [item_id] [prix] [quantité]</code>",
    ]
    await update.message.reply_text(
        card("📦 Produits de l'entreprise", body, icon="📦", style="thick"),
        parse_mode="HTML"
    )
    
@require_registered
@require_free
async def cmd_creer_produit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Crée un produit avec effet, coût variable selon l'effet."""
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut créer un produit.")
        return
    if len(context.args) < 4:
        await update.message.reply_text(
            "Usage : /creer_produit [nom] [effet] [valeur] [quantité]\n"
            "Effets : heal, energy, xp, money, buff\n"
            "Ex: /creer_produit \"Potion de soin\" heal 30 10"
        )
        return
    name = " ".join(context.args[:-3])
    effect = context.args[-3].lower()
    try:
        effect_value = int(context.args[-2])
        qty = int(context.args[-1])
        if qty <= 0 or qty > 1000 or effect_value <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Valeur ou quantité invalide (quantité max 1000, effet >0).")
        return
    if effect not in PRODUCT_EFFECTS:
        await update.message.reply_text(f"❌ Effet inconnu. Choisis parmi : {', '.join(PRODUCT_EFFECTS.keys())}")
        return

    # Calcul du coût unitaire
    base_cost = PRODUCT_EFFECTS[effect]["base_cost"]
    unit_cost = int(base_cost * (effect_value / 10) * (1 + company["level"] * 0.05) * (1 + company.get("rd_level", 0) * 0.03))
    if unit_cost < 100:
        unit_cost = 100
    total_cost = unit_cost * qty

    if company["treasury"] < total_cost:
        await update.message.reply_text(f"❌ Trésorerie insuffisante. Coût : {fmt(total_cost)}")
        return

    # Définir un emoji par défaut selon l'effet
    emoji_map = {"heal": "💊", "energy": "🔋", "xp": "📜", "money": "💰", "buff": "✨"}
    emoji = emoji_map.get(effect, "📦")
    description = PRODUCT_EFFECTS[effect]["desc"] + f" (valeur {effect_value})"

    async with aiosqlite.connect(DB_PATH) as db:
        # Vérifier si un item avec ce nom existe déjà
        async with db.execute("SELECT item_id FROM items WHERE name = ?", (name,)) as cur:
            row = await cur.fetchone()
        if row:
            await update.message.reply_text(f"❌ Un item nommé '{name}' existe déjà.")
            return
        # Insérer dans items
        await db.execute(
            "INSERT INTO items (name, type, rarity, value, effect_type, effect_value, emoji, description) VALUES (?, 'consumable', 'common', ?, ?, ?, ?, ?)",
            (name, unit_cost, effect, effect_value, emoji, description)
        )
        async with db.execute("SELECT last_insert_rowid()") as cur2:
            item_id = (await cur2.fetchone())[0]
        # Ajouter au stock du PDG
        await _add_item_to_inventory(user.id, item_id, qty, "product", db)
        # Enregistrer dans company_products (pour suivi)
        await db.execute(
            "INSERT INTO company_products (company_id, name, price, sales, revenue) VALUES (?,?,?,?,?)",
            (company["company_id"], name, unit_cost, 0, 0)
        )
        # Débiter la trésorerie
        await db.execute("UPDATE companies SET treasury = treasury - ? WHERE company_id = ?", (total_cost, company["company_id"]))
        await db.commit()
    await log_company_action(company["company_id"], "creation_produit", user.id, f"{name} x{qty} (effet {effect}, {effect_value})")
    await update.message.reply_text(
        f"✅ **Produit créé**\n\n"
        f"📦 Nom : {name}\n"
        f"✨ Effet : {effect} ({PRODUCT_EFFECTS[effect]['desc']}) - valeur {effect_value}\n"
        f"📦 Quantité : {qty}\n"
        f"💰 Coût unitaire : {fmt(unit_cost)}\n"
        f"💰 Coût total : {fmt(total_cost)}\n\n"
        f"Tu peux maintenant vendre ces produits sur le marché avec `/sellitem {item_id} [prix] [quantité]`\n"
        f"Ou les utiliser toi-même avec `/useitem {item_id}` (après achat)."
    )

@require_registered
@require_free
async def cmd_supprimer_produit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Supprime définitivement un produit du catalogue (même si stock épuisé)."""
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut supprimer un produit.")
        return
    if not context.args:
        await update.message.reply_text("Usage : /supprimer_produit [nom du produit]")
        return
    name = " ".join(context.args)
    async with aiosqlite.connect(DB_PATH) as db:
        # Vérifier si le produit existe dans company_products
        async with db.execute(
            "SELECT id FROM company_products WHERE company_id = ? AND name = ?",
            (company["company_id"], name)
        ) as cur:
            prod = await cur.fetchone()
        if not prod:
            await update.message.reply_text(f"❌ Produit '{name}' introuvable dans le catalogue.")
            return
        # Supprimer de company_products
        await db.execute(
            "DELETE FROM company_products WHERE company_id = ? AND name = ?",
            (company["company_id"], name)
        )
        # Supprimer de items (s'il existe)
        await db.execute("DELETE FROM items WHERE name = ?", (name,))
        # Supprimer également de l'inventaire du PDG (s'il en reste)
        await db.execute(
            "DELETE FROM inventory WHERE user_id = ? AND item_name = ? AND item_type = 'product'",
            (user.id, name)
        )
        await db.commit()
    await log_company_action(company["company_id"], "supprimer_produit", user.id, name)
    await update.message.reply_text(f"🗑️ Produit **{name}** supprimé définitivement du catalogue.")

@require_registered
@require_free
async def cmd_retirer_produit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retire une certaine quantité d'un produit de l'inventaire du PDG (perte)."""
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut retirer des produits.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /retirer_produit [item_id] [quantité]")
        return
    try:
        item_id = int(context.args[0])
        qty = int(context.args[1])
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT item_name, quantity FROM inventory WHERE user_id = ? AND item_id = ?", (user.id, item_id)) as cur:
            row = await cur.fetchone()
        if not row or row[1] < qty:
            await update.message.reply_text(f"❌ Quantité insuffisante. Tu en possèdes {row[1] if row else 0}.")
            return
        name = row[0]
        # Utiliser la même connexion pour la suppression
        if await _remove_item_from_inventory(user.id, item_id, qty, db):
            await db.commit()
        else:
            await update.message.reply_text("❌ Erreur lors du retrait.")
            return
    await log_company_action(company["company_id"], "retirer_produit", user.id, f"{name} x{qty}")
    await update.message.reply_text(f"🗑️ Retiré {qty} exemplaire(s) de **{name}** de ton inventaire (perte).") 

@require_registered
@require_free
async def cmd_setprix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Modifie le prix de vente conseillé d'un produit (affiché dans /produits)."""
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut modifier le prix d'un produit.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /setprix [nom produit] [prix]")
        return
    name = " ".join(context.args[:-1])
    try:
        new_price = int(context.args[-1])
        if new_price < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Prix invalide.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT item_id FROM items WHERE name = ?", (name,)) as cur:
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text(f"❌ Produit '{name}' introuvable.")
            return
        await db.execute("UPDATE items SET value = ? WHERE item_id = ?", (new_price, row[0]))
        await db.execute(
            "UPDATE company_products SET price = ? WHERE company_id = ? AND name = ?",
            (new_price, company["company_id"], name)
        )
        await db.commit()
    await update.message.reply_text(f"💰 Prix conseillé du produit **{name}** mis à jour : {fmt(new_price)}.")

# ─────────────────────────────────────────────────────────────────────────────
# COMMANDES POUR LES PRODUITS (donner, emoji, renommer, desc) – CORRIGÉES
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
@require_free
async def cmd_donner_produit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Donne un produit (item) à un autre joueur depuis l'inventaire."""
    if not update.message.reply_to_message:
        await update.message.reply_text("Répondez au message du joueur à qui vous voulez donner un produit.")
        return
    target = update.message.reply_to_message.from_user
    if target.id == update.effective_user.id:
        await update.message.reply_text("Vous ne pouvez pas vous donner un produit à vous-même.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /donner_produit [item_id] [quantité] (en répondant au message du destinataire)")
        return
    try:
        item_id = int(context.args[0])
        quantity = int(context.args[1])
        if quantity <= 0:
            raise ValueError
    except:
        await update.message.reply_text("Arguments invalides.")
        return
    from database import get_inventory
    inv = await get_inventory(update.effective_user.id)
    item_quantity = next((i["quantity"] for i in inv if i["item_id"] == item_id), 0)
    if item_quantity < quantity:
        await update.message.reply_text(f"Vous n'avez que {item_quantity} exemplaire(s) de cet item.")
        return
    from handlers.market import remove_item, add_item
    await remove_item(update.effective_user.id, item_id, quantity)
    await add_item(target.id, item_id, quantity, "don")
    await update.message.reply_text(f"✅ {quantity}x item #{item_id} donné à {target.full_name}.")

@require_registered
@require_free
async def cmd_emoji_produit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change l'emoji d'un produit existant."""
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut modifier l'emoji d'un produit.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /emoji_produit [nom_produit] [emoji]\nExemple : /emoji_produit \"Potion magique\" ✨")
        return

    # On prend tous les arguments sauf le dernier comme nom, le dernier comme emoji
    product_name = " ".join(context.args[:-1])
    new_emoji = context.args[-1]

    # Enlever les guillemets du nom
    product_name = strip_quotes(product_name)

    if not new_emoji or len(new_emoji) > 2:
        await update.message.reply_text("❌ Emoji invalide. Utilise un seul emoji ou deux (ex: 🍕, 🎁).")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT item_id FROM items WHERE name = ?", (product_name,)) as cur:
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text(f"❌ Produit '{product_name}' introuvable.")
            return
        item_id = row[0]

        # Vérifier que le PDG possède au moins un exemplaire (preuve de propriété)
        async with db.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND item_id = ? AND item_type = 'product'",
            (user.id, item_id)
        ) as cur2:
            if not await cur2.fetchone():
                await update.message.reply_text("❌ Ce produit ne fait pas partie de ton inventaire. Tu ne peux pas en changer l'emoji.")
                return

        await db.execute("UPDATE items SET emoji = ? WHERE item_id = ?", (new_emoji, item_id))
        await db.commit()

    await update.message.reply_text(f"✅ Emoji du produit **{product_name}** modifié pour {new_emoji}.")

@require_registered
@require_free
async def cmd_renommer_produit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Renomme un produit existant."""
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut renommer un produit.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage : /renommer_produit [ancien_nom] [nouveau_nom]\nExemple : /renommer_produit \"Potion basique\" \"Élixir soignant\"")
        return

    full_args = " ".join(context.args)
    # Gérer les guillemets simples ou doubles pour les deux noms
    match = re.match(r'^["\'](.+?)["\']\s+["\'](.+?)["\']$', full_args)
    if not match:
        # Fallback : on prend le premier mot comme ancien nom, le reste comme nouveau nom
        parts = full_args.split()
        if len(parts) < 2:
            await update.message.reply_text("❌ Format invalide. Utilisez des guillemets pour les noms.")
            return
        old_name = parts[0]
        new_name = " ".join(parts[1:])
    else:
        old_name = match.group(1)
        new_name = match.group(2)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT item_id FROM items WHERE name = ?", (old_name,)) as cur:
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text(f"❌ Produit '{old_name}' introuvable.")
            return
        item_id = row[0]

        async with db.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND item_id = ? AND item_type = 'product'",
            (user.id, item_id)
        ) as cur2:
            if not await cur2.fetchone():
                await update.message.reply_text("❌ Ce produit ne fait pas partie de ton inventaire. Tu ne peux pas le renommer.")
                return

        async with db.execute("SELECT 1 FROM items WHERE name = ?", (new_name,)) as cur3:
            if await cur3.fetchone():
                await update.message.reply_text(f"❌ Un produit nommé '{new_name}' existe déjà.")
                return

        await db.execute("UPDATE items SET name = ? WHERE item_id = ?", (new_name, item_id))
        await db.execute("UPDATE inventory SET item_name = ? WHERE item_id = ? AND user_id = ?", (new_name, item_id, user.id))
        await db.commit()

    await update.message.reply_text(f"✅ Produit **{old_name}** renommé en **{new_name}**.")

@require_registered
@require_free
async def cmd_desc_produit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Modifie la description d'un produit."""
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut modifier la description d'un produit.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage : /desc_produit \"nom du produit\" description\nMettez le nom entre guillemets.")
        return

    full_args = " ".join(context.args)
    # Gérer les guillemets simples ou doubles
    match = re.match(r'^["\'](.+?)["\']\s+(.*)$', full_args)
    if not match:
        await update.message.reply_text("Usage : /desc_produit \"nom du produit\" description\nMettez le nom entre guillemets.")
        return

    product_name = match.group(1)
    new_desc = match.group(2)

    if not new_desc:
        await update.message.reply_text("❌ La description ne peut pas être vide.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT item_id FROM items WHERE name = ?", (product_name,)) as cur:
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text(f"❌ Produit '{product_name}' introuvable.")
            return
        item_id = row[0]

        # Vérifier que le PDG possède ce produit
        async with db.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND item_id = ? AND item_type = 'product'",
            (user.id, item_id)
        ) as cur2:
            if not await cur2.fetchone():
                await update.message.reply_text("❌ Ce produit ne fait pas partie de ton inventaire. Tu ne peux pas modifier sa description.")
                return

        await db.execute("UPDATE items SET description = ? WHERE item_id = ?", (new_desc, item_id))
        await db.commit()

    await update.message.reply_text(f"✅ Description du produit **{product_name}** mise à jour.")

# ─────────────────────────────────────────────────────────────────────────────
# FUSIONS (avec consentement)
# ─────────────────────────────────────────────────────────────────────────────

@require_registered
@require_free
async def cmd_proposer_fusion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut proposer une fusion.")
        return
    if not context.args:
        await update.message.reply_text("Usage : /proposer_fusion [entreprise cible]")
        return
    target_name = " ".join(context.args)
    target = await get_company_by_name(target_name)
    if not target or target["company_id"] == company["company_id"]:
        await update.message.reply_text("❌ Entreprise cible introuvable.")
        return
    if target["dissolved"]:
        await update.message.reply_text("❌ Cette entreprise est dissoute.")
        return

    # Vérifier si une demande est déjà en cours
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT request_id FROM fusion_requests WHERE (from_company=? AND to_company=? OR from_company=? AND to_company=?) AND status='pending'",
            (company["company_id"], target["company_id"], target["company_id"], company["company_id"])
        ) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Une demande de fusion est déjà en attente entre ces deux entreprises.")
                return
        await db.execute(
            "INSERT INTO fusion_requests (from_company, to_company, amount, status, created_at) VALUES (?,?,?,?,?)",
            (company["company_id"], target["company_id"], company["treasury"], "pending", now())
        )
        await db.commit()
    target_owner = await get_user(target["owner_id"])
    await add_notification(
        target_owner["user_id"],
        f"🏢 **{company['name']}** souhaite fusionner avec votre entreprise. "
        f"Répondez avec `/repondre_fusion {target['company_id']} accepter` ou `/repondre_fusion {target['company_id']} refuser`."
    )
    await update.message.reply_text(f"📨 Demande de fusion envoyée à **{target['name']}**.")

@require_registered
@require_free
async def cmd_repondre_fusion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if len(context.args) != 2:
        await update.message.reply_text("Usage : /repondre_fusion [id_entreprise] [accepter/refuser]")
        return
    try:
        target_id = int(context.args[0])
        decision = context.args[1].lower()
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return
    # Vérifier que l'utilisateur est PDG de cette entreprise
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id or company["company_id"] != target_id:
        await update.message.reply_text("❌ Vous n'êtes pas le PDG de cette entreprise.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM fusion_requests WHERE to_company=? AND status='pending'",
            (target_id,)
        ) as cur:
            req = await cur.fetchone()
        if not req:
            await update.message.reply_text("❌ Aucune demande de fusion en attente pour cette entreprise.")
            return
        if decision == "accepter":
            from_comp = await get_company_by_id(req["from_company"])
            to_comp = await get_company_by_id(req["to_company"])
            if not from_comp or from_comp["dissolved"]:
                await update.message.reply_text("❌ L'entreprise émettrice a été dissoute entre-temps.")
                return
            # Valorisation simplifiée
            valuation = to_comp["treasury"] + (to_comp["treasury"] // 10) * to_comp["reputation"] // 100
            if from_comp["treasury"] < valuation:
                await update.message.reply_text("❌ L'entreprise émettrice n'a pas assez de trésorerie pour absorber votre entreprise.")
                await db.execute("UPDATE fusion_requests SET status='refused' WHERE request_id=?", (req["request_id"],))
                await db.commit()
                return
            # Fusion : from_comp absorbe to_comp
            await db.execute("UPDATE companies SET treasury=treasury-? WHERE company_id=?", (valuation, from_comp["company_id"]))
            await db.execute("UPDATE companies SET treasury=treasury+? WHERE company_id=?", (valuation, to_comp["company_id"]))
            # Transfert des employés et parts
            await db.execute("UPDATE company_members SET company_id=? WHERE company_id=?", (from_comp["company_id"], to_comp["company_id"]))
            await db.execute("UPDATE company_shares SET company_id=? WHERE company_id=?", (from_comp["company_id"], to_comp["company_id"]))
            await db.execute("UPDATE companies SET dissolved=1 WHERE company_id=?", (to_comp["company_id"],))
            await db.execute("UPDATE fusion_requests SET status='accepted', responded_at=? WHERE request_id=?", (now(), req["request_id"]))
            await db.commit()
            await log_company_action(from_comp["company_id"], "fusion_accept", user.id, f"avec {to_comp['name']}")
            await add_notification(from_comp["owner_id"], f"✅ La fusion avec {to_comp['name']} a été acceptée.")
            await update.message.reply_text(f"🤝 **Fusion réussie !** {from_comp['name']} a absorbé {to_comp['name']}.")
        else:
            await db.execute("UPDATE fusion_requests SET status='refused', responded_at=? WHERE request_id=?", (now(), req["request_id"]))
            await db.commit()
            await add_notification(req["from_company"], f"❌ {company['name']} a refusé votre demande de fusion.")
            await update.message.reply_text("❌ Fusion refusée.")

# ─────────────────────────────────────────────────────────────────────────────
# CONTRATS B2B enrichis (avec type, durée, bénéfices)
# ─────────────────────────────────────────────────────────────────────────────

CONTRACT_TYPES = ["service", "fourniture", "partenariat", "licence"]

@require_registered
@require_free
async def cmd_proposer_contrat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut proposer un contrat.")
        return
    if len(context.args) < 4:
        await update.message.reply_text(
            "Usage : /proposer_contrat [entreprise cible] [montant total] [durée (jours)] [type]\n"
            f"Types : {', '.join(CONTRACT_TYPES)}\n"
            "Ex: /proposer_contrat \"TechCorp\" 100000 30 service"
        )
        return
    # Extraction : le dernier argument est le type, avant la durée, avant le montant, le reste est le nom
    # On va récupérer les arguments en partant de la fin
    try:
        contract_type = context.args[-1].lower()
        duration = int(context.args[-2])
        amount = int(context.args[-3])
        target_name = " ".join(context.args[:-3])
        if duration <= 0 or amount <= 0 or contract_type not in CONTRACT_TYPES:
            raise ValueError
    except ValueError:
        await update.message.reply_text(f"❌ Arguments invalides. Types : {', '.join(CONTRACT_TYPES)}")
        return

    target = await get_company_by_name(target_name)
    if not target or target["company_id"] == company["company_id"]:
        await update.message.reply_text("❌ Entreprise cible introuvable.")
        return
    if target["dissolved"]:
        await update.message.reply_text("❌ Cette entreprise est dissoute.")
        return

    # Vérifier si un contrat en attente existe déjà
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT contract_id FROM contracts WHERE (from_company=? AND to_company=? OR from_company=? AND to_company=?) AND status='pending'",
            (company["company_id"], target["company_id"], target["company_id"], company["company_id"])
        ) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Un contrat est déjà en attente entre ces deux entreprises.")
                return
        # Insérer le contrat avec statut 'pending'
        await db.execute("""
            INSERT INTO contracts (from_company, to_company, amount, duration, contract_type, status, proposed_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """, (company["company_id"], target["company_id"], amount, duration, contract_type, now()))
        async with db.execute("SELECT last_insert_rowid()") as cur:
            contract_id = (await cur.fetchone())[0]
        await db.commit()

    target_owner = await get_user(target["owner_id"])
    await add_notification(
        target_owner["user_id"],
        f"📄 Nouveau contrat proposé par **{company['name']}** : {fmt(amount)} pour {duration} jours, type {contract_type}. "
        f"Répondez avec `/repondre_contrat {contract_id} accepter` ou `/repondre_contrat {contract_id} refuser`."
    )
    await update.message.reply_text(
        f"📄 Contrat proposé à **{target['name']}**\n"
        f"💰 Montant : {fmt(amount)}\n"
        f"⏳ Durée : {duration} jours\n"
        f"📌 Type : {contract_type}\n"
        f"_En attente de réponse._"
    )

@require_registered
@require_free
async def cmd_repondre_contrat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if len(context.args) != 2:
        await update.message.reply_text("Usage : /repondre_contrat [id_contrat] [accepter/refuser]")
        return
    try:
        contract_id = int(context.args[0])
        decision = context.args[1].lower()
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return

    # Récupérer le contrat
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM contracts WHERE contract_id=?", (contract_id,)) as cur:
            contract = await cur.fetchone()
        if not contract or contract["status"] != "pending":
            await update.message.reply_text("❌ Contrat introuvable ou déjà traité.")
            return
        # Vérifier que l'utilisateur est PDG de l'entreprise cible
        target_company = await get_company_by_id(contract["to_company"])
        if not target_company or target_company["owner_id"] != user.id:
            await update.message.reply_text("❌ Vous n'êtes pas le PDG de l'entreprise cible.")
            return
        if decision == "accepter":
            # Appliquer le contrat
            from_comp = await get_company_by_id(contract["from_company"])
            if not from_comp or from_comp["dissolved"]:
                await update.message.reply_text("❌ L'entreprise émettrice a été dissoute.")
                return
            # Vérifier si from_comp a assez de trésorerie
            if from_comp["treasury"] < contract["amount"]:
                await update.message.reply_text("❌ L'entreprise émettrice n'a pas assez de trésorerie.")
                await db.execute("UPDATE contracts SET status='cancelled' WHERE contract_id=?", (contract_id,))
                await db.commit()
                return
            # Transfert d'argent
            await db.execute("UPDATE companies SET treasury=treasury-? WHERE company_id=?", (contract["amount"], from_comp["company_id"]))
            await db.execute("UPDATE companies SET treasury=treasury+? WHERE company_id=?", (contract["amount"], target_company["company_id"]))
            # Bénéfices additionnels selon le type
            bonus_reputation = 0
            if contract["contract_type"] == "service":
                bonus_reputation = 3
            elif contract["contract_type"] == "fourniture":
                bonus_reputation = 2
            elif contract["contract_type"] == "partenariat":
                bonus_reputation = 5
            elif contract["contract_type"] == "licence":
                bonus_reputation = 4
            if bonus_reputation > 0:
                await db.execute("UPDATE companies SET reputation = MIN(100, reputation + ?) WHERE company_id=?", (bonus_reputation, from_comp["company_id"]))
                await db.execute("UPDATE companies SET reputation = MIN(100, reputation + ?) WHERE company_id=?", (bonus_reputation, target_company["company_id"]))
            # Mettre à jour le contrat avec date de fin
            end_date = now() + contract["duration"] * 86400
            await db.execute(
                "UPDATE contracts SET status='accepted', accepted_at=?, end_date=? WHERE contract_id=?",
                (now(), end_date, contract_id)
            )
            await db.commit()
            await log_company_action(from_comp["company_id"], "contrat_accepte", user.id, f"avec {target_company['name']} ({fmt(contract['amount'])})")
            await log_company_action(target_company["company_id"], "contrat_accepte", user.id, f"avec {from_comp['name']} ({fmt(contract['amount'])})")
            await add_notification(from_comp["owner_id"], f"✅ Le contrat avec {target_company['name']} a été accepté.")
            await update.message.reply_text(
                f"✅ Contrat accepté !\n\n"
                f"💰 {fmt(contract['amount'])} transféré de {from_comp['name']} vers {target_company['name']}.\n"
                f"⏳ Durée : {contract['duration']} jours.\n"
                f"📌 Type : {contract['contract_type']}\n"
                f"⭐ Réputation gagnée : +{bonus_reputation} chacune."
            )
        else:
            await db.execute("UPDATE contracts SET status='refused' WHERE contract_id=?", (contract_id,))
            await db.commit()
            await add_notification(contract["from_company"], f"❌ {target_company['name']} a refusé votre contrat.")
            await update.message.reply_text("❌ Contrat refusé.")

@require_registered
@require_free
async def cmd_negocier_contrat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permet de négocier un contrat en attente (changer montant et/ou durée)."""
    user = update.effective_user
    if len(context.args) < 3:
        await update.message.reply_text("Usage : /negocier_contrat [id_contrat] [nouveau_montant] [nouvelle_durée]")
        return
    try:
        contract_id = int(context.args[0])
        new_amount = int(context.args[1])
        new_duration = int(context.args[2])
        if new_amount <= 0 or new_duration <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM contracts WHERE contract_id=?", (contract_id,)) as cur:
            contract = await cur.fetchone()
        if not contract or contract["status"] != "pending":
            await update.message.reply_text("❌ Contrat introuvable ou déjà traité.")
            return
        # Vérifier que l'utilisateur est PDG de l'entreprise cible (peut négocier)
        target_company = await get_company_by_id(contract["to_company"])
        if not target_company or target_company["owner_id"] != user.id:
            await update.message.reply_text("❌ Vous n'êtes pas le PDG de l'entreprise cible.")
            return
        # Mettre à jour le contrat en négociation
        await db.execute(
            "UPDATE contracts SET amount=?, duration=?, status='negotiating', proposed_at=? WHERE contract_id=?",
            (new_amount, new_duration, now(), contract_id)
        )
        await db.commit()
    await add_notification(contract["from_company"], f"📝 Négociation pour le contrat #{contract_id} : {target_company['name']} propose {fmt(new_amount)} pour {new_duration} jours. Utilisez `/repondre_contrat {contract_id} accepter` ou `/repondre_contrat {contract_id} refuser`.")
    await update.message.reply_text(f"📝 Contre-offre envoyée. En attente de réponse.")

# ─────────────────────────────────────────────────────────────────────────────
# MAINTENANCE (sans ventes automatiques)
# ─────────────────────────────────────────────────────────────────────────────
async def process_company_maintenance():
    """Maintenance quotidienne : frais généraux, réputation, nettoyage, pas de ventes auto."""
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM companies WHERE dissolved=0") as cur:
            companies = [dict(row) for row in await cur.fetchall()]
        for c in companies:
            # Compter les employés
            async with db.execute("SELECT COUNT(*) FROM company_members WHERE company_id=?", (c["company_id"],)) as cur2:
                emp_count = (await cur2.fetchone())[0]
            overhead = c.get("overhead", 1000)
            maintenance_cost = emp_count * overhead
            new_treasury = c["treasury"] - maintenance_cost if c["treasury"] >= maintenance_cost else 0
            new_reputation = c["reputation"]
            if c["treasury"] < maintenance_cost:
                new_reputation = max(0, c["reputation"] - 10)
                if new_treasury == 0:
                    await db.execute(
                        "INSERT INTO company_logs (company_id, action, actor_id, details, timestamp) VALUES (?,?,?,?,?)",
                        (c["company_id"], "maintenance_neg", c["owner_id"], f"Trésorerie insuffisante, perte de réputation", now())
                    )
            # Pas de ventes automatiques
            # Mise à jour
            await db.execute(
                "UPDATE companies SET treasury=?, reputation=? WHERE company_id=?",
                (new_treasury, new_reputation, c["company_id"])
            )
            # Nettoyer les contrats expirés
            await db.execute(
                "UPDATE contracts SET status='expired' WHERE status='accepted' AND end_date < ?",
                (now(),)
            )
        # Nettoyer les annonces périmées (7 jours)
        await db.execute("DELETE FROM company_ads WHERE created_at < ?", (now() - 7*86400,))
        # Nettoyer les candidatures en négociation trop vieilles (48h)
        expire_time = now() - 48 * 3600
        await db.execute(
            "UPDATE company_applications SET status='refused' WHERE status='negotiating' AND applied_at < ?",
            (expire_time,)
        )
        await db.commit()

# ─────────────────────────────────────────────────────────────────────────────
# CALLBACK pour la pagination des pages entreprise
# ─────────────────────────────────────────────────────────────────────────────
async def company_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    if len(parts) < 4:
        return
    view = parts[2]
    try:
        company_id = int(parts[3])
    except ValueError:
        return
    company = await get_company_by_id(company_id)
    if not company:
        await query.edit_message_text("❌ Entreprise introuvable.")
        return
    viewer_company = await get_user_company(query.from_user.id)
    text, reply_markup = await _render_company_view(company, viewer_company, view)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)

# ─────────────────────────────────────────────────────────────────────────────
# TRANSFERT DE PROPRIÉTÉ
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_transfert_entreprise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut transférer l'entreprise.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Réponds au message du joueur à qui tu veux transférer l'entreprise.")
        return
    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te transférer l'entreprise à toi-même.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        async with db.execute(
            "SELECT 1 FROM company_members WHERE company_id = ? AND user_id = ?",
            (company["company_id"], target.id)
        ) as cur:
            if not await cur.fetchone():
                await update.message.reply_text(f"❌ {target.full_name} n'est pas employé dans cette entreprise.")
                return
        await db.execute(
            "UPDATE companies SET owner_id = ? WHERE company_id = ?",
            (target.id, company["company_id"])
        )
        await db.execute(
            "UPDATE company_members SET poste = 'Ex-PDG' WHERE user_id = ? AND company_id = ?",
            (user.id, company["company_id"])
        )
        await db.execute(
            "UPDATE company_members SET poste = 'PDG' WHERE user_id = ? AND company_id = ?",
            (target.id, company["company_id"])
        )
        await db.commit()
    await update.message.reply_text(
        f"👑 **Transfert d'entreprise réussi !**\n\n"
        f"**{company['name']}** est désormais dirigée par **{target.full_name}**.\n"
        f"Tu es devenu 'Ex-PDG'."
    )

# ─────────────────────────────────────────────────────────────────────────────
# COMMANDES FINANCIÈRES (parts)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_acheterparts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    if len(context.args) < 1:
        await update.message.reply_text(
            "📈 **Achat de parts**\n\n"
            "Usage : `/acheterparts [nom entreprise] [nombre]`\n"
            "Ou si tu es déjà dans une entreprise, `/acheterparts [nombre]` achète des parts de ton entreprise.\n\n"
            "Le prix par part dépend de la trésorerie, réputation et R&D."
        )
        return
    if len(context.args) == 1:
        try:
            shares = int(context.args[0])
            company = await get_user_company(user.id)
            if not company:
                await update.message.reply_text("❌ Tu n'es dans aucune entreprise. Précise le nom de l'entreprise cible.")
                return
        except ValueError:
            await update.message.reply_text("❌ Le nombre doit être un entier positif.")
            return
    else:
        try:
            shares = int(context.args[-1])
            company_name = " ".join(context.args[:-1])
            company = await get_company_by_name(company_name)
            if not company:
                await update.message.reply_text(f"❌ Entreprise '{company_name}' introuvable.")
                return
        except ValueError:
            await update.message.reply_text("❌ Le nombre doit être un entier positif.")
            return
    if shares <= 0:
        await update.message.reply_text("❌ Nombre invalide.")
        return
    rd_level = company.get("rd_level", 0)
    price_per_share = max(10, (company["treasury"] // 1000) * (50 + company["reputation"]) // 50 + rd_level * 100)
    MAX_PRICE = 10_000_000_000
    if price_per_share > MAX_PRICE:
        price_per_share = MAX_PRICE
        await update.message.reply_text("⚠️ Le prix par part a été plafonné à 10 milliards pour éviter un dépassement.")
    total_cost = shares * price_per_share
    MAX_TOTAL = 9_000_000_000_000_000
    if total_cost > MAX_TOTAL:
        await update.message.reply_text("❌ Le coût total est trop élevé. Contactez un administrateur.")
        return
    if total_cost > u["balance"]:
        await update.message.reply_text(f"❌ Fonds insuffisants. Coût : {fmt(total_cost)}", parse_mode="Markdown")
        return
    await update_balance(user.id, -total_cost)
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute(
            "INSERT INTO company_shares (user_id, company_id, shares) VALUES (?,?,?) "
            "ON CONFLICT(user_id, company_id) DO UPDATE SET shares = shares + ?",
            (user.id, company["company_id"], shares, shares)
        )
        await db.execute("UPDATE companies SET treasury = treasury + ? WHERE company_id = ?", (total_cost, company["company_id"]))
        await db.commit()
    await log_company_action(company["company_id"], "achat_parts", user.id, f"{shares} parts ({fmt(total_cost)})")
    await update.message.reply_text(
        f"📈 **Achat de parts réussi !**\n\n"
        f"🏢 Entreprise : **{escape_md(company['name'])}**\n"
        f"📊 {shares} parts à {fmt(price_per_share)} chacune.\n"
        f"💰 Total : {fmt(total_cost)}\n"
        f"💎 Tu détiens maintenant {await get_user_shares(user.id, company['company_id'])} parts de cette entreprise.\n\n"
        f"_L'entreprise reçoit cet argent pour se développer._",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_vendreparts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    if len(context.args) < 1:
        await update.message.reply_text(
            "📉 **Vente de parts**\n\n"
            "Usage : `/vendreparts [nom entreprise] [nombre]`\n"
            "Ou si tu es déjà dans une entreprise, `/vendreparts [nombre]` vend des parts de ton entreprise.\n\n"
            "Le prix de vente est le même que le prix d'achat (basé sur la valeur actuelle)."
        )
        return
    if len(context.args) == 1:
        try:
            shares = int(context.args[0])
            company = await get_user_company(user.id)
            if not company:
                await update.message.reply_text("❌ Tu n'es dans aucune entreprise. Précise le nom de l'entreprise cible.")
                return
        except ValueError:
            await update.message.reply_text("❌ Le nombre doit être un entier positif.")
            return
    else:
        try:
            shares = int(context.args[-1])
            company_name = " ".join(context.args[:-1])
            company = await get_company_by_name(company_name)
            if not company:
                await update.message.reply_text(f"❌ Entreprise '{company_name}' introuvable.")
                return
        except ValueError:
            await update.message.reply_text("❌ Le nombre doit être un entier positif.")
            return
    if shares <= 0:
        await update.message.reply_text("❌ Nombre invalide.")
        return
    current_shares = await get_user_shares(user.id, company["company_id"])
    if current_shares == 0:
        await update.message.reply_text(f"❌ Tu ne possèdes aucune part de **{escape_md(company['name'])}**.", parse_mode="Markdown")
        return
    if shares > current_shares:
        await update.message.reply_text(f"❌ Tu ne possèdes que {current_shares} parts de cette entreprise.", parse_mode="Markdown")
        return
    rd_level = company.get("rd_level", 0)
    price_per_share = max(10, (company["treasury"] // 1000) * (50 + company["reputation"]) // 50 + rd_level * 100)
    MAX_PRICE = 10_000_000_000
    if price_per_share > MAX_PRICE:
        price_per_share = MAX_PRICE
        await update.message.reply_text("⚠️ Le prix par part a été plafonné à 10 milliards pour éviter un dépassement.")
    total_value = shares * price_per_share
    MAX_TOTAL = 9_000_000_000_000_000
    if total_value > MAX_TOTAL:
        await update.message.reply_text("❌ La valeur totale est trop élevée. Contactez un administrateur.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        if shares == current_shares:
            await db.execute("DELETE FROM company_shares WHERE user_id=? AND company_id=?", (user.id, company["company_id"]))
        else:
            await db.execute("UPDATE company_shares SET shares = shares - ? WHERE user_id=? AND company_id=?", (shares, user.id, company["company_id"]))
        await db.commit()
    await update_balance(user.id, total_value)
    await log_company_action(company["company_id"], "vente_parts", user.id, f"{shares} parts ({fmt(total_value)})")
    await update.message.reply_text(
        f"💰 **Vente de parts réussie !**\n\n"
        f"🏢 Entreprise : **{escape_md(company['name'])}**\n"
        f"📉 {shares} parts vendues à {fmt(price_per_share)} chacune.\n"
        f"💵 Reçu : {fmt(total_value)}\n"
        f"📊 Il te reste {current_shares - shares} parts de cette entreprise.",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_dividendes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut verser des dividendes.")
        return
    percent = 10
    if context.args and context.args[0].replace("%", "").isdigit():
        percent = int(context.args[0].replace("%", ""))
        percent = max(0, min(100, percent))
    dividend_total = int(company["treasury"] * percent / 100)
    if dividend_total < 1000:
        await update.message.reply_text(f"💰 Trésorerie trop faible pour verser {percent}% de dividendes significatifs.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, shares FROM company_shares WHERE company_id=? AND shares > 0",
            (company["company_id"],)
        ) as cur:
            shareholders = [dict(s) for s in await cur.fetchall()]
    total_shares = sum(s["shares"] for s in shareholders)
    if total_shares == 0:
        await update.message.reply_text("❌ Aucune part en circulation.")
        return
    for sh in shareholders:
        share_ratio = sh["shares"] / total_shares
        amount = int(dividend_total * share_ratio)
        if amount > 0:
            await update_balance(sh["user_id"], amount)
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute("UPDATE companies SET treasury = treasury - ? WHERE company_id=?", (dividend_total, company["company_id"]))
        await db.commit()
    await log_company_action(company["company_id"], "dividendes", user.id, f"{percent}% ({fmt(dividend_total)})")
    await update.message.reply_text(
        f"💎 **Dividendes versés !**\n\n"
        f"📊 {percent}% de la trésorerie, soit {fmt(dividend_total)}\n"
        f"👥 Réparti entre {len(shareholders)} actionnaires.\n"
        f"🏦 Nouvelle trésorerie : {fmt(company['treasury'] - dividend_total)}",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_rd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut investir en R&D.")
        return
    if not context.args:
        await update.message.reply_text("Usage : /rd [montant]\nLe coût augmente avec le niveau actuel.")
        return
    amount = parse_amount(context.args[0], company["treasury"])
    if not amount or amount <= 0:
        await update.message.reply_text("❌ Montant invalide.")
        return
    current_level = company.get("rd_level", 0)
    spent = 0
    gained = 0
    next_cost = 50_000 * (current_level + 1)
    while spent + next_cost <= amount and spent + next_cost <= company["treasury"]:
        spent += next_cost
        gained += 1
        current_level += 1
        next_cost = 50_000 * (current_level + 1)
    if gained == 0:
        await update.message.reply_text(f"❌ Il faut au moins {fmt(next_cost)} pour atteindre le prochain niveau de R&D.")
        return
    new_level = company.get("rd_level", 0) + gained
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute(
            "UPDATE companies SET treasury=treasury-?, rd_level=rd_level+? WHERE company_id=?",
            (spent, gained, company["company_id"])
        )
        await db.commit()
    await log_company_action(company["company_id"], "rd_invest", user.id, f"+{gained} niveaux -> {new_level}")
    await update.message.reply_text(
        card(
            "🔬 R&D améliorée",
            [
                f"Niveaux gagnés : <b>+{gained}</b>",
                f"Niveau actuel : <b>{new_level}</b>",
                f"Investissement : <b>{fmt(spent)}</b>",
                f"Prochain coût : <b>{fmt(next_cost)}</b>",
            ],
            icon="🔬", style="thick"
        ),
        parse_mode="HTML"
    )

@require_registered
@require_free
async def cmd_setoverhead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut modifier les frais généraux.")
        return
    if not context.args:
        await update.message.reply_text("Usage : /setoverhead [montant]\nMontant par employé (ex: 1000)")
        return
    try:
        overhead = int(context.args[0])
        if overhead < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return
    await update_company_field(company["company_id"], "overhead", overhead)
    await update.message.reply_text(f"🏢 Frais généraux fixés à {fmt(overhead)} par employé (déduits quotidiennement).")

@require_registered
@require_free
async def cmd_annonce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["poste"] not in ("PDG", "Directeur"):
        await update.message.reply_text("❌ Réservé aux PDG et Directeurs.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /annonce [poste] [salaire proposé]")
        return
    poste = context.args[0]
    try:
        salary = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Salaire invalide.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute(
            "INSERT OR REPLACE INTO company_ads (company_id, poste, salary, created_at) VALUES (?,?,?,?)",
            (company["company_id"], poste, salary, now())
        )
        await db.commit()
    await update.message.reply_text(
        f"📢 **Annonce publiée**\n"
        f"Entreprise : {escape_md(company['name'])}\n"
        f"Poste : {escape_md(poste)}\n"
        f"Salaire : {fmt(salary)}\n\n"
        f"Les joueurs peuvent postuler avec `/postuler {escape_md(company['name'])} [salaire souhaité]`.",
        parse_mode="Markdown"
    )

@require_registered
@require_free
async def cmd_emplois(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT a.*, c.name as company_name
            FROM company_ads a JOIN companies c ON c.company_id=a.company_id
            WHERE a.created_at > ?
            ORDER BY a.created_at DESC LIMIT 20
        """, (now() - 7*86400,)) as cur:
            ads = await cur.fetchall()
    if not ads:
        await update.message.reply_text("📢 Aucune annonce récente.")
        return
    text = "📢 **Offres d'emploi**\n\n"
    for a in ads:
        text += f"🏢 **{escape_md(a['company_name'])}** – Poste: {escape_md(a['poste'])} – Salaire: {fmt(a['salary'])}\n"
        text += f"   Postulez avec `/postuler {escape_md(a['company_name'])} [salaire souhaité]`\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

@require_registered
@require_free
async def cmd_inviter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut inviter.")
        return
    if not update.message.reply_to_message or len(context.args) < 2:
        await update.message.reply_text("Usage : /inviter (en répondant à l'utilisateur) [salaire] [poste]")
        return
    target = update.message.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas t'inviter toi-même.")
        return
    try:
        salary = int(context.args[0])
        poste = " ".join(context.args[1:])
        if salary < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Salaire invalide.")
        return
    if await get_user_company(target.id):
        await update.message.reply_text("❌ Cette personne travaille déjà dans une entreprise.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.execute(
            "DELETE FROM company_invitations WHERE user_id=? AND company_id=? AND status='pending'",
            (target.id, company["company_id"])
        )
        await db.execute(
            "INSERT INTO company_invitations (user_id, company_id, poste, salary, invited_by, invited_at, status) VALUES (?,?,?,?,?,?,'pending')",
            (target.id, company["company_id"], poste, salary, user.id, now())
        )
        await db.commit()
    await add_notification(target.id, f"📩 Vous avez reçu une invitation de **{escape_md(company['name'])}** pour le poste de {escape_md(poste)} avec un salaire de {fmt(salary)}. Utilisez `/repondre_invitation {company['company_id']} accepter/refuser`.")
    await update.message.reply_text(f"✅ Invitation envoyée à {escape_md(target.full_name)}.")

@require_registered
@require_free
async def cmd_repondre_invitation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if len(context.args) != 2:
        await update.message.reply_text("Usage : /repondre_invitation [company_id] [accepter/refuser]")
        return
    try:
        company_id = int(context.args[0])
        decision = context.args[1].lower()
    except ValueError:
        await update.message.reply_text("❌ Arguments invalides.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM company_invitations WHERE user_id=? AND company_id=? AND status='pending'",
            (user.id, company_id)
        ) as cur:
            inv = await cur.fetchone()
        if not inv:
            await update.message.reply_text("❌ Aucune invitation en attente pour cette entreprise.")
            return
        if decision == "accepter":
            if await get_user_company(user.id):
                await update.message.reply_text("❌ Tu as déjà une entreprise, tu ne peux pas accepter.")
                return
            await db.execute("UPDATE company_invitations SET status='accepted' WHERE user_id=? AND company_id=?", (user.id, company_id))
            await db.execute("""
                INSERT INTO company_members (user_id, company_id, poste, base_salary, joined_at, activity_score)
                VALUES (?,?,?,?,?,0)
            """, (user.id, company_id, inv["poste"], inv["salary"], now()))
            await db.commit()
            await add_notification(inv["invited_by"], f"{user.full_name} a accepté l'invitation pour rejoindre **{inv['poste']}** dans votre entreprise.")
            await update.message.reply_text(f"✅ Vous avez rejoint l'entreprise en tant que {escape_md(inv['poste'])} avec un salaire de {fmt(inv['salary'])}.")
        else:
            await db.execute("UPDATE company_invitations SET status='refused' WHERE user_id=? AND company_id=?", (user.id, company_id))
            await db.commit()
            await add_notification(inv["invited_by"], f"{user.full_name} a refusé votre invitation.")
            await update.message.reply_text("❌ Invitation refusée.")

# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# FORMER (financer une formation pour un employé)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_former(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut financer une formation.")
        return
    if not update.message.reply_to_message or len(context.args) < 1:
        await update.message.reply_text("Usage : /former [montant] (en répondant à l'employé)")
        return
    target = update.message.reply_to_message.from_user
    try:
        amount = int(context.args[0])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return
    if company["treasury"] < amount:
        await update.message.reply_text("❌ Trésorerie insuffisante.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute("UPDATE companies SET treasury=treasury-? WHERE company_id=?", (amount, company["company_id"]))
        await db.execute("UPDATE company_members SET activity_score = activity_score + ? WHERE user_id=? AND company_id=?", (amount // 1000, target.id, company["company_id"]))
        await db.commit()
    await log_company_action(company["company_id"], "formation", user.id, f"{target.full_name} pour {fmt(amount)}")
    await update.message.reply_text(f"🎓 Formation financée pour {escape_md(target.full_name)} (+{amount//1000} pts d'activité).")

# ─────────────────────────────────────────────────────────────────────────────
# PRIME (donner une prime à un employé)
# ─────────────────────────────────────────────────────────────────────────────
@require_registered
@require_free
async def cmd_prime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    company = await get_user_company(user.id)
    if not company or company["owner_id"] != user.id:
        await update.message.reply_text("❌ Seul le PDG peut donner une prime.")
        return
    if not update.message.reply_to_message or len(context.args) < 1:
        await update.message.reply_text("Usage : /prime [montant] (en répondant à l'employé)")
        return
    target = update.message.reply_to_message.from_user
    try:
        amount = int(context.args[0])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return
    if company["treasury"] < amount:
        await update.message.reply_text("❌ Trésorerie insuffisante.")
        return
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await db.execute("UPDATE companies SET treasury=treasury-? WHERE company_id=?", (amount, company["company_id"]))
        await db.commit()
    await update_balance(target.id, amount)
    await log_company_action(company["company_id"], "prime", user.id, f"{target.full_name} ({fmt(amount)})")
    await update.message.reply_text(f"🎁 Prime de {fmt(amount)} versée à {escape_md(target.full_name)}.")