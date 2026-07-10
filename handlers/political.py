"""
handlers/political.py — Système politique massif, sans simulation.
Les joueurs contrôlent tout (élections, partis, lois, constitution, référendums, destitution).
Le bot applique les règles (coûts, quorum, majorité, délais).

MODIFICATIONS :
 - Dépouillement automatique des élections terminées (dans political_maintenance)
 - /candidater peut maintenant cibler une élection précise (par ID ou par poste)
"""
import aiosqlite
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from database import (
    DB_PATH, get_user, update_balance, update_field, increment_field,
    now, get_all_users_count, add_notification
)
from utils.decorators import require_registered, require_free, cooldown
from utils.helpers import fmt_money, card, fmt_duration, bullet_list, safe, escape_md
from config import (
    ELECTION_COST, POLITICAL_POSTS, ELECTION_KARMA_REQUIRED, ELECTION_QUORUM,
    ELECTION_COOLDOWN, ELECTION_DEFAULT_DURATION_HOURS, ELECTION_MAX_DURATION_HOURS,
    PARTY_CREATION_COST, PARTY_MIN_MEMBERS, PARTY_MAX_MEMBERS,
    LAW_PROPOSAL_COST, LAW_VOTE_DURATION, LAW_DEFAULT_MAJORITY,
    REFERENDUM_COST, REFERENDUM_DURATION,
    MOTION_SIGNATURES_NEEDED, MOTION_VOTE_DURATION, MOTION_MAJORITY, MOTION_COOLDOWN,
    CABINET_POSITIONS, DEFAULT_CONSTITUTION
)

# ----------------------------------------------------------------------
# 1. GESTION DE LA CONSTITUTION
# ----------------------------------------------------------------------
@require_registered
async def cmd_constitution(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche la constitution actuelle."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT text, amended_at, amended_by FROM constitution WHERE id=1") as cur:
            row = await cur.fetchone()
    if not row:
        text = DEFAULT_CONSTITUTION
        await update.message.reply_text(card("📜 Constitution", [text], icon="⚖️", style="thick"), parse_mode="HTML")
        return
    text, amended_at, amended_by = row
    footer = f"Dernière modification : {fmt_duration(now() - amended_at)} par {amended_by}"
    await update.message.reply_text(
        card("📜 Constitution", [text], icon="⚖️", style="thick", footer=footer),
        parse_mode="HTML"
    )

@require_registered
@require_free
@cooldown("last_constitution", 7*86400, "⏳ La constitution ne peut être modifiée qu'une fois par semaine.")
async def cmd_modifierconstitution(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Propose une modification de la constitution (déclenche un référendum)."""
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /modifierconstitution <nouveau texte>")
        return
    new_text = " ".join(context.args)
    if len(new_text) < 10:
        await update.message.reply_text("❌ Le texte doit faire au moins 10 caractères.")
        return
    # Vérifier que le joueur est élu à un poste de haut rang (optionnel)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT poste FROM political_offices WHERE occupant_id=?", (user.id,)) as cur:
            office = await cur.fetchone()
    if not office or office[0] not in ["Président", "Ministre", "Sénateur"]:
        await update.message.reply_text("❌ Seuls les Présidents, Ministres ou Sénateurs peuvent modifier la constitution.")
        return
    # Créer un référendum pour la modification
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO referendums (question, proposer_id, created_at, voting_ends_at, required_majority) VALUES (?,?,?,?,?)",
            (f"Modifier la constitution : {new_text[:200]}", user.id, now(), now() + REFERENDUM_DURATION, 0.6)
        )
        await db.commit()
    await update.message.reply_text(
        f"✅ Proposition de modification constitutionnelle enregistrée.\n"
        f"Un référendum est ouvert pour 72h. Tous les joueurs peuvent voter avec `/referendum pour` ou `/referendum contre`.\n"
        f"Majorité requise : 60%."
    )

# ----------------------------------------------------------------------
# 2. PARTIS POLITIQUES
# ----------------------------------------------------------------------
@require_registered
@require_free
async def cmd_creerparti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Crée un parti politique."""
    user = update.effective_user
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage : /creerparti <nom> [idéologie]")
        return
    name = " ".join(args)
    ideology = "Non définie"
    if len(args) >= 2:
        ideology = args[1]
    # Vérifier si le joueur est déjà dans un parti
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM party_members WHERE user_id=?", (user.id,)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Tu es déjà membre d'un parti. Quitte-le d'abord avec `/quitterparti`.")
                return
    u = await get_user(user.id)
    if u["balance"] < PARTY_CREATION_COST:
        await update.message.reply_text(f"❌ Fonds insuffisants. Coût : {fmt_money(PARTY_CREATION_COST)}")
        return
    await update_balance(user.id, -PARTY_CREATION_COST)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO parties (name, ideology, leader_id, treasury, created_at) VALUES (?,?,?,?,?)",
            (name, ideology, user.id, 0, now())
        )
        party_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO party_members (party_id, user_id, role, joined_at) VALUES (?,?,?,?)",
            (party_id, user.id, 'leader', now())
        )
        await db.commit()
    await update.message.reply_text(
        card(
            f"🎉 Parti créé : {name}",
            [f"Idéologie : {ideology}", f"Chef : {user.first_name}", f"Trésorerie : 0"],
            icon="🏛️", style="double"
        )
    )

