import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []
DB_PATH = "life_sim.db"
DB_VERSION = 2                     # pour les migrations futures
MAX_PLANTS_PER_USER = 10           # limite de jardinage
SQLITE_POOL_SIZE = 5               # pool de connexions (optionnel)

# ─── Economy ──────────────────────────────────────────────────────────────────
DAILY_MIN = 1_500          # était 5_000
DAILY_MAX = 3_000          # était 25_000
WORK_COOLDOWN = 4 * 3600   # 4h
DAILY_COOLDOWN = 86400
STARTING_BALANCE = 10_000

# Taxe progressive sur les grandes fortunes
WEALTH_TAX_BRACKETS = [
    (1_000_000, 0.01),   # 1% au-delà de 1M
    (10_000_000, 0.02),  # 2% au-delà de 10M
    (100_000_000, 0.03), # 3% au-delà de 100M
]

# ─── Education ─────────────────────────────────────────────────────────────────
DIPLOMES = ["Brevet", "Bac", "BTS", "Licence", "Master", "MBA", "Doctorat", "Habilitation"]
DIPLOME_COSTS = {
    "Brevet":         5_000,
    "Bac":           15_000,
    "BTS":           30_000,
    "Licence":       80_000,
    "Master":       250_000,
    "MBA":        1_000_000,
    "Doctorat":   3_000_000,
    "Habilitation":10_000_000,
}
DIPLOME_STUDY_TIME = {
    "Brevet":       3600,
    "Bac":          7200,
    "BTS":          7200,
    "Licence":     14400,
    "Master":      21600,
    "MBA":         28800,
    "Doctorat":    43200,
    "Habilitation":86400,
}
DIPLOME_PASS_RATE = {
    "Brevet":      0.85,
    "Bac":         0.75,
    "BTS":         0.70,
    "Licence":     0.65,
    "Master":      0.55,
    "MBA":         0.45,
    "Doctorat":    0.35,
    "Habilitation":0.20,
}
DIPLOME_SALARY_BONUS = {
    "Brevet":      1.05,
    "Bac":         1.10,
    "BTS":         1.20,
    "Licence":     1.35,
    "Master":      1.55,
    "MBA":         2.00,
    "Doctorat":    2.50,
    "Habilitation":3.50,
}

# Diplômes étendus
DIPLOMES_EXTENDED = [
    "Brevet", "Bac", "BTS", "Licence", "Master",
    "École de commerce", "Grande École", "MBA", "Doctorat", "Habilitation", "Doctorat honoris causa"
]
DIPLOME_COSTS_EXTENDED = {
    "École de commerce":         2_000_000,
    "Grande École":              5_000_000,
    "Doctorat honoris causa":   20_000_000,
}
DIPLOME_STUDY_TIME_EXTENDED = {
    "École de commerce":         43200,
    "Grande École":              86400,
    "Doctorat honoris causa":   172800,
}
DIPLOME_PASS_RATE_EXTENDED = {
    "École de commerce":         0.55,
    "Grande École":              0.45,
    "Doctorat honoris causa":    0.10,
}
DIPLOME_SALARY_BONUS_EXTENDED = {
    "École de commerce":         2.20,
    "Grande École":              2.80,
    "Doctorat honoris causa":    4.00,
}

