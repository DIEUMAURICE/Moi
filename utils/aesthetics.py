"""
utils/aesthetics.py
═══════════════════════════════════════════════════════════════════════
   LIFESIM ULTRA — MOTEUR ESTHÉTIQUE V2
═══════════════════════════════════════════════════════════════════════
Bibliothèque UI complète pour produire des messages riches, immersifs
et visuellement somptueux : cartes, panneaux ornés, barres artistiques,
animations textuelles, dégradés et bordures unicode.
"""
import html
import math
import random
from typing import Iterable, Optional


# ─── PALETTES DE BORDURES ────────────────────────────────────────────
BORDERS = {
    "double":  ("╔", "╗", "╚", "╝", "═", "║"),
    "single":  ("┌", "┐", "└", "┘", "─", "│"),
    "round":   ("╭", "╮", "╰", "╯", "─", "│"),
    "thick":   ("┏", "┓", "┗", "┛", "━", "┃"),
    "stars":   ("✦", "✦", "✦", "✦", "─", " "),
    "dots":    ("•", "•", "•", "•", "·", " "),
}

# Séparateurs élégants
SEP_HEAVY   = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
SEP_DOUBLE  = "═══════════════════════════════"
SEP_LIGHT   = "────────────────────────────"
SEP_DOTTED  = "· · · · · · · · · · · · · · ·"
SEP_FANCY   = "═══━━━━─────━━━━═══"
SEP_FLOWER  = "✦ ─────────────── ✦"
SEP_STAR    = "★ ━━━━━━━━━━━━━━━ ★"
SEP_DIAMOND = "◆ ─────────────── ◆"

# ─── EMOJIS DE STATUT ────────────────────────────────────────────────
STATUS_DOT = {
    "perfect":  "🟢",
    "good":     "🟢",
    "ok":       "🟡",
    "warn":     "🟠",
    "bad":      "🔴",
    "critical": "🚨",
}

LIFE_AURA = ["🌑", "🌒", "🌓", "🌔", "🌕"]
MOOD_EMOJI = ["😵", "😞", "😐", "🙂", "😄", "🤩"]
ENERGY_EMOJI = ["💤", "🥱", "😪", "😌", "⚡", "🔥"]

# ─── BARRES DE PROGRESSION ──────────────────────────────────────────
BAR_STYLES = {
    "blocks":  ("█", "▓", "▒", "░"),
    "shaded":  ("█", "▉", "▊", "▋", "▌", "▍", "▎", "▏"),
    "circles": ("●", "◉", "○", "·"),
    "dots":    ("⬤", "◐", "○", "·"),
    "stars":   ("★", "✦", "☆", "·"),
    "heart":   ("❤", "♥", "♡", "·"),
}