@require_registered
async def cmd_partis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liste tous les partis actifs."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT p.id, p.name, p.ideology, p.leader_id, COUNT(m.user_id) as members "
            "FROM parties p LEFT JOIN party_members m ON p.id=m.party_id "
            "WHERE p.disbanded=0 GROUP BY p.id ORDER BY members DESC"
        ) as cur:
            parties = await cur.fetchall()
    if not parties:
        await update.message.reply_text("Aucun parti politique n'existe encore. `/creerparti` pour en créer un.")
        return
    lines = [f"🏛️ **{p['name']}** – {p['ideology']} (Chef : {p['leader_id']}, Membres : {p['members']})" for p in parties]
    await update.message.reply_text(bullet_list(lines), parse_mode="Markdown")

@require_registered
async def cmd_rejoindreparti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rejoindre un parti (nécessite une invitation ou approbation ?). Ici on laisse libre."""
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /rejoindreparti <nom_du_parti>")
        return
    name = " ".join(context.args)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, leader_id FROM parties WHERE name=? AND disbanded=0", (name,)) as cur:
            party = await cur.fetchone()
        if not party:
            await update.message.reply_text("Parti introuvable.")
            return
        party_id, leader_id = party
        # Vérifier si déjà membre
        async with db.execute("SELECT 1 FROM party_members WHERE user_id=?", (user.id,)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Tu es déjà dans un parti. `/quitterparti` d'abord.")
                return
        # Vérifier la limite de membres
        async with db.execute("SELECT COUNT(*) FROM party_members WHERE party_id=?", (party_id,)) as cur:
            count = (await cur.fetchone())[0]
        if count >= PARTY_MAX_MEMBERS:
            await update.message.reply_text(f"❌ Ce parti a atteint sa limite de {PARTY_MAX_MEMBERS} membres.")
            return
        # Inscription directe (sans approbation) – on peut ajouter un système d'invite si voulu
        await db.execute(
            "INSERT INTO party_members (party_id, user_id, role, joined_at) VALUES (?,?,?,?)",
            (party_id, user.id, 'member', now())
        )
        await db.commit()
    await update.message.reply_text(f"✅ Tu as rejoint le parti **{name}**.")

@require_registered
async def cmd_quitterparti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT party_id, role FROM party_members WHERE user_id=?", (user.id,)) as cur:
            member = await cur.fetchone()
        if not member:
            await update.message.reply_text("❌ Tu n'es dans aucun parti.")
            return
        party_id, role = member
        if role == 'leader':
            # Vérifier s'il y a d'autres membres
            async with db.execute("SELECT COUNT(*) FROM party_members WHERE party_id=?", (party_id,)) as cur:
                count = (await cur.fetchone())[0]
            if count > 1:
                await update.message.reply_text("❌ Tu es le chef. Transfère d'abord le leadership avec `/transfertchefparti @user` ou dissous le parti avec `/dissoudreporti`.")
                return
            else:
                # Dernier membre, on dissout le parti
                await db.execute("UPDATE parties SET disbanded=1 WHERE id=?", (party_id,))
                await db.execute("DELETE FROM party_members WHERE party_id=?", (party_id,))
                await db.commit()
                await update.message.reply_text("⚰️ Le parti a été dissous. Tu n'en fais plus partie.")
        else:
            await db.execute("DELETE FROM party_members WHERE user_id=?", (user.id,))
            await db.commit()
            await update.message.reply_text("✅ Tu as quitté le parti.") 

@require_registered
async def cmd_transfertchefparti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transfère le leadership à un autre membre."""
    user = update.effective_user
    if not context.args or not context.args[0].startswith('@'):
        await update.message.reply_text("Usage : /transfertchefparti @nouveau_chef")
        return
    target_username = context.args[0][1:]
    # Récupérer target_id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id FROM users WHERE username LIKE ? LIMIT 1", (f"%{target_username}%",)) as cur:
            target = await cur.fetchone()
    if not target:
        await update.message.reply_text("❌ Joueur introuvable.")
        return
    target_id = target["user_id"]
    async with aiosqlite.connect(DB_PATH) as db:
        # Vérifier que l'utilisateur est chef
        async with db.execute("SELECT party_id FROM party_members WHERE user_id=? AND role='leader'", (user.id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text("❌ Tu n'es pas chef de parti.")
            return
        party_id = row[0]
        # Vérifier que la cible est membre du même parti
        async with db.execute("SELECT 1 FROM party_members WHERE party_id=? AND user_id=?", (party_id, target_id)) as cur:
            if not await cur.fetchone():
                await update.message.reply_text("❌ Ce joueur n'est pas membre de ton parti.")
                return
        # Transférer
        await db.execute("UPDATE party_members SET role='member' WHERE party_id=? AND user_id=?", (party_id, user.id))
        await db.execute("UPDATE party_members SET role='leader' WHERE party_id=? AND user_id=?", (party_id, target_id))
        await db.execute("UPDATE parties SET leader_id=? WHERE id=?", (target_id, party_id))
        await db.commit()
    await update.message.reply_text(f"✅ Le leadership a été transféré à {target_username}.")

# ----------------------------------------------------------------------
# 3. ÉLECTIONS (sans simulation)
# ----------------------------------------------------------------------
@require_registered
@require_free
@cooldown("last_election", ELECTION_COOLDOWN, "⏳ Tu as déjà lancé une élection récemment.")
async def cmd_lancerelection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            f"Usage : `/lancerelection <poste> [durée_h]`\nPostes : {', '.join(POLITICAL_POSTS)}\n"
            f"Durée par défaut : {ELECTION_DEFAULT_DURATION_HOURS}h, max {ELECTION_MAX_DURATION_HOURS}h.\n"
            f"Coût : variable. Karma requis : {ELECTION_KARMA_REQUIRED}."
        )
        return

    # --- Reconstruction du nom du poste (peut contenir plusieurs mots) ---
    poste = None
    duration_str = None
    # Créer un mapping des noms de postes en minuscules vers leur forme originale
    poste_lower_map = {p.lower(): p for p in POLITICAL_POSTS}
    for i in range(1, len(args) + 1):
        candidate_lower = " ".join(args[:i]).lower()
        if candidate_lower in poste_lower_map:
            poste = poste_lower_map[candidate_lower]
            # Le reste des arguments est la durée (si présente)
            if i < len(args):
                duration_str = args[i]
            break
    if not poste:
        await update.message.reply_text(f"❌ Poste inconnu. Choisis parmi : {', '.join(POLITICAL_POSTS)}")
        return
    # -----------------------------------------------------------------

    # Vérifier si une élection est déjà ouverte pour ce poste
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM elections WHERE poste=? AND status='open' AND ends_at>?", (poste, now())) as cur:
            if await cur.fetchone():
                await update.message.reply_text(f"❌ Une élection pour {poste} est déjà en cours.")
                return

    u = await get_user(user.id)
    cost = ELECTION_COST[poste]
    if u["balance"] < cost:
        await update.message.reply_text(f"❌ Solde insuffisant. Coût : {fmt_money(cost)}")
        return
    if u.get("karma", 0) < ELECTION_KARMA_REQUIRED:
        await update.message.reply_text(f"❌ Karma insuffisant ({ELECTION_KARMA_REQUIRED} requis).")
        return
    await update_balance(user.id, -cost)

    # --- Parsing de la durée (supporte "1h", "24", "1heure", etc.) ---
    duration_h = ELECTION_DEFAULT_DURATION_HOURS
    if duration_str:
        try:
            dur_str = duration_str.lower().replace('h', '').replace('heure', '').replace('hr', '').strip()
            d = int(float(dur_str))
            if 1 <= d <= ELECTION_MAX_DURATION_HOURS:
                duration_h = d
        except:
            pass
    # ----------------------------------------------------------------

    ends_at = now() + duration_h * 3600
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO elections (poste, started_by, started_at, ends_at, status) VALUES (?,?,?,?,?)",
            (poste, user.id, now(), ends_at, 'open')
        )
        await db.commit()

    await update.message.reply_text(
        card(f"🗳️ Élection lancée : {poste}",
             [f"💰 Coût : {fmt_money(cost)}", f"⏳ Durée : {duration_h}h",
              f"📢 `/candidater` pour se présenter", f"🗳️ `/voter @candidat` pour voter"],
             icon="🏛️", style="thick")
    )