# ─── Jobs (rééquilibrés) ─────────────────────────────────────────────────────
JOBS = {
    "Livreur":       {"min": 600,   "max": 2_400,   "xp": 30,  "need": None,      "sector": "Commerce"},
    "Serveur":       {"min": 750,   "max": 2_700,   "xp": 35,  "need": None,      "sector": "Commerce"},
    "Ouvrier":       {"min": 900,   "max": 3_000,   "xp": 40,  "need": None,      "sector": "Industrie"},
    "Caissier":      {"min": 600,   "max": 2_250,   "xp": 25,  "need": None,      "sector": "Commerce"},
    "Agent secu":    {"min": 1_050, "max": 3_600,   "xp": 45,  "need": "Brevet",  "sector": "Sécurité"},
    "Technicien":    {"min": 1_500, "max": 5_400,   "xp": 60,  "need": "BTS",     "sector": "Industrie"},
    "Infirmier":     {"min": 1_800, "max": 6_000,   "xp": 70,  "need": "BTS",     "sector": "Santé"},
    "Comptable":     {"min": 2_400, "max": 8_400,   "xp": 80,  "need": "Licence", "sector": "Finance"},
    "Développeur":   {"min": 3_600, "max": 13_500,  "xp": 100, "need": "Licence", "sector": "Tech"},
    "Ingénieur":     {"min": 4_500, "max": 16_500,  "xp": 110, "need": "Master",  "sector": "Industrie"},
    "Avocat":        {"min": 6_000, "max": 24_000,  "xp": 130, "need": "Master",  "sector": "Juridique"},
    "Médecin":       {"min": 7_500, "max": 30_000,  "xp": 150, "need": "Doctorat","sector": "Santé"},
    "Manager":       {"min": 5_400, "max": 21_000,  "xp": 120, "need": "MBA",     "sector": "Management"},
    "Directeur":     {"min": 12_000,"max": 45_000,  "xp": 200, "need": "MBA",     "sector": "Management"},
    "Chercheur":     {"min": 9_000, "max": 36_000,  "xp": 180, "need": "Doctorat","sector": "Science"},
    "Professeur":    {"min": 6_000, "max": 18_000,  "xp": 140, "need": "Master",  "sector": "Éducation"},
    "Architecte":    {"min": 6_600, "max": 25_500,  "xp": 145, "need": "Master",  "sector": "Construction"},
    "Pilote":        {"min": 10_500,"max": 39_000,  "xp": 160, "need": "Master",  "sector": "Aviation"},
    "PDG":           {"min": 30_000,"max": 150_000, "xp": 500, "need": "MBA",     "sector": "Management"},
    "Influenceur":   {"min": 1_500, "max": 60_000,  "xp": 80,  "need": None,      "sector": "Médias"},
    "Artiste":       {"min": 300,   "max": 90_000,  "xp": 70,  "need": None,      "sector": "Arts"},
    "Footballeur":   {"min": 3_000, "max": 300_000, "xp":200,  "need": None,      "sector": "Sport"},
    "Hackeur":       {"min": 6_000, "max": 60_000,  "xp": 170, "need": "Licence", "sector": "Tech"},
}

EVOLUTIVE_JOBS = {
    "Développeur": [
        {"name": "Développeur Junior",     "min": 3_600, "max": 7_500, "xp": 100, "need": "Licence", "sector": "Tech", "level_req": 1},
        {"name": "Développeur Confirmé",   "min": 7_500, "max": 13_500, "xp": 150, "need": "Licence", "sector": "Tech", "level_req": 2},
        {"name": "Lead Developer",         "min": 13_500,"max": 24_000, "xp": 200, "need": "Master",  "sector": "Tech", "level_req": 3},
        {"name": "CTO",                    "min": 24_000,"max": 45_000, "xp": 300, "need": "MBA",     "sector": "Tech", "level_req": 4},
    ],
    "Manager": [
        {"name": "Assistant Manager",      "min": 5_400, "max": 10_500, "xp": 120, "need": "Licence", "sector": "Management", "level_req": 1},
        {"name": "Manager",               "min": 10_500,"max": 21_000, "xp": 150, "need": "Master",  "sector": "Management", "level_req": 2},
        {"name": "Senior Manager",        "min": 21_000,"max": 36_000, "xp": 200, "need": "MBA",     "sector": "Management", "level_req": 3},
        {"name": "Directeur Général",     "min": 36_000,"max": 75_000, "xp": 300, "need": "MBA",     "sector": "Management", "level_req": 4},
    ],
}

# ─── Skills ────────────────────────────────────────────────────────────────────
SKILLS = [
    "Charisme", "Intelligence", "Force", "Agilité",
    "Négociation", "Leadership", "Créativité", "Technique",
    "Endurance", "Discrétion",
]
SKILL_LEVEL_COST = 5_000

# ─── Real Estate (prix ×3, loyers ×3, entretien ×3) ────────────────────────────
PROPERTIES = {
    "Studio":       {"price": 150_000,    "rent": 1_500,    "maint": 600,    "emoji": "🏠"},
    "Appartement":  {"price": 450_000,    "rent": 4_500,    "maint": 1_500,  "emoji": "🏢"},
    "Maison":       {"price": 1_200_000,  "rent": 12_000,   "maint": 3_600,  "emoji": "🏡"},
    "Villa":        {"price": 4_500_000,  "rent": 45_000,   "maint": 12_000, "emoji": "🏖️"},
    "Château":      {"price": 24_000_000, "rent": 240_000,  "maint": 60_000, "emoji": "🏰"},
    "Gratte-ciel":  {"price": 150_000_000,"rent": 1_500_000,"maint": 360_000,"emoji": "🏙️"},
    "Île privée":   {"price": 1_500_000_000,"rent":15_000_000,"maint":3_000_000,"emoji":"🏝️"},
}

