# utils/pagination.py
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from typing import Tuple, Optional, List

async def paginate_lines(
    lines: List[str],
    page: int = 1,
    per_page: int = 20,
    header: str = "",
    footer: str = ""
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Découpe une liste de lignes en pages avec boutons de navigation.
    Retourne (texte, markup) où markup peut être None si une seule page.
    """
    total_pages = (len(lines) + per_page - 1) // per_page
    if page < 1:
        page = 1
    if page > total_pages and total_pages > 0:
        page = total_pages

    start = (page - 1) * per_page
    end = start + per_page
    page_lines = lines[start:end]

    text = header + "\n" + "\n".join(page_lines) + footer

    keyboard = []
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"page_{page-1}"))
        nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"page_{page+1}"))
        keyboard.append(nav)

    return text, InlineKeyboardMarkup(keyboard) if keyboard else None

async def paginate_text(
    text: str,
    page: int = 1,
    per_page: int = 20
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """Pagine un long texte (découpé par lignes)."""
    lines = text.split("\n")
    return await paginate_lines(lines, page, per_page)

def build_callback_data(page: int) -> str:
    """Génère un callback_data standard pour la pagination."""
    return f"page_{page}"