@require_registered
@require_free
async def cmd_candidater(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Se présenter à une élection.
    Usage:
      /candidater                     → dernière élection ouverte
      /candidater <ID>                → élection spécifique par ID
      /candidater <poste>             → élection ouverte pour ce poste
      /candidater <poste> programme   → avec programme
      /candidater <ID> programme      → avec programme
    """
    user = update.effective_user
    args = context.args

    # Si aucun argument, on prend la dernière élection ouverte
    if not args:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id, poste FROM elections WHERE status='open' AND ends_at>? ORDER BY started_at DESC LIMIT 1",
                (now(),)
            ) as cur:
                election = await cur.fetchone()
        if not election:
            await update.message.reply_text("❌ Aucune élection ouverte.")
            return
        election_id, poste = election
        program = "Aucun programme"
    else:
        # Essayer d'interpréter le premier argument comme un ID
        election_id = None
        poste = None
        try:
            election_id = int(args[0])
            # Le reste est le programme
            program = " ".join(args[1:]) if len(args) > 1 else "Aucun programme"
        except ValueError:
            # Ce n'est pas un ID, c'est un poste (ou plusieurs mots)
            # On cherche à reconstituer le poste
            poste_lower_map = {p.lower(): p for p in POLITICAL_POSTS}
            found_poste = None
            for i in range(1, len(args) + 1):
                candidate_lower = " ".join(args[:i]).lower()
                if candidate_lower in poste_lower_map:
                    found_poste = poste_lower_map[candidate_lower]
                    program = " ".join(args[i:]) if i < len(args) else "Aucun programme"
                    break
            if not found_poste:
                # Si on n'a pas trouvé de poste, on prend tout comme programme et on cherche la dernière élection (comportement legacy)
                program = " ".join(args)
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute(
                        "SELECT id, poste FROM elections WHERE status='open' AND ends_at>? ORDER BY started_at DESC LIMIT 1",
                        (now(),)
                    ) as cur:
                        election = await cur.fetchone()
                if not election:
                    await update.message.reply_text("❌ Aucune élection ouverte.")
                    return
                election_id, poste = election
            else:
                poste = found_poste

        # Si on a un ID, on cherche l'élection
        if election_id is not None:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT id, poste FROM elections WHERE id=? AND status='open' AND ends_at>?",
                    (election_id, now())
                ) as cur:
                    election = await cur.fetchone()
            if not election:
                # Lister les élections ouvertes pour aider
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute(
                        "SELECT id, poste FROM elections WHERE status='open' AND ends_at>? ORDER BY ends_at ASC",
                        (now(),)
                    ) as cur:
                        ouvertes = await cur.fetchall()
                if ouvertes:
                    liste = "\n".join(f"ID {e[0]} : {e[1]}" for e in ouvertes)
                    await update.message.reply_text(
                        f"❌ Aucune élection ouverte avec cet ID.\n\nÉlections en cours :\n{liste}"
                    )
                else:
                    await update.message.reply_text("❌ Aucune élection ouverte.")
                return
            election_id, poste = election

        # Si on a un poste (sans ID), on cherche l'élection correspondante
        elif poste is not None:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT id, poste FROM elections WHERE status='open' AND ends_at>? AND LOWER(poste)=LOWER(?) ORDER BY started_at DESC LIMIT 1",
                    (now(), poste)
                ) as cur:
                    election = await cur.fetchone()
            if not election:
                # Lister les élections ouvertes pour aider
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute(
                        "SELECT DISTINCT poste FROM elections WHERE status='open' AND ends_at>?",
                        (now(),)
                    ) as cur:
                        ouvertes = await cur.fetchall()
                if ouvertes:
                    liste = ", ".join(row[0] for row in ouvertes)
                    await update.message.reply_text(
                        f"❌ Aucune élection ouverte pour le poste '{poste}'.\nÉlections en cours : {liste}"
                    )
                else:
                    await update.message.reply_text("❌ Aucune élection ouverte.")
                return
            election_id, poste = election

    # À ce stade, nous avons election_id, poste et program
    # Vérifier si déjà candidat
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM candidates WHERE election_id=? AND user_id=?", (election_id, user.id)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Tu es déjà candidat à cette élection.")
                return
        u = await get_user(user.id)
        if u.get("karma", 0) < ELECTION_KARMA_REQUIRED:
            await update.message.reply_text(f"❌ Karma insuffisant ({ELECTION_KARMA_REQUIRED} requis).")
            return
        # Récupérer le parti du joueur
        party_id = None
        async with db.execute("SELECT party_id FROM party_members WHERE user_id=?", (user.id,)) as cur:
            row = await cur.fetchone()
        if row:
            party_id = row[0]
        await db.execute(
            "INSERT INTO candidates (election_id, user_id, program, party_id) VALUES (?,?,?,?)",
            (election_id, user.id, program, party_id)
        )
        await db.commit()
    await update.message.reply_text(f"✅ Candidature enregistrée pour **{poste}** (élection #{election_id}).")

@require_registered
async def cmd_voter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args or not context.args[0].startswith('@'):
        await update.message.reply_text("Usage : `/voter @pseudo_du_candidat`")
        return
    target_username = context.args[0][1:]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, full_name FROM users WHERE username LIKE ? LIMIT 1", (f"%{target_username}%",)) as cur:
            target = await cur.fetchone()
    if not target:
        await update.message.reply_text("❌ Candidat introuvable.")
        return
    target_id = target["user_id"]
    async with aiosqlite.connect(DB_PATH) as db:
        # Élection ouverte
        async with db.execute(
            "SELECT id, poste, ends_at FROM elections WHERE status='open' AND ends_at>? ORDER BY started_at DESC LIMIT 1",
            (now(),)
        ) as cur:
            election = await cur.fetchone()
        if not election:
            await update.message.reply_text("❌ Aucune élection ouverte.")
            return
        election_id, poste, ends_at = election
        # Vérifier vote déjà fait
        async with db.execute("SELECT 1 FROM votes WHERE election_id=? AND voter_id=?", (election_id, user.id)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Tu as déjà voté.")
                return
        # Vérifier que le candidat existe dans cette élection
        async with db.execute("SELECT 1 FROM candidates WHERE election_id=? AND user_id=?", (election_id, target_id)) as cur:
            if not await cur.fetchone():
                await update.message.reply_text("❌ Ce joueur n'est pas candidat.")
                return
        await db.execute(
            "INSERT INTO votes (election_id, voter_id, candidate_id, voted_at) VALUES (?,?,?,?)",
            (election_id, user.id, target_id, now())
        )
        await db.commit()
    await update.message.reply_text(f"✅ Vote enregistré pour {target['full_name']}.")

@require_registered
async def cmd_depouiller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clôture l'élection terminée, vérifie le quorum et déclare le vainqueur."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, poste, started_by FROM elections WHERE status='open' AND ends_at<=? ORDER BY started_at DESC LIMIT 1",
            (now(),)
        ) as cur:
            election = await cur.fetchone()
    if not election:
        await update.message.reply_text("❌ Aucune élection terminée en attente.")
        return
    election_id = election["id"]
    poste = election["poste"]
    await auto_depouiller(election_id, poste)
    await update.message.reply_text("✅ Élection dépouillée automatiquement.")  # en fait c'est déjà fait dans auto_depouiller, mais on renvoie un message

# ----------------------------------------------------------------------
# 4. LOIS
# ----------------------------------------------------------------------
@require_registered
@require_free
async def cmd_proposerloi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Propose une nouvelle loi (vote de tous les joueurs)."""
    user = update.effective_user
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /proposerloi <titre> | <description> [effet_type valeur]")
        return
    full = " ".join(context.args)
    try:
        title_part, rest = full.split("|", 1)
        title = title_part.strip()
        rest = rest.strip()
    except:
        title = "Loi"
        rest = full
    # On peut aussi supporter un effet simple
    effect_type = None
    effect_value = None
    # Exemple simple : /proposerloi "Taxe de luxe" | "Augmente les impôts" tax 5
    words = rest.split()
    if len(words) >= 2 and words[-2].lower() in ("tax", "subsidy", "policy"):
        effect_type = words[-2].lower()
        try:
            effect_value = int(words[-1])
            rest = " ".join(words[:-2])
        except:
            pass
    description = rest
    u = await get_user(user.id)
    if u["balance"] < LAW_PROPOSAL_COST:
        await update.message.reply_text(f"❌ Coût de proposition : {fmt_money(LAW_PROPOSAL_COST)}")
        return
    await update_balance(user.id, -LAW_PROPOSAL_COST)
    ends_at = now() + LAW_VOTE_DURATION
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO laws (title, description, proposer_id, effect_type, effect_value, status, created_at, voting_ends_at, required_majority) VALUES (?,?,?,?,?,?,?,?,?)",
            (title, description, user.id, effect_type, effect_value, 'voting', now(), ends_at, LAW_DEFAULT_MAJORITY)
        )
        await db.commit()
    await update.message.reply_text(
        card(f"📜 Proposition de loi : {title}",
             [f"📝 {description}",
              f"🗳️ Vote ouvert jusqu'au {fmt_duration(LAW_VOTE_DURATION)}",
              f"Majorité requise : {int(LAW_DEFAULT_MAJORITY*100)}%",
              f"Utilisez `/voterloi {title} pour` ou `/voterloi {title} contre`"],
             icon="⚖️", style="thick")
    )