# ─── Vehicles (avec statistiques actives) ────────────────────────────────────
VEHICLES = {
    "Vélo":          {"price": 500,       "maint": 50,    "emoji": "🚲", "status": 1, "speed": 10,  "cargo": 5,  "luxury": 0,  "fuel_capacity": 0},
    "Scooter":       {"price": 3_000,     "maint": 200,   "emoji": "🛵", "status": 2, "speed": 30,  "cargo": 15, "luxury": 10, "fuel_capacity": 5},
    "Voiture":       {"price": 15_000,    "maint": 800,   "emoji": "🚗", "status": 3, "speed": 50,  "cargo": 40, "luxury": 30, "fuel_capacity": 40},
    "SUV":           {"price": 40_000,    "maint": 1_500, "emoji": "🚙", "status": 4, "speed": 45,  "cargo": 70, "luxury": 40, "fuel_capacity": 60},
    "Sport":         {"price": 120_000,   "maint": 4_000, "emoji": "🏎️", "status": 5, "speed": 85,  "cargo": 20, "luxury": 80, "fuel_capacity": 50},
    "Limousine":     {"price": 300_000,   "maint": 8_000, "emoji": "🚘", "status": 6, "speed": 60,  "cargo": 50, "luxury": 90, "fuel_capacity": 60},
    "Moto sport":    {"price": 25_000,    "maint": 1_200, "emoji": "🏍️", "status": 4, "speed": 80,  "cargo": 10, "luxury": 50, "fuel_capacity": 15},
    "Yacht":         {"price": 2_000_000, "maint": 50_000,"emoji": "⛵", "status": 7, "speed": 20,  "cargo": 100, "luxury": 95, "fuel_capacity": 200},
    "Jet privé":     {"price": 30_000_000,"maint": 500_000,"emoji": "✈️", "status": 8, "speed": 100, "cargo": 80, "luxury": 100, "fuel_capacity": 500},
    "Hélicoptère":   {"price": 5_000_000, "maint": 100_000,"emoji": "🚁", "status": 7, "speed": 90,  "cargo": 50, "luxury": 70, "fuel_capacity": 100},
}

# ─── Luxury Items (inchangés) ──────────────────────────────────────────────
LUXURY_ITEMS = {
    "Montre Rolex":     {"price": 50_000,    "prestige": 5,  "emoji": "⌚"},
    "Sac Hermès":       {"price": 30_000,    "prestige": 4,  "emoji": "👜"},
    "Collier diamant":  {"price": 200_000,   "prestige": 8,  "emoji": "💎"},
    "Costume Armani":   {"price": 15_000,    "prestige": 3,  "emoji": "👔"},
    "Caviar royal":     {"price": 5_000,     "prestige": 2,  "emoji": "🍾"},
    "Tableau d'art":    {"price": 500_000,   "prestige": 12, "emoji": "🖼️"},
    "Jet ski":          {"price": 25_000,    "prestige": 3,  "emoji": "🛥️"},
    "Pool house":       {"price": 1_000_000, "prestige": 15, "emoji": "🏊"},
    "Cheval de course": {"price": 3_000_000, "prestige": 20, "emoji": "🐎"},
    "Sous-marin perso": {"price": 50_000_000,"prestige":50,  "emoji": "🤿"},
}

# ─── Health ─────────────────────────────────────────────────────────────────────
MEDICINES = {
    "Aspirine":       {"price": 200,    "health": 10, "emoji": "💊"},
    "Antibiotiques":  {"price": 2_000,  "health": 30, "emoji": "💉"},
    "Chirurgie":      {"price": 50_000, "health": 80, "emoji": "🏥"},
    "Greffe":         {"price": 500_000,"health": 100,"emoji": "❤️"},
}
GYM_COST = 500
GYM_ENERGY_BONUS = 20
DOCTOR_BASE = 5_000

# ─── Travel (inchangés) ────────────────────────────────────────────────────
DESTINATIONS = {
    "Paris":          {"cost": 20_000,   "happiness": 15, "xp": 50,  "emoji": "🗼"},
    "New York":       {"cost": 50_000,   "happiness": 20, "xp": 80,  "emoji": "🗽"},
    "Tokyo":          {"cost": 60_000,   "happiness": 22, "xp": 90,  "emoji": "⛩️"},
    "Dubai":          {"cost": 80_000,   "happiness": 25, "xp": 100, "emoji": "🏙️"},
    "Maldives":       {"cost": 150_000,  "happiness": 35, "xp": 120, "emoji": "🏝️"},
    "Monaco":         {"cost": 300_000,  "happiness": 40, "xp": 150, "emoji": "🎰"},
    "Espace":         {"cost": 50_000_000,"happiness":100,"xp": 1000,"emoji": "🚀"},
}

