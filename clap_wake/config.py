from __future__ import annotations

import importlib
import json
import os
import re
import select
import sys
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from typing import Any

if os.name == "nt":  # pragma: no cover - windows-only path
    import msvcrt
else:  # pragma: no cover - exercised indirectly via behavior, not platform-specific imports
    import termios
    import tty

from .audio import (
    ClapConfig,
    calibrate_double_clap_profile,
    profile_from_dict,
    profile_to_dict,
    recommended_trigger_cooldown_seconds,
)
from .discovery import detect_known_targets
from .env_utils import load_env_value, save_env_value
from .media import find_highway_mp3
from .permissions import get_required_permission_keys, open_permission_settings, probe_permission
from .sound_library import (
    choose_audio_file_dialog,
    choose_directory_dialog,
    copy_audio_to_library,
    describe_existing_sound,
    get_media_library_dir,
    list_audio_files,
    normalize_user_path,
)

for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

APP_NAME = "ClapWakeUp"
YOUTUBE_FALLBACK_URL = "https://www.youtube.com/watch?v=l482T0yNkeo"
DEFAULT_LANGUAGE = "fr"
DEFAULT_WORKSPACE_DIRNAME = "working-directory-start-up"

AVAILABLE_TARGETS = [
    {"id": "codex_desktop", "label": "Codex Desktop"},
    {"id": "codex_cli", "label": "Codex CLI"},
    {"id": "claude_code", "label": "Claude Code"},
    {"id": "claude_web", "label": "claude.com"},
    {"id": "chatgpt_web", "label": "chatgpt.com"},
]

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 9,
    "language": DEFAULT_LANGUAGE,
    "workspace_dir": None,
    "selected_targets": [],
    "microphone": {
        "input_device": None,
        "input_device_name": None,
        "sample_rate": 16000,
        "blocksize": 512,
        "absolute_peak_threshold": 0.22,
        "relative_peak_multiplier": 5.5,
        "minimum_clap_gap_seconds": 0.12,
        "double_clap_max_gap_seconds": 0.85,
        "trigger_cooldown_seconds": 2.0,
        "profile": None,
    },
    "media": {
        "library_dir": None,
        "mode": "auto_downloads",
        "music_volume": 0.24,
        "selected_sound_path": None,
        "selected_folder_path": None,
        "selected_url": None,
        "youtube_fallback_url": YOUTUBE_FALLBACK_URL,
    },
    "realtime": {
        "api_key": None,
        "model": "gpt-realtime",
        "voice": "marin",
        "port": 8766,
        "assistant_name": "Jarvis",
        "welcome_name": "",
        "welcome_prompt": "",
        "launch_on_clap": False,
    },
    "dashboard": {
        "port": 8766,
    },
}

SETUP_TITLE_ASCII = r"""
   ____ _                  ____ _                 
  / ___| | __ _ _ __      / ___| | __ _ _ __      
 | |   | |/ _` | '_ \____| |   | |/ _` | '_ \     
 | |___| | (_| | |_) |____| |___| | (_| | |_) |    
  \____|_|\__,_| .__/      \____|_|\__,_| .__/     
               |_|                      |_|        
 __        __    _              _   _             
 \ \      / /_ _| | _____      | | | |_ __        
  \ \ /\ / / _` | |/ / _ \_____| | | | '_ \       
   \ V  V / (_| |   <  __/_____| |_| | |_) |      
    \_/\_/ \__,_|_|\_\___|      \___/| .__/       
                                      |_|          
"""

IRON_MAN_ASCII = r"""
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⢀⢄⢄⠢⡠⡀⢀⠄⡀⡀⠄⠄⠄⠄⠐⠡⠄⠉⠻⣻⣟⣿⣿⣄⠄⠄⠄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⢠⢣⠣⡎⡪⢂⠊⡜⣔⠰⡐⠠⠄⡾⠄⠈⠠⡁⡂⠄⠔⠸⣻⣿⣿⣯⢂⠄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⡀⠄⠄⠄⠄⠄⠄⠄⠐⢰⡱⣝⢕⡇⡪⢂⢊⢪⢎⢗⠕⢕⢠⣻⠄⠄⠄⠂⠢⠌⡀⠄⠨⢚⢿⣿⣧⢄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⡐⡈⠌⠄⠄⠄⠄⠄⠄⠄⡧⣟⢼⣕⢝⢬⠨⡪⡚⡺⡸⡌⡆⠜⣾⠄⠄⠄⠁⡐⠠⣐⠨⠄⠁⠹⡹⡻⣷⡕⢄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⢄⠇⠂⠄⠄⠄⠄⠄⠄⠄⢸⣻⣕⢗⠵⣍⣖⣕⡼⡼⣕⢭⢮⡆⠱⣽⡇⠄⠄⠂⠁⠄⢁⠢⡁⠄⠄⠐⠈⠺⢽⣳⣄⠄⠄
⠄⠄⠄⠄⠄⢔⢕⢌⠄⠄⠄⠄⠄⢀⠄⠄⣾⢯⢳⠹⠪⡺⡺⣚⢜⣽⣮⣳⡻⡇⡙⣜⡇⠄⠄⢸⠄⠄⠂⡀⢠⠂⠄⢶⠊⢉⡁⠨⡒⠄⠄
⠄⠄⠄⠄⡨⣪⣿⢰⠈⠄⠄⠄⡀⠄⠄⠄⣽⣵⢿⣸⢵⣫⣳⢅⠕⡗⣝⣼⣺⠇⡘⡲⠇⠄⠄⠨⠄⠐⢀⠐⠐⠡⢰⠁⠄⣴⣾⣷⣮⣇⠄
⠄⠄⠄⠄⡮⣷⣿⠪⠄⠄⠄⠠⠄⠂⠠⠄⡿⡞⡇⡟⣺⣺⢷⣿⣱⢕⢵⢺⢼⡁⠪⣘⡇⠄⠄⢨⠄⠐⠄⠄⢀⠄⢸⠄⠄⣿⣿⣿⣿⣿⡆
⠄⠄⠄⢸⣺⣿⣿⣇⠄⠄⠄⠄⢀⣤⣖⢯⣻⡑⢕⢭⢷⣻⣽⡾⣮⡳⡵⣕⣗⡇⠡⡣⣃⠄⠄⠸⠄⠄⠄⠄⠄⠄⠈⠄⠄⢻⣿⣿⣵⡿⣹
⠄⠄⠄⢸⣿⣿⣟⣯⢄⢤⢲⣺⣻⣻⡺⡕⡔⡊⡎⡮⣿⣿⣽⡿⣿⣻⣼⣼⣺⡇⡀⢎⢨⢐⢄⡀⠄⢁⠠⠄⠄⠐⠄⠣⠄⠸⣿⣿⣯⣷⣿
⠄⠄⠄⢸⣿⣿⣿⢽⠲⡑⢕⢵⢱⢪⡳⣕⢇⢕⡕⣟⣽⣽⣿⣿⣿⣿⣿⣿⣿⢗⢜⢜⢬⡳⣝⢸⣢⢀⠄⠄⠐⢀⠄⡀⠆⠄⠸⣿⣿⣿⣿
⠄⠄⠄⢸⣿⣿⣿⢽⣝⢎⡪⡰⡢⡱⡝⡮⡪⡣⣫⢎⣿⣿⣿⣿⣿⣿⠟⠋⠄⢄⠄⠈⠑⠑⠭⡪⡪⢏⠗⡦⡀⠐⠄⠄⠈⠄⠄⠙⣿⣿⣿
⠄⠄⠄⠘⣿⣿⣿⣿⡲⣝⢮⢪⢊⢎⢪⢺⠪⣝⢮⣯⢯⣟⡯⠷⠋⢀⣠⣶⣾⡿⠿⢀⣴⣖⢅⠪⠘⡌⡎⢍⣻⠠⠅⠄⠄⠈⠢⠄⠄⠙⠿
⠄⠄⠄⠄⣿⣿⣿⣿⣽⢺⢍⢎⢎⢪⡪⡮⣪⣿⣞⡟⠛⠋⢁⣠⣶⣿⡿⠛⠋⢀⣤⢾⢿⣕⢇⠡⢁⢑⠪⡳⡏⠄⠄⠄⠄⠄⠄⢑⠤⢀⢠
⠄⠄⠄⠄⢸⣿⣿⣿⣟⣮⡳⣭⢪⡣⡯⡮⠗⠋⠁⠄⠄⠈⠿⠟⠋⣁⣀⣴⣾⣿⣗⡯⡳⡕⡕⡕⡡⢂⠊⢮⠃⠄⠄⠄⠄⠄⢀⠐⠨⢁⠨
⠄⠄⠄⠄⠈⢿⣿⣿⣿⠷⠯⠽⠐⠁⠁⢀⡀⣤⢖⣽⢿⣦⣶⣾⣿⣿⣿⣿⣿⣿⢎⠇⡪⣸⡪⡮⠊⠄⠌⠎⡄⠄⠄⠄⠄⠄⠄⡂⢁⠉⡀
⠄⠄⠄⠄⠄⠈⠛⠚⠒⠵⣶⣶⣶⣶⢪⢃⢇⠏⡳⡕⣝⢽⡽⣻⣿⣿⣿⣿⡿⣺⠰⡱⢜⢮⡟⠁⠄⠄⠅⠅⢂⠐⠄⠐⢀⠄⠄⠄⠂⡁⠂
⠄⠄⠄⠄⠄⠄⠄⠰⠄⠐⢒⣠⣿⣟⢖⠅⠆⢝⢸⡪⡗⡅⡯⣻⣺⢯⡷⡯⡏⡇⡅⡏⣯⡟⠄⠄⠄⠨⡊⢔⢁⠠⠄⠄⠄⠄⠄⢀⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠹⣿⣿⣿⣿⢿⢕⢇⢣⢸⢐⢇⢯⢪⢪⠢⡣⠣⢱⢑⢑⠰⡸⡸⡇⠁⠄⠄⠠⡱⠨⢘⠄⠂⡀⠂⠄⠄⠄⠄⠈⠂⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠄⢻⣿⣿⣿⣟⣝⢔⢅⠸⡘⢌⠮⡨⡪⠨⡂⠅⡑⡠⢂⢇⢇⢿⠁⠄⢀⠠⠨⡘⢌⡐⡈⠄⠄⠠⠄⠄⠄⠄⠄⠄⠁
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠹⣿⣿⣿⣯⢢⢊⢌⢂⠢⠑⠔⢌⡂⢎⠔⢔⢌⠎⡎⡮⡃⢀⠐⡐⠨⡐⠌⠄⡑⠄⢂⠐⢀⠄⠄⠈⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠙⣿⣿⣿⣯⠂⡀⠔⢔⠡⡹⠰⡑⡅⡕⡱⠰⡑⡜⣜⡅⡢⡈⡢⡑⡢⠁⠰⠄⠨⢀⠐⠄⠄⠄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠈⠻⢿⣿⣷⣢⢱⠡⡊⢌⠌⡪⢨⢘⠜⡌⢆⢕⢢⢇⢆⢪⢢⡑⡅⢁⡖⡄⠄⠄⠄⢀⠄⠄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠛⢿⣿⣵⡝⣜⢐⠕⢌⠢⡑⢌⠌⠆⠅⠑⠑⠑⠝⢜⠌⠠⢯⡚⡜⢕⢄⠄⠁⠄⠄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠙⢿⣷⡣⣇⠃⠅⠁⠈⡠⡠⡔⠜⠜⣿⣗⡖⡦⣰⢹⢸⢸⢸⡘⠌⠄⠄⠄⠄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠈⠋⢍⣠⡤⡆⣎⢇⣇⢧⡳⡍⡆⢿⣯⢯⣞⡮⣗⣝⢎⠇⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠁⣿⣿⣎⢦⠣⠳⠑⠓⠑⠃⠩⠉⠈⠈⠉⠄⠁⠉⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠈⡿⡞⠁⠄⠄⢀⠐⢐⠠⠈⡌⠌⠂⡁⠌⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠈⢂⢂⢀⠡⠄⣈⠠⢄⠡⠒⠈⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄
⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠢⠠⠊⠨⠐⠈⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄⠄
"""


