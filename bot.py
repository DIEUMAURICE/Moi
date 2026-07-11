"""
bot.py — LIFESIM ULTRA V2 – Version corrigée et optimisée
═══════════════════════════════════════════════════════════════════════
Point d'entrée principal. Intègre :
  • Tous les anciens handlers (compat ascendante)
  • Le nouveau module multijoueur (handlers.multiplayer)
  • Le scheduler enrichi
  • Toutes les commandes avancées des entreprises
  • Phase 0 : commande /shop et son callback
  • Phase 1 : marché joueur (items, ventes, achats)
  • Module social ultime (liberté totale, multijoueur)
  • Module politique avancé (élections, partis, lois, etc.)
  • Système véhicules 2.0 (garage, réparation, carburant, sélection active)
  • Commandes véhicules : /acces_vip, /lieux_vip, /cargobonus
"""
import asyncio
import logging
import random
import aiosqlite
import time

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters
)

from config import BOT_TOKEN, ADMIN_IDS
from database import init_db, DB_PATH, now, run_migrations  # ← AJOUT run_migrations
from utils.error_handler import error_handler          # ← AJOUT

# ─── Handlers existants ─────────────────────────────────────────────
from handlers.legacy import cmd_legacy, cmd_reincarnate
from handlers.general import (
    cmd_start, cmd_aide, cmd_menu, cmd_nouveautes, menu_callback,
    cmd_guide, cmd_unknown_command, cmd_about,
    dev_callback, creator_callback,
    cmd_shop, shop_callback, cmd_parrainage,
)
from handlers.phone import cmd_phone, phone_callback, cmd_phone_event, cmd_phone_msg, cmd_phone_status, add_random_calendar_events
from handlers.profile import (
    cmd_profil, cmd_stats, cmd_badges, cmd_bio, cmd_setlocation,
    cmd_niveau, cmd_inventaire, cmd_titres, cmd_karma_view, cmd_topxp,
    cmd_historiquetitres,
)
from handlers.education import (
    cmd_etudes, cmd_etudier, cmd_examen, cmd_reviser, cmd_formation,
    cmd_competences,
)
from handlers.economy import (
    cmd_quotidien, cmd_travailler, cmd_metier, cmd_payer, cmd_compte,
    cmd_impots, cmd_richesse, cmd_dormir, cmd_manger, cmd_loterie,
    cmd_acheterticket, cmd_don, cmd_mestickets, cmd_tirage, cmd_promotion,
)
from handlers.bank import (
    cmd_banques, cmd_ouvrir, cmd_depot, cmd_retrait, cmd_soldebanque,
    cmd_pret, cmd_rembourser, cmd_mescomptes, process_bank_interests,
)
from handlers.realestate import (
    cmd_proprietes, cmd_acheter_bien, cmd_hypotheque, cmd_mesbiens,
    cmd_louer, cmd_vendre_bien, cmd_entretenir,
    cmd_proposer_location, cmd_meslocations, cmd_quitter_logement, rental_callback,
)
from handlers.health import (
    cmd_sante, cmd_medecin, cmd_hopital, cmd_gym, cmd_medicaments, cmd_assurance,
)
from handlers.crime import (
    cmd_crimes, cmd_commettre, cmd_caution, cmd_tribunal, cmd_avocat,
    cmd_gang, cmd_creergand, cmd_rejoindregang, cmd_quittergang,
    cmd_ganginfo, cmd_gangactions, cmd_gangcaisse, cmd_transfertchef,
    cmd_ganghold, cmd_gangupgrade, cmd_gangclassement, cmd_dissoudregang,
)
from handlers.casino import (
    cmd_slots, cmd_blackjack, cmd_roulette, cmd_crash, cmd_poker,
    cmd_mines, cmd_pmu, cmd_casino,
    bj_callback, crash_callback,
    slots_callback, roulette_callback, mines_callback, pmu_callback,
    roulette_number_input
)
from handlers.investments import (
    cmd_marche, cmd_acheteraction, cmd_vendreaction, cmd_portefeuille,
    cmd_historique, fluctuate_market,
)
from handlers.family import (
    cmd_mariage, cmd_acceptermariage, cmd_divorce as cmd_family_divorce, cmd_arbre, cmd_famille,
    cmd_ami, cmd_mesamis, cmd_leaderboard, cmd_adopter, cmd_creer_famille,
    cmd_inviterfamille, cmd_rejoindrefamille, cmd_quitterfamille,
    cmd_transfertclan, cmd_dissoudrefamille,
)
from handlers.travel import cmd_destinations, cmd_voyager, cmd_monstimbre
# ── Vehicles (ancien, pour achats/ventes) ─────────────────────────
from handlers.vehicle import (
    cmd_vehicules_liste, cmd_acheter_vehicule, cmd_mes_vehicules,
    cmd_reparer_vehicule, cmd_assurer_vehicule, cmd_vendre_vehicule,
    cmd_vehicule_info,
)
# ── Vehicles 2.0 (nouveau, garage, réparation, carburant) ────────
from handlers.vehicles import (
    cmd_garage, cmd_repair_vehicle, cmd_refuel,
    cmd_garage_select, cmd_garage_repair, cmd_garage_refuel,
    process_vehicles_maintenance,
)
# ── Luxury (commandes VIP) ──────────────────────────────────────────
from handlers.luxury import (
    cmd_luxe, cmd_acheter_luxe, cmd_prestige,
    cmd_classementprestige, cmd_prestigelog,
    cmd_acces_vip, cmd_lieux_vip,
)
from handlers.arena import (
    cmd_defier, cmd_defis, cmd_classement_arene, cmd_parier,
    challenge_callback, rps_callback, coinflip_callback, quiz_answer_callback,
)
from handlers.garden import (
    cmd_jardin, cmd_planter, cmd_arroser, cmd_recolter, cmd_vendrecolte,
)
from handlers.companies import (
    cmd_boites, cmd_infoboite, cmd_employes, cmd_postuler, cmd_demissionner,
    cmd_monentreprise, cmd_candidatures, cmd_accepter, cmd_refuser,
    cmd_nommer as cmd_nommer_entreprise, cmd_licencier, cmd_creerboite, cmd_dissoudreboite,
    cmd_depotboite, cmd_versersalaires, cmd_logsboite,
    cmd_classement_boites, cmd_parts, cmd_acheterparts, cmd_vendreparts,
    cmd_dividendes, cmd_emplois, cmd_inviter, cmd_repondre_invitation, cmd_transfert_entreprise,
    cmd_setsalaire, cmd_negocier, cmd_repondre_offre,
    cmd_rd, cmd_setoverhead, cmd_produits, cmd_creer_produit, cmd_setprix, cmd_auditboite,
    cmd_annonce, cmd_proposer_contrat, cmd_former, cmd_prime,
    cmd_repondre_contrat,
    company_page_callback,
    process_company_maintenance, cmd_retirerboite, cmd_renommer_entreprise, cmd_changer_secteur,
    cmd_donner_produit, cmd_emoji_produit, cmd_renommer_produit, cmd_desc_produit,
    cmd_supprimer_produit, cmd_retirer_produit,
    cmd_proposer_fusion, cmd_repondre_fusion, cmd_negocier_contrat
)
# ── Social (nouvelle version ultime) ───────────────────────────────
from handlers.social import (
    cmd_plateformes, cmd_poster, cmd_story, cmd_live, cmd_donner_live,
    cmd_sondage, cmd_vote, cmd_analytiques, cmd_creer_communaute,
    cmd_inviter_communaute, cmd_communaute, cmd_rejoindre_communaute, cmd_partager, cmd_lancer_tendance,
    cmd_utiliser_tendance, cmd_socialcoins, cmd_donner_socialcoins,
    cmd_collab, cmd_vendre_followers, cmd_noter_social, cmd_classement_social, cmd_resultats_sondage,
    cmd_mesabonnes, collab_callback_handler, buy_followers_callback, process_social_maintenance, process_social_revenue
)
# ── Political (nouveau système complet) ────────────────────────────
from handlers.political import (
    cmd_constitution, cmd_modifierconstitution,
    cmd_creerparti, cmd_partis, cmd_rejoindreparti, cmd_quitterparti, cmd_transfertchefparti,
    cmd_lancerelection, cmd_candidater, cmd_voter, cmd_depouiller,
    cmd_proposerloi, cmd_voterloi,
    cmd_nommer as cmd_nommer_politique, cmd_ministres,
    cmd_referendum, cmd_votereferendum,
    cmd_destituer, cmd_signer, cmd_vote_destitution,
    cmd_monposte, cmd_demissionnerposte, cmd_postes,
    political_maintenance, cmd_candidats, cmd_elections
)
from handlers.blackmarket import (
    cmd_noir, cmd_acheter_noir, cmd_hack_targets, cmd_hacker, cmd_defenses,
)
from handlers.missions import cmd_missions, cmd_missions_completed, cmd_resetmissions
from handlers.events import cmd_evenements, cmd_eventinfo
from handlers.admin import *
cmd_admin_divorce = cmd_divorce
from config import ADMIN_IDS
from handlers.guilds import (
    cmd_guild, cmd_guild_create, cmd_guild_invite, cmd_guild_join,
    cmd_guild_leave, cmd_guild_members, cmd_guild_desc, cmd_guild_promote,
    cmd_guild_transfer, cmd_guild_dissolve, cmd_guild_donate,
    cmd_guild_quest, cmd_guild_rank, guild_invite_callback, cmd_guild_chat,
    cmd_guild_declare_war, cmd_guild_war_status, cmd_guild_attack, cmd_guild_surrender,
    process_guild_maintenance,
)
from handlers.achievements import cmd_achievements, cmd_achievements_check
from handlers.competitions import (
    cmd_competition, cmd_competition_join, cmd_competition_history,
    start_new_competition, end_competition_and_reward,
)
from handlers.notifications import (
    cmd_notifications, cmd_notifications_history, cmd_notifications_clear,
    cmd_broadcast_notify, cmd_notify_user, cmd_notifications_stats,
    process_notifications, clear_old_notifications,
)
from handlers.reporting import (
    cmd_report, cmd_report_user, cmd_myreports,
    cmd_reports_list, cmd_report_handle, cmd_reports_stats,
    report_callback,
)
from handlers.life import cmd_vie, cmd_routine, cmd_journal
from handlers.progression import (
    cmd_crafting, cmd_craft,
    cmd_ranked, cmd_ranked_join, cmd_ranked_leaderboard, cmd_ranked_history,
    cmd_tutorial, tutorial_callback,
    cmd_graphstats,
)
# ─── Market (pour cargobonus) ────────────────────────────────────────
from handlers.market import (
    cmd_market, cmd_sellitem, cmd_buyitem, cmd_cancelitem, 
    cmd_useitem, cmd_myitems, cmd_cargobonus,
    market_page_callback, clean_expired_listings
)

