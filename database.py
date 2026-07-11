# database.py — version Phase 2 avec colonnes last_maintenance + social avancé + politique avancée + téléphone + véhicules 2.0
import aiosqlite
import asyncio
import time
import logging
from contextlib import asynccontextmanager
from config import (
    DB_PATH, STARTING_BALANCE, PLANTS, ASSETS, DB_VERSION, SOCIAL_COIN_STARTING_BALANCE,
    SQLITE_POOL_SIZE, CRAFTING_RECIPES
)

logger = logging.getLogger(__name__)

DB_TIMEOUT = 60.0
DB_POOL_SIZE = SQLITE_POOL_SIZE

def now():
    return int(time.time())


async def _configure_connection(db: aiosqlite.Connection):
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute(f"PRAGMA busy_timeout={int(DB_TIMEOUT * 1000)}")
    await db.execute("PRAGMA cache_size=10000")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA temp_store=MEMORY")
    await db.execute("PRAGMA wal_autocheckpoint=1000")


class SQLiteConnectionPool:
    def __init__(self, db_path: str, size: int = DB_POOL_SIZE):
        self.db_path = db_path
        self.size = size
        self._queue: asyncio.LifoQueue[aiosqlite.Connection] = asyncio.LifoQueue()
        self._created = 0
        self._lock = asyncio.Lock()

    async def _create_connection(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.db_path, timeout=DB_TIMEOUT)
        await _configure_connection(db)
        return db

    async def acquire(self) -> aiosqlite.Connection:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            async with self._lock:
                if self._created < self.size:
                    db = await self._create_connection()
                    self._created += 1
                    return db
            return await self._queue.get()

    async def release(self, db: aiosqlite.Connection):
        try:
            await db.rollback()
        except Exception:
            pass
        db.row_factory = None
        self._queue.put_nowait(db)


_DB_POOL = SQLiteConnectionPool(DB_PATH)


@asynccontextmanager
async def db_connection(row_factory=None):
    db = await _DB_POOL.acquire()
    db.row_factory = row_factory
    try:
        yield db
    finally:
        await _DB_POOL.release(db)

# ═══════════════════ ARGENT ATOMIQUE ═══════════════════
class InsufficientFunds(Exception):
    pass


async def debit_balance(user_id: int, amount: int) -> bool:
    if amount <= 0:
        raise ValueError("Le montant à débiter doit être positif")
    async with db_connection() as db:
        cur = await db.execute(
            "UPDATE users SET balance = balance - ? "
            "WHERE user_id = ? AND balance >= ?",
            (amount, user_id, amount),
        )
        await db.commit()
        return cur.rowcount > 0


async def credit_balance(user_id: int, amount: int) -> None:
    if amount <= 0:
        raise ValueError("Le montant à créditer doit être positif")
    async with db_connection() as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id),
        )
        await db.commit()


async def transfer_money(from_id: int, to_id: int, amount: int, tax: int = 0) -> bool:
    if amount <= 0:
        raise ValueError("Le montant à transférer doit être positif")
    if tax < 0 or tax >= amount:
        raise ValueError("Taxe invalide")
    async with db_connection() as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "UPDATE users SET balance = balance - ? "
                "WHERE user_id = ? AND balance >= ?",
                (amount, from_id, amount),
            )
            if cur.rowcount == 0:
                await db.rollback()
                return False
            await db.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (amount - tax, to_id),
            )
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            raise


# ═══════════════════ MIGRATIONS ═══════════════════
MIGRATIONS = [
    # (1, ["ALTER TABLE users ADD COLUMN vip_until INTEGER DEFAULT 0"]),
]


async def _column_exists(db, table: str, column: str) -> bool:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        cols = await cur.fetchall()
    return any(c[1] == column for c in cols)


async def run_migrations():
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await _configure_connection(db)
        async with db.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        current = row[0] if row else 0
        target = max((v for v, _ in MIGRATIONS), default=0)
        if current >= target:
            logger.info("✅ DB à jour (version %s).", current)
            return
        for version, statements in sorted(MIGRATIONS, key=lambda m: m[0]):
            if version <= current:
                continue
            logger.info("🔄 Migration vers la version %s...", version)
            try:
                await db.execute("BEGIN")
                for sql in statements:
                    await db.execute(sql)
                await db.execute(f"PRAGMA user_version = {version}")
                await db.commit()
                logger.info("✅ Migration %s appliquée.", version)
            except Exception:
                await db.rollback()
                logger.exception("❌ Échec de la migration %s, annulée.", version)
                raise

# ─── Le reste du fichier (inchangé, sauf la suppression des doubles CREATE TABLE contracts) ───

ALLOWED_FIELDS = {
    "username", "balance", "xp", "level", "karma", "prestige", "health",
    "energy", "happiness", "hunger", "stress", "age", "diplome", "job",
    "job_xp", "job_level", "location", "bio", "profile_pic", "profile_color",
    "prison_until", "hospital_until", "travel_until", "warnings", "banned",
    "god_mode", "defense_level", "legacy_level", "last_life_tick", "lifestyle_score",
    "study_revisions", "study_effort", "study_diplome", "study_start", 
    "plant_cooldown", "harvest_cooldown", "water_cooldown", "last_event_day", 
    "study_cooldown", "revision_cooldown", "formation_cooldown", 
    "last_seen", "work_last", "daily_last", "charity_given", "sleep_last", 
    "casino_total_bet", "casino_total_win", "last_crime", "social_followers", 
    "registered", "arena_wins", "arena_losses", "user_id", "crimes_done", "crimes_success", 
    "created_at", "political_post", "hack_attempts",
    "medecin_last", "gym_last", "medicaments_last", "hospital_last", "doctor_last", "hopital_last",
    "assurance_subscribe", "insure_vehicle",
    "total_earned", "total_spent",
    "missions_done",
    "travel_count",
    "plants_grown",
    "last_hold",
    "luxury_buy",
    "buy_property", "mortgage", "rent_property", "sell_property", "maintain_property",
    "propose_rental",
    "buy_vehicle", "repair_vehicle", "sell_vehicle",
    "post_cooldown", "collab_cooldown",
    "vote_cooldown",
    "buy_cooldown", "sell_cooldown",
    "report_cooldown",
    "guild_chat", "parier_cooldown",
    "story_cooldown", "live_cooldown", "social_rating_cooldown",
    "create_community_cooldown", "share_cooldown", "trend_cooldown",
    "poll_cooldown", "use_cooldown", "don_cooldown", "casino_last",
    "social_rating", "social_coins",
    "referred_by", "referral_count", "referral_rewards", "referral_activated",
    # Nouvelles colonnes politiques
    "last_election", "last_motion", "last_constitution", "last_hack",
    # Téléphone
    "phone_theme", "phone_ringtone",
    # Véhicules 2.0
    "active_vehicle_id"
}