@require_registered
async def cmd_voterloi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Vote pour ou contre une loi en cours."""
    user = update.effective_user
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /voterloi <titre> pour/contre")
        return
    title = " ".join(context.args[:-1])
    vote_choice = context.args[-1].lower()
    if vote_choice not in ("pour", "contre"):
        await update.message.reply_text("Choix : pour ou contre")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, status, voting_ends_at FROM laws WHERE title=? AND status='voting' AND voting_ends_at>?",
            (title, now())
        ) as cur:
            law = await cur.fetchone()
    if not law:
        await update.message.reply_text("❌ Loi introuvable ou vote terminé.")
        return
    law_id = law["id"]
    # Vérifier si déjà voté
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM law_votes WHERE law_id=? AND user_id=?", (law_id, user.id)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Tu as déjà voté sur cette loi.")
                return
        vote_value = 1 if vote_choice == "pour" else -1
        await db.execute(
            "INSERT INTO law_votes (law_id, user_id, vote, voted_at) VALUES (?,?,?,?)",
            (law_id, user.id, vote_value, now())
        )
        # Mettre à jour les compteurs (optionnel, peut être fait à la clôture)
        if vote_value == 1:
            await db.execute("UPDATE laws SET votes_for = votes_for + 1 WHERE id=?", (law_id,))
        else:
            await db.execute("UPDATE laws SET votes_against = votes_against + 1 WHERE id=?", (law_id,))
        await db.commit()
    await update.message.reply_text(f"✅ Vote enregistré : {vote_choice} pour la loi {title}.")

# ----------------------------------------------------------------------
# 5. GOUVERNEMENT (CABINET)
# ----------------------------------------------------------------------
@require_registered
async def cmd_nommer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nomme un ministre (réservé à l'élu principal)."""
    user = update.effective_user
    if len(context.args) < 2:
        await update.message.reply_text(f"Usage : /nommer @joueur <poste>\nPostes : {', '.join(CABINET_POSITIONS)}")
        return
    target_username = context.args[0][1:]
    position = context.args[1].title()
    if position not in CABINET_POSITIONS:
        await update.message.reply_text(f"❌ Poste inconnu. Choisis parmi : {', '.join(CABINET_POSITIONS)}")
        return
    # Vérifier que l'utilisateur est élu à un poste supérieur (Président ou Ministre principal)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT poste FROM political_offices WHERE occupant_id=?", (user.id,)) as cur:
            office = await cur.fetchone()
    if not office or office[0] not in ["Président", "Ministre"]:
        await update.message.reply_text("❌ Seuls le Président ou un Ministre peuvent nommer des ministres.")
        return
    # Récupérer target_id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id FROM users WHERE username LIKE ? LIMIT 1", (f"%{target_username}%",)) as cur:
            target = await cur.fetchone()
    if not target:
        await update.message.reply_text("❌ Joueur introuvable.")
        return
    target_id = target["user_id"]
    async with aiosqlite.connect(DB_PATH) as db:
        # Remplacer l'ancien occupant si nécessaire
        await db.execute("DELETE FROM cabinet_positions WHERE position=?", (position,))
        await db.execute(
            "INSERT INTO cabinet_positions (position, occupant_id, appointed_by, appointed_at) VALUES (?,?,?,?)",
            (position, target_id, user.id, now())
        )
        await db.commit()
    await update.message.reply_text(f"✅ {target_username} a été nommé(e) **{position}**.")