# ─── MODULE MULTIJOUEUR ────────────────────────────────────────────────
from handlers.multiplayer import (
    cmd_cadeau, cmd_salutations, cmd_echange,
    cmd_relations, cmd_classements, trade_callback, init_mp_tables,
    cmd_creerescouade, cmd_escouade, cmd_inviterescouade, cmd_quitterescouade,
    cmd_chatescouade, cmd_raid, squad_callback, process_multiplayer_maintenance,
)

# ─── Phase 1 : Marché joueur (commandes renommées) ─────────────────────
from handlers.market import (
    cmd_market, cmd_sellitem, cmd_buyitem, cmd_cancelitem, cmd_useitem, cmd_myitems,
    market_page_callback, clean_expired_listings
)

# ─── LOGGING ───────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── CONSTRUCTION DE L'APPLICATION ─────────────────────────────────────
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    # ── General ─────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("aide",        cmd_aide))
    app.add_handler(CommandHandler("help",        cmd_aide))
    app.add_handler(CommandHandler("guide",       cmd_guide))
    app.add_handler(CommandHandler("menu",        cmd_menu))
    app.add_handler(CommandHandler("parrainage",  cmd_parrainage))
    app.add_handler(CommandHandler("nouveautes",  cmd_nouveautes))
    app.add_handler(CommandHandler("vie",         cmd_vie))
    app.add_handler(CommandHandler("routine",     cmd_routine))
    app.add_handler(CommandHandler("choix",       cmd_routine))
    app.add_handler(CommandHandler("journal",     cmd_journal))
    app.add_handler(CommandHandler("shop",        cmd_shop))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu_"))
    app.add_handler(CallbackQueryHandler(shop_callback, pattern=r"^shop_"))
    app.add_handler(CallbackQueryHandler(tutorial_callback, pattern=r"^tutorial_"))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CallbackQueryHandler(dev_callback, pattern="^dev_info$"))
    app.add_handler(CallbackQueryHandler(creator_callback, pattern="^creator_info$"))
    #Phone
    app.add_handler(CommandHandler("phone", cmd_phone))
    app.add_handler(CommandHandler("phone_event", cmd_phone_event))
    app.add_handler(CommandHandler("phone_msg", cmd_phone_msg))
    app.add_handler(CallbackQueryHandler(phone_callback, pattern=r"^phone_"))
    app.add_handler(CommandHandler("status", cmd_phone_status))

    # ── Profile ─────────────────────────────────────────────────────
    app.add_handler(CommandHandler("profil",      cmd_profil))
    app.add_handler(CommandHandler("me",          cmd_profil))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("badges",      cmd_badges))
    app.add_handler(CommandHandler("bio",         cmd_bio))
    app.add_handler(CommandHandler("lieu",        cmd_setlocation))
    app.add_handler(CommandHandler("niveau",      cmd_niveau))
    app.add_handler(CommandHandler("inventaire",  cmd_inventaire))
    app.add_handler(CommandHandler("titres",      cmd_titres))
    app.add_handler(CommandHandler("karma",       cmd_karma_view))
    app.add_handler(CommandHandler("topxp",       cmd_topxp))
    app.add_handler(CommandHandler("historiquetitres", cmd_historiquetitres))

    # ── Education ───────────────────────────────────────────────────
    for c, h in [("etudes", cmd_etudes), ("etudier", cmd_etudier),
                 ("examen", cmd_examen), ("reviser", cmd_reviser),
                 ("formation", cmd_formation), ("competences", cmd_competences)]:
        app.add_handler(CommandHandler(c, h))

    # ── Economy ─────────────────────────────────────────────────────
    eco_cmds = {
        "quotidien": cmd_quotidien, "daily": cmd_quotidien,
        "travailler": cmd_travailler, "work": cmd_travailler,
        "metier": cmd_metier, "payer": cmd_payer, "pay": cmd_payer,
        "compte": cmd_compte, "acc": cmd_compte,
        "impots": cmd_impots, "richesse": cmd_richesse, "richlist": cmd_richesse,
        "dormir": cmd_dormir, "manger": cmd_manger,
        "loterie": cmd_loterie, "loto": cmd_loterie,
        "acheterticket": cmd_acheterticket, "don": cmd_don,
        "mestickets": cmd_mestickets, "tirage": cmd_tirage,
        "promotion": cmd_promotion,
    }
    for c, h in eco_cmds.items():
        app.add_handler(CommandHandler(c, h))

    # ── Bank ────────────────────────────────────────────────────────
    bank_cmds = {
        "banques": cmd_banques, "banks": cmd_banques, "ouvrir": cmd_ouvrir,
        "openbank": cmd_ouvrir, "depot": cmd_depot, "retrait": cmd_retrait,
        "soldebanque": cmd_soldebanque, "pret": cmd_pret,
        "rembourser": cmd_rembourser, "mescomptes": cmd_mescomptes,
    }
    for c, h in bank_cmds.items():
        app.add_handler(CommandHandler(c, h))

    # ── Real Estate (incluant nouvelles commandes de location) ──────
    re_cmds = {
        "proprietes": cmd_proprietes, "acheter": cmd_acheter_bien,
        "hypotheque": cmd_hypotheque, "mesbiens": cmd_mesbiens,
        "louer": cmd_louer, "vendre": cmd_vendre_bien,
        "entretenir": cmd_entretenir,
        "proposer_location": cmd_proposer_location,
        "meslocations": cmd_meslocations,
        "quitter_logement": cmd_quitter_logement,
    }
    for c, h in re_cmds.items():
        app.add_handler(CommandHandler(c, h))

    # ── Health ──────────────────────────────────────────────────────
    health_cmds = {
        "sante": cmd_sante, "medecin": cmd_medecin, "hopital": cmd_hopital,
        "gym": cmd_gym, "medicaments": cmd_medicaments, "assurance": cmd_assurance,
    }
    for c, h in health_cmds.items():
        app.add_handler(CommandHandler(c, h))

    # ── Crime ───────────────────────────────────────────────────────
    crime_cmds = {
        "crimes": cmd_crimes, "commettre": cmd_commettre, "caution": cmd_caution,
        "tribunal": cmd_tribunal, "juge": cmd_tribunal, "avocat": cmd_avocat,
        "gang": cmd_gang, "creergand": cmd_creergand,
        "rejoindregang": cmd_rejoindregang, "quittergang": cmd_quittergang,
        "ganginfo": cmd_ganginfo, "gangactions": cmd_gangactions,
        "gangcaisse": cmd_gangcaisse, "transfertchef": cmd_transfertchef,
        "ganghold": cmd_ganghold, "gangupgrade": cmd_gangupgrade,
        "gangclassement": cmd_gangclassement,
    }
    for c, h in crime_cmds.items():
        app.add_handler(CommandHandler(c, h))
    app.add_handler(CommandHandler("dissoudregang", cmd_dissoudregang))

    # ── Casino ──────────────────────────────────────────────────────
    casino_cmds = {
        "slots": cmd_slots, "blackjack": cmd_blackjack,
        "roulette": cmd_roulette, "crash": cmd_crash,
        "poker": cmd_poker, "mines": cmd_mines, "pmu": cmd_pmu,
    }
    for c, h in casino_cmds.items():
        app.add_handler(CommandHandler(c, h))
    app.add_handler(CallbackQueryHandler(bj_callback,    pattern=r"^bj_"))
    app.add_handler(CallbackQueryHandler(crash_callback, pattern=r"^crash_cashout:"))
    app.add_handler(CallbackQueryHandler(slots_callback, pattern=r"^slots_spin:"))
    app.add_handler(CallbackQueryHandler(roulette_callback, pattern=r"^roulette_"))
    app.add_handler(CallbackQueryHandler(mines_callback, pattern=r"^mines_"))
    app.add_handler(CallbackQueryHandler(pmu_callback, pattern=r"^pmu_"))
    app.add_handler(CommandHandler("casino", cmd_casino))
    # Correction du handler roulette avec group=10
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, roulette_number_input),
        group=10,
    )

    # ── Investments (bourse) ────────────────────────────────────────
    inv_cmds = {
        "marche": cmd_marche,
        "acheteraction": cmd_acheteraction, "buy": cmd_acheteraction,
        "vendreaction": cmd_vendreaction, "sell": cmd_vendreaction,
        "portefeuille": cmd_portefeuille, "portfolio": cmd_portefeuille,
        "historique": cmd_historique,
    }
    for c, h in inv_cmds.items():
        app.add_handler(CommandHandler(c, h))

    # ── Family ──────────────────────────────────────────────────────
    fam_cmds = {
        "mariage": cmd_mariage, "marry": cmd_mariage,
        "acceptermariage": cmd_acceptermariage, "divorce": cmd_family_divorce,
        "arbre": cmd_arbre, "tree": cmd_arbre, "famille": cmd_famille,
        "creerfamille": cmd_creer_famille, "ami": cmd_ami, "friend": cmd_ami,
        "mesamis": cmd_mesamis, "leaderboard": cmd_leaderboard,
        "adopter": cmd_adopter, "inviterfamille": cmd_inviterfamille,
        "rejoindrefamille": cmd_rejoindrefamille,
        "quitterfamille": cmd_quitterfamille,
        "transfertclan": cmd_transfertclan,
        "dissoudrefamille": cmd_dissoudrefamille,
    }
    for c, h in fam_cmds.items():
        app.add_handler(CommandHandler(c, h))

    # ── Travel ──────────────────────────────────────────────────────
    for c, h in [("destinations", cmd_destinations), ("voyager", cmd_voyager),
                 ("monstimbre", cmd_monstimbre)]:
        app.add_handler(CommandHandler(c, h))

    # ── Vehicles (ancien : achat/vente/assurance) ────────────────────
    for c, h in [("vehicules", cmd_vehicules_liste), ("acheterv", cmd_acheter_vehicule),
             ("mesvehicules", cmd_mes_vehicules), ("reparer", cmd_reparer_vehicule),
             ("assurerv", cmd_assurer_vehicule), ("vendrevehicule", cmd_vendre_vehicule),
             ("vehicule_info", cmd_vehicule_info)]:
        app.add_handler(CommandHandler(c, h))

    # ── Vehicles 2.0 (garage, repair, refuel) ───────────────────────
    app.add_handler(CommandHandler("garage", cmd_garage))
    app.add_handler(CommandHandler("repair", cmd_repair_vehicle))
    app.add_handler(CommandHandler("refuel", cmd_refuel))
    app.add_handler(CallbackQueryHandler(cmd_garage_select, pattern=r"^garage_select_"))
    app.add_handler(CallbackQueryHandler(cmd_garage_repair, pattern=r"^garage_repair_"))
    app.add_handler(CallbackQueryHandler(cmd_garage_refuel, pattern=r"^garage_refuel_"))

    # ── Luxury (VIP) ──────────────────────────────────────────────────
    for c, h in [("luxe", cmd_luxe), ("acheterLuxe", cmd_acheter_luxe),
                 ("prestige", cmd_prestige), ("classementprestige", cmd_classementprestige),
                 ("prestigelog", cmd_prestigelog)]:
        app.add_handler(CommandHandler(c, h))
    
    # ── Commandes VIP et cargo ──────────────────────────────────────
    app.add_handler(CommandHandler("acces_vip", cmd_acces_vip))
    app.add_handler(CommandHandler("lieux_vip", cmd_lieux_vip))
    app.add_handler(CommandHandler("cargobonus", cmd_cargobonus))

    # ── Arena ───────────────────────────────────────────────────────
    for c, h in [("defier", cmd_defier), ("defis", cmd_defis),
                 ("classementarene", cmd_classement_arene),
                 ("parier", cmd_parier), ("combat", cmd_defier),
                 ("arena", cmd_defier)]:
        app.add_handler(CommandHandler(c, h))
        
    # ── Garden ──────────────────────────────────────────────────────
    for c, h in [("jardin", cmd_jardin), ("planter", cmd_planter),
                 ("arroser", cmd_arroser), ("recolter", cmd_recolter),
                 ("vendrecolte", cmd_vendrecolte)]:
        app.add_handler(CommandHandler(c, h))

    # ── Companies ───────────────────────────────────────────────────
    comp_cmds = {
        "boites": cmd_boites, "listeboites": cmd_boites,
        "infoboite": cmd_infoboite, "employes": cmd_employes,
        "postuler": cmd_postuler, "demissionner": cmd_demissionner,
        "monentreprise": cmd_monentreprise, "candidatures": cmd_candidatures,
        "accepter": cmd_accepter, "refuser": cmd_refuser,
        "nommer": cmd_nommer_entreprise, "licencier": cmd_licencier,
        "creerboite": cmd_creerboite, "dissoudreboite": cmd_dissoudreboite,
        "depotboite": cmd_depotboite, "versersalaires": cmd_versersalaires,
        "logsboite": cmd_logsboite, "classement": cmd_classement_boites,
        "parts": cmd_parts, "acheterparts": cmd_acheterparts,
        "vendreparts": cmd_vendreparts, "dividendes": cmd_dividendes,
        "setsalaire": cmd_setsalaire,
        "negocier": cmd_negocier,
        "repondre_offre": cmd_repondre_offre,
        "rd": cmd_rd,
        "auditboite": cmd_auditboite,
        "setoverhead": cmd_setoverhead,
        "produits": cmd_produits,
        "creer_produit": cmd_creer_produit,
        "setprix": cmd_setprix,
        "annonce": cmd_annonce,
        "proposer_contrat": cmd_proposer_contrat,
        "former": cmd_former,
        "prime": cmd_prime,
        "emplois": cmd_emplois,
        "inviter": cmd_inviter,
        "repondre_invitation": cmd_repondre_invitation,
        "repondre_contrat": cmd_repondre_contrat,
    }
    for c, h in comp_cmds.items():
        app.add_handler(CommandHandler(c, h))
    app.add_handler(CommandHandler("transfert_entreprise", cmd_transfert_entreprise))
    app.add_handler(CommandHandler("retirerboite", cmd_retirerboite))
    app.add_handler(CommandHandler("renommer_entreprise", cmd_renommer_entreprise))
    app.add_handler(CommandHandler("changer_secteur", cmd_changer_secteur))
    app.add_handler(CommandHandler("donner_produit", cmd_donner_produit))
    app.add_handler(CommandHandler("emoji_produit", cmd_emoji_produit))
    app.add_handler(CommandHandler("renommer_produit", cmd_renommer_produit))
    app.add_handler(CommandHandler("desc_produit", cmd_desc_produit))
    app.add_handler(CommandHandler("supprimer_produit", cmd_supprimer_produit))
    app.add_handler(CommandHandler("retirer_produit", cmd_retirer_produit))
    app.add_handler(CommandHandler("proposer_fusion", cmd_proposer_fusion))
    app.add_handler(CommandHandler("repondre_fusion", cmd_repondre_fusion))
    app.add_handler(CommandHandler("proposer_contrat", cmd_proposer_contrat))
    app.add_handler(CommandHandler("repondre_contrat", cmd_repondre_contrat))
    app.add_handler(CommandHandler("negocier_contrat", cmd_negocier_contrat))

    # ── Social (nouvelle version) ─────────────────────────────────────
    social_commands = [
        ("plateformes", cmd_plateformes),
        ("poster", cmd_poster),
        ("story", cmd_story),
        ("live", cmd_live),
        ("donner_live", cmd_donner_live),
        ("sondage", cmd_sondage),
        ("vote", cmd_vote),
        ("analytiques", cmd_analytiques),
        ("creer_communaute", cmd_creer_communaute),
        ("inviter_communaute", cmd_inviter_communaute),
        ("communaute", cmd_communaute),
        ("rejoindre_communaute", cmd_rejoindre_communaute),
        ("partager", cmd_partager),
        ("lancer_tendance", cmd_lancer_tendance),
        ("utiliser_tendance", cmd_utiliser_tendance),
        ("socialcoins", cmd_socialcoins),
        ("donner_socialcoins", cmd_donner_socialcoins),
        ("collab", cmd_collab),
        ("vendre_followers", cmd_vendre_followers),
        ("noter", cmd_noter_social),
        ("classement_social", cmd_classement_social),
        ("mesabonnes", cmd_mesabonnes),
    ]
    for cmd, handler in social_commands:
        app.add_handler(CommandHandler(cmd, handler))
    app.add_handler(CommandHandler("resultats_sondage", cmd_resultats_sondage))

    # ── Political (nouveau système complet) ──────────────────────────
    political_commands = [
        ("constitution", cmd_constitution),
        ("modifierconstitution", cmd_modifierconstitution),
        ("creerparti", cmd_creerparti),
        ("partis", cmd_partis),
        ("rejoindreparti", cmd_rejoindreparti),
        ("quitterparti", cmd_quitterparti),
        ("transfertchefparti", cmd_transfertchefparti),
        ("lancerelection", cmd_lancerelection),
        ("candidater", cmd_candidater),
        ("voter", cmd_voter),
        ("depouiller", cmd_depouiller),
        ("proposerloi", cmd_proposerloi),
        ("voterloi", cmd_voterloi),
        ("nommerposte", cmd_nommer_politique),
        ("ministres", cmd_ministres),
        ("referendum", cmd_referendum),
        ("votereferendum", cmd_votereferendum),
        ("destituer", cmd_destituer),
        ("signer", cmd_signer),
        ("vote_destitution", cmd_vote_destitution),
        ("monposte", cmd_monposte),
        ("demissionnerposte", cmd_demissionnerposte),
        ("postes", cmd_postes),
        ("candidats", cmd_candidats),
        ("elections", cmd_elections),
    ]
    for cmd, handler in political_commands:
        app.add_handler(CommandHandler(cmd, handler))

    # ── Black Market ────────────────────────────────────────────────
    for c, h in [("noir", cmd_noir), ("acheternoir", cmd_acheter_noir),
                 ("hacktargets", cmd_hack_targets), ("hacker", cmd_hacker),
                 ("defenses", cmd_defenses)]:
        app.add_handler(CommandHandler(c, h))

    # ── Missions / Events ───────────────────────────────────────────
    for c, h in [("missions", cmd_missions), ("missions_completed", cmd_missions_completed),
                 ("resetmissions", cmd_resetmissions),
                 ("evenements", cmd_evenements), ("eventinfo", cmd_eventinfo)]:
        app.add_handler(CommandHandler(c, h))

    # ── Guilds ──────────────────────────────────────────────────────
    guild_cmds = {
        "guild": cmd_guild, "guild_create": cmd_guild_create,
        "guild_invite": cmd_guild_invite, "guild_join": cmd_guild_join,
        "guild_leave": cmd_guild_leave, "guild_members": cmd_guild_members,
        "guild_desc": cmd_guild_desc, "guild_promote": cmd_guild_promote,
        "guild_transfer": cmd_guild_transfer, "guild_dissolve": cmd_guild_dissolve,
        "guild_donate": cmd_guild_donate, "guild_quest": cmd_guild_quest,
        "guild_rank": cmd_guild_rank,
    }
    for c, h in guild_cmds.items():
        app.add_handler(CommandHandler(c, h))
    app.add_handler(CommandHandler("guild_declare_war", cmd_guild_declare_war))
    app.add_handler(CommandHandler("guild_war_status", cmd_guild_war_status))
    app.add_handler(CommandHandler("guild_attack", cmd_guild_attack))
    app.add_handler(CommandHandler("guild_surrender", cmd_guild_surrender))

    # ── Achievements ────────────────────────────────────────────────
    for c, h in [("achievements", cmd_achievements),
                 ("achievements_check", cmd_achievements_check),
                 ("succes", cmd_achievements)]:
        app.add_handler(CommandHandler(c, h))

    # ── Competitions ────────────────────────────────────────────────
    for c, h in [("competition", cmd_competition),
                 ("competition_join", cmd_competition_join),
                 ("competition_history", cmd_competition_history),
                 ("defi", cmd_competition)]:
        app.add_handler(CommandHandler(c, h))

    # ── Progression avancée ────────────────────────────────────────
    for c, h in [
        ("crafting", cmd_crafting),
        ("craft", cmd_craft),
        ("ranked", cmd_ranked),
        ("ranked_join", cmd_ranked_join),
        ("ranked_leaderboard", cmd_ranked_leaderboard),
        ("ranked_history", cmd_ranked_history),
        ("tutorial", cmd_tutorial),
        ("graphstats", cmd_graphstats),
    ]:
        app.add_handler(CommandHandler(c, h))

    # ── Notifications ───────────────────────────────────────────────
    for c, h in [("notifications", cmd_notifications),
                 ("notifications_history", cmd_notifications_history),
                 ("notifications_clear", cmd_notifications_clear),
                 ("notif", cmd_notifications),
                 ("broadcast_notify", cmd_broadcast_notify),
                 ("notify_user", cmd_notify_user),
                 ("notifications_stats", cmd_notifications_stats)]:
        app.add_handler(CommandHandler(c, h))

    # ── Reporting ───────────────────────────────────────────────────
    for c, h in [("report", cmd_report), ("report_user", cmd_report_user),
                 ("myreports", cmd_myreports), ("signalement", cmd_report),
                 ("reports_list", cmd_reports_list),
                 ("report_handle", cmd_report_handle),
                 ("reports_stats", cmd_reports_stats)]:
        app.add_handler(CommandHandler(c, h))

    # ── MULTIJOUEUR ─────────────────────────────────────────────────
    app.add_handler(CommandHandler("cadeau",      cmd_cadeau))
    app.add_handler(CommandHandler("salutations", cmd_salutations))
    app.add_handler(CommandHandler("salut",       cmd_salutations))
    app.add_handler(CommandHandler("echange",     cmd_echange))
    app.add_handler(CommandHandler("trade",       cmd_echange))
    app.add_handler(CommandHandler("relations",   cmd_relations))
    app.add_handler(CommandHandler("classements", cmd_classements))
    app.add_handler(CommandHandler("tops",        cmd_classements))
    app.add_handler(CommandHandler("creerescouade", cmd_creerescouade))
    app.add_handler(CommandHandler("escouade", cmd_escouade))
    app.add_handler(CommandHandler("inviterescouade", cmd_inviterescouade))
    app.add_handler(CommandHandler("quitterescouade", cmd_quitterescouade))
    app.add_handler(CommandHandler("chatescouade", cmd_chatescouade))
    app.add_handler(CommandHandler("raid", cmd_raid))
    app.add_handler(CallbackQueryHandler(trade_callback, pattern=r"^trade_"))
    app.add_handler(CallbackQueryHandler(squad_callback, pattern=r"^squad_"))

    # ── Admin ───────────────────────────────────────────────────────
    admin_cmds = {
        "admin": cmd_admin_aide,
        "addmoney": cmd_addmoney, "removemoney": cmd_removemoney,
        "setmoney": cmd_setmoney, "givemoney": cmd_addmoney,
        "cleardebt": cmd_cleardebt, "setbank": cmd_setbank,
        "resetuser": cmd_resetuser, "banuser": cmd_banuser,
        "unbanuser": cmd_unbanuser, "warn": cmd_warn,
        "clearwarn": cmd_clearwarn, "freezeuser": cmd_freezeuser,
        "unfreezeuser": cmd_unfreezeuser, "setjob": cmd_setjob,
        "setage": cmd_setage, "setkarma": cmd_setkarma,
        "setdiplome": cmd_setdiplome, "setprestige": cmd_setprestige,
        "sethp": cmd_sethp, "setenergy": cmd_setenergy,
        "sethappiness": cmd_sethappiness, "sethunger": cmd_sethunger,
        "setstress": cmd_setstress, "setxp": cmd_setxp,
        "setskill": cmd_setskill, "giveitem": cmd_giveitem,
        "userinfo": cmd_userinfo, "userstats": cmd_userstats,
        "topstats": cmd_topstats,
        "deletecompany": cmd_deletecompany, "listcompanies": cmd_listcompanies,
        "forcehire": cmd_forcehire, "crashmarket": cmd_crashmarket,
        "boommarket": cmd_boommarket, "resetmarket": cmd_resetmarket,
        "setprice": cmd_setprice, "createevent": cmd_createevent,
        "endevent": cmd_endevent, "lotowin": cmd_lotowin,
        "broadcast": cmd_broadcast, "announce": cmd_announce,
        "globalstats": cmd_botstats, "serverstats": cmd_botstats,
        "maintenance": cmd_maintenance, "godmode": cmd_godmode,
        "forceprison": cmd_forceprison, "freeprison": cmd_freeprison,
        "adminlogs": cmd_adminlogs, "resetcooldown": cmd_resetcooldown,
        "clearinventory": cmd_clearinventory, "setproperty": cmd_setproperty,
        "givebadge": cmd_givebadge, "reloadconfig": cmd_reloadconfig,
        "killuser": cmd_killuser, "timetravel": cmd_timetravel,
        "spawn": cmd_spawn,
        "adminpanel": cmd_adminpanel, "raidstatus": cmd_raidstatus,
        "forceraid": cmd_forceraid, "squadinfo": cmd_squadinfo,
        "legacy": cmd_legacy, "reincarnate": cmd_reincarnate,
        "guild_create_admin": cmd_guild_create_admin,
        "guild_delete": cmd_guild_delete,
        "guild_add": cmd_guild_add,
        "guild_remove": cmd_guild_remove,
        "guild_set_treasury": cmd_guild_set_treasury,
        "guild_set_level": cmd_guild_set_level,
        "resetmissions": cmd_resetmissions,
        "mission_force": cmd_mission_force,
        "comp_start": cmd_comp_start,
        "comp_end": cmd_comp_end,
        "comp_add_score": cmd_comp_add_score,
        "resetworld": cmd_resetworld,
    }
    for c, h in admin_cmds.items():
        app.add_handler(CommandHandler(c, h))
    # Commandes admin supplémentaires
    app.add_handler(CommandHandler("listusers", cmd_listusers))
    app.add_handler(CommandHandler("playerhistory", cmd_playerhistory))
    app.add_handler(CommandHandler("deleteuser", cmd_deleteuser))
    app.add_handler(CommandHandler("addbank", cmd_addbank))
    app.add_handler(CommandHandler("rembank", cmd_rembank))
    app.add_handler(CommandHandler("addprestige", cmd_addprestige))
    app.add_handler(CommandHandler("remprestige", cmd_remprestige))
    app.add_handler(CommandHandler("addxp", cmd_addxp))
    app.add_handler(CommandHandler("remxp", cmd_remxp))
    app.add_handler(CommandHandler("setlevel", cmd_setlevel))
    app.add_handler(CommandHandler("addkarma", cmd_addkarma))
    app.add_handler(CommandHandler("remkarma", cmd_remkarma))
    app.add_handler(CommandHandler("setlocation", cmd_setlocation))
    app.add_handler(CommandHandler("setbio", cmd_setbio))
    app.add_handler(CommandHandler("setcolor", cmd_setcolor))
    app.add_handler(CommandHandler("sethospital", cmd_sethospital))
    app.add_handler(CommandHandler("settravel", cmd_settravel))
    app.add_handler(CommandHandler("setitem", cmd_setitem))
    app.add_handler(CommandHandler("takeitem", cmd_takeitem))
    app.add_handler(CommandHandler("giveallitems", cmd_giveallitems))
    app.add_handler(CommandHandler("listitems", cmd_listitems))
    app.add_handler(CommandHandler("setpropertycond", cmd_setpropertycond))
    app.add_handler(CommandHandler("setvehicle", cmd_setvehicle))
    app.add_handler(CommandHandler("setvehiclecond", cmd_setvehiclecond))
    app.add_handler(CommandHandler("setluxury", cmd_setluxury))
    app.add_handler(CommandHandler("setblackmarket", cmd_setblackmarket))
    app.add_handler(CommandHandler("setrelation", cmd_setrelation))
    app.add_handler(CommandHandler("setmarriage", cmd_setmarriage))
    app.add_handler(CommandHandler("admin_divorce", cmd_admin_divorce))
    app.add_handler(CommandHandler("setfamily", cmd_setfamily))
    app.add_handler(CommandHandler("removefamily", cmd_removefamily))
    app.add_handler(CommandHandler("setcompanytreasury", cmd_setcompanytreasury))
    app.add_handler(CommandHandler("setcompanyreputation", cmd_setcompanyreputation))
    app.add_handler(CommandHandler("setcompanylevel", cmd_setcompanylevel))
    app.add_handler(CommandHandler("addcompanytreasury", cmd_addcompanytreasury))
    app.add_handler(CommandHandler("gang_create_admin", cmd_gang_create_admin))
    app.add_handler(CommandHandler("gang_delete", cmd_gang_delete))
    app.add_handler(CommandHandler("gang_add", cmd_gang_add))
    app.add_handler(CommandHandler("gang_remove", cmd_gang_remove))
    app.add_handler(CommandHandler("gang_set_treasury", cmd_gang_set_treasury))
    app.add_handler(CommandHandler("gang_set_reputation", cmd_gang_set_reputation))
    app.add_handler(CommandHandler("guild_set_xp", cmd_guild_set_xp))
    app.add_handler(CommandHandler("removetreasury", cmd_removetreasury))
    app.add_handler(CommandHandler("fix_inventory", cmd_fix_inventory))

    # ── Phase 1 : Marché joueur (items) ─────────────────────────────
    app.add_handler(CommandHandler("market", cmd_market))
    app.add_handler(CommandHandler("sellitem", cmd_sellitem))
    app.add_handler(CommandHandler("buyitem", cmd_buyitem))
    app.add_handler(CommandHandler("cancelitem", cmd_cancelitem))
    app.add_handler(CommandHandler("useitem", cmd_useitem))
    app.add_handler(CommandHandler("myitems", cmd_myitems))
    app.add_handler(CallbackQueryHandler(market_page_callback, pattern=r"^market_page_"))

    # ── Callbacks divers ────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(report_callback, pattern=r"^(report_|admin_report_)"))
    app.add_handler(CallbackQueryHandler(challenge_callback, pattern=r"^challenge_"))
    app.add_handler(CallbackQueryHandler(rps_callback, pattern=r"^rps\|"))
    app.add_handler(CallbackQueryHandler(coinflip_callback, pattern=r"^coinflip\|"))
    app.add_handler(CallbackQueryHandler(company_page_callback, pattern=r"^company_page_"))
    app.add_handler(CallbackQueryHandler(rental_callback, pattern=r"^rent_"))
    app.add_handler(CallbackQueryHandler(quiz_answer_callback, pattern=r"^quiz_answer\|"))
    app.add_handler(CallbackQueryHandler(guild_invite_callback, pattern=r"^guild_(accept|refuse)_"))
    # Callbacks pour le module social
    app.add_handler(CallbackQueryHandler(collab_callback_handler, pattern=r"^collab_(accept|refuse)_"))
    app.add_handler(CallbackQueryHandler(buy_followers_callback, pattern=r"^(buy|refuse)_followers_"))

    # ── Catch-all commande inconnue ─────────────────────────────────
    app.add_handler(MessageHandler(filters.COMMAND, cmd_unknown_command))

    # ── Gestionnaire d'erreur global ───────────────────────────────
    app.add_error_handler(error_handler)

    return app