TEXTS = {
    "fr": {
        "setup_title": "⚙️  Setup Clap Wake Up",
        "choose_language_title": "🌍 Choisis la langue du setup",
        "choose_language_prompt": "🌍 Langue [1=Français, 2=English] : ",
        "choose_language_selector": "Utilise ↑ ↓ puis Entree pour choisir la langue.",
        "targets_title": "🎯 Choisis ce qui doit s'ouvrir au double clap.",
        "targets_scan_title": "🔍 Detection locale des outils",
        "targets_scan_intro": "On cherche automatiquement Codex, Claude et les chemins connus avant les questions.",
        "targets_scan_found": "✅ {label}: {value}",
        "targets_scan_missing": "◌ {label}: non detecte",
        "targets_hint": "💡 Entre les numeros avec ou sans virgules. Exemple: 1 2 4",
        "targets_selector": "Utilise ↑ ↓ pour bouger, Espace pour cocher, puis Entree pour valider.",
        "targets_selector_title": "🎯 Cibles du double clap",
        "selector_fallback": "Le selecteur visuel n'est pas disponible ici, retour au mode texte.",
        "selection_invalid": "Selection invalide: {error}",
        "selection_retry": "Reessaie. Exemple valide: 1 4 5",
        "selection_empty": "Selection vide. Choisis au moins une cible.",
        "selection_keep_current": "Entrer garde la selection actuelle: {selection}",
        "workspace_prompt": "📁 Dossier de travail [{default}] : ",
        "custom_targets_title": "🧩 Ajouter des cibles personnalisees ?",
        "custom_targets_hint": "   Tu peux ajouter URLs, apps/fichiers/dossiers, commandes terminal ou shell.",
        "custom_targets_current": "Cibles personnalisees actuelles:",
        "custom_targets_keep": "Garder les cibles personnalisees actuelles ? [Y/n] : ",
        "custom_targets_add_more": "Ajouter d'autres cibles personnalisees ? [y/N] : ",
        "yes_no_retry": "Reponds par y ou n.",
        "custom_target_title": "🛠️  Cible personnalisee #{index}",
        "custom_target_invalid": "Choix invalide. Entre 1, 2, 3 ou 4.",
        "custom_target_label": "Nom affiche [Custom {index}] : ",
        "custom_url_prompt": "🔗 URL a ouvrir : ",
        "custom_url_empty": "URL vide pour la cible personnalisee.",
        "custom_path_prompt": '📂 Chemin a ouvrir (glisser/deposer accepte, "open" pour le selecteur) : ',
        "custom_path_empty": "Aucun chemin selectionne.",
        "custom_terminal_prompt": "⌨️  Commande terminal a executer : ",
        "custom_terminal_empty": "Commande terminal vide.",
        "custom_shell_prompt": "🖥️  Commande shell a executer : ",
        "custom_shell_empty": "Commande shell vide.",
        "media_title": "🎵 Choix du media au declenchement",
        "media_current": "Media actuel: {value}",
        "media_folder_current": "Dossier actuel: {value}",
        "media_url_current": "URL actuelle: {value}",
        "media_choice": "🎧 Choix : ",
        "media_invalid": "Choix invalide.",
        "audio_file_prompt": '🎵 Glisse/depose le fichier ici ou colle son chemin ("open" pour le selecteur) : ',
        "audio_none": "Aucun chemin saisi.",
        "audio_missing": "Fichier introuvable: {path}",
        "audio_imported": "📦 Son importe: {path}",
        "folder_prompt": '📂 Dossier source ("open" pour le selecteur) : ',
        "folder_none": "Aucun dossier saisi.",
        "folder_picker_none": "Aucun dossier choisi.",
        "folder_missing": "Dossier introuvable: {path}",
        "folder_scan_none": "Aucun fichier audio trouve dans ce dossier.",
        "folder_scan_found": "🎼 Fichiers trouves:",
        "folder_scan_more": "  ... {count} autres fichiers non affiches",
        "folder_scan_choice": "Numero du fichier a importer : ",
        "folder_scan_one": "Choisis un seul numero.",
        "file_picker_none": "Aucun fichier choisi.",
        "microphone_title": "🎙️ Choix du micro",
        "microphone_current": "Micro actuel: {value}",
        "microphone_hint": "Choisis le micro a utiliser pour la verification, la calibration et l'ecoute.",
        "microphone_selector": "Utilise ↑ ↓ puis Entree pour choisir le micro.",
        "microphone_selector_title": "🎙️ Micro du double clap",
        "microphone_default_tag": "par defaut",
        "microphone_auto_selected": "🎙️ Un seul micro detecte, selection automatique: {value}",
        "microphone_none_detected": "Aucun micro d'entree detecte, on gardera le micro par defaut du systeme.",
        "microphone_keep_current": "Entrer garde le micro actuel: {selection}",
        "microphone_invalid": "Choix invalide. Entre un numero de la liste.",
        "microphone_selected": "🎙️ Micro selectionne: {value}",
        "trigger_title": "🎚️  Reglage du declenchement",
        "trigger_prompt": "🎚️  Temps mini entre deux declenchements complets, en secondes [{default}] : ",
        "trigger_number": "Entre un nombre valide, par exemple 2 ou 1.5.",
        "trigger_positive": "Entre une valeur positive.",
        "calibration_title": "👏 Calibration du double clap",
        "calibration_existing": "Un profil de double clap existe deja.",
        "calibration_recommend": "Recommande: fais 4 doubles claquements pour apprendre ton rythme et ton son.",
        "calibration_now_yes": "👏 Calibrer maintenant ? [Y/n] : ",
        "calibration_now_no": "👏 Calibrer maintenant ? [y/N] : ",
        "calibration_prepare": "🤖 Preparation calibration double clap.",
        "calibration_instructions": "Quand c'est parti, fais 4 doubles claquements avec ton rythme naturel.",
        "calibration_progress": "👏 Double clap capture {current}/{total}",
        "calibration_done": "✨ Calibration terminee.",
        "calibration_profile": "Profil: score moyen={score:.3f}, transient moyen={transient:.3f}, gap moyen={gap:.3f}s, tol={tolerance:.3f}",
        "openai_hint_present": "deja presente dans .env",
        "openai_hint_missing": "optionnelle pour Localhost Welcome",
        "openai_prompt": "🔑 Cle OpenAI [{hint}, Entrer pour ne pas changer] : ",
        "openai_saved": "💾 Cle enregistree dans {path}",
        "permissions_title": "🔐 Verification des permissions",
        "permissions_intro": "On teste maintenant les permissions systeme utiles pour eviter les popups au premier lancement.",
        "permissions_ok": "✅ {label}: OK",
        "permissions_blocked": "⚠️  {label}: acces non confirme",
        "permissions_details": "   {message}",
        "permissions_open_settings": "⚙️  Ouvrir les reglages systeme pour {label} ? [Y/n] : ",
        "permissions_retry": "🔁 Retester {label} maintenant ? [Y/n] : ",
        "permissions_skip": "On continue. Tu pourras refaire cette verification plus tard.",
        "permissions_done": "✨ Verification des permissions terminee.",
        "permissions_opened": "📂 Reglages ouverts pour {label}.",
        "permissions_unavailable": "Impossible d'ouvrir automatiquement les reglages pour {label}.",
        "video_prompt": "🔗 URL video [{default}] : ",
        "video_invalid": "URL invalide. Il faut commencer par http:// ou https://",
        "realtime_title": "🌐 Reglages Localhost Welcome",
        "realtime_local_url": "🌐 Localhost Welcome utilise {url}",
        "realtime_assistant_name": "🤖 Nom de l'IA [{default}] : ",
        "realtime_name": "👋 Nom a utiliser dans le message de bienvenue [{default}] : ",
        "realtime_voice": "🗣️  Voix Realtime [{default}] : ",
        "realtime_launch_yes": "✨ Ouvrir le message de bienvenue OpenAI Realtime au double clap ? [Y/n] : ",
        "realtime_launch_no": "✨ Ouvrir le message de bienvenue OpenAI Realtime au double clap ? [y/N] : ",
        "realtime_prompt": "✨ Prompt de bienvenue [{default}] : ",
        "config_saved": "✨ Config sauvee pour:",
        "codex_desktop_prompt": "Commande personnalisee pour ouvrir Codex Desktop",
        "codex_desktop_hint": "Laisse vide pour utiliser le comportement par defaut de l'OS",
        "terminal_command_prompt": "Commande terminal pour {label}",
        "terminal_command_hint": "Exemple: codex ou claude",
    },
    "en": {
        "setup_title": "⚙️  Clap Wake Up Setup",
        "choose_language_title": "🌍 Choose setup language",
        "choose_language_prompt": "🌍 Language [1=English, 2=Français] : ",
        "choose_language_selector": "Use ↑ ↓ then Enter to choose the language.",
        "targets_title": "🎯 Choose what should open on double clap.",
        "targets_scan_title": "🔍 Local tool detection",
        "targets_scan_intro": "Scanning Codex, Claude, and common local install paths before the questions.",
        "targets_scan_found": "✅ {label}: {value}",
        "targets_scan_missing": "◌ {label}: not found",
        "targets_hint": "💡 Enter numbers with or without commas. Example: 1 2 4",
        "targets_selector": "Use ↑ ↓ to move, Space to toggle, then Enter to confirm.",
        "targets_selector_title": "🎯 Double clap targets",
        "selector_fallback": "Visual selector is not available here, falling back to text mode.",
        "selection_invalid": "Invalid selection: {error}",
        "selection_retry": "Try again. Valid example: 1 4 5",
        "selection_empty": "Empty selection. Choose at least one target.",
        "selection_keep_current": "Press Enter to keep current selection: {selection}",
        "workspace_prompt": "📁 Working directory [{default}] : ",
        "custom_targets_title": "🧩 Add custom targets?",
        "custom_targets_hint": "   You can add URLs, apps/files/folders, terminal commands, or shell commands.",
        "custom_targets_current": "Current custom targets:",
        "custom_targets_keep": "Keep current custom targets? [Y/n] : ",
        "custom_targets_add_more": "Add more custom targets? [y/N] : ",
        "yes_no_retry": "Answer with y or n.",
        "custom_target_title": "🛠️  Custom target #{index}",
        "custom_target_invalid": "Invalid choice. Enter 1, 2, 3, or 4.",
        "custom_target_label": "Display name [Custom {index}] : ",
        "custom_url_prompt": "🔗 URL to open : ",
        "custom_url_empty": "Empty URL for custom target.",
        "custom_path_prompt": '📂 Path to open (drag and drop supported, "open" for picker) : ',
        "custom_path_empty": "No path selected.",
        "custom_terminal_prompt": "⌨️  Terminal command to run : ",
        "custom_terminal_empty": "Empty terminal command.",
        "custom_shell_prompt": "🖥️  Shell command to run : ",
        "custom_shell_empty": "Empty shell command.",
        "media_title": "🎵 Media choice on trigger",
        "media_current": "Current media: {value}",
        "media_folder_current": "Current folder: {value}",
        "media_url_current": "Current URL: {value}",
        "media_choice": "🎧 Choice : ",
        "media_invalid": "Invalid choice.",
        "audio_file_prompt": '🎵 Drag and drop the file here or paste its path ("open" for picker) : ',
        "audio_none": "No path entered.",
        "audio_missing": "File not found: {path}",
        "audio_imported": "📦 Imported sound: {path}",
        "folder_prompt": '📂 Source folder ("open" for picker) : ',
        "folder_none": "No folder entered.",
        "folder_picker_none": "No folder selected.",
        "folder_missing": "Folder not found: {path}",
        "folder_scan_none": "No audio files found in this folder.",
        "folder_scan_found": "🎼 Files found:",
        "folder_scan_more": "  ... {count} more files not shown",
        "folder_scan_choice": "File number to import : ",
        "folder_scan_one": "Choose exactly one number.",
        "file_picker_none": "No file selected.",
        "microphone_title": "🎙️ Microphone selection",
        "microphone_current": "Current microphone: {value}",
        "microphone_hint": "Choose the microphone to use for permission checks, calibration, and listening.",
        "microphone_selector": "Use ↑ ↓ then Enter to choose the microphone.",
        "microphone_selector_title": "🎙️ Double clap microphone",
        "microphone_default_tag": "default",
        "microphone_auto_selected": "🎙️ Only one microphone detected, auto-selected: {value}",
        "microphone_none_detected": "No input microphone was detected, so the system default microphone will be used.",
        "microphone_keep_current": "Press Enter to keep the current microphone: {selection}",
        "microphone_invalid": "Invalid choice. Enter a number from the list.",
        "microphone_selected": "🎙️ Selected microphone: {value}",
        "trigger_title": "🎚️  Trigger tuning",
        "trigger_prompt": "🎚️  Minimum time between full triggers, in seconds [{default}] : ",
        "trigger_number": "Enter a valid number, for example 2 or 1.5.",
        "trigger_positive": "Enter a positive value.",
        "calibration_title": "👏 Double clap calibration",
        "calibration_existing": "A double clap profile already exists.",
        "calibration_recommend": "Recommended: do 4 double claps so the app learns your rhythm and sound.",
        "calibration_now_yes": "👏 Calibrate now? [Y/n] : ",
        "calibration_now_no": "👏 Calibrate now? [y/N] : ",
        "calibration_prepare": "🤖 Preparing double clap calibration.",
        "calibration_instructions": "When ready, do 4 natural double claps.",
        "calibration_progress": "👏 Double clap captured {current}/{total}",
        "calibration_done": "✨ Calibration complete.",
        "calibration_profile": "Profile: avg score={score:.3f}, avg transient={transient:.3f}, avg gap={gap:.3f}s, tol={tolerance:.3f}",
        "openai_hint_present": "already present in .env",
        "openai_hint_missing": "optional for Localhost Welcome",
        "openai_prompt": "🔑 OpenAI key [{hint}, press Enter to keep current] : ",
        "openai_saved": "💾 Key saved in {path}",
        "permissions_title": "🔐 Permissions check",
        "permissions_intro": "The setup now tests the system permissions that matter so the first real launch is smoother.",
        "permissions_ok": "✅ {label}: OK",
        "permissions_blocked": "⚠️  {label}: access not confirmed",
        "permissions_details": "   {message}",
        "permissions_open_settings": "⚙️  Open system settings for {label}? [Y/n] : ",
        "permissions_retry": "🔁 Retry {label} now? [Y/n] : ",
        "permissions_skip": "Continuing. You can run this check again later.",
        "permissions_done": "✨ Permission check complete.",
        "permissions_opened": "📂 Settings opened for {label}.",
        "permissions_unavailable": "Could not open system settings automatically for {label}.",
        "video_prompt": "🔗 Video URL [{default}] : ",
        "video_invalid": "Invalid URL. It must start with http:// or https://",
        "realtime_title": "🌐 Localhost Welcome settings",
        "realtime_local_url": "🌐 Localhost Welcome uses {url}",
        "realtime_assistant_name": "🤖 AI name [{default}] : ",
        "realtime_name": "👋 Name to use in the welcome message [{default}] : ",
        "realtime_voice": "🗣️  Realtime voice [{default}] : ",
        "realtime_launch_yes": "✨ Open the OpenAI Realtime welcome on double clap? [Y/n] : ",
        "realtime_launch_no": "✨ Open the OpenAI Realtime welcome on double clap? [y/N] : ",
        "realtime_prompt": "✨ Welcome prompt [{default}] : ",
        "config_saved": "✨ Config saved for:",
        "codex_desktop_prompt": "Custom command to open Codex Desktop",
        "codex_desktop_hint": "Leave empty to use the OS default behavior",
        "terminal_command_prompt": "Terminal command for {label}",
        "terminal_command_hint": "Example: codex or claude",
    },
}