@require_registered
async def cmd_ministres(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le gouvernement actuel."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM cabinet_positions ORDER BY position") as cur:
            cabinet = await cur.fetchall()
    if not cabinet:
        await update.message.reply_text("Aucun ministre nommé pour le moment.")
        return
    lines = [f"**{c['position']}** : {c['occupant_id']}" for c in cabinet]
    await update.message.reply_text(bullet_list(lines), parse_mode="Markdown")

# ----------------------------------------------------------------------
# 6. RÉFÉRENDUMS
# ----------------------------------------------------------------------
@require_registered
@require_free
async def cmd_referendum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Propose un référendum (vote direct de tous)."""
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /referendum <question>")
        return
    question = " ".join(context.args)
    u = await get_user(user.id)
    if u["balance"] < REFERENDUM_COST:
        await update.message.reply_text(f"❌ Coût : {fmt_money(REFERENDUM_COST)}")
        return
    await update_balance(user.id, -REFERENDUM_COST)
    ends_at = now() + REFERENDUM_DURATION
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO referendums (question, proposer_id, created_at, voting_ends_at, status) VALUES (?,?,?,?,?)",
            (question, user.id, now(), ends_at, 'open')
        )
        await db.commit()
    await update.message.reply_text(
        card("🗳️ Référendum",
             [f"Question : {question}",
              f"Votez avec `/votereferendum pour` ou `/votereferendum contre`",
              f"Durée : {fmt_duration(REFERENDUM_DURATION)}"],
             icon="📢", style="thick")
    )

@require_registered
async def cmd_votereferendum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args or context.args[0].lower() not in ('pour', 'contre'):
        await update.message.reply_text("Usage : /votereferendum pour/contre")
        return
    choice = 1 if context.args[0].lower() == 'pour' else -1
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, status, voting_ends_at FROM referendums WHERE status='open' AND voting_ends_at>? LIMIT 1", (now(),)) as cur:
            ref = await cur.fetchone()
        if not ref:
            await update.message.reply_text("❌ Aucun référendum ouvert.")
            return
        ref_id = ref[0]
        # Vérifier double vote
        async with db.execute("SELECT 1 FROM referendum_votes WHERE referendum_id=? AND user_id=?", (ref_id, user.id)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Tu as déjà voté.")
                return
        await db.execute(
            "INSERT INTO referendum_votes (referendum_id, user_id, vote, voted_at) VALUES (?,?,?,?)",
            (ref_id, user.id, choice, now())
        )
        if choice == 1:
            await db.execute("UPDATE referendums SET votes_for = votes_for + 1 WHERE id=?", (ref_id,))
        else:
            await db.execute("UPDATE referendums SET votes_against = votes_against + 1 WHERE id=?", (ref_id,))
        await db.commit()
    await update.message.reply_text("✅ Vote enregistré.")

# ----------------------------------------------------------------------
# 7. DESTITUTION (MOTION)
# ----------------------------------------------------------------------
@require_registered
@require_free
@cooldown("last_motion", MOTION_COOLDOWN, "⏳ Tu as déjà lancé une motion récemment.")
async def cmd_destituer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args or not context.args[0].startswith('@'):
        await update.message.reply_text("Usage : /destituer @elu")
        return
    target_username = context.args[0][1:]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, full_name FROM users WHERE username LIKE ? LIMIT 1", (f"%{target_username}%",)) as cur:
            target = await cur.fetchone()
    if not target:
        await update.message.reply_text("❌ Joueur introuvable.")
        return
    target_id = target["user_id"]
    # Vérifier qu'il occupe un poste
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT poste FROM political_offices WHERE occupant_id=?", (target_id,)) as cur:
            office = await cur.fetchone()
    if not office:
        await update.message.reply_text("❌ Ce joueur n'occupe aucun poste.")
        return
    # Vérifier s'il y a une motion en cours
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT motion_id FROM motions WHERE target_id=? AND status='pending'", (target_id,)) as cur:
            if await cur.fetchone():
                await update.message.reply_text(f"❌ Une motion est déjà en cours contre {target['full_name']}.")
                return
    # Créer la motion
    ends_at = now() + MOTION_VOTE_DURATION
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO motions (target_id, initiated_by, initiated_at, ends_at, status, signatures_needed, signatures) VALUES (?,?,?,?,?,?,?)",
            (target_id, user.id, now(), ends_at, 'pending', MOTION_SIGNATURES_NEEDED, 0)
        )
        motion_id = cursor.lastrowid
        # Auto-signature de l'initiateur
        await db.execute(
            "INSERT INTO motion_signatures (motion_id, user_id, signed_at) VALUES (?,?,?)",
            (motion_id, user.id, now())
        )
        await db.execute("UPDATE motions SET signatures = signatures + 1 WHERE motion_id=?", (motion_id,))
        await db.commit()
    await update.message.reply_text(
        card(f"⚖️ Motion contre {target['full_name']}",
             [f"Poste : {office[0]}", f"Signatures nécessaires : {MOTION_SIGNATURES_NEEDED}",
              f"Les joueurs peuvent signer avec `/signer @{target_username}`",
              f"Délai : {fmt_duration(MOTION_VOTE_DURATION)}"],
             icon="⚠️", style="round")
    )

@require_registered
async def cmd_signer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args or not context.args[0].startswith('@'):
        await update.message.reply_text("Usage : /signer @elu")
        return
    target_username = context.args[0][1:]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id FROM users WHERE username LIKE ? LIMIT 1", (f"%{target_username}%",)) as cur:
            target = await cur.fetchone()
    if not target:
        await update.message.reply_text("❌ Joueur introuvable.")
        return
    target_id = target["user_id"]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT motion_id, signatures_needed, signatures FROM motions WHERE target_id=? AND status='pending'",
            (target_id,)
        ) as cur:
            motion = await cur.fetchone()
    if not motion:
        await update.message.reply_text("❌ Aucune motion de destitution active contre ce joueur.")
        return
    motion_id, needed, current = motion
    # Vérifier si déjà signé
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM motion_signatures WHERE motion_id=? AND user_id=?", (motion_id, user.id)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Tu as déjà signé.")
                return
        await db.execute(
            "INSERT INTO motion_signatures (motion_id, user_id, signed_at) VALUES (?,?,?)",
            (motion_id, user.id, now())
        )
        await db.execute("UPDATE motions SET signatures = signatures + 1 WHERE motion_id=?", (motion_id,))
        new_sig = current + 1
        await db.commit()
    if new_sig >= needed:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE motions SET status='voting' WHERE motion_id=?", (motion_id,))
            await db.commit()
        await update.message.reply_text(
            f"✅ Signatures suffisantes ! Un vote de destitution est ouvert pour 48h.\n"
            f"Votez avec `/vote_destitution pour` ou `/vote_destitution contre`."
        )
    else:
        await update.message.reply_text(f"✅ Signature enregistrée ({new_sig}/{needed}).")

@require_registered
async def cmd_vote_destitution(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args or context.args[0].lower() not in ('pour', 'contre'):
        await update.message.reply_text("Usage : `/vote_destitution pour` ou `/vote_destitution contre`")
        return
    choice = context.args[0].lower()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT motion_id, target_id, ends_at FROM motions WHERE status='voting' AND ends_at>? LIMIT 1",
            (now(),)
        ) as cur:
            motion = await cur.fetchone()
    if not motion:
        await update.message.reply_text("❌ Aucun vote de destitution actif.")
        return
    motion_id, target_id, ends_at = motion
    # Vérifier double vote
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM motion_votes WHERE motion_id=? AND user_id=?", (motion_id, user.id)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("❌ Tu as déjà voté.")
                return
        await db.execute(
            "INSERT INTO motion_votes (motion_id, user_id, vote, voted_at) VALUES (?,?,?,?)",
            (motion_id, user.id, choice, now())
        )
        await db.commit()
    await update.message.reply_text(f"✅ Vote enregistré : {choice}.")

# ----------------------------------------------------------------------
# 8. COMMANDES UTILITAIRES
# ----------------------------------------------------------------------
@require_registered
async def cmd_monposte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM political_offices WHERE occupant_id=?", (user.id,)) as cur:
            office = await cur.fetchone()
    if not office:
        await update.message.reply_text("Tu n'occupes aucun poste politique.")
        return
    await update.message.reply_text(
        card("🏛️ Ton mandat",
             [f"Poste : **{office['poste']}**",
              f"Élu le : {office['elected_at']}",
              f"Pour démissionner : `/demissionnerposte confirmer`"],
             icon="📜", style="thick")
    )

@require_registered
@require_free
async def cmd_demissionnerposte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args or context.args[0].lower() != "confirmer":
        await update.message.reply_text("⚠️ Confirme avec `/demissionnerposte confirmer`")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT poste FROM political_offices WHERE occupant_id=?", (user.id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text("❌ Tu n'occupes aucun poste.")
            return
        poste = row[0]
        await db.execute("DELETE FROM political_offices WHERE occupant_id=?", (user.id,))
        await db.commit()
    await update_field(user.id, "political_post", "")
    await update.message.reply_text(f"👋 Tu as démissionné de {poste}.")

@require_registered
async def cmd_postes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT po.poste, po.occupant_id, u.full_name FROM political_offices po JOIN users u ON u.user_id=po.occupant_id"
        ) as cur:
            offices = await cur.fetchall()
    if not offices:
        await update.message.reply_text("Aucun poste occupé.")
        return
    lines = [f"🏛️ **{o['poste']}** : {o['full_name']}" for o in offices]
    await update.message.reply_text(bullet_list(lines), parse_mode="Markdown")

@require_registered
async def cmd_candidats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche la liste des candidats pour une élection spécifique ou toutes."""
    args = context.args
    # Nettoyer l'argument (minuscules, suppression des espaces superflus)
    poste_filter = " ".join(args).strip().lower() if args else None

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if poste_filter:
            # Comparaison insensible à la casse (LOWER)
            async with db.execute(
                "SELECT id, poste FROM elections WHERE status='open' AND ends_at > ? AND LOWER(poste) = ? ORDER BY started_at DESC LIMIT 1",
                (now(), poste_filter)
            ) as cur:
                election = await cur.fetchone()
            if not election:
                # Lister les élections ouvertes pour aider
                async with db.execute("SELECT DISTINCT poste FROM elections WHERE status='open' AND ends_at > ?", (now(),)) as cur2:
                    open_postes = [r["poste"] for r in await cur2.fetchall()]
                if open_postes:
                    await update.message.reply_text(f"❌ Aucune élection ouverte pour ce poste.\nÉlections en cours : {', '.join(open_postes)}")
                else:
                    await update.message.reply_text("❌ Aucune élection ouverte actuellement.")
                return
            elections = [election]
        else:
            async with db.execute(
                "SELECT id, poste FROM elections WHERE status='open' AND ends_at > ? ORDER BY started_at ASC",
                (now(),)
            ) as cur:
                elections = await cur.fetchall()
            if not elections:
                await update.message.reply_text("Aucune élection ouverte.")
                return

        all_messages = []
        for election in elections:
            election_id = election["id"]
            poste = election["poste"]
            async with db.execute("""
                SELECT u.user_id, u.full_name, u.username, c.program
                FROM candidates c
                JOIN users u ON u.user_id = c.user_id
                WHERE c.election_id = ?
            """, (election_id,)) as cur:
                candidates = await cur.fetchall()
            if not candidates:
                lines = [f"🏛️ <b>Élection : {poste}</b>", "   Aucun candidat pour le moment."]
            else:
                lines = [f"🏛️ <b>Élection : {poste}</b>"]
                for idx, cand in enumerate(candidates, 1):
                    prog = (cand["program"][:100] + "…") if len(cand["program"]) > 100 else cand["program"]
                    username = cand["username"] or "inconnu"
                    lines.append(f"{idx}. <b>{cand['full_name']}</b> (@{username})\n   📝 {prog}")
            all_messages.append("\n".join(lines))

        await update.message.reply_text("\n\n━━━━━━━━━━━━━━━━━━━━━━\n\n".join(all_messages), parse_mode="HTML")