# ─── Crime (inchangés) ─────────────────────────────────────────────────────
CRIMES = {
    "Pickpocket":     {"reward": (1_000, 5_000),    "jail": 1800,  "success": 0.70, "karma": -5},
    "Vol à l'étalage":{"reward": (2_000, 8_000),    "jail": 3600,  "success": 0.65, "karma": -8},
    "Cambriolage":    {"reward": (10_000, 50_000),   "jail": 7200,  "success": 0.55, "karma": -15},
    "Arnaque":        {"reward": (20_000, 100_000),  "jail": 10800, "success": 0.50, "karma": -20},
    "Braquage":       {"reward": (50_000, 300_000),  "jail": 21600, "success": 0.40, "karma": -30},
    "Trafic drogue":  {"reward": (100_000, 500_000), "jail": 43200, "success": 0.35, "karma": -40},
    "Cybercriminalité":{"reward":(200_000,1_000_000),"jail": 86400, "success": 0.30, "karma": -50},
    "Hold-up banque": {"reward": (500_000,2_000_000),"jail":172800, "success": 0.20, "karma": -80},
}
BAIL_COST_RATIO = 0.5
LAWYER_COST = 50_000

# ─── Casino ─────────────────────────────────────────────────────────────────────
CASINO_MIN_BET = 100
CASINO_MAX_BET = 10_000_000
ROULETTE_NUMBERS = list(range(0, 37))

# ─── Investments / Market (inchangés) ───────────────────────────────────────
ASSETS = [
    {"name": "TechCorp",     "price": 1_000,    "volatility": 0.18, "sector": "Tech",     "emoji": "💻"},
    {"name": "CryptoX",      "price": 5_000,    "volatility": 0.40, "sector": "Crypto",   "emoji": "₿"},
    {"name": "Or virtuel",   "price": 50_000,   "volatility": 0.06, "sector": "Matières", "emoji": "🥇"},
    {"name": "PétroCorp",    "price": 2_500,    "volatility": 0.14, "sector": "Énergie",  "emoji": "🛢️"},
    {"name": "PharmaCo",     "price": 8_000,    "volatility": 0.12, "sector": "Santé",    "emoji": "💊"},
    {"name": "ImmoCorp",     "price": 15_000,   "volatility": 0.08, "sector": "Immo",     "emoji": "🏢"},
    {"name": "AutoMakers",   "price": 3_000,    "volatility": 0.20, "sector": "Auto",     "emoji": "🚗"},
    {"name": "FoodChain",    "price": 1_500,    "volatility": 0.09, "sector": "Alim",     "emoji": "🍔"},
    {"name": "EnergiVerte",  "price": 4_500,    "volatility": 0.22, "sector": "Énergie",  "emoji": "⚡"},
    {"name": "LuxuryBrand",  "price": 25_000,   "volatility": 0.11, "sector": "Luxe",     "emoji": "👑"},
    {"name": "MoonCoin",     "price": 100,      "volatility": 0.80, "sector": "Crypto",   "emoji": "🌙"},
    {"name": "DefenseCo",    "price": 12_000,   "volatility": 0.07, "sector": "Défense",  "emoji": "🛡️"},
]

# ─── Banks (inchangés) ─────────────────────────────────────────────────────
BANKS = [
    {"name": "Banque Populaire",   "interest": 0.010, "min": 1_000,     "loan_max": 10_000},
    {"name": "Crédit National",    "interest": 0.020, "min": 10_000,    "loan_max": 100_000},
    {"name": "Banque Privée",      "interest": 0.035, "min": 500_000,   "loan_max": 5_000_000},
    {"name": "Banque Élite",       "interest": 0.055, "min": 5_000_000, "loan_max": 50_000_000},
    {"name": "Banque des Dieux",   "interest": 0.080, "min": 1_000_000_000, "loan_max": 1_000_000_000},
]