# ═══════════════════════════════════════════════════════════════════
#                     PLANIFICATEUR OPTIMISÉ (BATCH + PAUSES)
# ═══════════════════════════════════════════════════════════════════
last_daily_run = 0

async def scheduler(app: Application):
    global last_daily_run
    logger.info("⏰ Planificateur optimisé démarré")
    
    while True:
        try:
            # TÂCHES LÉGÈRES (30s)
            try:
                await fluctuate_market()
            except Exception as e:
                logger.warning(f"⚠️ fluctuate_market: {e}")

            try:
                await process_multiplayer_maintenance()
            except Exception as e:
                logger.warning(f"⚠️ multiplayer_maintenance: {e}")
            
            try:
                await asyncio.wait_for(process_bank_interests(), timeout=10.0)
            except Exception as e:
                logger.warning(f"⚠️ bank_interests: {e}")
            
            try:
                await asyncio.wait_for(process_notifications(app.bot), timeout=10.0)
            except Exception as e:
                logger.warning(f"⚠️ notifications: {e}")
            
            try:
                await expire_old_trades()
            except Exception as e:
                logger.warning(f"⚠️ expire_trades: {e}")
            
            try:
                await clean_expired_listings()
            except Exception as e:
                logger.warning(f"⚠️ clean_expired_listings: {e}")
           
            try:
                await add_random_calendar_events()
            except Exception as e:
                logger.warning(f"⚠️ add_random_calendar_events: {e}")

            # Maintenance politique (clôture des votes, motions)
            try:
                await political_maintenance()
            except Exception as e:
                logger.warning(f"⚠️ political_maintenance: {e}")

            # TÂCHES QUOTIDIENNES
            now_ts = time.time()
            if now_ts - last_daily_run >= 86400:
                last_daily_run = now_ts
                logger.info("🔄 Démarrage maintenance quotidienne")
                
                from handlers.missions import reset_daily_missions
                from handlers.crime import process_gang_maintenance
                from handlers.economy import process_daily_tax, process_lottery_draw
                from handlers.education import process_education_maintenance
                from handlers.garden import process_garden_maintenance
                from handlers.health import process_health_maintenance
                from handlers.luxury import process_prestige_decay
                from handlers.realestate import process_realestate_maintenance
                from handlers.travel import process_travel_maintenance
                from handlers.vehicles import process_vehicles_maintenance
                from handlers.companies import process_company_maintenance
                from handlers.competitions import end_competition_and_reward, start_new_competition
                from handlers.notifications import clear_old_notifications
                from database import record_price_history, collect_rents, degrade_vehicles

                tasks = [
                    reset_daily_missions(),
                    collect_rents(),
                    degrade_vehicles(),
                    end_competition_and_reward(),
                    start_new_competition(),
                    process_company_maintenance(),
                    process_gang_maintenance(),
                    process_daily_tax(),
                    process_lottery_draw(),
                    process_education_maintenance(),
                    process_garden_maintenance(),
                    process_health_maintenance(),
                    process_prestige_decay(),
                    political_maintenance(),
                    process_guild_maintenance(),
                    process_realestate_maintenance(),
                    process_social_maintenance(),
                    process_social_revenue(),
                    process_travel_maintenance(),
                    process_vehicles_maintenance(),
                    clear_old_notifications(30),
                    record_price_history(),
                    add_random_calendar_events(),
                ]
                
                batch_size = 5
                for i in range(0, len(tasks), batch_size):
                    await asyncio.gather(*tasks[i:i+batch_size], return_exceptions=True)
                    if i + batch_size < len(tasks):
                        await asyncio.sleep(1)
                
                logger.info("✅ Maintenance quotidienne complétée")

        except Exception as e:
            logger.error(f"Erreur planificateur: {e}")
        
        await asyncio.sleep(30)


async def expire_old_trades():
    from database import now
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        async with db.execute(
            "SELECT trade_id, from_id, offer_money FROM mp_trades "
            "WHERE status='pending' AND expires_at < ?", (now(),)
        ) as cur:
            expired = await cur.fetchall()
        for trade_id, from_id, offer in expired:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (offer, from_id))
            await db.execute("UPDATE mp_trades SET status='expired' WHERE trade_id=?", (trade_id,))
        await db.commit()
        if expired:
            logger.info(f"⏰ {len(expired)} trades expirés et remboursés.")


# ═══════════════════════════════════════════════════════════════════
#                          MAIN
# ═══════════════════════════════════════════════════════════════════
async def main():
    await init_db()
    await run_migrations()   # ← AJOUT
    await init_mp_tables()
    logger.info("✅ Base de données initialisée (avec tables MP, guildes, véhicules 2.0).")
    app = build_app()
    logger.info("🤖 LifeSim Ultra V2 démarré ! Prêt à recevoir des messages.")
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        await scheduler(app)
        await app.updater.stop()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())