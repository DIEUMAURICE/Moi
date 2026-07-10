import io
import math
from datetime import datetime

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import ContextTypes

from config import CRAFTING_RECIPES, STARTING_BALANCE
from database import (
    DB_PATH,
    add_life_journal,
    get_inventory,
    get_user,
    increment_field,
    now,
    snapshot_user_core_stats,
    update_balance,
)
from utils.aesthetics import alert, card
from utils.decorators import cooldown, require_free, require_registered
from utils.helpers import escape_html, fmt, fmt_time

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    MATPLOTLIB_AVAILABLE = True
except Exception:
    MATPLOTLIB_AVAILABLE = False


RANKED_SEASON_DURATION = 30 * 86400
RANKED_START_RATING = 1000
RANKED_MIN_RATING = 100
RANKED_MAX_LEADERBOARD = 15

RANKED_REWARDS = {
    1: {"balance": 1_500_000, "xp": 2500, "prestige": 50, "badge": "🏆 Champion ranked"},
    2: {"balance": 750_000, "xp": 1500, "prestige": 30, "badge": "🥈 Finaliste ranked"},
    3: {"balance": 350_000, "xp": 900, "prestige": 15, "badge": "🥉 Podium ranked"},
}

TUTORIAL_STEPS = [
    {
        "title": "Bienvenue dans LifeSim",
        "description": "Découvre les raccourcis essentiels puis lance la première étape du parcours guidé.",
        "objective": "Lis les bases, puis valide cette étape pour recevoir ton pack de départ.",
        "tips": [
            "Commence par `/menu`, `/vie` et `/profil` pour voir l'état de ton personnage.",
            "Le tutoriel peut être repris à tout moment avec `/tutorial`.",
        ],
        "reward": {"balance": 5000},
        "validator": "always",
    },
    {
        "title": "Gagner tes premiers revenus",
        "description": "Utilise l'économie de base pour générer un premier revenu.",
        "objective": "Travaille au moins une fois avec `/travailler` ou augmente tes gains totaux.",
        "tips": [
            "Tu peux aussi utiliser `/quotidien` pour récupérer une récompense simple.",
            "Le but est d'avoir déjà commencé à faire tourner ton économie.",
        ],
        "reward": {"items": [("Potion de soin", 1)]},
        "validator": "economy",
    },
    {
        "title": "Monter en progression",
        "description": "Teste une action qui fait progresser ton personnage.",
        "objective": "Gagne un peu d'XP, monte d'un niveau, ou commence des études.",
        "tips": [
            "Essaye `/etudes`, `/etudier`, `/missions` ou un mini-jeu d'arène.",
            "Le tutoriel valide automatiquement si ton profil a déjà progressé.",
        ],
        "reward": {"items": [("Potion d'énergie", 1), ("Parchemin d'XP", 1)]},
        "validator": "progression",
    },
    {
        "title": "Obtenir un objet",
        "description": "Entre dans la boucle inventaire, loot et marché.",
        "objective": "Possède au moins un objet dans ton inventaire, puis valide l'étape.",
        "tips": [
            "Tu peux gagner des objets via arène, missions, casino ou récompenses du tutoriel.",
            "Consulte ensuite `/inventaire` pour voir tes objets disponibles.",
        ],
        "reward": {"items": [("Petit coffre", 1)]},
        "validator": "inventory",
    },
    {
        "title": "Découvrir le multijoueur",
        "description": "Teste un aspect compétitif ou coopératif du jeu.",
        "objective": "Fais au moins un duel d'arène, rejoins une compétition, ou lance la saison ranked.",
        "tips": [
            "Tu peux utiliser `/defier`, `/competition_join` ou `/ranked_join`.",
            "Les saisons ranked utilisent les duels PvP pour faire évoluer ta cote.",
        ],
        "reward": {"balance": 15000, "items": [("Potion de soin", 1), ("Potion d'énergie", 1)]},
        "validator": "multiplayer",
    },
]


def _ranked_tier(rating: int) -> tuple[str, str]:
    if rating >= 1800:
        return "Diamant", "💎"
    if rating >= 1500:
        return "Platine", "🔷"
    if rating >= 1300:
        return "Or", "🥇"
    if rating >= 1100:
        return "Argent", "🥈"
    return "Bronze", "🥉"


def _season_name_from_timestamp(ts: int) -> str:
    dt = datetime.utcfromtimestamp(ts)
    return f"Saison {dt.year}-{dt.month:02d}"