async def init_db():
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        await _configure_connection(db)

        # ---------- Table users ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT DEFAULT '',
            full_name   TEXT DEFAULT '',
            balance     INTEGER DEFAULT 0 CHECK (balance >= 0),
            xp          INTEGER DEFAULT 0,
            level       INTEGER DEFAULT 1,
            karma       INTEGER DEFAULT 0,
            prestige    INTEGER DEFAULT 0,
            health      INTEGER DEFAULT 100,
            energy      INTEGER DEFAULT 100,
            happiness   INTEGER DEFAULT 100,
            hunger      INTEGER DEFAULT 100,
            stress      INTEGER DEFAULT 0,
            age         INTEGER DEFAULT 18,
            diplome     TEXT DEFAULT '',
            job         TEXT DEFAULT '',
            job_xp      INTEGER DEFAULT 0,
            job_level   INTEGER DEFAULT 1,
            daily_last  INTEGER DEFAULT 0,
            work_last   INTEGER DEFAULT 0,
            study_start INTEGER DEFAULT 0,
            study_diplome TEXT DEFAULT '',
            study_effort  REAL DEFAULT 1.0,
            study_revisions INTEGER DEFAULT 0,
            prison_until INTEGER DEFAULT 0,
            hospital_until INTEGER DEFAULT 0,
            travel_until INTEGER DEFAULT 0,
            profile_color TEXT DEFAULT '🔵',
            profile_pic   TEXT DEFAULT '',
            bio           TEXT DEFAULT '',
            location      TEXT DEFAULT '',
            registered    INTEGER DEFAULT 0,
            created_at    INTEGER DEFAULT 0,
            last_seen     INTEGER DEFAULT 0,
            warnings      INTEGER DEFAULT 0,
            banned        INTEGER DEFAULT 0,
            god_mode      INTEGER DEFAULT 0,
            total_earned  INTEGER DEFAULT 0,
            total_spent   INTEGER DEFAULT 0,
            crimes_done   INTEGER DEFAULT 0,
            crimes_success INTEGER DEFAULT 0,
            missions_done INTEGER DEFAULT 0,
            arena_wins    INTEGER DEFAULT 0,
            arena_losses  INTEGER DEFAULT 0,
            travel_count  INTEGER DEFAULT 0,
            lottery_wins  INTEGER DEFAULT 0,
            casino_total_bet INTEGER DEFAULT 0,
            casino_total_win INTEGER DEFAULT 0,
            plants_grown  INTEGER DEFAULT 0,
            hack_attempts INTEGER DEFAULT 0,
            social_followers INTEGER DEFAULT 0,
            political_post TEXT DEFAULT '',
            influence_score INTEGER DEFAULT 0,
            charity_given INTEGER DEFAULT 0,
            insured       INTEGER DEFAULT 0,
            insurance_last INTEGER DEFAULT 0,
            frozen_until  INTEGER DEFAULT 0,
            defense_level INTEGER DEFAULT 0,
            last_crime    INTEGER DEFAULT 0,
            sleep_last    INTEGER DEFAULT 0,
            last_hack     INTEGER DEFAULT 0,
            legacy_level  INTEGER DEFAULT 0,
            last_life_tick INTEGER DEFAULT 0,
            lifestyle_score INTEGER DEFAULT 50,
            db_version    INTEGER DEFAULT 0,
            last_event_day INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT 0,
            referral_count INTEGER DEFAULT 0,
            referral_rewards INTEGER DEFAULT 0,
            referral_activated INTEGER DEFAULT 0,
            phone_theme   TEXT DEFAULT 'dark',
            phone_ringtone TEXT DEFAULT 'classic',
            active_vehicle_id INTEGER DEFAULT 0
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_balance ON users(balance DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_xp ON users(xp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)")

        # ---------- Table bank_accounts ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS bank_accounts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            bank_name   TEXT,
            balance     INTEGER DEFAULT 0,
            loan        INTEGER DEFAULT 0,
            loan_due    INTEGER DEFAULT 0,
            loan_penalty_applied INTEGER DEFAULT 0,
            opened_at   INTEGER,
            last_interest INTEGER DEFAULT 0,
            UNIQUE(user_id, bank_name)
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bank_user ON bank_accounts(user_id)")

        # ---------- Table properties ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            prop_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            prop_type   TEXT,
            name        TEXT DEFAULT '',
            rented_to   INTEGER DEFAULT 0,
            rented_at   INTEGER DEFAULT 0,
            purchased_at INTEGER,
            condition   INTEGER DEFAULT 100,
            mortgage    INTEGER DEFAULT 0,
            mortgage_due INTEGER DEFAULT 0
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_properties_user ON properties(user_id)")

        # ---------- Table rental_proposals ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS rental_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            tenant_id INTEGER NOT NULL,
            rent INTEGER NOT NULL,
            proposed_at INTEGER NOT NULL,
            status TEXT DEFAULT 'pending'
        )""")

        # ---------- Table rental_agreements ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS rental_agreements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            tenant_id INTEGER NOT NULL,
            rent INTEGER NOT NULL,
            start_date INTEGER NOT NULL,
            end_date INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active'
        )""")

        # ---------- Table vehicles ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS vehicles (
            veh_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            veh_type    TEXT,
            condition   INTEGER DEFAULT 100,
            insured     INTEGER DEFAULT 0,
            purchased_at INTEGER,
            last_maintenance INTEGER DEFAULT 0,
            speed       INTEGER DEFAULT 0,
            cargo       INTEGER DEFAULT 0,
            luxury      INTEGER DEFAULT 0,
            fuel        INTEGER DEFAULT 0
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_vehicles_user ON vehicles(user_id)")

        # ---------- Table skills ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            user_id     INTEGER,
            skill_name  TEXT,
            level       INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, skill_name)
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_skills_user ON skills(user_id)")

        # ---------- Table garden ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS garden (
            plot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            plant_type  TEXT,
            planted_at  INTEGER,
            watered_at  INTEGER DEFAULT 0,
            water_count INTEGER DEFAULT 0,
            ready       INTEGER DEFAULT 0
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_garden_user ON garden(user_id)")

        # ---------- Table investments ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS investments (
            inv_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            asset_name  TEXT,
            quantity    REAL DEFAULT 0,
            avg_price   REAL DEFAULT 0,
            bought_at   INTEGER
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_investments_user ON investments(user_id)")

        # ---------- Table market_prices ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS market_prices (
            asset_name  TEXT PRIMARY KEY,
            price       REAL,
            updated_at  INTEGER
        )""")

        # ---------- Table companies ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            company_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE,
            owner_id    INTEGER,
            sector      TEXT,
            treasury    INTEGER DEFAULT 0,
            reputation  INTEGER DEFAULT 50,
            level       INTEGER DEFAULT 1,
            dissolved   INTEGER DEFAULT 0,
            created_at  INTEGER,
            rd_level    INTEGER DEFAULT 0,
            overhead    INTEGER DEFAULT 1000
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS company_members (
            user_id     INTEGER,
            company_id  INTEGER,
            poste       TEXT DEFAULT 'Employé',
            salary      INTEGER DEFAULT 0,
            base_salary INTEGER DEFAULT 0,
            activity_score INTEGER DEFAULT 0,
            joined_at   INTEGER,
            PRIMARY KEY (user_id, company_id)
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_company_members_user ON company_members(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_company_members_company ON company_members(company_id)")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS company_applications (
            app_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            company_id  INTEGER,
            status      TEXT DEFAULT 'pending',
            desired_salary INTEGER DEFAULT 0,
            applied_at  INTEGER
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS company_logs (
            log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id  INTEGER,
            action      TEXT,
            actor_id    INTEGER,
            details     TEXT,
            timestamp   INTEGER
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS company_shares (
            user_id     INTEGER,
            company_id  INTEGER,
            shares      INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, company_id)
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_company_shares_company ON company_shares(company_id)")

        # -------- Table company_contracts (version enrichie) --------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS company_contracts (
            contract_id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_company INTEGER NOT NULL,
            to_company   INTEGER NOT NULL,
            amount       INTEGER NOT NULL,
            duration     INTEGER NOT NULL,
            status       TEXT DEFAULT 'pending',
            proposed_at  INTEGER NOT NULL
        )""")

        # -------- Table contracts (version enrichie, avec accepted_at et end_date) --------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS contracts (
            contract_id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_company INTEGER NOT NULL,
            to_company INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            duration INTEGER NOT NULL,         -- en jours
            contract_type TEXT DEFAULT 'service',
            status TEXT DEFAULT 'pending',
            proposed_at INTEGER NOT NULL,
            accepted_at INTEGER DEFAULT 0,
            end_date INTEGER DEFAULT 0
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS company_products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id  INTEGER NOT NULL,
            name        TEXT NOT NULL,
            price       INTEGER NOT NULL,
            sales       INTEGER DEFAULT 0,
            revenue     INTEGER DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS company_ads (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id  INTEGER NOT NULL,
            poste       TEXT NOT NULL,
            salary      INTEGER NOT NULL,
            created_at  INTEGER NOT NULL
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS company_invitations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            company_id  INTEGER NOT NULL,
            poste       TEXT NOT NULL,
            salary      INTEGER NOT NULL,
            invited_by  INTEGER NOT NULL,
            invited_at  INTEGER NOT NULL,
            status      TEXT DEFAULT 'pending',
            UNIQUE(user_id, company_id)
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_invitations_user ON company_invitations(user_id, status)")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS sector_events (
            event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            sector      TEXT NOT NULL,
            description TEXT NOT NULL,
            event_date  INTEGER NOT NULL
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sector_events_date ON sector_events(event_date DESC)")
        # Fusions
        await db.execute("""
        CREATE TABLE IF NOT EXISTS fusion_requests (
            request_id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_company INTEGER NOT NULL,
            to_company INTEGER NOT NULL,
            amount INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            responded_at INTEGER DEFAULT 0
        )""")

        # ---------- Table family ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS family (
            family_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT DEFAULT '',
            founder_id  INTEGER,
            created_at  INTEGER
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS family_members (
            user_id     INTEGER PRIMARY KEY,
            family_id   INTEGER,
            role        TEXT DEFAULT 'Membre',
            joined_at   INTEGER
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS family_invites (
            family_id   INTEGER NOT NULL,
            invited_id  INTEGER NOT NULL,
            invited_by  INTEGER NOT NULL,
            created_at  INTEGER NOT NULL,
            PRIMARY KEY (family_id, invited_id)
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS marriages (
            user_id     INTEGER PRIMARY KEY,
            partner_id  INTEGER,
            married_at  INTEGER,
            status      TEXT DEFAULT 'active',
            divorced_at INTEGER DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS marriage_requests (
            from_id     INTEGER,
            to_id       INTEGER,
            created_at  INTEGER,
            PRIMARY KEY (from_id, to_id)
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS friendships (
            user_id     INTEGER,
            friend_id   INTEGER,
            since       INTEGER,
            PRIMARY KEY (user_id, friend_id)
        )""")

        # ---------- Table inventory ----------
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='inventory'")
        table_exists = await cursor.fetchone()

        if table_exists:
            cursor = await db.execute("PRAGMA index_list('inventory')")
            indexes = await cursor.fetchall()
            bad_constraint = False
            for idx in indexes:
                idx_name = idx[1]
                if idx_name in ('sqlite_autoindex_inventory_1', 'idx_inventory_item'):
                    bad_constraint = True
                    break
            if bad_constraint:
                logger.warning("🔄 Correction de la table inventory (contrainte UNIQUE sur item_id seul)...")
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute("""
                    CREATE TABLE inventory_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        item_id INTEGER,
                        item_type TEXT,
                        item_name TEXT,
                        quantity INTEGER DEFAULT 1,
                        acquired_at INTEGER,
                        UNIQUE(user_id, item_id)
                    )
                """)
                await db.execute("""
                    INSERT OR REPLACE INTO inventory_new (user_id, item_id, item_type, item_name, quantity, acquired_at)
                    SELECT user_id, item_id, item_type, item_name, SUM(quantity), MIN(acquired_at)
                    FROM inventory
                    GROUP BY user_id, item_id
                """)
                await db.execute("DROP TABLE inventory")
                await db.execute("ALTER TABLE inventory_new RENAME TO inventory")
                await db.execute("PRAGMA foreign_keys=ON")
                logger.info("✅ Table inventory corrigée avec contrainte UNIQUE(user_id, item_id)")
            else:
                await db.execute("CREATE INDEX IF NOT EXISTS idx_inventory_user ON inventory(user_id)")
                await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_user_item ON inventory(user_id, item_id)")
        else:
            await db.execute("""
                CREATE TABLE inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    item_id INTEGER,
                    item_type TEXT,
                    item_name TEXT,
                    quantity INTEGER DEFAULT 1,
                    acquired_at INTEGER,
                    UNIQUE(user_id, item_id)
                )
            """)

        # ---------- Table items ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS items (
            item_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            type        TEXT NOT NULL,
            rarity      TEXT DEFAULT 'common',
            value       INTEGER DEFAULT 0,
            effect_type TEXT,
            effect_value INTEGER DEFAULT 0,
            emoji       TEXT DEFAULT '📦',
            description TEXT
        )""")

        # ---------- Table market_listings ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS market_listings (
            listing_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id   INTEGER NOT NULL,
            item_id     INTEGER NOT NULL,
            quantity    INTEGER NOT NULL DEFAULT 1,
            price       INTEGER NOT NULL,
            created_at  INTEGER NOT NULL,
            expires_at  INTEGER NOT NULL,
            status      TEXT DEFAULT 'active'
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_market_listings ON market_listings(status, item_id, price)")

        # ---------- Table auction_listings ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS auction_listings (
            listing_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id   INTEGER,
            item_name   TEXT,
            item_type   TEXT,
            start_price INTEGER,
            current_bid INTEGER DEFAULT 0,
            highest_bidder INTEGER DEFAULT 0,
            ends_at     INTEGER,
            status      TEXT DEFAULT 'active'
        )""")

        # ---------- Table crime_log ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS crime_log (
            log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            crime_type  TEXT,
            success     INTEGER,
            reward      INTEGER DEFAULT 0,
            jail_time   INTEGER DEFAULT 0,
            timestamp   INTEGER
        )""")

        # ---------- Table travel_log ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS travel_log (
            log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            destination TEXT,
            cost        INTEGER,
            timestamp   INTEGER
        )""")

        # ---------- Table missions ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            user_id     INTEGER,
            mission_name TEXT,
            mission_type TEXT,
            progress    INTEGER DEFAULT 0,
            target      INTEGER,
            reward      INTEGER,
            xp_reward   INTEGER,
            completed   INTEGER DEFAULT 0,
            completed_at INTEGER DEFAULT 0,
            period      TEXT DEFAULT 'daily',
            reset_at    INTEGER,
            PRIMARY KEY (user_id, mission_name, period)
        )""")

        # ---------- Table politics (ancienne, gardée pour compatibilité ascendante) ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS politics (
            user_id     INTEGER PRIMARY KEY,
            post        TEXT DEFAULT '',
            votes       INTEGER DEFAULT 0,
            approval    INTEGER DEFAULT 50,
            mandate_end INTEGER DEFAULT 0,
            campaign_fund INTEGER DEFAULT 0,
            last_decree INTEGER DEFAULT 0
        )""")

        # ---------- Table social_media ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_media (
            user_id     INTEGER,
            platform    TEXT,
            followers   INTEGER DEFAULT 0,
            posts       INTEGER DEFAULT 0,
            revenue_per_day INTEGER DEFAULT 0,
            last_post   INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, platform)
        )""")

        # ---------- Table blackmarket_log ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS blackmarket_log (
            log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            item_name   TEXT,
            price       INTEGER,
            success     INTEGER,
            timestamp   INTEGER
        )""")

        # ---------- Table world_events ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS world_events (
            event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT,
            effect      TEXT,
            severity    REAL,
            started_at  INTEGER,
            ends_at     INTEGER,
            active      INTEGER DEFAULT 1
        )""")

        # ---------- Table lottery_tickets ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS lottery_tickets (
            ticket_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            draw_id     INTEGER,
            numbers     TEXT,
            won         INTEGER DEFAULT 0,
            purchased_at INTEGER
        )""")

        # ---------- Table lottery_draws ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS lottery_draws (
            draw_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            jackpot     INTEGER DEFAULT 10000000,
            winning_numbers TEXT DEFAULT '',
            drawn_at    INTEGER DEFAULT 0,
            winner_id   INTEGER DEFAULT 0
        )""")

        # ---------- Table hack_log ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS hack_log (
            log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            hacker_id   INTEGER,
            target_type TEXT,
            success     INTEGER,
            reward      INTEGER DEFAULT 0,
            timestamp   INTEGER
        )""")

        # ---------- Table pvp_challenges ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS pvp_challenges (
            challenge_id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenger_id INTEGER,
            target_id    INTEGER,
            bet          INTEGER DEFAULT 0,
            mode         TEXT DEFAULT '1v1',
            status       TEXT DEFAULT 'pending',
            created_at   INTEGER
        )""")

        # ---------- Table insurance ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS insurance (
            user_id     INTEGER PRIMARY KEY,
            type        TEXT DEFAULT 'basic',
            premium     INTEGER DEFAULT 0,
            coverage    INTEGER DEFAULT 0,
            since       INTEGER DEFAULT 0,
            claims      INTEGER DEFAULT 0
        )""")

        # ---------- Table admin_logs ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS admin_logs (
            log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id    INTEGER,
            action      TEXT,
            target_id   INTEGER,
            details     TEXT,
            timestamp   INTEGER
        )""")

        # ---------- Table price_history ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_name  TEXT,
            price       REAL,
            recorded_at INTEGER,
            UNIQUE(asset_name, recorded_at)
        )""")

        # ---------- Table gangs ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS gangs (
            gang_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE,
            founder_id  INTEGER,
            treasury    INTEGER DEFAULT 0,
            reputation  INTEGER DEFAULT 0,
            created_at  INTEGER,
            last_hold   INTEGER DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS gang_members (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            gang_id     INTEGER,
            user_id     INTEGER UNIQUE,
            role        TEXT DEFAULT 'Membre',
            joined_at   INTEGER
        )""")

        # ---------- Table adoptions ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS adoptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id   INTEGER,
            child_id    INTEGER UNIQUE,
            adopted_at  INTEGER
        )""")

        # ---------- Table casino_log ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS casino_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            game        TEXT,
            bet         INTEGER DEFAULT 0,
            profit      INTEGER DEFAULT 0,
            played_at   INTEGER
        )""")

        # ---------- Table bank_loans ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS bank_loans (
            loan_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            amount      INTEGER,
            interest    REAL DEFAULT 0.05,
            repaid      INTEGER DEFAULT 0,
            created_at  INTEGER,
            repaid_at   INTEGER DEFAULT 0
        )""")

        # ---------- Table user_badges ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_badges (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            badge       TEXT,
            earned_at   INTEGER
        )""")

        # ---------- Table title_history ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS title_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            old_title   TEXT,
            new_title   TEXT,
            changed_at  INTEGER
        )""")

        # ---------- Table plants ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS plants (
            plant_type  TEXT PRIMARY KEY,
            grow_time   INTEGER,
            value       INTEGER,
            water_needed INTEGER,
            illegal     INTEGER DEFAULT 0
        )""")

        # ---------- Table mission_log ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mission_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            mission_name TEXT,
            reward      INTEGER,
            xp          INTEGER,
            completed_at INTEGER
        )""")

        # ---------- Table social_log ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            revenue     INTEGER,
            timestamp   INTEGER
        )""")

        # ---------- Table guilds ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guilds (
            guild_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT UNIQUE NOT NULL,
            owner_id     INTEGER NOT NULL,
            description  TEXT DEFAULT '',
            treasury     INTEGER DEFAULT 0,
            level        INTEGER DEFAULT 1,
            xp           INTEGER DEFAULT 0,
            created_at   INTEGER NOT NULL,
            quest_type   TEXT,
            quest_target INTEGER DEFAULT 0,
            quest_progress INTEGER DEFAULT 0,
            quest_ends_at INTEGER DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_members (
            guild_id     INTEGER NOT NULL,
            user_id      INTEGER NOT NULL,
            role         TEXT DEFAULT 'Membre',
            joined_at    INTEGER NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_invites (
            invite_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id     INTEGER NOT NULL,
            invited_id   INTEGER NOT NULL,
            invited_by   INTEGER NOT NULL,
            created_at   INTEGER NOT NULL,
            expires_at   INTEGER NOT NULL,
            status       TEXT DEFAULT 'pending'
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_logs (
            log_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id     INTEGER NOT NULL,
            action       TEXT NOT NULL,
            actor_id     INTEGER NOT NULL,
            details      TEXT,
            timestamp    INTEGER NOT NULL
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_quest_proposals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id     INTEGER NOT NULL,
            proposer_id  INTEGER NOT NULL,
            quest_key    TEXT NOT NULL,
            proposal_id  INTEGER NOT NULL,
            status       TEXT DEFAULT 'pending',
            created_at   INTEGER NOT NULL
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_votes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id  INTEGER NOT NULL,
            user_id      INTEGER NOT NULL,
            vote         TEXT NOT NULL,
            UNIQUE(proposal_id, user_id)
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_competitions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_type TEXT NOT NULL,
            challenge_desc TEXT,
            starts_at    INTEGER NOT NULL,
            ends_at      INTEGER NOT NULL,
            ended        INTEGER DEFAULT 0
        )""")
        # ---------- Table guild_wars ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_wars (
            war_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_a     INTEGER NOT NULL,
            guild_b     INTEGER NOT NULL,
            started_at  INTEGER NOT NULL,
            ends_at     INTEGER NOT NULL,
            status      TEXT DEFAULT 'active',
            winner      INTEGER DEFAULT 0,
            score_a     INTEGER DEFAULT 0,
            score_b     INTEGER DEFAULT 0
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_guild_wars ON guild_wars(status)")

        # ---------- Table user_achievements ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_achievements (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            achievement_id  TEXT NOT NULL,
            unlocked_at     INTEGER NOT NULL,
            UNIQUE(user_id, achievement_id)
        )""")

        # ---------- Table competitions ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS competitions (
            comp_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            comp_type   TEXT NOT NULL,
            starts_at   INTEGER NOT NULL,
            ends_at     INTEGER NOT NULL,
            ended       INTEGER DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS competition_scores (
            comp_id     INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            score       INTEGER DEFAULT 0,
            rank        INTEGER,
            PRIMARY KEY (comp_id, user_id)
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS competition_participants (
            comp_id     INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            joined_at   INTEGER DEFAULT 0,
            PRIMARY KEY (comp_id, user_id)
        )""")

        # ---------- Table ranked seasons ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS ranked_seasons (
            season_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            starts_at    INTEGER NOT NULL,
            ends_at      INTEGER NOT NULL,
            active       INTEGER DEFAULT 1,
            winner_user_id INTEGER DEFAULT 0,
            created_at   INTEGER NOT NULL
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ranked_seasons_active ON ranked_seasons(active, ends_at)")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS ranked_stats (
            season_id    INTEGER NOT NULL,
            user_id      INTEGER NOT NULL,
            rating       INTEGER DEFAULT 1000,
            wins         INTEGER DEFAULT 0,
            losses       INTEGER DEFAULT 0,
            draws        INTEGER DEFAULT 0,
            peak_rating  INTEGER DEFAULT 1000,
            joined_at    INTEGER NOT NULL,
            last_match_at INTEGER DEFAULT 0,
            PRIMARY KEY (season_id, user_id)
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ranked_stats_rating ON ranked_stats(season_id, rating DESC)")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS ranked_match_history (
            match_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id     INTEGER NOT NULL,
            source        TEXT DEFAULT 'arena',
            winner_id     INTEGER DEFAULT 0,
            loser_id      INTEGER DEFAULT 0,
            is_draw       INTEGER DEFAULT 0,
            winner_delta  INTEGER DEFAULT 0,
            loser_delta   INTEGER DEFAULT 0,
            played_at     INTEGER NOT NULL
        )""")

        # ---------- Table tutorial ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tutorial_progress (
            user_id       INTEGER PRIMARY KEY,
            step_index    INTEGER DEFAULT 0,
            completed     INTEGER DEFAULT 0,
            claimed_step  INTEGER DEFAULT -1,
            updated_at    INTEGER NOT NULL
        )""")

        # ---------- Table user stat history ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_stat_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            stat_name    TEXT NOT NULL,
            stat_value   REAL NOT NULL,
            recorded_at  INTEGER NOT NULL
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_stat_history ON user_stat_history(user_id, stat_name, recorded_at DESC)")

        # ---------- Table notifications ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            title       TEXT NOT NULL,
            message     TEXT NOT NULL,
            type        TEXT DEFAULT 'info',
            delay       INTEGER DEFAULT 0,
            created_at  INTEGER NOT NULL,
            sent        INTEGER DEFAULT 0,
            sent_at     INTEGER DEFAULT 0
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, sent)")

        # ---------- Table life_journal ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS life_journal (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            category    TEXT NOT NULL,
            summary     TEXT NOT NULL,
            severity    TEXT DEFAULT 'info',
            created_at  INTEGER NOT NULL
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_life_journal_user ON life_journal(user_id, created_at DESC)")

        # ---------- Table reports ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            report_type     TEXT NOT NULL,
            message         TEXT NOT NULL,
            target_id       INTEGER,
            status          TEXT DEFAULT 'pending',
            admin_response  TEXT,
            created_at      INTEGER NOT NULL,
            resolved_at     INTEGER DEFAULT 0
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reports_target_time ON reports(target_id, user_id, created_at)")

        # ---------- Table mp_trades ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS mp_trades (
            trade_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id     INTEGER NOT NULL,
            to_id       INTEGER NOT NULL,
            offer_money INTEGER DEFAULT 0,
            offer_items TEXT DEFAULT '',
            request_money INTEGER DEFAULT 0,
            request_items TEXT DEFAULT '',
            status      TEXT DEFAULT 'pending',
            created_at  INTEGER NOT NULL,
            expires_at  INTEGER NOT NULL
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON mp_trades(status, expires_at)")

        # ---------- Table game_calendar (pour le téléphone) ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS game_calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            created_at INTEGER NOT NULL,
            user_id INTEGER DEFAULT 0
        )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_calendar_date ON game_calendar(date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_calendar_user ON game_calendar(user_id)")
        
        # ---------- Table phone_messages ----------
        await db.execute("""
        CREATE TABLE IF NOT EXISTS phone_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER NOT NULL,
            to_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            read INTEGER DEFAULT 0
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_phone_messages_to ON phone_messages(to_id, read, created_at)")

        # ---------- Index supplémentaires ----------
        await db.execute("CREATE INDEX IF NOT EXISTS idx_company_products ON company_products(company_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_contracts_to ON company_contracts(to_company, status)")
        try:
            await db.execute("ALTER TABLE users ADD COLUMN parier_cooldown INTEGER DEFAULT 0")
        except aiosqlite.OperationalError:
            pass

        # ---------- Migration pour guild_logs (colonne actor_id) ----------
        try:
            await db.execute("ALTER TABLE guild_logs ADD COLUMN actor_id INTEGER")
            logger.info("✅ Colonne actor_id ajoutée à guild_logs")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE guilds ADD COLUMN war_score INTEGER DEFAULT 0")
        except aiosqlite.OperationalError:
             pass
        # Migration : ajouter la colonne status à guild_invites si elle n'existe pas
        try:
            await db.execute("ALTER TABLE guild_invites ADD COLUMN status TEXT DEFAULT 'pending'")
            logger.info("✅ Colonne status ajoutée à guild_invites")
        except aiosqlite.OperationalError:
            pass  # La colonne existe déjà

        # ---------- Migrations (colonnes manquantes) ----------
        for col in ["job_level", "legacy_level", "last_life_tick", "lifestyle_score", "last_event_day", 
                    "plant_cooldown", "harvest_cooldown", "water_cooldown", "study_cooldown", 
                    "revision_cooldown", "formation_cooldown", "buy_property", "mortgage", 
                    "rent_property", "sell_property", "maintain_property"]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
            except aiosqlite.OperationalError:
                pass

        # Ajout des champs de cooldown manquants
        cooldown_fields = [
            "medecin_last", "gym_last", "medicaments_last", "hospital_last", "doctor_last", "hopital_last",
            "assurance_subscribe", "insure_vehicle", "buy_cooldown", "sell_cooldown", "post_cooldown",
            "collab_cooldown", "vote_cooldown", "report_cooldown", "guild_chat", "luxury_buy",
            "buy_vehicle", "repair_vehicle", "sell_vehicle", "propose_rental", "last_hold",
            "story_cooldown", "live_cooldown", "social_rating_cooldown", "create_community_cooldown",
            "share_cooldown", "trend_cooldown", "poll_cooldown", "use_cooldown", "don_cooldown",
            "casino_last", "parier_cooldown"
        ]
        for col in cooldown_fields:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
            except aiosqlite.OperationalError:
                pass

        migrations = [
            ("companies", "rd_level", "INTEGER DEFAULT 0"),
            ("companies", "overhead", "INTEGER DEFAULT 1000"),
            ("company_members", "base_salary", "INTEGER DEFAULT 0"),
            ("company_applications", "desired_salary", "INTEGER DEFAULT 0"),
        ]
        for table, column, col_type in migrations:
            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except aiosqlite.OperationalError:
                pass

        try:
            await db.execute("UPDATE company_members SET base_salary = salary WHERE base_salary = 0 AND salary > 0")
        except aiosqlite.OperationalError:
            pass
          # ── Migration pour la table contracts (colonnes manquantes) ──
        try:
            await db.execute("ALTER TABLE contracts ADD COLUMN accepted_at INTEGER DEFAULT 0")
            logger.info("✅ Colonne accepted_at ajoutée à contracts")
        except aiosqlite.OperationalError:
            pass

        try:
            await db.execute("ALTER TABLE contracts ADD COLUMN end_date INTEGER DEFAULT 0")
            logger.info("✅ Colonne end_date ajoutée à contracts")
        except aiosqlite.OperationalError:
            pass

        # Version de la base
        try:
            await db.execute("ALTER TABLE users ADD COLUMN db_version INTEGER DEFAULT 0")
        except aiosqlite.OperationalError:
            pass
        await db.execute("UPDATE users SET db_version = ? WHERE db_version = 0", (DB_VERSION,))

        # ---------- PHASE 2 : Ajout des colonnes last_maintenance ----------
        for table in ["properties", "vehicles"]:
            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN last_maintenance INTEGER DEFAULT 0")
                logger.info(f"✅ Colonne last_maintenance ajoutée à {table}")
            except aiosqlite.OperationalError:
                pass

        # ---------- Téléphone : colonnes phone_theme et phone_ringtone (déjà dans CREATE TABLE) ----------
        # On s'assure qu'elles existent au cas où la table existait déjà
        for col in ["phone_theme", "phone_ringtone"]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT 'dark'")
            except aiosqlite.OperationalError:
                pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN phone_theme TEXT DEFAULT 'dark'")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN phone_ringtone TEXT DEFAULT 'classic'")
        except aiosqlite.OperationalError:
            pass

        # ---------- PHASE VEHICULES 2.0 : colonnes speed, cargo, luxury, fuel dans vehicles ----------
        for col in ["speed", "cargo", "luxury", "fuel"]:
            try:
                await db.execute(f"ALTER TABLE vehicles ADD COLUMN {col} INTEGER DEFAULT 0")
                logger.info(f"✅ Colonne {col} ajoutée à vehicles")
            except aiosqlite.OperationalError:
                pass  # déjà existante

        # ---------- active_vehicle_id dans users (déjà dans CREATE TABLE, mais sécurité) ----------
        try:
            await db.execute("ALTER TABLE users ADD COLUMN active_vehicle_id INTEGER DEFAULT 0")
            logger.info("✅ Colonne active_vehicle_id ajoutée à users")
        except aiosqlite.OperationalError:
            pass

        # ---------- Initialisation des données ----------
        for a in ASSETS:
            await db.execute(
                "INSERT OR IGNORE INTO market_prices (asset_name, price, updated_at) VALUES (?,?,?)",
                (a["name"], a["price"], now())
            )

        async with db.execute("SELECT COUNT(*) FROM lottery_draws") as cur:
            count = (await cur.fetchone())[0]
        if count == 0:
            await db.execute(
                "INSERT INTO lottery_draws (jackpot, drawn_at) VALUES (?,0)",
                (10_000_000,)
            )

        # Mise à jour des plantes
        for plant, data in PLANTS.items():
            await db.execute("""
                INSERT INTO plants(plant_type, grow_time, value, water_needed, illegal)
                VALUES(?,?,?,?,?)
                ON CONFLICT(plant_type) DO UPDATE SET
                    grow_time=excluded.grow_time,
                    value=excluded.value,
                    water_needed=excluded.water_needed,
                    illegal=excluded.illegal
            """, (plant, data["grow_time"], data["value"], data["water_needed"], 1 if data.get("illegal") else 0))

        # Insertion des items de base
        base_items = [
            ("Potion de soin", "consumable", "common", 500, "heal", 20, "💊", "Restaure 20% de santé"),
            ("Potion d'énergie", "consumable", "common", 300, "energy", 20, "🔋", "Restaure 20% d'énergie"),
            ("Parchemin d'XP", "consumable", "rare", 2000, "xp", 100, "📜", "Donne 100 XP"),
            ("Petit coffre", "key", "common", 1000, "money", 5000, "🎁", "Contient 5000 coins"),
            ("Épée rouillée", "weapon", "common", 1500, "damage", 5, "⚔️", "+5 dégâts en combat"),
            ("Bouclier en bois", "armor", "common", 1200, "defense", 5, "🛡️", "+5 défense"),
            ("Amulette de chance", "cosmetic", "epic", 50000, "luck", 5, "🍀", "+5% chance de succès"),
            ("Parchemin de téléportation", "consumable", "rare", 8000, "travel", 1, "🌀", "Téléportation instantanée"),
        ]
        for name, typ, rarity, value, effect_type, effect_val, emoji, desc in base_items:
            await db.execute("""
                INSERT OR IGNORE INTO items (name, type, rarity, value, effect_type, effect_value, emoji, description)
                VALUES (?,?,?,?,?,?,?,?)
            """, (name, typ, rarity, value, effect_type, effect_val, emoji, desc))

        for recipe in CRAFTING_RECIPES.values():
            output = recipe["output"]
            await db.execute("""
                INSERT OR IGNORE INTO items (name, type, rarity, value, effect_type, effect_value, emoji, description)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                output["name"],
                output["type"],
                output["rarity"],
                output["value"],
                output["effect_type"],
                output["effect_value"],
                output["emoji"],
                output["description"],
            ))

        # =========================================================================
        # ========== MODULE SOCIAL AVANCÉ (NOUVELLES TABLES) =====================
        # =========================================================================
        # Historique des actions sociales
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            platform TEXT,
            action_type TEXT,
            gain INTEGER DEFAULT 0,
            timestamp INTEGER NOT NULL
        )
        """)
        # Stories
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            platform TEXT,
            content TEXT,
            created_at INTEGER,
            expires_at INTEGER
        )
        """)
        # Lives
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_lives (
            live_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            platform TEXT,
            started_at INTEGER,
            ends_at INTEGER,
            active INTEGER DEFAULT 1
        )
        """)
        # Sondages
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_polls (
            poll_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            options TEXT,
            created_at INTEGER NOT NULL,
            active INTEGER DEFAULT 1
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_poll_votes (
            poll_id INTEGER,
            user_id INTEGER,
            option_index INTEGER,
            PRIMARY KEY (poll_id, user_id)
        )
        """)
        # Communautés
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_communities (
            community_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            owner_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            disbanded INTEGER DEFAULT 0
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_community_members (
            community_id INTEGER,
            user_id INTEGER,
            role TEXT DEFAULT 'member',
            joined_at INTEGER,
            PRIMARY KEY (community_id, user_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_community_invites (
            invite_id INTEGER PRIMARY KEY AUTOINCREMENT,
            community_id INTEGER,
            invited_id INTEGER,
            inviter_id INTEGER,
            created_at INTEGER,
            status TEXT DEFAULT 'pending'
        )
        """)
        # Tendances
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_trends (
            trend_id INTEGER PRIMARY KEY AUTOINCREMENT,
            hashtag TEXT NOT NULL,
            creator_id INTEGER,
            multiplier REAL DEFAULT 1.5,
            expires_at INTEGER NOT NULL,
            active INTEGER DEFAULT 1
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_user_trends (
            user_id INTEGER,
            hashtag TEXT,
            multiplier REAL,
            used INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, hashtag)
        )
        """)
        # Offres de vente de followers
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_follower_offers (
            offer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER,
            buyer_id INTEGER,
            quantity INTEGER,
            price INTEGER,
            status TEXT DEFAULT 'pending',
            created_at INTEGER
        )
        """)
        # Demandes de collaboration
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_collab_requests (
            request_id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER,
            to_id INTEGER,
            collab_type TEXT,
            duration_h INTEGER,
            status TEXT DEFAULT 'pending',
            created_at INTEGER
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_active_collabs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER,
            to_id INTEGER,
            collab_type TEXT,
            expires_at INTEGER
        )
        """)
        # Réputation sociale
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_reputation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            rater_id INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(user_id, rater_id)
        )
        """)
        # Engagement boosts (stories)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS social_engagement_boosts (
            user_id INTEGER,
            platform TEXT,
            multiplier REAL,
            expires_at INTEGER,
            PRIMARY KEY (user_id, platform)
        )
        """)
        # Ajout des colonnes manquantes dans users pour le social
        for col in ["social_rating", "social_coins", "referred_by", "referral_count", "referral_rewards", "referral_activated"]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
            except aiosqlite.OperationalError:
                pass
        # Initialisation des SocialCoins pour les anciens utilisateurs
        await db.execute(f"UPDATE users SET social_coins = {SOCIAL_COIN_STARTING_BALANCE} WHERE social_coins IS NULL OR social_coins = 0")

        # =========================================================================
        # ========== PARRAINAGE ===================================================
        # =========================================================================
        await db.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER PRIMARY KEY,
            created_at INTEGER NOT NULL,
            starter_rewarded INTEGER DEFAULT 0,
            activated_at INTEGER DEFAULT 0
        )
        """)

        # =========================================================================
        # ========== POLITIQUE AVANCÉE (NOUVELLES TABLES) =========================
        # =========================================================================
        # Élections
        await db.execute("""
        CREATE TABLE IF NOT EXISTS elections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poste TEXT NOT NULL,
            started_by INTEGER NOT NULL,
            started_at INTEGER NOT NULL,
            ends_at INTEGER NOT NULL,
            status TEXT DEFAULT 'open',
            quorum INTEGER DEFAULT 0,
            description TEXT DEFAULT ''
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            election_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            program TEXT,
            party_id INTEGER,
            PRIMARY KEY (election_id, user_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            election_id INTEGER NOT NULL,
            voter_id INTEGER NOT NULL,
            candidate_id INTEGER NOT NULL,
            voted_at INTEGER NOT NULL,
            PRIMARY KEY (election_id, voter_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS political_offices (
            poste TEXT PRIMARY KEY,
            occupant_id INTEGER,
            elected_at INTEGER,
            election_id INTEGER,
            last_activity INTEGER DEFAULT 0
        )
        """)
        # Partis politiques
        await db.execute("""
        CREATE TABLE IF NOT EXISTS parties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            ideology TEXT,
            leader_id INTEGER NOT NULL,
            treasury INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            disbanded INTEGER DEFAULT 0
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS party_members (
            party_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT DEFAULT 'member',
            joined_at INTEGER NOT NULL,
            PRIMARY KEY (party_id, user_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS party_invites (
            invite_id INTEGER PRIMARY KEY AUTOINCREMENT,
            party_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            invited_by INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            status TEXT DEFAULT 'pending'
        )
        """)
        # Constitution (une seule ligne active)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS constitution (
            id INTEGER PRIMARY KEY CHECK (id=1),
            text TEXT NOT NULL,
            amended_by INTEGER,
            amended_at INTEGER NOT NULL
        )
        """)
        # Lois
        await db.execute("""
        CREATE TABLE IF NOT EXISTS laws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            proposer_id INTEGER NOT NULL,
            effect_type TEXT,
            effect_value INTEGER,
            status TEXT DEFAULT 'proposed',
            votes_for INTEGER DEFAULT 0,
            votes_against INTEGER DEFAULT 0,
            required_majority REAL DEFAULT 0.5,
            created_at INTEGER NOT NULL,
            voting_ends_at INTEGER,
            enacted_at INTEGER
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS law_votes (
            law_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            vote INTEGER NOT NULL,
            voted_at INTEGER NOT NULL,
            PRIMARY KEY (law_id, user_id)
        )
        """)
        # Gouvernement (cabinet)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS cabinet_positions (
            position TEXT PRIMARY KEY,
            occupant_id INTEGER,
            appointed_by INTEGER,
            appointed_at INTEGER,
            FOREIGN KEY(occupant_id) REFERENCES users(user_id)
        )
        """)
        # Référendums
        await db.execute("""
        CREATE TABLE IF NOT EXISTS referendums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            proposer_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            voting_ends_at INTEGER NOT NULL,
            votes_for INTEGER DEFAULT 0,
            votes_against INTEGER DEFAULT 0,
            status TEXT DEFAULT 'open',
            required_majority REAL DEFAULT 0.5
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS referendum_votes (
            referendum_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            vote INTEGER NOT NULL,
            voted_at INTEGER NOT NULL,
            PRIMARY KEY (referendum_id, user_id)
        )
        """)
        # Motions de destitution
        await db.execute("""
        CREATE TABLE IF NOT EXISTS motions (
            motion_id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER NOT NULL,
            initiated_by INTEGER NOT NULL,
            initiated_at INTEGER NOT NULL,
            ends_at INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            signatures_needed INTEGER DEFAULT 0,
            signatures INTEGER DEFAULT 0
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS motion_signatures (
            motion_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            signed_at INTEGER NOT NULL,
            PRIMARY KEY (motion_id, user_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS motion_votes (
            motion_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            vote TEXT NOT NULL,
            voted_at INTEGER NOT NULL,
            PRIMARY KEY (motion_id, user_id)
        )
        """)
        # Historique politique
        await db.execute("""
        CREATE TABLE IF NOT EXISTS political_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            description TEXT NOT NULL,
            involved_user_id INTEGER,
            timestamp INTEGER NOT NULL
        )
        """)

        # Ajout des nouvelles colonnes de cooldown politique dans users
        for col in ["last_election", "last_motion", "last_constitution"]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
            except aiosqlite.OperationalError:
                pass

        # Insertion de la constitution par défaut si vide
        async with db.execute("SELECT COUNT(*) FROM constitution") as cur:
            if (await cur.fetchone())[0] == 0:
                await db.execute(
                    "INSERT INTO constitution (id, text, amended_by, amended_at) VALUES (1, ?, 0, ?)",
                    ("Le pouvoir émane des joueurs. Les élections sont libres. Les lois sont votées par tous.", now())
                )

        # Commit final
        await db.commit()
        logger.info("✅ Base de données initialisée (Phase 2 + social avancé + politique avancée + téléphone + véhicules 2.0).")
        # À la fin de init_db(), après le commit
        await migrate_vehicle_stats()