def t(language: str, key: str, **kwargs: Any) -> str:
    return TEXTS.get(language, TEXTS[DEFAULT_LANGUAGE])[key].format(**kwargs)


def terminal_ui_available() -> bool:
    term = os.environ.get("TERM", "")
    return (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and (os.name == "nt" or (term and term != "dumb"))
    )


def _ansi_enabled() -> bool:
    return os.name != "nt" or os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM")


def _cursor_up(lines: int) -> str:
    return f"\x1b[{lines}A" if lines > 0 else ""


def _clear_line() -> str:
    return "\r\x1b[2K" if _ansi_enabled() else "\r"


@contextmanager
def _hidden_cursor() -> Any:
    if _ansi_enabled():
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()
    try:
        yield
    finally:
        if _ansi_enabled():
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()


@contextmanager
def _raw_keyboard_mode() -> Any:
    if os.name == "nt":  # pragma: no cover - windows-only path
        yield
        return

    fd = sys.stdin.fileno()
    original_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original_settings)


def _read_key() -> str:
    if os.name == "nt":  # pragma: no cover - windows-only path
        first = msvcrt.getwch()
        if first in {"\x00", "\xe0"}:
            second = msvcrt.getwch()
            return {
                "H": "up",
                "P": "down",
            }.get(second, "")
        if first == "\r":
            return "enter"
        if first == " ":
            return "space"
        if first == "\x1b":
            return "escape"
        if first.lower() == "q":
            return "quit"
        return ""

    fd = sys.stdin.fileno()
    first = os.read(fd, 1)
    if first in {b"\r", b"\n"}:
        return "enter"
    if first == b" ":
        return "space"
    if first == b"\x1b":
        sequence = b""
        while select.select([fd], [], [], 0.01)[0]:
            sequence += os.read(fd, 1)
            if len(sequence) >= 2:
                break
        if sequence[:2] == b"[A":
            return "up"
        if sequence[:2] == b"[B":
            return "down"
        return "escape"
    if first.lower() == b"q":
        return "quit"
    return ""


