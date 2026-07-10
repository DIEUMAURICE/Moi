import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes
from database import DB_PATH, get_user, update_balance, get_bank_account, get_all_bank_accounts
from utils.decorators import require_registered
from utils.helpers import fmt, now, parse_amount
from config import BANKS, DIPLOME_SALARY_BONUS


@require_registered
async def cmd_banques(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🏦 **Banques disponibles**\n\n"
    for b in BANKS:
        text += (
            f"🏛️ **{b['name']}**\n"
            f"  📈 Intérêts : {b['interest'] * 100:.1f}%/jour\n"
            f"  💰 Dépôt minimum : {fmt(b['min'])}\n"
            f"  💳 Prêt maximum : {fmt(b['loan_max'])}\n\n"
        )
    text += "👉 /ouvrir [nom banque] — ouvrir un compte\n"
    text += "_Plus ton dépôt est élevé, meilleurs sont les intérêts !_"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
async def cmd_ouvrir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    if not context.args:
        banks_list = "\n".join(f"• {b['name']}" for b in BANKS)
        await update.message.reply_text(
            f"🏦 Choisis une banque :\n{banks_list}\n\nUsage : /ouvrir [Nom Banque]",
            parse_mode="Markdown"
        )
        return

    bank_name = " ".join(context.args)
    bank = next((b for b in BANKS if b["name"].lower() == bank_name.lower()), None)
    if not bank:
        await update.message.reply_text("❌ Banque introuvable. /banques pour voir la liste.")
        return

    existing = await get_bank_account(user.id, bank["name"])
    if existing:
        await update.message.reply_text(
            f"✅ Tu as déjà un compte à la **{bank['name']}**.\n"
            f"💰 Solde : {fmt(existing['balance'])}",
            parse_mode="Markdown"
        )
        return

    if u["balance"] < bank["min"]:
        await update.message.reply_text(
            f"❌ La **{bank['name']}** exige un dépôt minimum de **{fmt(bank['min'])}**.\n"
            f"💰 Ton solde : {fmt(u['balance'])}",
            parse_mode="Markdown"
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO bank_accounts (user_id, bank_name, balance, loan, loan_due, loan_penalty_applied, opened_at, last_interest) VALUES (?,?,0,0,0,0,?,?)",
            (user.id, bank["name"], now(), now())
        )
        await db.commit()

    await update.message.reply_text(
        f"✅ **Compte ouvert à la {bank['name']} !**\n\n"
        f"📈 Intérêts : {bank['interest'] * 100:.1f}% par jour\n"
        f"💳 Prêt max : {fmt(bank['loan_max'])}\n\n"
        f"👉 /depot montant — déposer de l'argent\n"
        f"_Les intérêts sont calculés quotidiennement automatiquement._",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_depot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)

    accounts = await get_all_bank_accounts(user.id)
    if not accounts:
        await update.message.reply_text("❌ Tu n'as pas de compte bancaire. /ouvrir pour en créer un.")
        return

    if not context.args:
        await update.message.reply_text("Usage : /depot montant [banque optionnelle]")
        return

    amount = parse_amount(context.args[0], u["balance"])
    if not amount or amount <= 0:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if amount > u["balance"]:
        await update.message.reply_text(f"❌ Fonds insuffisants ! Solde : {fmt(u['balance'])}")
        return

    # Choisir la banque
    if len(context.args) > 1:
        bank_name = " ".join(context.args[1:])
        acc = await get_bank_account(user.id, bank_name)
        if not acc:
            await update.message.reply_text(f"❌ Aucun compte à la banque **{bank_name}**. /mescomptes")
            return
    else:
        if len(accounts) > 1:
            await update.message.reply_text(
                "⚠️ Tu as plusieurs comptes. Précise la banque : `/depot 1000 NomBanque`\n"
                f"Comptes : {', '.join(a['bank_name'] for a in accounts)}"
            )
            return
        acc = accounts[0]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bank_accounts SET balance=balance+? WHERE user_id=? AND bank_name=?",
            (amount, user.id, acc["bank_name"])
        )
        await db.commit()

    await update_balance(user.id, -amount)

    bank = next((b for b in BANKS if b["name"] == acc["bank_name"]), None)
    daily = int((acc["balance"] + amount) * (bank["interest"] if bank else 0.01))

    await update.message.reply_text(
        f"🏦 **Dépôt réussi !**\n\n"
        f"🏛️ Banque : {acc['bank_name']}\n"
        f"💰 Montant déposé : **{fmt(amount)}**\n"
        f"💵 Nouveau solde bancaire : {fmt(acc['balance'] + amount)}\n"
        f"📈 Intérêts quotidiens estimés : +{fmt(daily)}",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_retrait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    accounts = await get_all_bank_accounts(user.id)

    if not accounts:
        await update.message.reply_text("❌ Tu n'as pas de compte bancaire.")
        return

    if not context.args:
        await update.message.reply_text("Usage : /retrait montant [banque optionnelle]")
        return

    amount = parse_amount(context.args[0])
    if not amount or amount <= 0:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if len(context.args) > 1:
        bank_name = " ".join(context.args[1:])
        acc = await get_bank_account(user.id, bank_name)
        if not acc:
            await update.message.reply_text(f"❌ Aucun compte à la banque **{bank_name}**.")
            return
    else:
        if len(accounts) > 1:
            await update.message.reply_text(
                "⚠️ Tu as plusieurs comptes. Précise la banque : `/retrait 1000 NomBanque`\n"
                f"Comptes : {', '.join(a['bank_name'] for a in accounts)}"
            )
            return
        acc = accounts[0]

    if amount > acc["balance"]:
        await update.message.reply_text(
            f"❌ Solde bancaire insuffisant !\n"
            f"💰 Solde : {fmt(acc['balance'])}"
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bank_accounts SET balance=balance-? WHERE user_id=? AND bank_name=?",
            (amount, user.id, acc["bank_name"])
        )
        await db.commit()

    await update_balance(user.id, amount)

    await update.message.reply_text(
        f"✅ **Retrait réussi !**\n\n"
        f"🏛️ Banque : {acc['bank_name']}\n"
        f"💰 Montant retiré : **{fmt(amount)}**\n"
        f"💵 Reste en banque : {fmt(acc['balance'] - amount)}",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_soldebanque(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    accounts = await get_all_bank_accounts(user.id)

    if not accounts:
        await update.message.reply_text("❌ Tu n'as pas de compte bancaire. /ouvrir pour en créer un.")
        return

    text = f"🏦 **Tes comptes bancaires**\n\n"
    total_balance = 0
    total_loan = 0
    for acc in accounts:
        bank = next((b for b in BANKS if b["name"] == acc["bank_name"]), None)
        interest = bank["interest"] if bank else 0.01
        daily = int(acc["balance"] * interest)
        total_balance += acc["balance"]
        total_loan += acc["loan"]
        text += (
            f"🏛️ **{acc['bank_name']}**\n"
            f"  💰 Solde : {fmt(acc['balance'])}\n"
            f"  📈 Intérêts/jour : +{fmt(daily)}\n"
            f"  💳 Prêt actif : {fmt(acc['loan'])}\n\n"
        )
    text += f"💎 **Total bancaire : {fmt(total_balance)}**\n"
    text += f"💳 Total prêts : {fmt(total_loan)}"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_registered
async def cmd_pret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    accounts = await get_all_bank_accounts(user.id)

    if not accounts:
        await update.message.reply_text("❌ Tu dois d'abord ouvrir un compte bancaire.")
        return

    # Utiliser le premier compte ou celui spécifié
    if len(context.args) > 1:
        bank_name = " ".join(context.args[1:])
        acc = await get_bank_account(user.id, bank_name)
        if not acc:
            await update.message.reply_text(f"❌ Compte introuvable : {bank_name}")
            return
    else:
        if len(accounts) > 1:
            await update.message.reply_text(
                "⚠️ Tu as plusieurs comptes. Précise la banque : `/pret montant NomBanque`\n"
                f"Comptes : {', '.join(a['bank_name'] for a in accounts)}"
            )
            return
        acc = accounts[0]

    bank = next((b for b in BANKS if b["name"] == acc["bank_name"]), None)
    if not bank:
        await update.message.reply_text("❌ Banque introuvable dans la configuration.")
        return

    if acc["loan"] > 0:
        await update.message.reply_text(
            f"❌ Tu as déjà un prêt actif de **{fmt(acc['loan'])}** !\n"
            f"👉 /rembourser pour rembourser avant de prendre un nouveau prêt.",
            parse_mode="Markdown"
        )
        return

    if not context.args:
        await update.message.reply_text(
            f"🏛️ **Banque : {bank['name']}**\n"
            f"💳 Prêt maximum : {fmt(bank['loan_max'])}\n"
            f"📊 Taux d'intérêt prêt : 8%\n\n"
            f"Usage : /pret montant"
        )
        return

    amount = parse_amount(context.args[0])
    if not amount or amount <= 0 or amount > bank["loan_max"]:
        await update.message.reply_text(f"❌ Montant invalide. Max : {fmt(bank['loan_max'])}")
        return

    # Score de crédit (karma > -100 et moins de 20 crimes réussis)
    credit_ok = u.get("karma", 0) > -100 and u.get("crimes_success", 0) < 20

    if not credit_ok:
        await update.message.reply_text(
            "❌ **Prêt refusé !**\n\n"
            "Ton score de crédit est trop bas.\n"
            "• Karma trop négatif\n"
            "• Trop de crimes commis\n\n"
            "_Améliore ton karma pour obtenir un prêt._"
        )
        return

    loan_with_interest = int(amount * 1.08)
    due_date = now() + 30 * 86400  # 30 jours

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bank_accounts SET loan=?, loan_due=?, loan_penalty_applied=0 WHERE user_id=? AND bank_name=?",
            (loan_with_interest, due_date, user.id, acc["bank_name"])
        )
        await db.commit()

    await update_balance(user.id, amount)

    await update.message.reply_text(
        f"💳 **Prêt accordé !**\n\n"
        f"💰 Montant reçu : **{fmt(amount)}**\n"
        f"📊 Avec intérêts (8%) : {fmt(loan_with_interest)}\n"
        f"📅 À rembourser dans : 30 jours\n\n"
        f"⚠️ Si tu ne rembourses pas à temps, une pénalité unique de 5% s'appliquera.\n"
        f"👉 /rembourser montant  ou  /rembourser tout",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_rembourser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await get_user(user.id)
    accounts = await get_all_bank_accounts(user.id)
    acc = next((a for a in accounts if a["loan"] > 0), None)

    if not acc:
        await update.message.reply_text("❌ Tu n'as pas de prêt actif.")
        return

    # Vérifier si le prêt est en retard
    overdue = now() > acc.get("loan_due", now() + 1)
    penalty = 0
    if overdue and not acc.get("loan_penalty_applied"):
        penalty = int(acc["loan"] * 0.05)
        # Marquer la pénalité comme appliquée (pour ne la facturer qu'une fois)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE bank_accounts SET loan_penalty_applied=1 WHERE user_id=? AND bank_name=?",
                (user.id, acc["bank_name"])
            )
            await db.commit()
    total_due = acc["loan"] + penalty

    # Si pas d'argument, afficher le montant dû
    if not context.args:
        penalty_line = f"⚠️ Pénalité retard : +{fmt(penalty)}\n" if penalty else ""
        await update.message.reply_text(
            f"💳 **Remboursement de prêt**\n\n"
            f"🏛️ Banque : {acc['bank_name']}\n"
            f"💰 Montant dû : {fmt(acc['loan'])}\n"
            f"{penalty_line}"
            f"💎 Total à rembourser : **{fmt(total_due)}**\n\n"
            f"Usage : `/rembourser montant` ou `/rembourser tout`",
            parse_mode="Markdown"
        )
        return

    # Gestion du "tout"
    arg = context.args[0].lower()
    if arg in ("tout", "all", "total"):
        amount = total_due
    else:
        amount = parse_amount(arg, u["balance"])

    if not amount or amount <= 0:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if amount > u["balance"]:
        await update.message.reply_text(f"❌ Solde insuffisant. Tu as {fmt(u['balance'])}.")
        return

    pay = min(amount, total_due)
    remaining = max(0, total_due - pay)

    # Mise à jour du prêt
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bank_accounts SET loan=? WHERE user_id=? AND bank_name=?",
            (remaining, user.id, acc["bank_name"])
        )
        await db.commit()

    await update_balance(user.id, -pay)

    await update.message.reply_text(
        f"✅ **Remboursement effectué !**\n\n"
        f"💰 Payé : {fmt(pay)}\n"
        f"💳 Reste dû : {fmt(remaining)}\n"
        f"{'✨ Prêt entièrement remboussé !' if remaining == 0 else ''}",
        parse_mode="Markdown"
    )


@require_registered
async def cmd_mescomptes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    accounts = await get_all_bank_accounts(user.id)

    if not accounts:
        await update.message.reply_text("❌ Tu n'as aucun compte bancaire.\n/ouvrir pour en créer un.")
        return

    text = f"🏦 **Tous tes comptes — {user.full_name}**\n\n"
    for acc in accounts:
        bank = next((b for b in BANKS if b["name"] == acc["bank_name"]), {})
        interest_daily = int(acc["balance"] * bank.get("interest", 0))
        text += (
            f"🏛️ **{acc['bank_name']}**\n"
            f"  💰 Solde : {fmt(acc['balance'])}\n"
            f"  📈 +{fmt(interest_daily)}/jour\n"
            f"  💳 Prêt : {fmt(acc['loan'])}\n\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


async def process_bank_interests():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM bank_accounts WHERE balance > 0 AND last_interest > 0"
        ) as cur:
            accounts = [dict(r) for r in await cur.fetchall()]   # ← conversion

    for acc in accounts:
        bank = next((b for b in BANKS if b["name"] == acc["bank_name"]), None)
        if not bank:
            continue
        last = acc["last_interest"]
        current_time = now()
        days_passed = max(0, (current_time - last) // 86400)
        if days_passed == 0:
            continue
        interest_per_day = int(acc["balance"] * bank["interest"])
        total_interest = interest_per_day * days_passed
        if total_interest > 0:
            async with aiosqlite.connect(DB_PATH) as db2:
                await db2.execute(
                    "UPDATE bank_accounts SET balance=balance+?, last_interest=? WHERE id=?",
                    (total_interest, current_time, acc["id"])
                )
                await db2.execute(
                    "UPDATE users SET balance=balance+?, total_earned=total_earned+? WHERE user_id=?",
                    (total_interest, total_interest, acc["user_id"])
                )
                await db2.commit()
