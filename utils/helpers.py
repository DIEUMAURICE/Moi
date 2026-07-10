"""
utils/helpers.py — LIFESIM ULTRA V2
═══════════════════════════════════════════════════════════════════════
Helpers principaux du jeu. Maintient la compatibilité avec l'API
existante tout en pontant vers le nouveau moteur esthétique.
"""
import time
import math
import re
import html
import random
from typing import Iterable

# Pont esthétique
from utils.aesthetics import (
    fmt_money, fmt_number, rich_bar, gradient_bar, stat_card, mini_stat,
    card, hero_banner, section, alert, event_box, reward_card,
    flavor, numbered_list, bullet_list as ae_bullet_list, keyed_list,
    fmt_duration, cooldown_message, age_stage, stars, celebrate,
    menu_header, safe, SEP_HEAVY, SEP_LIGHT, SEP_DOUBLE,
)


# ─── Temps ──────────────────────────────────────────────────────────
def now() -> int:
    return int(time.time())


def fmt_time(seconds: int) -> str:
    """Format temps lisible (compat ascendante)."""
    return fmt_duration(seconds)


def cooldown_remaining(last: int, cooldown: int) -> int:
    return max(0, (last + cooldown) - now())


def is_on_cooldown(last: int, cooldown: int) -> bool:
    return cooldown_remaining(last, cooldown) > 0


# ─── Argent ─────────────────────────────────────────────────────────
def fmt(n: int | float) -> str:
    """Format somme — wrapper compatible avec ancienne API."""
    return fmt_money(n)


def parse_amount(s: str, balance: int = None) -> int | None:
    """
    Parse '1k', '2.5m', '100%', 'tout', 'all', 'half', 'quart', ou int.
    Support enrichi : moitié, quart, dixième.
    """
    if s is None:
        return None
    s = s.strip().lower().replace(",", ".").replace(" ", "").replace("_", "")
    if balance is not None:
        if s in ("all", "tout", "max", "everything"):
            return balance
        if s in ("half", "moitie", "moitié", "1/2"):
            return balance // 2
        if s in ("quart", "1/4", "quarter"):
            return balance // 4
        if s in ("third", "tiers", "1/3"):
            return balance // 3
        if s in ("ten", "dixieme", "dixième", "1/10"):
            return balance // 10
    if s.endswith("%") and balance is not None:
        try:
            pct = float(s[:-1]) / 100
            return max(1, int(balance * pct))
        except ValueError:
            return None
    multipliers = {
        "k": 1_000, "m": 1_000_000, "b": 1_000_000_000,
        "t": 1_000_000_000_000, "qa": 1_000_000_000_000_000,
    }
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)" + re.escape(suffix), s)
            if not match:
                return None
            return int(float(match.group(1)) * mult)
    try:
        return int(float(s))
    except ValueError:
        return None


# ─── XP / Niveau / Titres ───────────────────────────────────────────
def get_level(xp: int) -> int:
    return max(1, int(math.sqrt(xp / 500)) + 1)


def xp_for_level(level: int) -> int:
    return (level - 1) ** 2 * 500


def xp_progress(xp: int) -> tuple[int, int, int]:
    """Retourne (lvl, xp_dans_lvl, xp_pour_next)."""
    lvl = get_level(xp)
    start = xp_for_level(lvl)
    end = xp_for_level(lvl + 1)
    return lvl, xp - start, end - start


def get_title(balance: int) -> str:
    from config import TITLES
    title = TITLES[0]["title"]
    for t in TITLES:
        if balance >= t["min"]:
            title = t["title"]
    return title


# ─── Clamp ──────────────────────────────────────────────────────────
def clamp(value, low, high):
    return max(low, min(high, value))


# ─── Barres ─────────────────────────────────────────────────────────
def health_bar(value: int, max_val: int = 100, length: int = 10) -> str:
    """Barre simple compatible avec l'API existante."""
    return rich_bar(value, max_val, length, "blocks")


def progress_bar(value: int, max_val: int = 100, length: int = 10) -> str:
    return rich_bar(value, max_val, length)


def format_profile_bar(name: str, value: int, emoji: str = "") -> str:
    bar = rich_bar(value, 100, 12)
    return f"{emoji} <b>{name}</b> [<code>{bar}</code>] <b>{value}%</b>"


# ─── Statut & emojis ────────────────────────────────────────────────
def status_emoji(value: int) -> str:
    if value >= 85: return "🟢"
    if value >= 60: return "🟢"
    if value >= 35: return "🟡"
    if value >= 15: return "🟠"
    return "🔴"


def stress_emoji(value: int) -> str:
    if value <= 20: return "🟢"
    if value <= 45: return "🟡"
    if value <= 70: return "🟠"
    return "🔴"


def stat_line(label: str, value: int, emoji: str, inverted: bool = False) -> str:
    """Ligne de stat compacte stylée — compat ascendante."""
    meter = rich_bar(int(value), 100, 12)
    icon = stress_emoji(value) if inverted else status_emoji(value)
    return f"{emoji} <b>{label}</b> {icon} <code>{meter}</code> <b>{int(value)}%</b>"