def _render_inline_selector(
    title: str,
    hint: str,
    options: list[str],
    cursor_index: int,
    selected_indexes: set[int] | None = None,
) -> list[str]:
    lines = ["", title, hint, ""]
    for index, option in enumerate(options):
        marker = "›"
        cursor = marker if index == cursor_index else " "
        if selected_indexes is None:
            lines.append(f" {cursor} {option}")
        else:
            checked = "x" if index in selected_indexes else " "
            lines.append(f" {cursor} [{checked}] {option}")
    lines.append("")
    return lines


def _clear_rendered_lines(lines_rendered: int) -> None:
    if lines_rendered <= 0:
        return
    if _ansi_enabled():
        sys.stdout.write(_cursor_up(lines_rendered))
    for index in range(lines_rendered):
        sys.stdout.write(_clear_line())
        if index < lines_rendered - 1:
            sys.stdout.write("\n")
    if _ansi_enabled():
        sys.stdout.write(_cursor_up(lines_rendered - 1))
    sys.stdout.flush()


def inline_single_select(
    title: str,
    hint: str,
    options: list[tuple[str, str]],
    default_value: str,
) -> str | None:
    values = [value for value, _label in options]
    labels = [label for _value, label in options]
    current_index = values.index(default_value) if default_value in values else 0
    lines_rendered = 0

    with _hidden_cursor(), _raw_keyboard_mode():
        while True:
            if lines_rendered and _ansi_enabled():
                sys.stdout.write(_cursor_up(lines_rendered))
            rendered = _render_inline_selector(title, hint, labels, current_index)
            for line in rendered:
                sys.stdout.write(f"{_clear_line()}{line}\n")
            sys.stdout.flush()
            lines_rendered = len(rendered)

            key = _read_key()
            if key == "up":
                current_index = (current_index - 1) % len(labels)
                continue
            if key == "down":
                current_index = (current_index + 1) % len(labels)
                continue
            if key == "enter":
                _clear_rendered_lines(lines_rendered)
                return values[current_index]
            if key in {"escape", "quit"}:
                _clear_rendered_lines(lines_rendered)
                return None


def inline_multi_select(
    title: str,
    hint: str,
    options: list[tuple[int, str]],
    default_values: list[int] | None = None,
) -> list[int] | None:
    values = [value for value, _label in options]
    labels = [label for _value, label in options]
    selected_indexes = {
        index for index, value in enumerate(values) if value in (default_values or [])
    }
    current_index = min(selected_indexes) if selected_indexes else 0
    lines_rendered = 0

    with _hidden_cursor(), _raw_keyboard_mode():
        while True:
            if lines_rendered and _ansi_enabled():
                sys.stdout.write(_cursor_up(lines_rendered))
            rendered = _render_inline_selector(
                title=title,
                hint=hint,
                options=labels,
                cursor_index=current_index,
                selected_indexes=selected_indexes,
            )
            for line in rendered:
                sys.stdout.write(f"{_clear_line()}{line}\n")
            sys.stdout.flush()
            lines_rendered = len(rendered)

            key = _read_key()
            if key == "up":
                current_index = (current_index - 1) % len(labels)
                continue
            if key == "down":
                current_index = (current_index + 1) % len(labels)
                continue
            if key == "space":
                if current_index in selected_indexes:
                    selected_indexes.remove(current_index)
                else:
                    selected_indexes.add(current_index)
                continue
            if key == "enter":
                _clear_rendered_lines(lines_rendered)
                return [values[index] for index in sorted(selected_indexes)]
            if key in {"escape", "quit"}:
                _clear_rendered_lines(lines_rendered)
                return None