def _recipe_label(recipe_key: str, recipe: dict) -> str:
    return recipe.get("name") or recipe_key.replace("_", " ").title()


async def _fetch_item_by_name(db, item_name: str):
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT item_id, name, emoji, rarity, type, effect_type, effect_value, description, value FROM items WHERE name = ?",
        (item_name,),
    ) as cur:
        return await cur.fetchone()


async def _fetch_item_by_id(db, item_id: int):
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT item_id, name, emoji, rarity, type, effect_type, effect_value, description, value FROM items WHERE item_id = ?",
        (item_id,),
    ) as cur:
        return await cur.fetchone()


async def _get_inventory_quantity(db, user_id: int, item_id: int) -> int:
    async with db.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND item_id = ?",
        (user_id, item_id),
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def _add_item_to_inventory(db, user_id: int, item_id: int, item_name: str, quantity: int, item_type: str = ""):
    current_qty = await _get_inventory_quantity(db, user_id, item_id)
    if current_qty > 0:
        await db.execute(
            "UPDATE inventory SET quantity = quantity + ? WHERE user_id = ? AND item_id = ?",
            (quantity, user_id, item_id),
        )
    else:
        await db.execute(
            "INSERT INTO inventory (user_id, item_id, item_type, item_name, quantity, acquired_at) VALUES (?,?,?,?,?,?)",
            (user_id, item_id, item_type, item_name, quantity, now()),
        )


async def _remove_item_from_inventory(db, user_id: int, item_id: int, quantity: int) -> bool:
    current_qty = await _get_inventory_quantity(db, user_id, item_id)
    if current_qty < quantity:
        return False
    if current_qty == quantity:
        await db.execute("DELETE FROM inventory WHERE user_id = ? AND item_id = ?", (user_id, item_id))
    else:
        await db.execute(
            "UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND item_id = ?",
            (quantity, user_id, item_id),
        )
    return True


async def _insert_badge(db, user_id: int, badge: str):
    await db.execute(
        "INSERT INTO user_badges (user_id, badge, earned_at) VALUES (?, ?, ?)",
        (user_id, badge, now()),
    )


async def _finalize_ranked_season(db, season: dict):
    db.row_factory = aiosqlite.Row
    async with db.execute(
        """
        SELECT rs.user_id, rs.rating, rs.wins, rs.losses, rs.draws, u.full_name
        FROM ranked_stats rs
        JOIN users u ON u.user_id = rs.user_id
        WHERE rs.season_id = ?
        ORDER BY rs.rating DESC, rs.wins DESC, rs.peak_rating DESC, rs.joined_at ASC
        LIMIT 3
        """,
        (season["season_id"],),
    ) as cur:
        podium = [dict(r) for r in await cur.fetchall()]

    winner_user_id = podium[0]["user_id"] if podium else 0
    await db.execute(
        "UPDATE ranked_seasons SET active = 0, winner_user_id = ? WHERE season_id = ?",
        (winner_user_id, season["season_id"]),
    )
    await db.commit()

    for place, row in enumerate(podium, start=1):
        reward = RANKED_REWARDS.get(place)
        if not reward:
            continue
        if reward.get("balance"):
            await update_balance(row["user_id"], reward["balance"])
        if reward.get("xp"):
            await increment_field(row["user_id"], "xp", reward["xp"])
        if reward.get("prestige"):
            await increment_field(row["user_id"], "prestige", reward["prestige"])
        if reward.get("badge"):
            async with aiosqlite.connect(DB_PATH) as reward_db:
                await _insert_badge(reward_db, row["user_id"], reward["badge"])
                await reward_db.commit()


async def ensure_active_ranked_season() -> dict:
    current_ts = now()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT * FROM ranked_seasons WHERE active = 1 ORDER BY season_id DESC"
        ) as cur:
            active_rows = [dict(r) for r in await cur.fetchall()]

        for season in active_rows:
            if season["ends_at"] <= current_ts:
                await _finalize_ranked_season(db, season)

        async with db.execute(
            "SELECT * FROM ranked_seasons WHERE active = 1 AND ends_at > ? ORDER BY season_id DESC LIMIT 1",
            (current_ts,),
        ) as cur:
            active = await cur.fetchone()
        if active:
            return dict(active)

        starts_at = current_ts
        ends_at = starts_at + RANKED_SEASON_DURATION
        season_name = _season_name_from_timestamp(starts_at)
        cursor = await db.execute(
            """
            INSERT INTO ranked_seasons(name, starts_at, ends_at, active, winner_user_id, created_at)
            VALUES (?, ?, ?, 1, 0, ?)
            """,
            (season_name, starts_at, ends_at, current_ts),
        )
        await db.commit()

        async with db.execute("SELECT * FROM ranked_seasons WHERE season_id = ?", (cursor.lastrowid,)) as cur:
            return dict(await cur.fetchone())