# ----------------------------------------------------------------------
# 9. MAINTENANCE AUTOMATIQUE (clôture des votes, application des résultats)
# ----------------------------------------------------------------------
async def auto_depouiller(election_id: int, poste: str):
    """
    Dépouille automatiquement une élection et attribue le poste.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Compter les votes
        async with db.execute(
            "SELECT candidate_id, COUNT(*) as votes FROM votes WHERE election_id=? GROUP BY candidate_id ORDER BY votes DESC",
            (election_id,)
        ) as cur:
            results = await cur.fetchall()

        async with db.execute(
            "SELECT COUNT(*) as total FROM votes WHERE election_id=?",
            (election_id,)
        ) as cur:
            total_votes = (await cur.fetchone())[0]

        total_players = await get_all_users_count()
        quorum_needed = int(total_players * ELECTION_QUORUM)

        if total_votes < quorum_needed or not results:
            await db.execute("UPDATE elections SET status='cancelled' WHERE id=?", (election_id,))
            await db.commit()
            # Notification éventuelle
            return

        winner = results[0]
        winner_id = winner["candidate_id"]
        winner_votes = winner["votes"]

        # Supprimer ancien occupant
        await db.execute("DELETE FROM political_offices WHERE poste=?", (poste,))
        await db.execute(
            "INSERT INTO political_offices (poste, occupant_id, elected_at, election_id, last_activity) VALUES (?,?,?,?,?)",
            (poste, winner_id, now(), election_id, now())
        )
        await db.execute("UPDATE elections SET status='closed' WHERE id=?", (election_id,))
        await update_field(winner_id, "political_post", poste)
        await increment_field(winner_id, "prestige", 50)
        await db.commit()

        # Notification au vainqueur
        try:
            await add_notification(winner_id, f"🏆 Tu as remporté l'élection pour {poste} avec {winner_votes} voix !")
        except:
            pass


async def political_maintenance():
    """
    À appeler périodiquement (toutes les heures) pour clôturer les votes expirés.
    Inclut le dépouillement automatique des élections.
    """
    now_ts = now()
    async with aiosqlite.connect(DB_PATH) as db:
        # 0. Dépouiller les élections expirées
        async with db.execute(
            "SELECT id, poste FROM elections WHERE status='open' AND ends_at <= ?",
            (now_ts,)
        ) as cur:
            expired_elections = await cur.fetchall()
        for election_id, poste in expired_elections:
            await auto_depouiller(election_id, poste)

        # 1. Clôturer les lois en vote expirées
        async with db.execute("SELECT id, votes_for, votes_against, required_majority FROM laws WHERE status='voting' AND voting_ends_at <= ?", (now_ts,)) as cur:
            expired_laws = await cur.fetchall()
        for law_id, votes_for, votes_against, majority in expired_laws:
            total = votes_for + votes_against
            if total > 0 and (votes_for / total) >= majority:
                await db.execute("UPDATE laws SET status='passed' WHERE id=?", (law_id,))
            else:
                await db.execute("UPDATE laws SET status='rejected' WHERE id=?", (law_id,))
        # 2. Clôturer les référendums expirés
        async with db.execute("SELECT id, votes_for, votes_against, required_majority FROM referendums WHERE status='open' AND voting_ends_at <= ?", (now_ts,)) as cur:
            expired_refs = await cur.fetchall()
        for ref_id, votes_for, votes_against, majority in expired_refs:
            total = votes_for + votes_against
            if total > 0 and (votes_for / total) >= majority:
                await db.execute("UPDATE referendums SET status='accepted' WHERE id=?", (ref_id,))
            else:
                await db.execute("UPDATE referendums SET status='rejected' WHERE id=?", (ref_id,))
        # 3. Motions de destitution expirées (status='voting')
        async with db.execute("SELECT motion_id, target_id FROM motions WHERE status='voting' AND ends_at <= ?", (now_ts,)) as cur:
            expired_motions = await cur.fetchall()
        for motion_id, target_id in expired_motions:
            async with db.execute("SELECT COUNT(*) as total, SUM(CASE WHEN vote='pour' THEN 1 ELSE 0 END) as pour FROM motion_votes WHERE motion_id=?", (motion_id,)) as cur2:
                stats = await cur2.fetchone()
            total = stats[0] or 0
            pour = stats[1] or 0
            if total > 0 and (pour / total) >= MOTION_MAJORITY:
                await db.execute("DELETE FROM political_offices WHERE occupant_id=?", (target_id,))
                await db.execute("UPDATE users SET political_post='' WHERE user_id=?", (target_id,))
                await db.execute("UPDATE motions SET status='accepted' WHERE motion_id=?", (motion_id,))
                await add_notification(target_id, "Tu as été destitué de ton poste politique par un vote populaire.")
            else:
                await db.execute("UPDATE motions SET status='rejected' WHERE motion_id=?", (motion_id,))
        await db.commit()


@require_registered
async def cmd_elections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liste toutes les élections en cours."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, poste, started_by, started_at, ends_at, "
            "(SELECT COUNT(*) FROM candidates WHERE election_id = elections.id) as nb_candidats "
            "FROM elections WHERE status='open' AND ends_at > ? ORDER BY ends_at ASC",
            (now(),)
        ) as cur:
            elections = await cur.fetchall()
    if not elections:
        await update.message.reply_text("Aucune élection en cours.")
        return
    lines = ["🗳️ **Élections en cours**\n"]
    for e in elections:
        time_left = e["ends_at"] - now()
        lines.append(
            f"🏛️ **{e['poste']}** — {fmt_duration(time_left)} restant\n"
            f"   📢 Candidats : {e['nb_candidats']}   |   `/candidats {e['poste']}`"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")