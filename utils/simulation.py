"""
utils/simulation.py — LIFESIM ULTRA V2
═══════════════════════════════════════════════════════════════════════
Moteur de simulation passive OPTIMISÉ.
Désormais équipé de retry automatique en cas de verrouillage de base.
PHASE 2 : Réduction des gains passifs (intérêts, salaire), pénalités santé.
"""
import aiosqlite
import random
import asyncio
import logging
from config import DB_PATH
from utils.helpers import now, clamp, lifestyle_score, fmt_time
from utils.aesthetics import card, alert, fmt_money, age_stage

logger = logging.getLogger(__name__)


def compute_condition_penalties(u: dict) -> dict:
    """Calcule des multiplicateurs cohérents selon l'état global du joueur."""
    hunger = int(u.get("hunger", 100) or 0)
    energy = int(u.get("energy", 100) or 0)
    stress = int(u.get("stress", 0) or 0)
    health = int(u.get("health", 100) or 0)
    happiness = int(u.get("happiness", 100) or 0)
    score = int(lifestyle_score(u))

    productivity = 1.0
    recovery = 1.0
    passive_income = 1.0
    risk = 0
    warnings = []

    if hunger < 35:
        productivity *= 0.88
        passive_income *= 0.92
        risk += 1
        warnings.append("faim")
    if hunger < 15:
        productivity *= 0.78
        recovery *= 0.92
        risk += 1

    if energy < 35:
        productivity *= 0.84
        passive_income *= 0.94
        risk += 1
        warnings.append("fatigue")
    if energy < 15:
        productivity *= 0.72
        recovery *= 0.85
        risk += 1

    if stress > 65:
        productivity *= 0.86
        recovery *= 0.90
        risk += 1
        warnings.append("stress")
    if stress > 85:
        productivity *= 0.75
        passive_income *= 0.90
        risk += 1

    if health < 45:
        productivity *= 0.82
        recovery *= 0.90
        passive_income *= 0.88
        risk += 1
        warnings.append("santé fragile")
    if health < 20:
        productivity *= 0.68
        passive_income *= 0.78
        risk += 1

    if happiness < 30:
        productivity *= 0.92
        warnings.append("moral bas")
    if happiness < 15:
        productivity *= 0.86
        risk += 1

    if score < 45:
        productivity *= 0.90
        passive_income *= 0.92
    if score < 25:
        productivity *= 0.82
        passive_income *= 0.85
        recovery *= 0.88
        risk += 1

    severe_combo = risk >= 4 or (stress > 88 and energy < 18) or (hunger < 10 and health < 25)
    collapse_risk = severe_combo and (health < 18 or energy < 10 or hunger < 8)

    return {
        "score": score,
        "productivity_mult": max(0.45, round(productivity, 2)),
        "passive_income_mult": max(0.40, round(passive_income, 2)),
        "recovery_mult": max(0.55, round(recovery, 2)),
        "risk": risk,
        "warnings": warnings,
        "severe_combo": severe_combo,
        "collapse_risk": collapse_risk,
    }