# ─── Garden / Farm (inchangés) ──────────────────────────────────────────────
PLANTS = {
    "Blé":          {"grow_time": 1800,  "value": 300,     "emoji": "🌾", "water_needed": 1},
    "Rose":         {"grow_time": 3600,  "value": 600,     "emoji": "🌹", "water_needed": 2},
    "Tomate":       {"grow_time": 5400,  "value": 900,     "emoji": "🍅", "water_needed": 2},
    "Fraise":       {"grow_time": 7200,  "value": 1_500,   "emoji": "🍓", "water_needed": 3},
    "Tournesol":    {"grow_time": 10800, "value": 2_000,   "emoji": "🌻", "water_needed": 2},
    "Cannabis":     {"grow_time": 21600, "value": 10_000,  "emoji": "🌿", "water_needed": 4, "illegal": True},
    "Cactus":       {"grow_time": 86400, "value": 15_000,  "emoji": "🌵", "water_needed": 1},
    "Bambou":       {"grow_time": 43200, "value": 8_000,   "emoji": "🎋", "water_needed": 3},
    "Truffe":       {"grow_time": 172800,"value": 100_000, "emoji": "🍄", "water_needed": 5},
    "Orchidée bleue":{"grow_time":259200,"value":500_000,  "emoji": "🌺", "water_needed": 6},
}

# ─── Sectors (inchangés) ───────────────────────────────────────────────────
SECTORS = [
    "Technologie", "Finance", "Commerce", "Industrie", "Santé",
    "Médias", "Immobilier", "Énergie", "Éducation", "Juridique",
    "Alimentation", "Sport", "Arts", "Défense", "Luxe",
]

# ─── Company Ranks (inchangés) ──────────────────────────────────────────────
COMPANY_RANKS = ["Stagiaire", "Assistant", "Employé", "Senior", "Responsable", "Manager", "Directeur", "PDG"]
COMPANY_CREATION_COST = 100_000_000
COMPANY_SALARY_MAP = {
    "Stagiaire":   500,    "Assistant":  2_000,  "Employé":     5_000,
    "Senior":     12_000,  "Responsable":25_000, "Manager":    50_000,
    "Directeur": 100_000,  "PDG":       250_000,
}

# ─── Social / Influence (inchangés) ─────────────────────────────────────────
SOCIAL_PLATFORMS = ["YouTube", "Instagram", "TikTok", "Twitter", "Twitch", "Podcast"]

# ─── Political (inchangés) ──────────────────────────────────────────────────
POLITICAL_POSTS = ["Conseiller municipal", "Maire", "Député", "Sénateur", "Ministre", "Président"]
ELECTION_COST = {
    "Conseiller municipal": 50_000,
    "Maire":               500_000,
    "Député":            2_000_000,
    "Sénateur":          5_000_000,
    "Ministre":         20_000_000,
    "Président":       100_000_000,
}

# ─── Arena / PvP (inchangés) ────────────────────────────────────────────────
ARENA_MODES = ["1v1 Combat", "Tournoi", "Battle Royale", "Gladiateur", "Parie"]

# ─── Titles / Prestige (inchangés) ──────────────────────────────────────────
TITLES = [
    {"min": 0, "title": "🧸 Nouveau-né"},
    {"min": 10_000, "title": "👶 Citoyen"},
    {"min": 100_000, "title": "🏘️ Propriétaire"},
    {"min": 500_000, "title": "💼 Investisseur"},
    {"min": 1_000_000, "title": "💰 Millionnaire"},
    {"min": 10_000_000, "title": "🦁 Lion"},
    {"min": 50_000_000, "title": "👑 Roi"},
    {"min": 100_000_000, "title": "💎 Légende"},
    {"min": 1_000_000_000, "title": "⭐ Dieu"},
]

PRESTIGE_RANKS = [
    {"min": 0,   "name": "🥚 Inconnu"},
    {"min": 10,  "name": "🌱 Bourgeois"},
    {"min": 50,  "name": "💼 Notable"},
    {"min": 150, "name": "🎩 Aristocrate"},
    {"min": 300, "name": "💎 Élite"},
    {"min": 600, "name": "👑 Noblesse"},
    {"min": 1000,"name": "⭐ Légende"},
]

# ─── VIP (Véhicules) ──────────────────────────────────────────────────────────
VIP_LUXE_REQUIRED = 70  # Niveau de luxe minimum pour l'accès VIP
VIP_LIEUX = [
    "🥂 Lounge privé du Casino Royal",
    "🏝️ Plage privée du Yacht Club",
    "🎭 Balcon VIP de l'Opéra",
    "🍾 Salon privé du Ritz",
    "🎰 Salle de jeux exclusive",
    "🚁 Héliport privé",
    "⛵ Marina VIP",
    "🏛️ Galerie d'art privée",
]

# ─── Badges (inchangés) ────────────────────────────────────────────────────
BADGES = [
    "Millionnaire", "Milliardaire", "Docteur", "MBA", "Guerrier", "Champion",
    "Voyageur", "Globe-trotter", "Influenceur", "Criminel", "Saint", "Démon",
    "Philanthrope", "Fermier", "Accompli", "Prestigieux", "Légendaire", "Survivant",
    "Globe-trotter", "Collector", "Boss", "Hacker", "Politicien", "Chef d'entreprise",
]