# ==================== FONCTIONS UTILITAIRES ====================
# (toutes ces fonctions utilisent `timeout=DB_TIMEOUT`)

async def _record_stat_snapshot_with_db(db, user_id: int, stat_name: str, stat_value, recorded_at: int | None = None):
    await db.execute(
        "INSERT INTO user_stat_history (user_id, stat_name, stat_value, recorded_at) VALUES (?,?,?,?)",
        (user_id, stat_name, float(stat_value), recorded_at or now())
    )


async def record_user_stat_snapshot(user_id: int, stat_name: str, stat_value):
    async with db_connection() as db:
        await _record_stat_snapshot_with_db(db, user_id, stat_name, stat_value)
        await db.commit()


async def snapshot_user_core_stats(user_id: int, db=None):
    owns_connection = db is None
    if owns_connection:
        async with db_connection(row_factory=aiosqlite.Row) as conn:
            await snapshot_user_core_stats(user_id, conn)
            await conn.commit()
        return

    previous_row_factory = db.row_factory
    db.row_factory = aiosqlite.Row
    try:
        async with db.execute("SELECT balance, xp, prestige FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return
        timestamp = now()
        await _record_stat_snapshot_with_db(db, user_id, "balance", row["balance"], timestamp)
        await _record_stat_snapshot_with_db(db, user_id, "xp", row["xp"], timestamp)
        await _record_stat_snapshot_with_db(db, user_id, "prestige", row["prestige"], timestamp)
    finally:
        db.row_factory = previous_row_factory


async def get_user(user_id: int, username: str = "", full_name: str = "") -> dict:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        if row:
            u = dict(row)
            fields = []
            values = []
            if username and u["username"] != username:
                fields.append("username=?")
                values.append(username)
            if full_name and u.get("full_name") != full_name:
                fields.append("full_name=?")
                values.append(full_name)
            if fields:
                fields.append("last_seen=?")
                values.append(now())
                values.append(user_id)
                await db.execute(f"UPDATE users SET {', '.join(fields)} WHERE user_id=?", values)
                await db.commit()
            return u
        await db.execute("""
            INSERT INTO users (user_id, username, full_name, balance, registered, created_at, last_seen, last_life_tick, lifestyle_score, db_version, social_coins)
            VALUES (?,?,?,?,0,?,?,?,50,?,?)
        """, (user_id, username, full_name, STARTING_BALANCE, now(), now(), now(), DB_VERSION, SOCIAL_COIN_STARTING_BALANCE))
        await snapshot_user_core_stats(user_id, db)
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cur:
            return dict(await cur.fetchone())

async def update_balance(user_id: int, amount: int):
    for attempt in range(3):
        try:
            async with db_connection() as db:
                if amount > 0:
                    await db.execute(
                        "UPDATE users SET balance=balance+?, total_earned=total_earned+? WHERE user_id=?",
                        (amount, amount, user_id)
                    )
                else:
                    async with db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)) as cur:
                        row = await cur.fetchone()
                        if row and row[0] + amount < 0:
                            raise ValueError("Solde insuffisant")
                    await db.execute(
                        "UPDATE users SET balance=balance+?, total_spent=total_spent+? WHERE user_id=?",
                        (amount, -amount, user_id)
                    )
                await snapshot_user_core_stats(user_id, db)
                await db.commit()
            return
        except aiosqlite.OperationalError as e:
            if "database is locked" in str(e) and attempt < 2:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            raise