def choose_language(default_language: str | None) -> str:
    current = default_language or DEFAULT_LANGUAGE
    if terminal_ui_available():
        result = inline_single_select(
            title="🌍 Clap Wake Up",
            hint=t(current, "choose_language_selector"),
            options=[
                ("fr", "🇫🇷 Français"),
                ("en", "🇬🇧 English"),
            ],
            default_value=current if current in {"fr", "en"} else DEFAULT_LANGUAGE,
        )
        if result in {"fr", "en"}:
            return result
        return current if current in {"fr", "en"} else DEFAULT_LANGUAGE

    default_choice = "2" if current == "en" else "1"
    print()
    print("🌍  Language / Langue")
    print("  1. Français")
    print("  2. English")
    print()
    raw = input(f"🌍 Choice / Choix [{default_choice}] : ").strip()
    if raw == "2":
        return "en"
    if raw == "1":
        return "fr"
    if raw == "":
        return current if current in {"fr", "en"} else DEFAULT_LANGUAGE
    return current if current in {"fr", "en"} else DEFAULT_LANGUAGE


def _configured_input_device_index(microphone: dict[str, Any]) -> int | None:
    raw = microphone.get("input_device")
    if raw in {None, ""}:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _default_input_device_index(sd: Any) -> int | None:
    default_device = getattr(getattr(sd, "default", None), "device", None)
    if isinstance(default_device, (list, tuple)):
        candidate = default_device[0] if default_device else None
    else:
        candidate = default_device
    if candidate in {None, -1}:
        return None
    try:
        return int(candidate)
    except (TypeError, ValueError):
        return None


def list_input_microphones() -> list[dict[str, Any]]:
    try:
        sd = importlib.import_module("sounddevice")
    except ImportError:
        return []

    try:
        raw_devices = sd.query_devices()
    except Exception:
        return []

    try:
        hostapi_names = [str(item.get("name") or "") for item in sd.query_hostapis()]
    except Exception:
        hostapi_names = []

    default_index = _default_input_device_index(sd)
    microphones: list[dict[str, Any]] = []
    for index, device in enumerate(raw_devices):
        max_input_channels = int(device.get("max_input_channels", 0) or 0)
        if max_input_channels < 1:
            continue

        hostapi_name = ""
        try:
            hostapi_index = int(device.get("hostapi", -1))
        except (TypeError, ValueError):
            hostapi_index = -1
        if 0 <= hostapi_index < len(hostapi_names):
            hostapi_name = hostapi_names[hostapi_index]

        microphones.append(
            {
                "index": index,
                "name": str(device.get("name") or f"Input {index}"),
                "hostapi_name": hostapi_name,
                "is_default": index == default_index,
            }
        )
    return microphones


def format_microphone_label(device: dict[str, Any], language: str) -> str:
    label = str(device.get("name") or f"Input {device.get('index', '?')}")
    hostapi_name = str(device.get("hostapi_name") or "")
    if hostapi_name:
        label = f"{label} ({hostapi_name})"
    if device.get("is_default"):
        label = f"{label} [{t(language, 'microphone_default_tag')}]"
    return label


def describe_configured_microphone(microphone: dict[str, Any]) -> str | None:
    name = microphone.get("input_device_name")
    device_index = _configured_input_device_index(microphone)
    if name and device_index is not None:
        return f"{name} ({device_index})"
    if name:
        return str(name)
    if device_index is not None:
        return str(device_index)
    return None


def _set_selected_microphone(microphone: dict[str, Any], device: dict[str, Any]) -> None:
    microphone["input_device"] = int(device["index"])
    microphone["input_device_name"] = str(device.get("name") or f"Input {device['index']}")


def _default_microphone_choice(
    microphone: dict[str, Any], devices: list[dict[str, Any]]
) -> dict[str, Any]:
    configured_index = _configured_input_device_index(microphone)
    if configured_index is not None:
        for device in devices:
            if int(device["index"]) == configured_index:
                return device
    for device in devices:
        if device.get("is_default"):
            return device
    return devices[0]


def prompt_for_microphone_selection(config: dict[str, Any], language: str) -> None:
    microphone = config["microphone"]
    devices = list_input_microphones()

    print()
    print(t(language, "microphone_title"))
    print()

    current = describe_configured_microphone(microphone)
    if current:
        print(t(language, "microphone_current", value=current))

    if not devices:
        microphone["input_device"] = None
        microphone["input_device_name"] = None
        print(t(language, "microphone_none_detected"))
        return

    if len(devices) == 1:
        selected = devices[0]
        _set_selected_microphone(microphone, selected)
        print(t(language, "microphone_auto_selected", value=format_microphone_label(selected, language)))
        return

    default_device = _default_microphone_choice(microphone, devices)
    print(t(language, "microphone_hint"))
    print()

    if terminal_ui_available():
        selected_index = inline_single_select(
            title=t(language, "microphone_selector_title"),
            hint=t(language, "microphone_selector"),
            options=[
                (str(device["index"]), format_microphone_label(device, language))
                for device in devices
            ],
            default_value=str(default_device["index"]),
        )
        if selected_index is not None:
            selected = next(
                (device for device in devices if str(device["index"]) == selected_index),
                default_device,
            )
            _set_selected_microphone(microphone, selected)
            print(t(language, "microphone_selected", value=format_microphone_label(selected, language)))
            return
        print()
        print(t(language, "selector_fallback"))
        print()

    for display_index, device in enumerate(devices, start=1):
        print(f"  {display_index}. {format_microphone_label(device, language)}")
    print()
    print(t(language, "microphone_keep_current", selection=format_microphone_label(default_device, language)))
    print()

    while True:
        raw = input("> ").strip()
        if not raw:
            selected = default_device
            break
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(devices):
                selected = devices[choice - 1]
                break
        print(t(language, "microphone_invalid"))

    _set_selected_microphone(microphone, selected)
    print(t(language, "microphone_selected", value=format_microphone_label(selected, language)))


def get_default_welcome_prompt(language: str) -> str:
    if language == "en":
        return "Welcome me with energy in English, then ask for my first goal of the session."
    return "Souhaite-moi la bienvenue de facon energique, en francais, puis demande mon premier objectif de la session."


def get_app_home() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / APP_NAME
    return Path.home() / ".config" / APP_NAME.lower()


def get_config_path() -> Path:
    return get_app_home() / "config.json"


def get_log_path() -> Path:
    return get_app_home() / "clap-wake.log"


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def get_default_downloads_dir() -> Path:
    return Path.home() / "Downloads"


def get_default_assets_audio_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "audio"


def get_default_workspace_dir(base_dir: Path | None = None) -> Path:
    return (base_dir or Path.cwd()) / DEFAULT_WORKSPACE_DIRNAME


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or get_config_path()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        user_config = json.load(handle)

    config = deepcopy(DEFAULT_CONFIG)
    merge_dict(config, user_config)
    migrate_config(config)
    return config


def save_config(config: dict[str, Any], config_path: Path | None = None) -> Path:
    path = config_path or get_config_path()
    ensure_parent_dir(path)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    return path


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge_dict(base[key], value)
        else:
            base[key] = value


def prompt_setup(config_path: Path | None = None) -> Path:
    target_lookup = {item["id"]: item["label"] for item in AVAILABLE_TARGETS}
    existing_config = load_existing_or_default(config_path)
    config = deepcopy(DEFAULT_CONFIG)
    merge_dict(config, existing_config)
    detected_targets = detect_known_targets()
    existing_targets_by_id = {
        target["id"]: target for target in existing_config.get("selected_targets", []) if target.get("id")
    }
    builtin_target_ids = {item["id"] for item in AVAILABLE_TARGETS}
    existing_custom_targets = [
        deepcopy(target)
        for target in existing_config.get("selected_targets", [])
        if target.get("id") not in builtin_target_ids
    ]

    print_setup_banner()
    print()
    language = choose_language(config.get("language"))
    config["language"] = language
    if config["realtime"].get("welcome_prompt") in {
        "",
        DEFAULT_CONFIG["realtime"]["welcome_prompt"],
        get_default_welcome_prompt("fr"),
        get_default_welcome_prompt("en"),
    }:
        config["realtime"]["welcome_prompt"] = get_default_welcome_prompt(language)

    print(t(language, "setup_title"))
    print()
    print_detected_targets_summary(language, detected_targets)
    print()
    print(t(language, "targets_title"))
    print()
    selected_ids = prompt_for_targets_selection(language, detected_targets, existing_config)

    selected_targets = []
    for item_index in selected_ids:
        target = AVAILABLE_TARGETS[item_index - 1]
        selected_targets.append(
            build_target_config(
                target["id"],
                target["label"],
                language=language,
                detected=detected_targets.get(target["id"]),
                existing=existing_targets_by_id.get(target["id"]),
            )
        )
    selected_targets.extend(prompt_for_custom_targets(language, existing_custom_targets))

    workspace_default = str(Path(config.get("workspace_dir") or get_default_workspace_dir()))
    print()
    workspace_input = input(t(language, "workspace_prompt", default=workspace_default)).strip()
    workspace_dir = workspace_input or workspace_default

    workspace_path = Path(workspace_dir).expanduser()
    workspace_path.mkdir(parents=True, exist_ok=True)
    config["workspace_dir"] = str(workspace_path)
    maybe_prompt_openai_env(config, language)

    config["selected_targets"] = selected_targets
    config["media"]["library_dir"] = str(get_media_library_dir())
    prompt_for_microphone_selection(config, language)
    prompt_for_permissions(config, language)
    prompt_for_media(config, language)
    prompt_for_clap_calibration(config, language)
    prompt_for_realtime(config, language)

    print()
    print(t(language, "config_saved"))
    for target in selected_targets:
        print(f"  - {target.get('label', target_lookup.get(target['id'], target['id']))}")

    return save_config(config, config_path=config_path)