# ─── Events (inchangés) ──────────────────────────────────────────────────
WORLD_EVENTS = [
    {"name": "Crash boursier",       "effect": "market_crash",    "severity": 0.4},
    {"name": "Boom économique",       "effect": "market_boom",     "severity": 0.3},
    {"name": "Pandémie mondiale",     "effect": "health_crisis",   "severity": 0.5},
    {"name": "Révolution tech",       "effect": "tech_boom",       "severity": 0.25},
    {"name": "Guerre commerciale",    "effect": "trade_war",       "severity": 0.3},
    {"name": "Élections mondiales",   "effect": "political_shift", "severity": 0.2},
    {"name": "Catastrophe naturelle", "effect": "disaster",        "severity": 0.6},
    {"name": "Découverte énergie",    "effect": "energy_shift",    "severity": 0.35},
    {"name": "Crise immobilière",     "effect": "realestate_crash","severity": 0.45},
    {"name": "Festival mondial",      "effect": "happiness_boost", "severity": 0.1},
]

# ─── Missions avec livraisons intégrées ──────────────────────────────────────
DAILY_MISSIONS = [
    {"name": "Travailler 3 fois",          "type": "work",       "target": 3,          "reward": 15_000,     "xp": 200},
    {"name": "Gagner 30K en jeux",         "type": "casino_win", "target": 30_000,     "reward": 45_000,     "xp": 300},
    {"name": "Payer qqun",                 "type": "pay",        "target": 1,          "reward": 3_000,      "xp": 100},
    {"name": "Planter 3 plantes",          "type": "plant",      "target": 3,          "reward": 9_000,      "xp": 150},
    {"name": "Investir en bourse",         "type": "invest",     "target": 1,          "reward": 22_500,     "xp": 250},
    {"name": "Se connecter",               "type": "login",      "target": 1,          "reward": 1_500,      "xp": 50},
    {"name": "Commettre un crime",         "type": "crime",      "target": 1,          "reward": 15_000,     "xp": 200},
    {"name": "Voyager",                    "type": "travel",     "target": 1,          "reward": 30_000,     "xp": 400},
    {"name": "Récolter des plantes",       "type": "harvest",    "target": 2,          "reward": 7_500,      "xp": 120},
    {"name": "Dépenser 150K en luxe",      "type": "luxury",     "target": 150_000,    "reward": 60_000,     "xp": 500},
    # ─── MISSIONS DE LIVRAISON ───
    {"name": "📦 Livraison légère",        "type": "delivery",   "target": 1,          "reward": 20_000,     "xp": 200,  "cargo_required": 0},
    {"name": "📦 Livraison lourde",        "type": "delivery",   "target": 1,          "reward": 50_000,     "xp": 500,  "cargo_required": 50},
    {"name": "📦 Livraison massive",       "type": "delivery",   "target": 1,          "reward": 100_000,    "xp": 1000, "cargo_required": 80},
]

WEEKLY_MISSIONS = [
    {"name": "Atteindre 300K de solde",    "type": "balance",    "target": 300_000,    "reward": 150_000,   "xp": 1000},
    {"name": "Gagner 5 combats d'arène",   "type": "arena_win",  "target": 5,          "reward": 225_000,   "xp": 1500},
    {"name": "Monter de niveau d'études",  "type": "diploma",    "target": 1,          "reward": 90_000,    "xp": 800},
    {"name": "Recruter 3 employés",        "type": "hire",       "target": 3,          "reward": 300_000,   "xp": 2000},
    {"name": "Voyager 3 fois",             "type": "travel",     "target": 3,          "reward": 240_000,   "xp": 1800},
    # ─── MISSIONS DE LIVRAISON HEBDOMADAIRES ───
    {"name": "📦 10 livraisons",           "type": "delivery",   "target": 10,         "reward": 500_000,   "xp": 2000, "cargo_required": 30},
]