# ─── FORMATAGE NUMÉRIQUE PRO ────────────────────────────────────────
def fmt_money(n: float | int, currency: str = "$") -> str:
    """Format somptueux pour l'argent — affichage international."""
    n = float(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1e15:
        return f"{sign}{n/1e15:.2f}Qa{currency}"
    if n >= 1e12:
        return f"{sign}{n/1e12:.2f}T{currency}"
    if n >= 1e9:
        return f"{sign}{n/1e9:.2f}B{currency}"
    if n >= 1e6:
        return f"{sign}{n/1e6:.2f}M{currency}"
    if n >= 1e3:
        return f"{sign}{n/1e3:.1f}K{currency}"
    return f"{sign}{int(n):,}{currency}".replace(",", " ")


def fmt_number(n: float | int) -> str:
    """Format chiffres avec séparateurs de milliers."""
    return f"{int(n):,}".replace(",", " ")


def fmt_percent(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}%"


# ─── BARRES DE PROGRESSION RICHES ───────────────────────────────────
def rich_bar(value: int, max_val: int = 100, length: int = 12, style: str = "blocks") -> str:
    """Barre de progression artistique avec multiples styles."""
    chars = BAR_STYLES.get(style, BAR_STYLES["blocks"])
    full, empty = chars[0], chars[-1]
    value = max(0, min(max_val, value))
    ratio = value / max_val
    filled = int(ratio * length)
    return full * filled + empty * (length - filled)


def gradient_bar(value: int, max_val: int = 100, length: int = 14, inverted: bool = False) -> str:
    """Barre qui change de couleur selon le niveau (via emojis)."""
    value = max(0, min(max_val, value))
    pct = (value / max_val) * 100
    if inverted:
        pct = 100 - pct
    ratio = value / max_val
    filled = int(ratio * length)
    bar = "█" * filled + "░" * (length - filled)
    if pct >= 80: prefix = "🟢"
    elif pct >= 60: prefix = "🟢"
    elif pct >= 40: prefix = "🟡"
    elif pct >= 20: prefix = "🟠"
    else: prefix = "🔴"
    return f"{prefix} {bar}"


def stat_card(label: str, value: int, max_val: int = 100, emoji: str = "▫️", inverted: bool = False) -> str:
    """Carte de statistique stylée."""
    pct = value if max_val == 100 else int((value / max_val) * 100)
    bar = rich_bar(value, max_val, 12, "blocks")
    if inverted:
        dot = STATUS_DOT["good"] if value <= 20 else STATUS_DOT["ok"] if value <= 45 else STATUS_DOT["warn"] if value <= 70 else STATUS_DOT["bad"]
    else:
        dot = STATUS_DOT["perfect"] if pct >= 85 else STATUS_DOT["ok"] if pct >= 60 else STATUS_DOT["warn"] if pct >= 35 else STATUS_DOT["bad"]
    return f"{emoji} <b>{label}</b>  {dot}\n   <code>{bar}</code> <b>{pct}%</b>"


def mini_stat(label: str, value: int, max_val: int = 100, emoji: str = "•", inverted: bool = False) -> str:
    """Format compact d'une statistique."""
    bar = rich_bar(value, max_val, 8)
    dot = STATUS_DOT["perfect"] if value >= 75 and not inverted else (
          STATUS_DOT["ok"] if value >= 40 and not inverted else STATUS_DOT["bad"])
    if inverted:
        dot = STATUS_DOT["good"] if value <= 20 else STATUS_DOT["warn"] if value <= 60 else STATUS_DOT["bad"]
    return f"{emoji}{dot} {label}: <code>{bar}</code> {value}"


# ─── PANNEAUX ET CARTES ─────────────────────────────────────────────
def card(
    title: str,
    body: Iterable[str] | str,
    footer: Optional[str] = None,
    icon: str = "✦",
    style: str = "double",
) -> str:
    """
    Génère une carte élégante avec bordures stylisées.
    style: 'double' | 'single' | 'round' | 'thick' | 'stars'
    """
    if isinstance(body, str):
        body = [body]
    body_text = "\n".join(str(line) for line in body)
    title_clean = html.escape(str(title))

    if style == "double":
        header = f"╔══ {icon} ══════════════════════╗"
        bottom = f"╚══════════════════════════════╝"
    elif style == "thick":
        header = f"┏━━ {icon} ━━━━━━━━━━━━━━━━━━━━━┓"
        bottom = f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
    elif style == "round":
        header = f"╭── {icon} ──────────────────────╮"
        bottom = f"╰──────────────────────────────╯"
    elif style == "stars":
        header = f"✦ ─────  {icon}  ───── ✦"
        bottom = f"✦ ─────────────────── ✦"
    else:
        header = f"┌── {icon} ──────────────────────┐"
        bottom = f"└──────────────────────────────┘"

    text = f"{header}\n   <b>{title_clean}</b>\n{SEP_HEAVY}\n{body_text}"
    if footer:
        text += f"\n{SEP_LIGHT}\n<i>{html.escape(footer)}</i>"
    text += f"\n{bottom}"
    return text


def hero_banner(title: str, subtitle: str = "", icon: str = "🌟") -> str:
    """Bannière de premier plan, idéale pour /start, victoires, etc."""
    title = html.escape(title)
    subtitle = html.escape(subtitle)
    txt = (
        f"╔══════════════════════════════════╗\n"
        f"  {icon}  <b>{title.upper()}</b>  {icon}\n"
        f"╚══════════════════════════════════╝"
    )
    if subtitle:
        txt += f"\n<i>{subtitle}</i>"
    return txt


def section(title: str, lines: Iterable[str], icon: str = "▸") -> str:
    """Section nommée avec lignes."""
    body = "\n".join(str(l) for l in lines)
    return f"\n<b>{icon} {html.escape(title)}</b>\n{SEP_LIGHT}\n{body}"


def alert(level: str, message: str) -> str:
    """Alertes joliment formatées."""
    icons = {"success": "✅", "info": "💡", "warning": "⚠️", "danger": "🚨", "error": "❌", "tip": "💎"}
    titles = {"success": "Succès", "info": "Info", "warning": "Attention",
              "danger": "Danger", "error": "Erreur", "tip": "Astuce"}
    icon = icons.get(level, "•")
    title = titles.get(level, "Notification")
    return f"<b>{icon} {title}</b> ─ {message}"


def event_box(title: str, description: str, rewards: list[str] = None, penalties: list[str] = None) -> str:
    """Boîte d'événement narratif."""
    lines = [f"<i>{html.escape(description)}</i>", ""]
    if rewards:
        lines.append("<b>🎁 Récompenses</b>")
        lines.extend(f"  ➕ {r}" for r in rewards)
    if penalties:
        if rewards:
            lines.append("")
        lines.append("<b>⚠️ Conséquences</b>")
        lines.extend(f"  ➖ {p}" for p in penalties)
    return card(title, lines, icon="📜", style="thick")


def reward_card(title: str, items: list[tuple[str, str]]) -> str:
    """Carte de récompenses (label, valeur)."""
    lines = []
    for label, value in items:
        lines.append(f"  ✨ <b>{label}</b> ──── {value}")
    return card(title, lines, icon="🎁", style="stars")


def comparison_table(rows: list[tuple[str, str, str]]) -> str:
    """Tableau 3 colonnes : label / avant / après."""
    out = []
    for label, before, after in rows:
        out.append(f"<b>{label}</b>\n  ┃ avant : <code>{before}</code>\n  ┃ après : <code>{after}</code>")
    return "\n\n".join(out)


# ─── NARRATION & FLAVOR TEXT ────────────────────────────────────────
FLAVOR_INTROS = [
    "Le destin frappe à ta porte…",
    "Une nouvelle journée s'écoule…",
    "L'horloge du monde tourne…",
    "Le vent du changement souffle…",
    "Au cœur de la cité bouillonnante…",
    "Sous un ciel changeant…",
    "Dans le tumulte du quotidien…",
    "Les heures filent sans prévenir…",
]

FLAVOR_SUCCESS = [
    "Tout s'est déroulé comme prévu.",
    "Le sort t'a souri aujourd'hui.",
    "Un succès net et propre.",
    "Tu maîtrises ton art.",
    "Les étoiles s'alignent en ta faveur.",
]

FLAVOR_FAIL = [
    "La chance t'a tourné le dos.",
    "Ce n'était pas ton jour.",
    "Le destin reste imprévisible.",
    "Une leçon dure à digérer.",
    "Demain sera un autre jour.",
]


def flavor(kind: str = "intro") -> str:
    pool = {"intro": FLAVOR_INTROS, "win": FLAVOR_SUCCESS, "lose": FLAVOR_FAIL}.get(kind, FLAVOR_INTROS)
    return random.choice(pool)


# ─── LISTES STYLÉES ─────────────────────────────────────────────────
def numbered_list(items: list[str], bullets: list[str] = None) -> str:
    """Liste numérotée stylée."""
    out = []
    for i, item in enumerate(items, 1):
        out.append(f"  <b>{i:>2}.</b> {item}")
    return "\n".join(out)


def bullet_list(items: list[str], bullet: str = "▸") -> str:
    return "\n".join(f"  {bullet} {it}" for it in items)


def keyed_list(pairs: list[tuple[str, str]], sep: str = "·") -> str:
    """Liste clé/valeur alignée."""
    return "\n".join(f"  {sep} <b>{k}</b> ─ {v}" for k, v in pairs)


def tag_chips(tags: list[str]) -> str:
    """Petits chips de tags : [Tag1] [Tag2] [Tag3]"""
    return "  ".join(f"<code>「{t}」</code>" for t in tags)


# ─── TEMPS & DURÉES ─────────────────────────────────────────────────
def fmt_duration(seconds: int, verbose: bool = False) -> str:
    """Formate une durée en notation lisible."""
    if seconds <= 0:
        return "instantané" if verbose else "0s"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if days: parts.append(f"{days}j")
    if hours: parts.append(f"{hours}h")
    if mins and not days: parts.append(f"{mins}m")
    if secs and not days and not hours: parts.append(f"{secs}s")
    return " ".join(parts) if parts else "0s"


def cooldown_message(remaining: int, action: str = "cette action") -> str:
    """Message stylé pour un cooldown."""
    return card(
        "Action verrouillée",
        [
            f"⏳ <b>{action.capitalize()}</b> est encore en récupération.",
            f"⌛ Reviens dans <b>{fmt_duration(remaining)}</b>.",
            "",
            "<i>Patience : le temps forge la stratégie.</i>",
        ],
        icon="🔒",
        style="round",
    )


# ─── AGE & STADES DE VIE ────────────────────────────────────────────
def age_stage(age: int) -> tuple[str, str]:
    """Retourne (label, emoji) selon l'âge."""
    if age < 13:   return ("Enfance", "🧒")
    if age < 18:   return ("Adolescence", "👦")
    if age < 25:   return ("Jeune adulte", "🧑")
    if age < 40:   return ("Adulte", "👨")
    if age < 60:   return ("Âge mûr", "👨‍🦱")
    if age < 75:   return ("Senior", "👴")
    return ("Vieillesse", "🧓")


# ─── ETOILES (rating) ───────────────────────────────────────────────
def stars(value: int, max_val: int = 5) -> str:
    """Affiche une note en étoiles."""
    full = "★" * value
    empty = "☆" * (max_val - value)
    return full + empty


# ─── CONFETTI / CÉLÉBRATION ─────────────────────────────────────────
def celebrate(message: str) -> str:
    """Message de célébration avec confettis."""
    confettis = random.sample(["🎉", "🎊", "✨", "🎆", "🎇", "🌟", "💫", "⭐"], 3)
    return f"{confettis[0]} {confettis[1]} {confettis[2]} <b>{message}</b> {confettis[0]} {confettis[1]} {confettis[2]}"


def critical_failure(message: str) -> str:
    return f"💔 <b>{message}</b> 💔"


# ─── HEADERS ESTHÉTIQUES POUR MENUS ─────────────────────────────────
def menu_header(title: str, subtitle: str = "") -> str:
    sub = f"\n<i>{html.escape(subtitle)}</i>" if subtitle else ""
    return (
        f"╔═══════════════════════════════╗\n"
        f"     🎮  <b>{html.escape(title).upper()}</b>\n"
        f"╚═══════════════════════════════╝{sub}"
    )


# ─── ESCAPING UTILS ─────────────────────────────────────────────────
def safe(text: str) -> str:
    return html.escape(str(text))


__all__ = [
    "BORDERS", "SEP_HEAVY", "SEP_DOUBLE", "SEP_LIGHT", "SEP_DOTTED",
    "SEP_FANCY", "SEP_FLOWER", "SEP_STAR", "SEP_DIAMOND",
    "fmt_money", "fmt_number", "fmt_percent",
    "rich_bar", "gradient_bar", "stat_card", "mini_stat",
    "card", "hero_banner", "section", "alert",
    "event_box", "reward_card", "comparison_table",
    "flavor", "numbered_list", "bullet_list", "keyed_list", "tag_chips",
    "fmt_duration", "cooldown_message", "age_stage", "stars",
    "celebrate", "critical_failure", "menu_header", "safe",
]
