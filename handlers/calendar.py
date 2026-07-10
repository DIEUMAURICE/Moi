# handlers/calendar.py
import time
from database import DB_PATH, get_user, now
from config import SEASONS, SEASON_EFFECTS

def get_real_season() -> dict:
    """Retourne la saison réelle basée sur la date du serveur."""
    day_of_year = time.localtime().tm_yday
    if day_of_year < 80 or day_of_year >= 355:
        season = "Hiver"
    elif day_of_year < 172:
        season = "Printemps"
    elif day_of_year < 266:
        season = "Été"
    else:
        season = "Automne"
    return {"season": season, "day": day_of_year}

def get_game_season(age_seconds: int) -> str:
    """
    Calcule la saison en fonction du temps de jeu (age_seconds).
    Une année de jeu = 86400 * 365 secondes (365 jours).
    Les saisons se succèdent : Printemps -> Été -> Automne -> Hiver.
    """
    if age_seconds <= 0:
        return "Printemps"
    # Une année complète = 4 saisons, chaque saison dure 86400 * 365 / 4 = 7 884 000 secondes
    seconds_per_season = 86400 * 365 // 4
    cycle = (age_seconds // seconds_per_season) % 4
    season_map = ["Printemps", "Été", "Automne", "Hiver"]
    return season_map[cycle]

async def get_user_season(user_id: int) -> dict:
    """
    Retourne la saison du jeu pour un utilisateur spécifique,
    basée sur son âge en secondes de jeu (depuis sa création).
    """
    u = await get_user(user_id)
    created_at = u.get("created_at", 0)
    if created_at <= 0:
        age_sec = 0
    else:
        age_sec = now() - created_at
    season = get_game_season(age_sec)
    return {
        "season": season,
        "effects": SEASON_EFFECTS.get(season, SEASON_EFFECTS["Printemps"])
    }

async def get_calendar(user_id: int = None):
    """
    Point d'entrée principal pour le reste du bot.
    Si user_id est fourni, retourne la saison de jeu personnalisée.
    Sinon, retourne la saison réelle (pour les événements mondiaux).
    """
    if user_id is not None:
        return await get_user_season(user_id)
    else:
        return get_real_season()