# ─── Black Market (inchangés) ───────────────────────────────────────────────
BLACK_MARKET_ITEMS = {
    "Faux passeport":     {"price": 200_000,   "risk": 0.30, "karma": -25, "emoji": "📄"},
    "Arme illégale":      {"price": 500_000,   "risk": 0.40, "karma": -40, "emoji": "🔫"},
    "Dossier volé":       {"price": 100_000,   "risk": 0.25, "karma": -20, "emoji": "📂"},
    "Carte blanche":      {"price": 1_000_000, "risk": 0.15, "karma": -10, "emoji": "💳"},
    "Hack gouvernemental":{"price": 5_000_000, "risk": 0.50, "karma": -60, "emoji": "💻"},
    "Formule secrète":    {"price": 3_000_000, "risk": 0.35, "karma": -30, "emoji": "🧪"},
    "Relique volée":      {"price": 800_000,   "risk": 0.30, "karma": -25, "emoji": "🏺"},
    "Organes":            {"price": 2_000_000, "risk": 0.60, "karma": -80, "emoji": "🫀"},
}

# ─── Hacking (inchangés) ───────────────────────────────────────────────────
HACK_TARGETS = {
    "Particulier":      {"reward": (5_000, 30_000),   "success": 0.70, "skill_need": 0},
    "PME":              {"reward": (50_000, 200_000),  "success": 0.55, "skill_need": 3},
    "Grande entreprise":{"reward": (500_000,2_000_000),"success": 0.35, "skill_need": 6},
    "Banque centrale":  {"reward": (5_000_000,20_000_000),"success":0.15,"skill_need": 9},
    "Gouvernement":     {"reward": (10_000_000,50_000_000),"success":0.05,"skill_need": 12},
}

# ─── Cities (inchangés) ────────────────────────────────────────────────────
CITIES = {
    "Paris": {
        "realestate_mult": 1.5,
        "vehicle_mult": 1.2,
        "salary_mult": 1.3,
        "market_mult": 1.1,
        "crime_mult": 0.8,
        "emoji": "🗼"
    },
    "New York": {
        "realestate_mult": 1.8,
        "vehicle_mult": 1.3,
        "salary_mult": 1.5,
        "market_mult": 1.2,
        "crime_mult": 0.9,
        "emoji": "🗽"
    },
    "Dubai": {
        "realestate_mult": 2.0,
        "vehicle_mult": 1.5,
        "salary_mult": 1.7,
        "market_mult": 1.3,
        "crime_mult": 0.7,
        "emoji": "🏙️"
    },
    "Campagne": {
        "realestate_mult": 0.6,
        "vehicle_mult": 0.8,
        "salary_mult": 0.7,
        "market_mult": 0.9,
        "crime_mult": 1.2,
        "emoji": "🌾"
    },
}

# ─── Calendar / Seasons (inchangés) ──────────────────────────────────────
SEASONS = ["Printemps", "Été", "Automne", "Hiver"]
SEASON_EFFECTS = {
    "Printemps": {"garden_mult": 1.2, "happiness_gain": 2, "crime_mult": 1.0},
    "Été":       {"garden_mult": 1.5, "happiness_gain": 5, "crime_mult": 1.1},
    "Automne":   {"garden_mult": 0.8, "happiness_gain": 0, "crime_mult": 0.9},
    "Hiver":     {"garden_mult": 0.5, "happiness_gain": -5, "crime_mult": 0.8},
}

# ─── Crafting ──────────────────────────────────────────────────────────────────
CRAFTING_RECIPES = {
    "kit_soins": {
        "name": "Kit de soins",
        "ingredients": [
            {"item": "Potion de soin", "quantity": 2},
            {"item": "Potion d'énergie", "quantity": 1},
        ],
        "output": {
            "name": "Kit de soins",
            "type": "consumable",
            "rarity": "rare",
            "value": 2500,
            "effect_type": "heal",
            "effect_value": 50,
            "emoji": "🧰",
            "description": "Restaure 50% de santé",
            "quantity": 1,
        },
    },
    "seringue_adrenaline": {
        "name": "Seringue d'adrénaline",
        "ingredients": [
            {"item": "Potion d'énergie", "quantity": 2},
            {"item": "Parchemin d'XP", "quantity": 1},
        ],
        "output": {
            "name": "Seringue d'adrénaline",
            "type": "consumable",
            "rarity": "rare",
            "value": 3200,
            "effect_type": "energy",
            "effect_value": 60,
            "emoji": "💉",
            "description": "Restaure 60% d'énergie",
            "quantity": 1,
        },
    },
    "guide_strategique": {
        "name": "Guide stratégique",
        "ingredients": [
            {"item": "Parchemin d'XP", "quantity": 2},
            {"item": "Petit coffre", "quantity": 1},
        ],
        "output": {
            "name": "Guide stratégique",
            "type": "consumable",
            "rarity": "epic",
            "value": 7500,
            "effect_type": "xp",
            "effect_value": 350,
            "emoji": "📘",
            "description": "Donne 350 XP",
            "quantity": 1,
        },
    },
    "caisse_de_survie": {
        "name": "Caisse de survie",
        "ingredients": [
            {"item": "Potion de soin", "quantity": 1},
            {"item": "Potion d'énergie", "quantity": 1},
            {"item": "Petit coffre", "quantity": 1},
        ],
        "output": {
            "name": "Caisse de survie",
            "type": "consumable",
            "rarity": "epic",
            "value": 10000,
            "effect_type": "money",
            "effect_value": 15000,
            "emoji": "📦",
            "description": "Contient 15 000 coins",
            "quantity": 1,
        },
    },
}