def parse_selection(raw: str, max_item: int) -> list[int]:
    if not raw:
        return []

    selected: list[int] = []
    seen: set[int] = set()
    for chunk in re.findall(r"\d+", raw):
        value = int(chunk)
        if value < 1 or value > max_item:
            raise ValueError(f"Choix invalide: {value}")
        if value not in seen:
            selected.append(value)
            seen.add(value)
    return selected


def prompt_for_selection(max_item: int, language: str) -> list[int]:
    while True:
        selection = input("> ").strip()
        try:
            selected_ids = parse_selection(selection, max_item)
        except ValueError as exc:
            print(t(language, "selection_invalid", error=exc))
            print(t(language, "selection_retry"))
            continue

        if not selected_ids:
            print(t(language, "selection_empty"))
            continue

        return selected_ids


def prompt_for_targets_selection(
    language: str,
    detected_targets: dict[str, Any],
    existing_config: dict[str, Any] | None = None,
) -> list[int]:
    existing_ids = {
        target.get("id")
        for target in (existing_config or {}).get("selected_targets", [])
        if target.get("id") in {item["id"] for item in AVAILABLE_TARGETS}
    }
    default_selected = [
        index
        for index, target in enumerate(AVAILABLE_TARGETS, start=1)
        if target["id"] in existing_ids
    ]

    if terminal_ui_available():
        values: list[tuple[int, str]] = []
        for index, target in enumerate(AVAILABLE_TARGETS, start=1):
            detected_label = format_detected_target(detected_targets.get(target["id"]))
            suffix = f"  ✅ {detected_label}" if detected_label else ""
            values.append((index, f"{target['label']}{suffix}"))

        while True:
            selected = inline_multi_select(
                title=t(language, "targets_selector_title"),
                hint=t(language, "targets_selector"),
                options=values,
                default_values=default_selected,
            )
            if selected is None:
                break
            if selected:
                return selected
            print()
            print(t(language, "selection_empty"))
            print()
        print()
        print(t(language, "selector_fallback"))
        print()

    for index, target in enumerate(AVAILABLE_TARGETS, start=1):
        detected_label = format_detected_target(detected_targets.get(target["id"]))
        suffix = f"  ✅ {detected_label}" if detected_label else ""
        print(f"  {index}. {target['label']}{suffix}")
    print()
    print(t(language, "targets_hint"))
    if default_selected:
        labels = ", ".join(str(index) for index in default_selected)
        print(t(language, "selection_keep_current", selection=labels))
    print()
    while True:
        selection = input("> ").strip()
        if not selection and default_selected:
            return default_selected
        try:
            selected_ids = parse_selection(selection, len(AVAILABLE_TARGETS))
        except ValueError as exc:
            print(t(language, "selection_invalid", error=exc))
            print(t(language, "selection_retry"))
            continue

        if not selected_ids:
            print(t(language, "selection_empty"))
            continue

        return selected_ids