# ─── ÉVÉNEMENTS PASSIFS DE VIE (inchangés) ─────────────────────────
PASSIVE_LIFE_EVENTS = [
    {
        "id": "old_friend_call",
        "weight": 8,
        "title": "📞 Un vieil ami t'a appelé",
        "narrative": "Un ami perdu de vue t'a appelé pour prendre des nouvelles.",
        "happiness": 5, "stress": -5,
    },
    {
        "id": "stress_at_work",
        "weight": 10,
        "title": "📊 Coup de pression au boulot",
        "narrative": "Une deadline impossible t'a tenu éveillé toute la nuit.",
        "stress": 8, "energy": -10,
    },
    {
        "id": "neighbor_argument",
        "weight": 6,
        "title": "🏠 Engueulade avec un voisin",
        "narrative": "Une dispute pour du bruit a éclaté.",
        "stress": 6, "happiness": -4,
    },
    {
        "id": "lucky_find",
        "weight": 5,
        "title": "💰 Trouvaille chanceuse",
        "narrative": "Tu as trouvé un billet oublié dans une vieille veste.",
        "money": (50, 500),
    },
    {
        "id": "minor_cold",
        "weight": 7,
        "title": "🤧 Petit rhume",
        "narrative": "Un coup de froid t'a affaibli quelques jours.",
        "health": -4, "energy": -5,
    },
    {
        "id": "great_sleep",
        "weight": 6,
        "title": "😴 Excellente nuit",
        "narrative": "Tu as dormi comme un bébé, tu te sens régénéré.",
        "energy": 15, "stress": -8,
    },
    {
        "id": "spontaneous_idea",
        "weight": 4,
        "title": "💡 Éclair de génie",
        "narrative": "Une idée brillante t'a traversé l'esprit.",
        "xp": (20, 80),
    },
    {
        "id": "social_invite",
        "weight": 5,
        "title": "🥂 Invitation à une soirée",
        "narrative": "Tu as été invité(e) à une fête sympa.",
        "happiness": 8, "stress": -3,
    },
    {
        "id": "bad_news",
        "weight": 4,
        "title": "📰 Mauvaise nouvelle",
        "narrative": "Une nouvelle déprimante a tourné en boucle dans ta tête.",
        "happiness": -6, "stress": 5,
    },
    {
        "id": "compliment",
        "weight": 6,
        "title": "🌹 Compliment marquant",
        "narrative": "Quelqu'un t'a fait un compliment qui t'a touché(e).",
        "happiness": 6,
    },
]

# ─── SAISONS ────────────────────────────────────────────────────────
def current_season() -> str:
    import datetime
    m = datetime.datetime.utcnow().month
    if m in (12, 1, 2): return "hiver"
    if m in (3, 4, 5):  return "printemps"
    if m in (6, 7, 8):  return "été"
    return "automne"

SEASON_EFFECTS = {
    "hiver":     {"happiness": -2, "energy": -1, "icon": "❄️"},
    "printemps": {"happiness": +3, "energy": +2, "icon": "🌸"},
    "été":       {"happiness": +2, "energy": +1, "icon": "☀️"},
    "automne":   {"happiness": -1, "energy": 0,  "icon": "🍂"},
}

# ─── VIEILLISSEMENT (1 an tous les 7 jours) ────────────────────────
def years_passed_since(timestamp: int) -> int:
    days = (now() - timestamp) // 86400
    return days // 7

# ─── FONCTION DE RETRY ──────────────────────────────────────────────
async def _execute_with_retry(db_fn, max_retries=2):
    """Exécute avec retry (2 tentatives max, délai de 0.2s)."""
    for attempt in range(max_retries):
        try:
            return await db_fn()
        except aiosqlite.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                wait = 0.2 * (attempt + 1)  # 0.2s puis 0.4s
                logger.warning(f"⚠️ DB locked, retry {attempt+1}/{max_retries} après {wait}s")
                await asyncio.sleep(wait)
            else:
                raise
        except Exception:
            raise