async def update_field(user_id: int, field: str, value):
    if field not in ALLOWED_FIELDS:
        raise ValueError(f"Champ non autorisé : {field}")
    async with db_connection() as db:
        await db.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, user_id))
        if field in {"balance", "xp", "prestige"}:
            await snapshot_user_core_stats(user_id, db)
        await db.commit()

async def increment_field(user_id: int, field: str, amount=1):
    if field not in ALLOWED_FIELDS:
        raise ValueError(f"Champ non autorisé : {field}")
    async with db_connection() as db:
        await db.execute(f"UPDATE users SET {field}={field}+? WHERE user_id=?", (amount, user_id))
        if field in {"balance", "xp", "prestige"}:
            await snapshot_user_core_stats(user_id, db)
        await db.commit()

async def add_life_journal(user_id: int, category: str, summary: str, severity: str = 'info'):
    async with db_connection() as db:
        await db.execute(
            "INSERT INTO life_journal (user_id, category, summary, severity, created_at) VALUES (?,?,?,?,?)",
            (user_id, category, summary, severity, now())
        )
        await db.commit()

async def get_life_journal(user_id: int, limit: int = 10) -> list:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM life_journal WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, limit)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def get_top_rich(limit=10) -> list:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT user_id, full_name, username, balance FROM users ORDER BY balance DESC LIMIT ?",
            (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def get_bank_account(user_id: int, bank_name: str = None) -> dict | None:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        if bank_name:
            async with db.execute(
                "SELECT * FROM bank_accounts WHERE user_id=? AND bank_name=?", (user_id, bank_name)
            ) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT * FROM bank_accounts WHERE user_id=? ORDER BY balance DESC LIMIT 1", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

async def get_all_bank_accounts(user_id: int) -> list:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM bank_accounts WHERE user_id=?", (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def get_user_company(user_id: int) -> dict | None:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute("""
            SELECT c.*, cm.poste, cm.base_salary, cm.activity_score, cm.joined_at as member_since
            FROM company_members cm JOIN companies c ON c.company_id=cm.company_id
            WHERE cm.user_id=? AND c.dissolved=0
        """, (user_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

async def get_company_by_name(name: str) -> dict | None:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM companies WHERE LOWER(name)=LOWER(?) AND dissolved=0", (name,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

async def get_company_by_id(company_id: int) -> dict | None:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute("SELECT * FROM companies WHERE company_id=?", (company_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

async def get_all_companies(sector=None) -> list:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        if sector:
            async with db.execute(
                "SELECT * FROM companies WHERE dissolved=0 AND sector=? ORDER BY treasury DESC",
                (sector,)
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            "SELECT * FROM companies WHERE dissolved=0 ORDER BY treasury DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def log_company_action(company_id: int, action: str, actor_id: int, details: str = ""):
    async with db_connection() as db:
        await db.execute(
            "INSERT INTO company_logs (company_id, action, actor_id, details, timestamp) VALUES (?,?,?,?,?)",
            (company_id, action, actor_id, details, now())
        )
        await db.commit()

async def get_skill(user_id: int, skill_name: str) -> int:
    async with db_connection() as db:
        async with db.execute(
            "SELECT level FROM skills WHERE user_id=? AND skill_name=?", (user_id, skill_name)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

async def upgrade_skill(user_id: int, skill_name: str):
    current = await get_skill(user_id, skill_name)
    async with db_connection() as db:
        await db.execute("""
            INSERT INTO skills (user_id, skill_name, level) VALUES (?,?,1)
            ON CONFLICT(user_id, skill_name) DO UPDATE SET level=level+1
        """, (user_id, skill_name))
        await db.commit()
    return current + 1

async def get_all_skills(user_id: int) -> dict:
    async with db_connection() as db:
        async with db.execute(
            "SELECT skill_name, level FROM skills WHERE user_id=?", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
        return {r[0]: r[1] for r in rows}

async def get_properties(user_id: int) -> list:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM properties WHERE user_id=?", (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def get_vehicles(user_id: int) -> list:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM vehicles WHERE user_id=?", (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def get_portfolio(user_id: int) -> list:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM investments WHERE user_id=? AND quantity>0", (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def get_market_price(asset_name: str) -> float:
    async with db_connection() as db:
        async with db.execute(
            "SELECT price FROM market_prices WHERE asset_name=?", (asset_name,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0.0

async def update_market_price(asset_name: str, price: float):
    async with db_connection() as db:
        await db.execute(
            "UPDATE market_prices SET price=?, updated_at=? WHERE asset_name=?",
            (price, now(), asset_name)
        )
        await db.commit()

async def get_active_event() -> dict | None:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM world_events WHERE active=1 AND ends_at>? ORDER BY started_at DESC LIMIT 1",
            (now(),)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

async def get_marriage(user_id: int) -> dict | None:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM marriages WHERE user_id=? AND status='active'", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

async def get_inventory(user_id: int) -> list:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM inventory WHERE user_id=?", (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def add_notification(user_id: int, message: str):
    async with db_connection() as db:
        await db.execute(
            "INSERT INTO notifications (user_id, title, message, created_at) VALUES (?, 'Notification', ?, ?)",
            (user_id, message, now())
        )
        await db.commit()

async def get_garden(user_id: int) -> list:
    async with db_connection(row_factory=aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM garden WHERE user_id=?", (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def log_admin(admin_id: int, action: str, target_id: int, details: str = ""):
    async with db_connection() as db:
        await db.execute(
            "INSERT INTO admin_logs (admin_id, action, target_id, details, timestamp) VALUES (?,?,?,?,?)",
            (admin_id, action, target_id, details, now())
        )
        await db.commit()

# ==================== FONCTIONS SUPPLÉMENTAIRES POUR LE SCHEDULER ====================
async def record_price_history():
    from config import ASSETS
    async with db_connection() as db:
        for asset in ASSETS:
            price = await get_market_price(asset["name"])
            await db.execute(
                "INSERT INTO price_history (asset_name, price, recorded_at) VALUES (?, ?, ?)",
                (asset["name"], price, now()),
            )
        await db.commit()

async def collect_rents():
    async with db_connection() as db:
        async with db.execute("""
            SELECT ra.id, ra.owner_id, ra.tenant_id, ra.rent, p.prop_id
            FROM rental_agreements ra
            JOIN properties p ON p.prop_id = ra.property_id
            WHERE ra.status = 'active'
        """) as cur:
            rentals = await cur.fetchall()
        for agreement_id, owner_id, tenant_id, rent, prop_id in rentals:
            async with db.execute("SELECT balance FROM users WHERE user_id=?", (tenant_id,)) as cur2:
                tenant_balance = (await cur2.fetchone())[0]
            if tenant_balance >= rent:
                await db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (rent, tenant_id))
                await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (rent, owner_id))
            else:
                await db.execute("UPDATE rental_agreements SET status = 'expelled', end_date = ? WHERE id = ?", (now(), agreement_id))
                await db.execute("UPDATE properties SET rented_to = 0 WHERE prop_id = ?", (prop_id,))
                await add_notification(tenant_id, f"🏠 Tu as été expulsé du logement #{prop_id} (loyer impayé).")
                await add_notification(owner_id, f"🏠 Ton locataire a été expulsé du bien #{prop_id} (loyer impayé).")
        await db.commit()

async def degrade_vehicles():
    async with db_connection() as db:
        await db.execute("UPDATE vehicles SET condition = MAX(0, condition - 2)")
        await db.commit()

# ==================== FONCTION UTILE POUR LA POLITIQUE ====================
async def get_all_users_count() -> int:
    async with db_connection() as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE registered=1") as cur:
            return (await cur.fetchone())[0] or 0
        
async def migrate_vehicle_stats():
    """Met à jour les véhicules existants avec les statistiques de config.VEHICLES"""
    from config import VEHICLES
    
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT) as db:
        # Vérifier si les colonnes existent
        cursor = await db.execute("PRAGMA table_info(vehicles)")
        columns = [row[1] for row in await cursor.fetchall()]
        
        # Ajouter les colonnes manquantes
        for col in ["speed", "cargo", "luxury", "fuel"]:
            if col not in columns:
                try:
                    await db.execute(f"ALTER TABLE vehicles ADD COLUMN {col} INTEGER DEFAULT 0")
                    logger.info(f"✅ Colonne {col} ajoutée à vehicles")
                except aiosqlite.OperationalError:
                    pass
        
        # Mettre à jour les véhicules existants avec les stats du config
        async with db.execute("SELECT veh_id, veh_type FROM vehicles") as cur:
            vehicles = await cur.fetchall()
        
        for veh_id, veh_type in vehicles:
            veh_data = VEHICLES.get(veh_type, {})
            if veh_data:
                await db.execute("""
                    UPDATE vehicles 
                    SET speed = COALESCE(speed, ?),
                        cargo = COALESCE(cargo, ?),
                        luxury = COALESCE(luxury, ?),
                        fuel = COALESCE(fuel, ?)
                    WHERE veh_id = ?
                """, (
                    veh_data.get("speed", 0),
                    veh_data.get("cargo", 0),
                    veh_data.get("luxury", 0),
                    veh_data.get("fuel_capacity", 0),
                    veh_id
                ))
        
        await db.commit()
        logger.info(f"✅ {len(vehicles)} véhicules mis à jour avec leurs statistiques")