# ─── Panneaux ───────────────────────────────────────────────────────
def panel(title: str, lines: Iterable[str], footer: str | None = None) -> str:
    """Panneau classique — compat ascendante mais esthétique modernisée."""
    body = "\n".join(str(l) for l in lines)
    text = (
        f"╔══════════════════════════════════╗\n"
        f"   ✦  <b>{html.escape(title)}</b>\n"
        f"╚══════════════════════════════════╝\n"
        f"{body}"
    )
    if footer:
        text += f"\n{SEP_LIGHT}\n<i>{html.escape(footer)}</i>"
    return text


def bullet_list(items: list[str], bullet: str = "▸") -> str:
    return "\n".join(f"  {bullet} {it}" for it in items)


# ─── Karma & succès ─────────────────────────────────────────────────
def roll_success(base_prob: float, skill_bonus: float = 0.0, karma_bonus: float = 0.0,
                 luck_bonus: float = 0.0) -> bool:
    prob = clamp(base_prob + skill_bonus + karma_bonus + luck_bonus, 0.01, 0.98)
    return random.random() < prob


def get_karma_multiplier(karma: int) -> float:
    if karma >= 500:  return 1.40
    if karma >= 200:  return 1.25
    if karma >= 100:  return 1.15
    if karma >= 50:   return 1.08
    if karma <= -500: return 0.55
    if karma <= -200: return 0.75
    if karma <= -100: return 0.85
    if karma <= -50:  return 0.92
    return 1.0


def karma_label(karma: int) -> str:
    """Retourne libellé moral."""
    if karma >= 500:  return "Saint(e) 😇"
    if karma >= 200:  return "Vertueux(se) ✨"
    if karma >= 50:   return "Bienveillant(e) 🌿"
    if karma >= -50:  return "Neutre ⚖️"
    if karma >= -200: return "Trouble 🌫️"
    if karma >= -500: return "Sombre 🌑"
    return "Maudit(e) 👹"


# ─── Stats lifestyle ────────────────────────────────────────────────
def lifestyle_score(user: dict) -> int:
    base = (
        user.get("health", 100)
        + user.get("energy", 100)
        + user.get("hunger", 100)
        + user.get("happiness", 100)
    ) / 4
    stress_penalty = user.get("stress", 0) * 0.35
    score = int(clamp(base - stress_penalty, 0, 100))
    return score


def life_state_label(score: int) -> str:
    if score >= 90: return "✨ Au sommet"
    if score >= 80: return "🌟 Élan total"
    if score >= 65: return "💫 Vie stable"
    if score >= 50: return "⚖️ Sous contrôle"
    if score >= 35: return "🌫️ Zone fragile"
    if score >= 20: return "⚠️ Situation critique"
    return "🚨 Au bord du gouffre"


def wealth_class(balance: int) -> str:
    if balance >= 1_000_000_000: return "💎 Magnat"
    if balance >= 100_000_000:   return "👑 Élite"
    if balance >= 10_000_000:    return "🥇 Riche"
    if balance >= 1_000_000:     return "💼 Millionnaire"
    if balance >= 250_000:       return "🏠 Aisé(e)"
    if balance >= 50_000:        return "🛋️ Confortable"
    if balance >= 10_000:        return "🍽️ Stable"
    if balance >= 1_000:         return "💸 Modeste"
    return "🪙 Précaire"


# ─── Escaping ───────────────────────────────────────────────────────
def escape_md(text: str) -> str:
    """Échappe seulement les caractères les plus dangereux pour éviter un rendu trop chargé."""
    text = str(text)
    return re.sub(r"([\\_*`\[\]])", r"\\\1", text)
    

def escape_html(text: str) -> str:
    return html.escape(str(text))


# ─── Randomized flavor ──────────────────────────────────────────────
def random_quote() -> str:
    quotes = [
        "« Le destin appartient à ceux qui agissent. »",
        "« Chaque choix forge ton avenir. »",
        "« La fortune sourit aux audacieux. »",
        "« Un pas après l'autre, l'empire se bâtit. »",
        "« La vie est un puzzle aux infinies combinaisons. »",
        "« Le silence du sage parle plus fort que le bruit du fou. »",
        "« Au fond du gouffre se cache la plus grande opportunité. »",
        "« La patience est la mère de toutes les victoires. »",
    ]
    return random.choice(quotes)


# ─── Re-exports modernes ────────────────────────────────────────────
__all__ = [
    # Compat ascendante
    "now", "fmt_time", "cooldown_remaining", "is_on_cooldown",
    "fmt", "parse_amount", "get_level", "xp_for_level", "xp_progress",
    "get_title", "clamp", "health_bar", "progress_bar", "format_profile_bar",
    "status_emoji", "stress_emoji", "stat_line", "panel", "bullet_list",
    "roll_success", "get_karma_multiplier", "karma_label",
    "lifestyle_score", "life_state_label", "wealth_class",
    "escape_md", "escape_html", "random_quote",
    # Re-exports esthétiques
    "fmt_money", "fmt_number", "rich_bar", "gradient_bar",
    "stat_card", "mini_stat", "card", "hero_banner", "section",
    "alert", "event_box", "reward_card", "flavor",
    "numbered_list", "keyed_list", "fmt_duration", "cooldown_message",
    "age_stage", "stars", "celebrate", "menu_header", "safe",
]