# ─── SIMULATION PRINCIPALE OPTIMISÉE AVEC RETRY ─────────────────────
async def apply_passive_simulation(user_id: int) -> dict:
    """
    Applique une simulation passive avec retry automatique.
    Retourne un dict avec 'alert' si des changements notables sont survenus.
    PHASE 2 : Réduction des gains passifs (salaire divisé par 3, intérêts divisés par 5).
    """

    async def _simulate():
        async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT user_id, registered, balance, xp, level, karma, prestige,
                       health, energy, hunger, happiness, stress, age, job, diplome,
                       prison_until, hospital_until, frozen_until, last_life_tick,
                       last_seen, created_at, sleep_last, lifestyle_score
                FROM users WHERE user_id=?
            """, (user_id,)) as cur:
                row = await cur.fetchone()
            if not row or not row["registered"]:
                return {"changed": False}
            row = dict(row)

            current = now()
            last_tick = max(row["last_life_tick"], row["last_seen"], row["created_at"])
            elapsed = current - last_tick

            # Seuil minimal : 30 minutes
            if elapsed < 1800:
                return {"changed": False}

            hours = min(168, elapsed // 3600)
            if hours < 1:
                return {"changed": False}

            penalties = compute_condition_penalties(row)

            # ─── Calcul des variations ─────────────────────────────────
            hunger_loss   = min(60, hours * 4)
            energy_gain   = min(45, hours * 4)
            stress_loss   = min(row["stress"], int(hours * 2.5))
            happiness_delta = min(8, hours // 4)

            season = current_season()
            season_fx = SEASON_EFFECTS[season]
            happiness_delta += season_fx["happiness"]
            energy_gain += season_fx["energy"]

            new_hunger    = clamp(row["hunger"] - hunger_loss, 0, 100)
            new_energy    = clamp(row["energy"] + energy_gain, 0, 100)
            new_stress    = clamp(row["stress"] - stress_loss, 0, 100)
            new_happiness = clamp(row["happiness"] + happiness_delta, 0, 100)
            new_health    = clamp(row["health"], 0, 100)
            balance_delta = 0
            xp_delta = 0

            # Pénalités faim
            if new_hunger <= 30:
                new_happiness = clamp(new_happiness - 8, 0, 100)
                new_stress = clamp(new_stress + 6, 0, 100)
            if new_hunger <= 10:
                new_health = clamp(new_health - 10, 0, 100)

            # Stress chronique
            if new_stress >= 75:
                new_happiness = clamp(new_happiness - 6, 0, 100)
                new_health = clamp(new_health - 3, 0, 100)

            if penalties["severe_combo"]:
                new_health = clamp(new_health - 6, 0, 100)
                new_happiness = clamp(new_happiness - 8, 0, 100)
                new_stress = clamp(new_stress + 5, 0, 100)
                xp_delta -= 12

            # Insomnie (pas dormi depuis > 2 jours)
            sleep_last = row["sleep_last"] or 0
            if sleep_last and (current - sleep_last) > 86400 * 2:
                new_energy = clamp(new_energy - 10, 0, 100)
                new_stress = clamp(new_stress + 5, 0, 100)

            # Décrémentation des peines (prison/hôpital)
            prison_until = row["prison_until"] or 0
            hospital_until = row["hospital_until"] or 0
            frozen_until = row["frozen_until"] or 0

            if prison_until > 0:
                prison_until = max(0, prison_until - elapsed)
            if hospital_until > 0:
                hospital_until = max(0, hospital_until - elapsed)
            if frozen_until > 0:
                frozen_until = max(0, frozen_until - elapsed)

            # Vieillissement (seulement si pas gelé et pas en prison)
            if frozen_until <= 0 and prison_until <= 0:
                created = row["created_at"]
                new_age = max(row["age"], 18 + years_passed_since(created))
            else:
                new_age = row["age"]

            # ─── Gains passifs (salaire, loyers, intérêts) ─────────────
            # PHASE 2 : Réduction du salaire passif (divisé par 3)
            async with db.execute("""
                SELECT salary FROM company_members WHERE user_id=?
            """, (user_id,)) as cur2:
                job_row = await cur2.fetchone()
            if job_row and job_row["salary"] > 0:
                hourly_salary = job_row["salary"] / 24
                salary_gain = int(hourly_salary * hours)
                # Réduction : on divise par 3 pour éviter l'inflation passive
                salary_gain = salary_gain // 3
                balance_delta += salary_gain

            # PHASE 2 : Réduction des intérêts bancaires (taux divisé par 5)
            async with db.execute("""
                SELECT SUM(balance) as total FROM bank_accounts WHERE user_id=?
            """, (user_id,)) as cur4:
                bank_row = await cur4.fetchone()
            if bank_row:
                total = bank_row["total"]
                if total is not None and total > 0:
                    daily_rate = 0.0002  # 0.02% par jour (au lieu de 0.1%)
                    hourly_rate = daily_rate / 24
                    interest = int(total * hourly_rate * hours)
                    balance_delta += interest

            balance_delta = int(balance_delta * penalties["passive_income_mult"])

            # PHASE 2 : Pénalité de santé (si santé < 30) → réduction des gains
            if new_health < 30:
                penalty = max(0.1, 1 - (30 - new_health) / 100)  # entre 0.1 et 1
                balance_delta = int(balance_delta * penalty)
                if penalty < 0.8:
                    xp_delta = -10  # perte symbolique d'XP

            if penalties["collapse_risk"]:
                hospital_extension = min(6 * 3600, hours * 1800)
                hospital_until = max(hospital_until, current + hospital_extension)
                new_health = clamp(new_health + 10, 0, 100)
                new_energy = clamp(new_energy + 15, 0, 100)
                new_stress = clamp(new_stress - 8, 0, 100)

            # ─── Événement aléatoire (plafonné à 1 fois par jour réel) ──
            last_event_day = row.get("last_event_day", 0)
            today = current // 86400
            if today > last_event_day and random.random() < 0.25:
                event = random.choices(PASSIVE_LIFE_EVENTS, weights=[e["weight"] for e in PASSIVE_LIFE_EVENTS])[0]
                new_happiness = clamp(new_happiness + event.get("happiness", 0), 0, 100)
                new_stress    = clamp(new_stress    + event.get("stress", 0), 0, 100)
                new_energy    = clamp(new_energy    + event.get("energy", 0), 0, 100)
                new_health    = clamp(new_health    + event.get("health", 0), 0, 100)
                if "money" in event:
                    lo, hi = event["money"]
                    amt = random.randint(lo, hi)
                    balance_delta += amt
                if "xp" in event:
                    lo, hi = event["xp"]
                    xp_delta += random.randint(lo, hi)
                await db.execute("UPDATE users SET last_event_day = ? WHERE user_id = ?", (today, user_id))

            # Mise à jour du lifestyle_score
            new_lifestyle = lifestyle_score({
                "health": new_health, "energy": new_energy,
                "hunger": new_hunger, "happiness": new_happiness,
                "stress": new_stress,
            })

            # ─── Enregistrement en base (une seule transaction) ─────────
            await db.execute("""
                UPDATE users SET
                    hunger = ?, energy = ?, stress = ?, happiness = ?, health = ?,
                    age = ?, prison_until = ?, hospital_until = ?, frozen_until = ?,
                    balance = balance + ?, xp = xp + ?,
                    lifestyle_score = ?,
                    last_seen = ?, last_life_tick = ?
                WHERE user_id = ?
            """, (
                new_hunger, new_energy, new_stress, new_happiness, new_health,
                new_age, prison_until, hospital_until, frozen_until,
                balance_delta, xp_delta, new_lifestyle,
                current, current, user_id,
            ))

            # ─── Journal de vie (optionnel) ────────────────────────────
            if hours >= 6 or balance_delta != 0 or xp_delta != 0:
                summary = f"{hours}h écoulées, faim -{hunger_loss}, énergie +{energy_gain}"
                await db.execute(
                    "INSERT INTO life_journal (user_id, category, summary, severity, created_at) VALUES (?,?,?,?,?)",
                    (user_id, "temps", summary, "info", current)
                )

            await db.commit()

            # ─── Message d'alerte si changement significatif ────────────
            alert_text = ""
            if hours >= 6 or balance_delta != 0 or xp_delta != 0:
                body = [
                    f"⏳ <b>{hours}h</b> se sont écoulées",
                    f"{season_fx['icon']} Saison : <b>{season.capitalize()}</b>",
                    f"🍽️ Faim : {new_hunger}%  ⚡ Énergie : {new_energy}%",
                    f"😰 Stress : {new_stress}%  ❤️ Santé : {new_health}%",
                    f"😊 Bonheur : {new_happiness}%",
                ]
                if balance_delta:
                    sign = "+" if balance_delta > 0 else ""
                    body.append(f"💰 Économie passive : {sign}{fmt_money(balance_delta)}")
                if xp_delta:
                    sign = "+" if xp_delta > 0 else ""
                    body.append(f"⭐ {sign}{xp_delta} XP")
                if penalties["warnings"]:
                    body.append(f"⚠️ Fragilités : <b>{', '.join(penalties['warnings'])}</b>")
                if penalties["severe_combo"]:
                    body.append("🚨 Ton état général dégrade fortement tes performances.")
                if penalties["collapse_risk"]:
                    body.append("🏥 Ton personnage a subi un malaise et a été placé au repos médical.")
                alert_text = card(
                    "Simulation de ton absence",
                    body,
                    footer="Tape /vie pour plus de détails.",
                    icon="🌍", style="thick"
                )

            return {
                "changed": True,
                "hours": hours,
                "alert": alert_text,
                "new_stats": {
                    "hunger": new_hunger, "energy": new_energy,
                    "stress": new_stress, "happiness": new_happiness,
                    "health": new_health, "age": new_age,
                }
            }

    return await _execute_with_retry(_simulate)