async def _get_ranked_profile(user_id: int, season_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ranked_stats WHERE season_id = ? AND user_id = ?",
            (season_id, user_id),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def _get_or_create_tutorial_progress(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tutorial_progress WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return dict(row)

        await db.execute(
            """
            INSERT INTO tutorial_progress(user_id, step_index, completed, claimed_step, updated_at)
            VALUES (?, 0, 0, -1, ?)
            """,
            (user_id, now()),
        )
        await db.commit()

    return {"user_id": user_id, "step_index": 0, "completed": 0, "claimed_step": -1, "updated_at": now()}


async def _set_tutorial_progress(user_id: int, *, step_index: int, completed: int, claimed_step: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE tutorial_progress
            SET step_index = ?, completed = ?, claimed_step = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (step_index, completed, claimed_step, now(), user_id),
        )
        await db.commit()


async def _reset_tutorial_progress(user_id: int):
    progress = await _get_or_create_tutorial_progress(user_id)
    await _set_tutorial_progress(
        user_id,
        step_index=0,
        completed=0,
        claimed_step=max(progress.get("claimed_step", -1), len(TUTORIAL_STEPS) - 1 if progress.get("completed") else progress.get("claimed_step", -1)),
    )


async def _user_joined_active_competition(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT 1
            FROM competition_participants cp
            JOIN competitions c ON c.comp_id = cp.comp_id
            WHERE cp.user_id = ? AND c.ended = 0 AND c.ends_at > ?
            LIMIT 1
            """,
            (user_id, now()),
        ) as cur:
            row = await cur.fetchone()
    return bool(row)


async def _validate_tutorial_step(user_id: int, validator: str) -> tuple[bool, str]:
    if validator == "always":
        return True, "Étape d'introduction prête."

    u = await get_user(user_id)

    if validator == "economy":
        earned = u.get("total_earned", 0)
        if earned > 0 or u.get("balance", STARTING_BALANCE) > STARTING_BALANCE:
            return True, "Tu as déjà commencé à gagner de l'argent."
        return False, "Travaille une fois avec `/travailler` ou récupère ton `/quotidien`."

    if validator == "progression":
        if u.get("xp", 0) >= 50 or u.get("level", 1) >= 2 or bool(u.get("diplome")):
            return True, "Ta progression a bien été détectée."
        return False, "Gagne de l'XP ou commence tes études pour valider cette étape."

    if validator == "inventory":
        inventory = await get_inventory(user_id)
        if inventory:
            return True, "Ton inventaire contient déjà au moins un objet."
        return False, "Obtiens un objet, puis vérifie à nouveau avec `/inventaire`."

    if validator == "multiplayer":
        ranked_season = await ensure_active_ranked_season()
        ranked_profile = await _get_ranked_profile(user_id, ranked_season["season_id"])
        total_pvp = (u.get("arena_wins", 0) or 0) + (u.get("arena_losses", 0) or 0)
        if total_pvp > 0 or ranked_profile or await _user_joined_active_competition(user_id):
            return True, "Ton activité multijoueur a bien été repérée."
        return False, "Essaie `/defier`, `/competition_join` ou `/ranked_join`."

    return False, "Validateur inconnu."


async def _grant_tutorial_reward(user_id: int, reward: dict) -> list[str]:
    granted = []

    if reward.get("balance"):
        await update_balance(user_id, reward["balance"])
        granted.append(f"💰 {fmt(reward['balance'])}")

    items = reward.get("items", [])
    if items:
        async with aiosqlite.connect(DB_PATH) as db:
            for item_name, quantity in items:
                item = await _fetch_item_by_name(db, item_name)
                if not item:
                    continue
                await _add_item_to_inventory(db, user_id, item["item_id"], item["name"], quantity, item["type"])
                granted.append(f"{item['emoji']} {item['name']} ×{quantity}")
            await db.commit()

    return granted


def _tutorial_keyboard(completed: bool = False):
    if completed:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔄 Recommencer le tutoriel", callback_data="tutorial_reset")]]
        )
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Valider cette étape", callback_data="tutorial_validate")],
            [InlineKeyboardButton("🔄 Réinitialiser", callback_data="tutorial_reset")],
        ]
    )


async def _render_tutorial(update_or_query, user_id: int, edit: bool = False):
    progress = await _get_or_create_tutorial_progress(user_id)
    completed = bool(progress.get("completed"))

    if completed:
        body = [
            "Tu as terminé le parcours guidé de LifeSim.",
            "",
            "Commandes utiles pour continuer :",
            "  • `/crafting` pour transformer tes objets",
            "  • `/ranked` pour suivre la saison compétitive",
            "  • `/graphstats` pour visualiser ta progression",
            "  • `/menu` pour retrouver toutes les catégories",
        ]
        text = card("TUTORIEL TERMINÉ", body, icon="🎓", style="thick", footer="Tu peux le relancer si tu veux refaire le parcours.")
        reply_markup = _tutorial_keyboard(completed=True)
    else:
        step_index = min(progress["step_index"], len(TUTORIAL_STEPS) - 1)
        step = TUTORIAL_STEPS[step_index]
        body = [
            f"Étape <b>{step_index + 1}</b> / <b>{len(TUTORIAL_STEPS)}</b>",
            f"<b>{escape_html(step['title'])}</b>",
            "",
            escape_html(step["description"]),
            "",
            f"🎯 Objectif : {escape_html(step['objective'])}",
            "",
            "<b>Conseils</b>",
            *[f"  • {escape_html(tip)}" for tip in step.get("tips", [])],
        ]

        reward_preview = []
        if step["reward"].get("balance"):
            reward_preview.append(f"💰 {fmt(step['reward']['balance'])}")
        for item_name, quantity in step["reward"].get("items", []):
            reward_preview.append(f"📦 {item_name} ×{quantity}")
        if reward_preview:
            body.extend(["", "<b>Récompense</b>", f"  • {' · '.join(reward_preview)}"])

        text = card("TUTORIEL INTERACTIF", body, icon="🎓", style="thick", footer="Valide l'étape quand les conditions sont remplies.")
        reply_markup = _tutorial_keyboard(completed=False)

    if edit and hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    elif hasattr(update_or_query, "reply_text"):
        await update_or_query.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)


async def apply_ranked_match_result(
    winner_id: int | None,
    loser_id: int | None,
    *,
    is_draw: bool = False,
    source: str = "arena",
) -> dict | None:
    season = await ensure_active_ranked_season()

    if not winner_id or not loser_id:
        return None

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM ranked_stats
            WHERE season_id = ? AND user_id IN (?, ?)
            ORDER BY user_id
            """,
            (season["season_id"], winner_id, loser_id),
        ) as cur:
            profiles = [dict(r) for r in await cur.fetchall()]

        if len(profiles) != 2:
            return None

        profile_by_user = {row["user_id"]: row for row in profiles}
        p1 = profile_by_user[winner_id]
        p2 = profile_by_user[loser_id]

        r1 = p1["rating"]
        r2 = p2["rating"]
        expected_1 = 1 / (1 + math.pow(10, (r2 - r1) / 400))
        expected_2 = 1 / (1 + math.pow(10, (r1 - r2) / 400))

        games_1 = p1["wins"] + p1["losses"] + p1["draws"]
        games_2 = p2["wins"] + p2["losses"] + p2["draws"]
        k1 = 40 if games_1 < 10 else 32
        k2 = 40 if games_2 < 10 else 32

        score_1 = 0.5 if is_draw else 1.0
        score_2 = 0.5 if is_draw else 0.0

        new_r1 = max(RANKED_MIN_RATING, int(round(r1 + k1 * (score_1 - expected_1))))
        new_r2 = max(RANKED_MIN_RATING, int(round(r2 + k2 * (score_2 - expected_2))))
        delta_1 = new_r1 - r1
        delta_2 = new_r2 - r2

        if is_draw:
            await db.execute(
                """
                UPDATE ranked_stats
                SET rating = ?, draws = draws + 1, peak_rating = MAX(peak_rating, ?), last_match_at = ?
                WHERE season_id = ? AND user_id = ?
                """,
                (new_r1, new_r1, now(), season["season_id"], winner_id),
            )
            await db.execute(
                """
                UPDATE ranked_stats
                SET rating = ?, draws = draws + 1, peak_rating = MAX(peak_rating, ?), last_match_at = ?
                WHERE season_id = ? AND user_id = ?
                """,
                (new_r2, new_r2, now(), season["season_id"], loser_id),
            )
        else:
            await db.execute(
                """
                UPDATE ranked_stats
                SET rating = ?, wins = wins + 1, peak_rating = MAX(peak_rating, ?), last_match_at = ?
                WHERE season_id = ? AND user_id = ?
                """,
                (new_r1, new_r1, now(), season["season_id"], winner_id),
            )
            await db.execute(
                """
                UPDATE ranked_stats
                SET rating = ?, losses = losses + 1, peak_rating = MAX(peak_rating, ?), last_match_at = ?
                WHERE season_id = ? AND user_id = ?
                """,
                (new_r2, new_r2, now(), season["season_id"], loser_id),
            )

        await db.execute(
            """
            INSERT INTO ranked_match_history
            (season_id, source, winner_id, loser_id, is_draw, winner_delta, loser_delta, played_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                season["season_id"],
                source,
                0 if is_draw else winner_id,
                0 if is_draw else loser_id,
                1 if is_draw else 0,
                delta_1,
                delta_2,
                now(),
            ),
        )
        await db.commit()

    return {
        "season_name": season["name"],
        "winner_id": winner_id,
        "loser_id": loser_id,
        "winner_delta": delta_1,
        "loser_delta": delta_2,
        "draw": is_draw,
    }


def format_ranked_result_note(result: dict | None) -> str:
    if not result:
        return ""
    if result["draw"]:
        return f"\n🏅 Ranked : match classé pris en compte ({result['winner_delta']:+d} / {result['loser_delta']:+d})"
    return f"\n🏅 Ranked : cote mise à jour ({result['winner_delta']:+d} / {result['loser_delta']:+d})"


@require_registered
async def cmd_crafting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    inventory = await get_inventory(user.id)
    quantities = {row.get("item_id"): row.get("quantity", 0) for row in inventory}

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        body = []
        for recipe_key, recipe in CRAFTING_RECIPES.items():
            parts = []
            craftable = True
            for ingredient in recipe["ingredients"]:
                item = await _fetch_item_by_name(db, ingredient["item"])
                if not item:
                    craftable = False
                    parts.append(f"❓ {ingredient['item']} ×{ingredient['quantity']}")
                    continue
                owned = quantities.get(item["item_id"], 0)
                icon = "✅" if owned >= ingredient["quantity"] else "❌"
                craftable = craftable and owned >= ingredient["quantity"]
                parts.append(f"{icon} {item['emoji']} {item['name']} {owned}/{ingredient['quantity']}")

            output = recipe["output"]
            status = "PRÊT" if craftable else "MANQUE DES RESSOURCES"
            body.extend(
                [
                    f"<b>{escape_html(_recipe_label(recipe_key, recipe))}</b> → {escape_html(output['emoji'])} {escape_html(output['name'])} ×{output.get('quantity', 1)}",
                    f"  {escape_html(output['description'])}",
                    f"  Ingrédients : {' · '.join(parts)}",
                    f"  Utilisation : <code>/craft {recipe_key} [quantité]</code> · Statut : <b>{status}</b>",
                    "",
                ]
            )

    if not body:
        await update.message.reply_text(alert("info", "Aucune recette de crafting n'est disponible."), parse_mode="HTML")
        return

    await update.message.reply_text(
        card("CRAFTING", body[:-1], icon="🛠️", style="thick", footer="Les recettes utilisent les objets déjà présents dans ton inventaire."),
        parse_mode="HTML",
    )


@require_registered
@require_free
@cooldown("craft_cooldown", 3, "⏳ Attends quelques secondes avant de relancer un craft.")
async def cmd_craft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            card(
                "CRAFT",
                [
                    "Utilisation : <code>/craft [recette] [quantité]</code>",
                    "Exemple : <code>/craft kit_soins 2</code>",
                    "Liste des recettes : <code>/crafting</code>",
                ],
                icon="🛠️",
                style="thick",
            ),
            parse_mode="HTML",
        )
        return

    recipe_key = context.args[0].lower().strip()
    recipe = CRAFTING_RECIPES.get(recipe_key)
    if not recipe:
        await update.message.reply_text(alert("error", "Recette inconnue. Utilise `/crafting` pour voir la liste."), parse_mode="HTML")
        return

    try:
        quantity = int(context.args[1]) if len(context.args) > 1 else 1
        if quantity <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(alert("error", "La quantité doit être un entier positif."), parse_mode="HTML")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        resolved_ingredients = []
        missing = []
        for ingredient in recipe["ingredients"]:
            item = await _fetch_item_by_name(db, ingredient["item"])
            if not item:
                await update.message.reply_text(
                    alert("error", f"L'objet requis `{ingredient['item']}` n'existe pas en base."),
                    parse_mode="HTML",
                )
                return
            needed = ingredient["quantity"] * quantity
            owned = await _get_inventory_quantity(db, user.id, item["item_id"])
            if owned < needed:
                missing.append(f"{item['emoji']} {item['name']} {owned}/{needed}")
            resolved_ingredients.append((dict(item), needed))

        if missing:
            await update.message.reply_text(
                card(
                    "CRAFT IMPOSSIBLE",
                    [
                        f"Recette : <b>{escape_html(_recipe_label(recipe_key, recipe))}</b>",
                        "Ressources manquantes :",
                        *[f"  • {escape_html(line)}" for line in missing],
                    ],
                    icon="❌",
                    style="thick",
                ),
                parse_mode="HTML",
            )
            return

        for item, needed in resolved_ingredients:
            await _remove_item_from_inventory(db, user.id, item["item_id"], needed)

        output_def = recipe["output"]
        output_item = await _fetch_item_by_name(db, output_def["name"])
        if not output_item:
            await update.message.reply_text(
                alert("error", "L'objet fabriqué n'existe pas encore en base. Redémarre le bot pour relancer l'initialisation."),
                parse_mode="HTML",
            )
            return

        produced_qty = output_def.get("quantity", 1) * quantity
        await _add_item_to_inventory(db, user.id, output_item["item_id"], output_item["name"], produced_qty, output_item["type"])
        await db.commit()

    await add_life_journal(
        user.id,
        "crafting",
        f"Fabrication de {produced_qty}x {output_def['emoji']} {output_def['name']} via la recette {recipe_key}.",
        severity="success",
    )

    await update.message.reply_text(
        card(
            "CRAFT RÉUSSI",
            [
                f"Recette : <b>{escape_html(_recipe_label(recipe_key, recipe))}</b>",
                f"Résultat : {output_def['emoji']} <b>{escape_html(output_def['name'])}</b> ×<b>{produced_qty}</b>",
                f"Effet : {escape_html(output_def['description'])}",
            ],
            icon="🛠️",
            style="thick",
            footer="Retrouve l'objet dans `/inventaire` puis utilise `/useitem [id]` si c'est un consommable.",
        ),
        parse_mode="HTML",
    )


@require_registered
async def cmd_ranked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    season = await ensure_active_ranked_season()
    profile = await _get_ranked_profile(user.id, season["season_id"])

    body = [
        f"Saison active : <b>{escape_html(season['name'])}</b>",
        f"⏳ Fin dans : <b>{fmt_time(max(0, season['ends_at'] - now()))}</b>",
        "",
    ]

    if profile:
        total_games = profile["wins"] + profile["losses"] + profile["draws"]
        tier_name, tier_icon = _ranked_tier(profile["rating"])
        body.extend(
            [
                f"{tier_icon} Ligue : <b>{tier_name}</b>",
                f"🏅 Cote : <b>{profile['rating']}</b> · Pic : <b>{profile['peak_rating']}</b>",
                f"⚔️ Bilan : <b>{profile['wins']}V</b> / <b>{profile['losses']}D</b> / <b>{profile['draws']}N</b>",
                f"🎮 Matchs joués : <b>{total_games}</b>",
                "",
                "Pour faire évoluer ta cote, joue des duels PvP contre un autre joueur inscrit à la saison.",
            ]
        )
    else:
        body.extend(
            [
                "Tu n'es pas encore inscrit à cette saison compétitive.",
                "",
                "Étapes :",
                "  1. Utilise <code>/ranked_join</code>",
                "  2. Lance ou accepte un duel avec <code>/defier</code>",
                "  3. Consulte ensuite <code>/ranked_leaderboard</code>",
            ]
        )

    await update.message.reply_text(
        card("SAISON RANKED", body, icon="🏅", style="thick", footer="Historique : `/ranked_history`"),
        parse_mode="HTML",
    )


@require_registered
async def cmd_ranked_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    season = await ensure_active_ranked_season()
    existing = await _get_ranked_profile(user.id, season["season_id"])
    if existing:
        await update.message.reply_text(
            alert("info", f"Tu es déjà inscrit à <b>{escape_html(season['name'])}</b> avec une cote de <b>{existing['rating']}</b>."),
            parse_mode="HTML",
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO ranked_stats(season_id, user_id, rating, wins, losses, draws, peak_rating, joined_at, last_match_at)
            VALUES (?, ?, ?, 0, 0, 0, ?, ?, 0)
            """,
            (season["season_id"], user.id, RANKED_START_RATING, RANKED_START_RATING, now()),
        )
        await db.commit()

    await add_life_journal(
        user.id,
        "ranked",
        f"Inscription à la saison classée {season['name']} avec une cote initiale de {RANKED_START_RATING}.",
        severity="info",
    )
    await update.message.reply_text(
        card(
            "INSCRIPTION RANKED",
            [
                f"Tu rejoins <b>{escape_html(season['name'])}</b>.",
                f"🏅 Cote initiale : <b>{RANKED_START_RATING}</b>",
                "Joue maintenant un duel PvP contre un autre joueur inscrit pour faire évoluer ta cote.",
            ],
            icon="🏅",
            style="thick",
        ),
        parse_mode="HTML",
    )


@require_registered
async def cmd_ranked_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    season = await ensure_active_ranked_season()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT rs.user_id, rs.rating, rs.wins, rs.losses, rs.draws, u.full_name
            FROM ranked_stats rs
            JOIN users u ON u.user_id = rs.user_id
            WHERE rs.season_id = ?
            ORDER BY rs.rating DESC, rs.wins DESC, rs.peak_rating DESC
            LIMIT ?
            """,
            (season["season_id"], RANKED_MAX_LEADERBOARD),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text(
            alert("info", "Personne n'est encore inscrit à la saison ranked actuelle."),
            parse_mode="HTML",
        )
        return

    medals = ["🥇", "🥈", "🥉"]
    body = [f"Saison : <b>{escape_html(season['name'])}</b>", ""]
    for index, row in enumerate(rows, start=1):
        badge = medals[index - 1] if index <= 3 else f"#{index}"
        tier_name, tier_icon = _ranked_tier(row["rating"])
        body.append(
            f"{badge} <b>{escape_html(row['full_name'])}</b> · {tier_icon} {tier_name} · {row['rating']} · {row['wins']}V/{row['losses']}D/{row['draws']}N"
        )

    await update.message.reply_text(
        card("CLASSEMENT RANKED", body, icon="🏆", style="thick"),
        parse_mode="HTML",
    )


@require_registered
async def cmd_ranked_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT s.name, s.starts_at, s.ends_at, s.winner_user_id,
                   rs.rating, rs.peak_rating, rs.wins, rs.losses, rs.draws
            FROM ranked_stats rs
            JOIN ranked_seasons s ON s.season_id = rs.season_id
            WHERE rs.user_id = ?
            ORDER BY s.ends_at DESC, s.season_id DESC
            LIMIT 10
            """,
            (user.id,),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text(alert("info", "Tu n'as encore aucun historique ranked."), parse_mode="HTML")
        return

    body = []
    for row in rows:
        status = "active" if row["ends_at"] > now() else "terminée"
        tier_name, tier_icon = _ranked_tier(row["rating"])
        body.extend(
            [
                f"<b>{escape_html(row['name'])}</b> · {status}",
                f"  {tier_icon} {tier_name} · Cote finale <b>{row['rating']}</b> · Pic <b>{row['peak_rating']}</b>",
                f"  Bilan : {row['wins']}V/{row['losses']}D/{row['draws']}N",
                "",
            ]
        )

    await update.message.reply_text(
        card("HISTORIQUE RANKED", body[:-1], icon="📜", style="thick"),
        parse_mode="HTML",
    )


@require_registered
async def cmd_tutorial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if context.args and context.args[0].lower() == "reset":
        await _reset_tutorial_progress(user.id)
    await _render_tutorial(update.message, user.id)


async def tutorial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    action = query.data.replace("tutorial_", "", 1)

    if action == "reset":
        await _reset_tutorial_progress(user_id)
        await _render_tutorial(query, user_id, edit=True)
        return

    if action != "validate":
        await query.answer("Action inconnue.", show_alert=True)
        return

    progress = await _get_or_create_tutorial_progress(user_id)
    if progress.get("completed"):
        await _render_tutorial(query, user_id, edit=True)
        return

    step_index = min(progress["step_index"], len(TUTORIAL_STEPS) - 1)
    step = TUTORIAL_STEPS[step_index]
    ok, detail = await _validate_tutorial_step(user_id, step["validator"])

    if not ok:
        await query.answer(detail, show_alert=True)
        return

    rewards = []
    already_claimed = progress.get("claimed_step", -1) >= step_index
    if not already_claimed:
        rewards = await _grant_tutorial_reward(user_id, step["reward"])
    next_index = step_index + 1
    completed = 1 if next_index >= len(TUTORIAL_STEPS) else 0
    await _set_tutorial_progress(
        user_id,
        step_index=min(next_index, len(TUTORIAL_STEPS) - 1),
        completed=completed,
        claimed_step=step_index,
    )
    await add_life_journal(
        user_id,
        "tutorial",
        f"Étape tutoriel validée : {step['title']}. Récompenses : {', '.join(rewards) if rewards else 'aucune'}.",
        severity="success",
    )

    await query.answer("Étape validée avec succès.", show_alert=False)
    await _render_tutorial(query, user_id, edit=True)
    if rewards:
        await query.message.reply_text(
            card(
                "RÉCOMPENSES DU TUTORIEL",
                [f"  • {escape_html(reward)}" for reward in rewards],
                icon="🎁",
                style="thick",
                footer=detail,
            ),
            parse_mode="HTML",
        )


@require_registered
async def cmd_graphstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not MATPLOTLIB_AVAILABLE:
        await update.message.reply_text(
            alert("error", "Matplotlib n'est pas disponible. Installe les dépendances du projet pour activer `/graphstats`."),
            parse_mode="HTML",
        )
        return

    user = update.effective_user
    await snapshot_user_core_stats(user.id)

    stat_filter = "all"
    period_days = 30

    for arg in context.args:
        lower = arg.lower()
        if lower in {"all", "balance", "xp", "prestige"}:
            stat_filter = lower
        elif lower.isdigit():
            period_days = max(1, min(180, int(lower)))

    names = ["balance", "xp", "prestige"] if stat_filter == "all" else [stat_filter]
    current_ts = now()
    from_ts = current_ts - period_days * 86400

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        series = {}
        for stat_name in names:
            async with db.execute(
                """
                SELECT recorded_at, stat_value
                FROM user_stat_history
                WHERE user_id = ? AND stat_name = ? AND recorded_at >= ?
                ORDER BY recorded_at ASC
                """,
                (user.id, stat_name, from_ts),
            ) as cur:
                rows = await cur.fetchall()
            if not rows:
                continue
            series[stat_name] = rows

    if not series:
        await update.message.reply_text(
            alert("info", "Pas assez d'historique pour générer un graphique sur cette période."),
            parse_mode="HTML",
        )
        return

    fig, axes = plt.subplots(len(series), 1, figsize=(10, 3.6 * len(series)), squeeze=False)
    palette = {"balance": "#22c55e", "xp": "#3b82f6", "prestige": "#f59e0b"}
    titles = {"balance": "Richesse", "xp": "XP", "prestige": "Prestige"}

    for axis, (stat_name, rows) in zip(axes.flatten(), series.items()):
        x_values = [datetime.fromtimestamp(r["recorded_at"]) for r in rows]
        y_values = [r["stat_value"] for r in rows]
        axis.plot(x_values, y_values, marker="o", linewidth=2.2, markersize=4, color=palette[stat_name])
        axis.fill_between(x_values, y_values, color=palette[stat_name], alpha=0.12)
        axis.set_title(titles.get(stat_name, stat_name).capitalize(), fontsize=12, loc="left")
        axis.grid(True, linestyle="--", alpha=0.25)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{int(value):,}".replace(",", " ")))
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))

    plt.tight_layout()

    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    output.seek(0)

    await update.message.reply_photo(
        photo=InputFile(output, filename="graphstats.png"),
        caption=card(
            "GRAPHIQUES DE STATS",
            [
                f"Période : <b>{period_days} jour(s)</b>",
                f"Courbes : <b>{', '.join(titles.get(name, name) for name in series.keys())}</b>",
                "Utilisation : <code>/graphstats [all|balance|xp|prestige] [jours]</code>",
            ],
            icon="📈",
            style="thick",
        ),
        parse_mode="HTML",
    )