def build_target_config(
    target_id: str,
    label: str,
    language: str,
    detected: dict[str, Any] | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if target_id == "codex_desktop":
        detected_app = (existing or {}).get("app_path") or (detected or {}).get("app_path")
        default_command = (existing or {}).get("custom_command", "")
        command = input(
            default_prompt(
                t(language, "codex_desktop_prompt"),
                default_command,
                t(language, "codex_desktop_hint"),
            )
        ).strip()
        return {
            "id": target_id,
            "label": label,
            "custom_command": command or default_command or None,
            "app_path": detected_app,
        }

    if target_id in {"codex_cli", "claude_code"}:
        fallback_command = "codex" if target_id == "codex_cli" else "claude"
        default_command = (
            (existing or {}).get("command")
            or (detected or {}).get("command")
            or fallback_command
        )
        command = input(
            default_prompt(
                t(language, "terminal_command_prompt", label=label),
                default_command,
                t(language, "terminal_command_hint"),
            )
        ).strip()
        return {"id": target_id, "label": label, "command": command or default_command}

    if target_id == "claude_web":
        return {"id": target_id, "label": label, "url": "https://claude.com"}

    if target_id == "chatgpt_web":
        return {"id": target_id, "label": label, "url": "https://chatgpt.com"}

    if target_id == "welcome_localhost":
        return {"id": target_id, "label": label}

    raise ValueError(f"Unsupported target id: {target_id}")


def prompt_for_custom_targets(language: str, existing_custom_targets: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    custom_targets: list[dict[str, Any]] = [deepcopy(target) for target in (existing_custom_targets or [])]
    print()
    print(t(language, "custom_targets_title"))
    print(t(language, "custom_targets_hint"))
    print()

    if custom_targets:
        print(t(language, "custom_targets_current"))
        for target in custom_targets:
            print(f"  - {target.get('label', target.get('id', 'custom'))}")
        print()
        while True:
            answer = input(t(language, "custom_targets_keep")).strip().casefold()
            if answer in {"", "y", "yes", "oui", "o"}:
                break
            if answer in {"n", "no", "non"}:
                custom_targets = []
                break
            print(t(language, "yes_no_retry"))

    while True:
        answer = input(t(language, "custom_targets_add_more")).strip().casefold()
        if answer in {"", "n", "no", "non"}:
            return custom_targets
        if answer in {"y", "yes", "oui", "o"}:
            custom_targets.append(prompt_for_custom_target(len(custom_targets) + 1, language))
            continue
        print(t(language, "yes_no_retry"))


def prompt_for_custom_target(index: int, language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    option_labels = {
        "fr": [
            "URL",
            "App, fichier ou dossier a ouvrir",
            "Commande terminal",
            "Commande shell",
        ],
        "en": [
            "URL",
            "App, file, or folder to open",
            "Terminal command",
            "Shell command",
        ],
    }[language]
    print()
    print(t(language, "custom_target_title", index=index))
    print()
    for option_index, label in enumerate(option_labels, start=1):
        print(f"  {option_index}. {label}")
    print()

    while True:
        choice = input("> ").strip()
        if choice not in {"1", "2", "3", "4"}:
            print(t(language, "custom_target_invalid"))
            continue
        break

    label = input(t(language, "custom_target_label", index=index)).strip() or f"Custom {index}"

    if choice == "1":
        url = input(t(language, "custom_url_prompt")).strip()
        if not url:
            raise ValueError(t(language, "custom_url_empty"))
        return {
            "id": "custom_url",
            "label": label,
            "url": url,
        }

    if choice == "2":
        raw_path = input(t(language, "custom_path_prompt")).strip()
        if raw_path.casefold() == "open":
            chosen = choose_audio_file_dialog() or choose_directory_dialog()
            if chosen is None:
                raise ValueError(t(language, "custom_path_empty"))
            path = chosen
        else:
            path = normalize_user_path(raw_path)
        return {
            "id": "custom_path",
            "label": label,
            "path": str(path),
        }

    if choice == "3":
        command = input(t(language, "custom_terminal_prompt")).strip()
        if not command:
            raise ValueError(t(language, "custom_terminal_empty"))
        return {
            "id": "custom_terminal_command",
            "label": label,
            "command": command,
        }

    command = input(t(language, "custom_shell_prompt")).strip()
    if not command:
        raise ValueError(t(language, "custom_shell_empty"))
    return {
        "id": "custom_shell_command",
        "label": label,
        "command": command,
    }


def default_prompt(title: str, default: str, hint: str | None = None) -> str:
    parts = [title]
    if hint:
        parts.append(f"({hint})")
    if default:
        parts.append(f"[{default}]")
    return " ".join(parts) + " : "


def prompt_yes_no(language: str, prompt: str, default: bool) -> bool:
    default_value = "y" if default else "n"
    while True:
        raw = input(prompt).strip().casefold()
        if not raw:
            return default
        if raw in {"y", "yes", "oui", "o"}:
            return True
        if raw in {"n", "no", "non"}:
            return False
        print(t(language, "yes_no_retry"))


def load_existing_or_default(config_path: Path | None) -> dict[str, Any]:
    if config_path and config_path.exists():
        return load_config(config_path)
    return deepcopy(DEFAULT_CONFIG)


def migrate_config(config: dict[str, Any]) -> None:
    media = config.setdefault("media", {})
    realtime = config.setdefault("realtime", {})
    microphone = config.setdefault("microphone", {})
    dashboard = config.setdefault("dashboard", {})
    selected_targets = list(config.get("selected_targets", []))
    config_version = int(config.get("version", 1) or 1)

    if not media.get("library_dir"):
        media["library_dir"] = str(get_media_library_dir())
    if not media.get("mode"):
        media["mode"] = "auto_downloads"
    if "downloads_dir" in media and not media.get("selected_folder_path"):
        media["selected_folder_path"] = media.get("downloads_dir")
    if media.get("selected_url") and not media.get("selected_sound_path") and media.get("mode") != "url":
        media["mode"] = "url"

    if config.get("version", 1) < 4 and microphone.get("trigger_cooldown_seconds") == 8.0:
        microphone["trigger_cooldown_seconds"] = 2.0

    if not dashboard.get("port"):
        dashboard["port"] = realtime.get("port") or 8766
    if not realtime.get("port"):
        realtime["port"] = dashboard.get("port") or 8766
    if config_version < 9 and realtime.get("port") == 8765:
        realtime["port"] = dashboard.get("port") or 8766
    if config_version < 9 and any(target.get("id") == "welcome_localhost" for target in selected_targets):
        realtime["launch_on_clap"] = True
        config["selected_targets"] = [target for target in selected_targets if target.get("id") != "welcome_localhost"]

    config["version"] = DEFAULT_CONFIG["version"]
    merge_dict(config["realtime"], realtime)
    merge_dict(config["media"], media)
    merge_dict(config["microphone"], microphone)
    merge_dict(config["dashboard"], dashboard)


def prompt_for_media(config: dict[str, Any], language: str) -> None:
    media = config["media"]
    seed_default_media_selection(media)
    existing_sound = media.get("selected_sound_path")
    current_sound = describe_existing_sound(existing_sound)
    default_choice = default_media_choice(media)
    options = {
        "fr": [
            "Un son precis",
            "Un dossier, puis jouer un son au hasard",
            "Un dossier, puis choisir un son",
            "Une URL YouTube/video",
            "Mode auto assets/audio -> mp3 Highway sinon YouTube fallback",
            "Ne rien jouer",
        ],
        "en": [
            "One specific sound",
            "A folder, then play a random sound",
            "A folder, then choose one sound",
            "A YouTube/video URL",
            "Auto assets/audio mode -> Highway mp3, otherwise YouTube fallback",
            "Play nothing",
        ],
    }[language]
    print()
    print(t(language, "media_title"))
    print()
    if current_sound:
        print(t(language, "media_current", value=current_sound))
    if media.get("selected_folder_path"):
        print(t(language, "media_folder_current", value=media["selected_folder_path"]))
    if media.get("selected_url"):
        print(t(language, "media_url_current", value=media["selected_url"]))
    if current_sound or media.get("selected_folder_path") or media.get("selected_url"):
        print()
    for index, label in enumerate(options, start=1):
        print(f"  {index}. {label}")
    print()

    while True:
        raw_input = input(t(language, "media_choice")).strip()
        raw = raw_input or default_choice
        if raw not in {"1", "2", "3", "4", "5", "6"}:
            print(t(language, "media_invalid"))
            continue

        if not raw_input and media_selection_is_ready(media, raw):
            return

        reset_media_selection(media)

        if raw == "1":
            imported = prompt_for_audio_path(language)
            if imported:
                media["mode"] = "single_file"
                media["selected_sound_path"] = str(imported)
                return
            continue

        if raw == "2":
            directory = prompt_for_folder(language)
            if directory:
                media["mode"] = "folder_random"
                media["selected_folder_path"] = str(directory)
                return
            continue

        if raw == "3":
            imported, directory = prompt_for_audio_directory_choice(language)
            if imported:
                media["mode"] = "single_file"
                media["selected_sound_path"] = str(imported)
                media["selected_folder_path"] = str(directory)
                return
            continue

        if raw == "4":
            url = prompt_for_video_url(media.get("selected_url"), language)
            if url:
                media["mode"] = "url"
                media["selected_url"] = url
                return
            continue

        if raw == "5":
            media["mode"] = "auto_downloads"
            media["selected_folder_path"] = str(get_default_assets_audio_dir())
            return

        media["mode"] = "none"
        return


def default_media_choice(media: dict[str, Any]) -> str:
    mode = media.get("mode", "auto_downloads")
    if mode == "folder_random" and media.get("selected_folder_path"):
        return "2"
    if mode == "single_file":
        if media.get("selected_folder_path") and media.get("selected_sound_path"):
            return "3"
        if media.get("selected_sound_path"):
            return "1"
    if mode == "url" and media.get("selected_url"):
        return "4"
    if mode == "none":
        return "6"
    return "5"


def seed_default_media_selection(media: dict[str, Any]) -> None:
    if media.get("selected_sound_path") or media.get("selected_url"):
        return
    if media.get("mode") not in {"auto_downloads", "", None}:
        return

    assets_dir = media.get("selected_folder_path") or get_default_assets_audio_dir()
    media["selected_folder_path"] = str(Path(assets_dir).expanduser())
    detected = find_highway_mp3(assets_dir)
    if detected and detected.exists():
        media["mode"] = "single_file"
        media["selected_sound_path"] = str(detected)
        return

    media["mode"] = "auto_downloads"


def media_selection_is_ready(media: dict[str, Any], choice: str) -> bool:
    if choice == "1":
        return bool(media.get("selected_sound_path"))
    if choice == "2":
        return bool(media.get("selected_folder_path"))
    if choice == "3":
        return bool(media.get("selected_sound_path")) and bool(media.get("selected_folder_path"))
    if choice == "4":
        return bool(media.get("selected_url"))
    if choice in {"5", "6"}:
        return True
    return False

def prompt_for_audio_path(language: str) -> Path | None:
    print()
    raw = input(t(language, "audio_file_prompt")).strip()
    if not raw:
        print(t(language, "audio_none"))
        return None

    if raw.casefold() == "open":
        return prompt_with_file_dialog(language)

    path = normalize_user_path(raw)
    if not path.exists() or not path.is_file():
        print(t(language, "audio_missing", path=path))
        return None

    imported = copy_audio_to_library(path)
    print(t(language, "audio_imported", path=imported))
    return imported


def prompt_for_audio_directory_choice(language: str) -> tuple[Path | None, Path | None]:
    directory = prompt_for_folder(language)
    if directory is None:
        return None, None

    audio_files = list_audio_files(directory)
    if not audio_files:
        print(t(language, "folder_scan_none"))
        return None, directory

    print()
    print(t(language, "folder_scan_found"))
    for index, path in enumerate(audio_files[:50], start=1):
        print(f"  {index}. {path.name}")
    if len(audio_files) > 50:
        print(t(language, "folder_scan_more", count=len(audio_files) - 50))

    while True:
        choice = input(t(language, "folder_scan_choice")).strip()
        try:
            selected = parse_selection(choice, len(audio_files))
        except ValueError as exc:
            print(t(language, "selection_invalid", error=exc))
            continue

        if len(selected) != 1:
            print(t(language, "folder_scan_one"))
            continue

        imported = copy_audio_to_library(audio_files[selected[0] - 1])
        print(t(language, "audio_imported", path=imported))
        return imported, directory


def prompt_for_folder(language: str) -> Path | None:
    print()
    raw = input(t(language, "folder_prompt")).strip()
    if not raw:
        print(t(language, "folder_none"))
        return None

    if raw.casefold() == "open":
        directory = choose_directory_dialog()
        if directory is None:
            print(t(language, "folder_picker_none"))
            return None
    else:
        directory = normalize_user_path(raw)

    if not directory.exists() or not directory.is_dir():
        print(t(language, "folder_missing", path=directory))
        return None
    return directory


def prompt_with_file_dialog(language: str) -> Path | None:
    path = choose_audio_file_dialog()
    if path is None:
        print(t(language, "file_picker_none"))
        return None
    imported = copy_audio_to_library(path)
    print(t(language, "audio_imported", path=imported))
    return imported


def prompt_for_clap_calibration(config: dict[str, Any], language: str) -> None:
    microphone = config["microphone"]
    existing_profile = microphone.get("profile")
    print()
    print(t(language, "calibration_title"))
    print()
    if existing_profile:
        print(t(language, "calibration_existing"))
        default_answer = "n"
    else:
        print(t(language, "calibration_recommend"))
        default_answer = "y"

    while True:
        prompt_key = "calibration_now_yes" if default_answer == "y" else "calibration_now_no"
        answer = input(t(language, prompt_key)).strip().casefold()
        if not answer:
            answer = default_answer
        if answer in {"n", "no", "non"}:
            return
        if answer in {"y", "yes", "oui", "o"}:
            run_clap_calibration(config, language)
            return
        print(t(language, "yes_no_retry"))


def run_clap_calibration(config: dict[str, Any], language: str | None = None) -> None:
    resolved_language = language or config.get("language", DEFAULT_LANGUAGE)
    clap_config = build_clap_config(config["microphone"])
    print()
    print(t(resolved_language, "calibration_prepare"))
    print(t(resolved_language, "calibration_instructions"))
    print()

    def on_progress(current: int, total: int) -> None:
        print(t(resolved_language, "calibration_progress", current=current, total=total))

    profile = calibrate_double_clap_profile(clap_config, on_progress=on_progress)
    config["microphone"]["profile"] = profile_to_dict(profile)
    config["microphone"]["trigger_cooldown_seconds"] = recommended_trigger_cooldown_seconds(
        profile,
        clap_config.double_clap_max_gap_seconds,
        fallback=float(config["microphone"].get("trigger_cooldown_seconds", 2.0)),
    )
    print(t(resolved_language, "calibration_done"))
    print(
        t(
            resolved_language,
            "calibration_profile",
            score=profile.average_score,
            transient=profile.average_transient,
            gap=profile.average_gap,
            tolerance=profile.match_tolerance,
        )
    )


def build_clap_config(microphone: dict[str, Any]) -> ClapConfig:
    profile = profile_from_dict(microphone.get("profile"))
    return ClapConfig(
        sample_rate=int(microphone["sample_rate"]),
        blocksize=int(microphone["blocksize"]),
        absolute_peak_threshold=float(microphone["absolute_peak_threshold"]),
        relative_peak_multiplier=float(microphone["relative_peak_multiplier"]),
        minimum_clap_gap_seconds=float(microphone["minimum_clap_gap_seconds"]),
        double_clap_max_gap_seconds=float(microphone["double_clap_max_gap_seconds"]),
        trigger_cooldown_seconds=recommended_trigger_cooldown_seconds(
            profile,
            float(microphone["double_clap_max_gap_seconds"]),
            fallback=float(microphone.get("trigger_cooldown_seconds", 2.0)),
        ),
        input_device=_configured_input_device_index(microphone),
        input_device_name=microphone.get("input_device_name") or None,
        profile=profile,
    )


def format_detected_target(detected: dict[str, Any] | None) -> str | None:
    if not detected or not detected.get("found"):
        return None
    if detected.get("method") == "app_path":
        return Path(detected["app_path"]).name
    if detected.get("method") == "command":
        return detected.get("command_name") or Path(detected["command"]).name
    return None


def detected_target_path(detected: dict[str, Any] | None) -> str | None:
    if not detected or not detected.get("found"):
        return None
    if detected.get("method") == "app_path":
        return str(detected.get("app_path") or "")
    if detected.get("method") == "command":
        return str(detected.get("command") or "")
    return None


def print_detected_targets_summary(language: str, detected_targets: dict[str, Any]) -> None:
    print(t(language, "targets_scan_title"))
    print(t(language, "targets_scan_intro"))
    print()
    for target in AVAILABLE_TARGETS:
        label = target["label"]
        detected_value = detected_target_path(detected_targets.get(target["id"]))
        if detected_value:
            print(t(language, "targets_scan_found", label=label, value=detected_value))
        else:
            print(t(language, "targets_scan_missing", label=label))


def print_setup_banner() -> None:
    print(SETUP_TITLE_ASCII.rstrip())
    print()
    print(IRON_MAN_ASCII)


def reset_media_selection(media: dict[str, Any]) -> None:
    media["selected_sound_path"] = None
    media["selected_folder_path"] = None
    media["selected_url"] = None


def prompt_for_video_url(existing_url: str | None, language: str) -> str | None:
    default = existing_url or YOUTUBE_FALLBACK_URL
    print()
    raw = input(t(language, "video_prompt", default=default)).strip()
    value = raw or default
    if not value.startswith(("http://", "https://")):
        print(t(language, "video_invalid"))
        return None
    return value


def maybe_prompt_openai_env(config: dict[str, Any], language: str) -> None:
    workspace_dir = Path(config["workspace_dir"] or Path.cwd())
    env_path = workspace_dir / ".env"
    existing = load_env_value(env_path, "OPENAI_API_KEY")
    hint_key = "openai_hint_present" if existing else "openai_hint_missing"
    print()
    raw = input(t(language, "openai_prompt", hint=t(language, hint_key))).strip()
    if raw:
        save_env_value(env_path, "OPENAI_API_KEY", raw)
        config["realtime"]["api_key"] = None
        print(t(language, "openai_saved", path=env_path))


def prompt_for_permissions(config: dict[str, Any], language: str) -> None:
    print()
    print(t(language, "permissions_title"))
    print(t(language, "permissions_intro"))
    print()

    for key in get_required_permission_keys(sys.platform, config.get("selected_targets", [])):
        while True:
            result = probe_permission(key, microphone_config=config.get("microphone", {}))
            if result.granted:
                print(t(language, "permissions_ok", label=result.label))
                print(t(language, "permissions_details", message=result.message))
                print()
                break

            print(t(language, "permissions_blocked", label=result.label))
            print(t(language, "permissions_details", message=result.message))

            if result.can_open_settings:
                if prompt_yes_no(
                    language,
                    t(language, "permissions_open_settings", label=result.label),
                    default=True,
                ):
                    if open_permission_settings(key):
                        print(t(language, "permissions_opened", label=result.label))
                    else:
                        print(t(language, "permissions_unavailable", label=result.label))
            else:
                print(t(language, "permissions_unavailable", label=result.label))

            if prompt_yes_no(
                language,
                t(language, "permissions_retry", label=result.label),
                default=True,
            ):
                print()
                continue

            print(t(language, "permissions_skip"))
            print()
            break

    print(t(language, "permissions_done"))


def prompt_for_realtime(config: dict[str, Any], language: str) -> None:
    realtime = config["realtime"]
    dashboard = config.setdefault("dashboard", {})
    localhost_port = int(dashboard.get("port") or realtime.get("port") or 8766)
    dashboard["port"] = localhost_port
    realtime["port"] = localhost_port
    print()
    print(t(language, "realtime_title"))
    print()
    print(t(language, "realtime_local_url", url=f"http://127.0.0.1:{localhost_port}/welcome/"))
    print()
    assistant_name_default = realtime.get("assistant_name") or "Jarvis"
    assistant_name = input(t(language, "realtime_assistant_name", default=assistant_name_default)).strip()
    realtime["assistant_name"] = assistant_name or assistant_name_default

    name_default = realtime.get("welcome_name") or os.environ.get("USER") or os.environ.get("USERNAME") or ""
    name = input(t(language, "realtime_name", default=name_default)).strip()
    realtime["welcome_name"] = name or name_default

    voice_default = realtime.get("voice", "marin")
    voice = input(t(language, "realtime_voice", default=voice_default)).strip()
    realtime["voice"] = voice or voice_default

    prompt_default = realtime.get(
        "welcome_prompt",
        get_default_welcome_prompt(language),
    )
    welcome_prompt = input(t(language, "realtime_prompt", default=prompt_default)).strip()
    realtime["welcome_prompt"] = welcome_prompt or prompt_default

    launch_default = bool(realtime.get("launch_on_clap", False))
    prompt_key = "realtime_launch_yes" if launch_default else "realtime_launch_no"
    realtime["launch_on_clap"] = prompt_yes_no(language, t(language, prompt_key), default=launch_default)