# ─── Misc (inchangés) ──────────────────────────────────────────────────────
XP_PER_LEVEL = 1_000
LOTTERY_TICKET_COST = 10_000
LOTTERY_JACKPOT_BASE = 10_000_000
START_PHOTO_PATH = "IMG_20260608_154531_402.jpg"

# ============================================================================
# ======================  SOCIAL AVANCÉ (NOUVEAU)  ============================
# ============================================================================

# Paramètres généraux
SOCIAL_PLATFORMS = ["YouTube", "Instagram", "TikTok", "Twitter", "Twitch", "Podcast"]  # inchangé mais redéfini pour clarté

# Coûts et taxes
SOCIAL_COLLAB_COST = 50_000               # coût fixe d'une collaboration (en argent)
SOCIAL_TRANSFER_TAX = 0.10                # 10% de taxe sur les ventes de followers

# Tendances
SOCIAL_TREND_COST = 5_000                 # coût pour lancer une tendance (en argent)
SOCIAL_TREND_DURATION = 86400             # 24 heures en secondes
SOCIAL_TREND_MULTIPLIER = 1.5             # multiplicateur de gains pour ceux qui utilisent la tendance

# Stories et Lives
SOCIAL_STORY_DURATION = 86400             # 24h avant disparition
SOCIAL_MIN_FOLLOWERS_FOR_LIVE = 5_000     # seuil minimal pour lancer un live

# Économie interne (SocialCoins)
SOCIAL_COIN_NAME = "SocialCoin"           # nom de la monnaie
SOCIAL_COIN_STARTING_BALANCE = 100        # solde initial

# ============================================================================
# ======================  POLITIQUE AVANCÉE (NOUVEAU)  ========================
# ============================================================================

# Élections
ELECTION_KARMA_REQUIRED = 100
ELECTION_QUORUM = 0.20                    # 20% des joueurs inscrits
ELECTION_COOLDOWN = 7 * 86400             # 7 jours
ELECTION_DEFAULT_DURATION_HOURS = 24
ELECTION_MAX_DURATION_HOURS = 72

# Partis politiques
PARTY_CREATION_COST = 500_000
PARTY_MIN_MEMBERS = 2
PARTY_MAX_MEMBERS = 100

# Lois
LAW_PROPOSAL_COST = 50_000
LAW_VOTE_DURATION = 48 * 3600             # 48h
LAW_DEFAULT_MAJORITY = 0.5

# Référendums
REFERENDUM_COST = 100_000
REFERENDUM_DURATION = 72 * 3600           # 72h

# Destitution (motion de censure)
MOTION_SIGNATURES_NEEDED = 5
MOTION_VOTE_DURATION = 48 * 3600
MOTION_MAJORITY = 0.5
MOTION_COOLDOWN = 3 * 86400               # 3 jours

# Cabinet (ministères)
CABINET_POSITIONS = ["Économie", "Défense", "Justice", "Intérieur", "Éducation", "Santé", "Affaires étrangères"]

# Constitution par défaut
DEFAULT_CONSTITUTION = "Le pouvoir émane des joueurs. Les élections sont libres. Les lois sont votées par tous."

# ─── Casino horaire ──────────────────────────────────────────────────────────
TIME_MULTIPLIER = 24          # 1 jour de jeu = 1 heure réelle
CASINO_OPEN_HOUR = 17         # 22h (heure de jeu)
CASINO_CLOSE_HOUR = 5         # 5h (heure de jeu) – le casino ferme à 5h du matin

PRODUCT_EFFECTS = {
    "heal":    {"base_cost": 2000000,   "desc": "Restaure de la santé"},
    "energy":  {"base_cost": 1500000,   "desc": "Restaure de l'énergie"},
    "xp":      {"base_cost": 3000000,   "desc": "Donne de l'XP"},
    "money":   {"base_cost": 2500000,   "desc": "Donne de l'argent"},
    "buff":    {"base_cost": 5000000,   "desc": "Boost temporaire (stat) : à définir"},
}