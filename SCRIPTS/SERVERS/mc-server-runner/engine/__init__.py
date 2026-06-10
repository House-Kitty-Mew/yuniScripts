"""
MC Server Runner — Core Engine
Sandboxed Minecraft server management with VFS, virtual networking, and mod system.

Modules:
    database.py       — SQLite schema + CRUD for server_data.db
    converter.py      — File-DB conversion with SHA-256 integrity validation
    atomic.py         — Journal-based atomic operation manager
    vfs.py            — Virtual File System with simulated root /
    mod_manager.py    — Mod versioning, dependency checking, backups, rollbacks
    networking.py     — Virtual network adapter sandbox
    runner.py         — MC server process lifecycle management
    server_profiles.py— Server type profiles + auto-download setup
    dynamic_deps.py   — Dynamic dependency detection, env management, hot-reload
    downloader.py     — Minecraft server file acquisition (Mojang, Fabric, Forge)
"""

from engine.database import get_db, Database
from engine.converter import file_to_db_blob, db_blob_to_file, bytes_to_db_blob, validate_file_integrity
from engine.atomic import get_journal, atomic_write
from engine.vfs import VFS
from engine.mod_manager import ModManager
from engine.networking import NetworkManager, PortManager
from engine.runner import ServerRunner

# Dynamic dependency system
from engine.dynamic_deps import (
    DynamicDeps,
    JavaDetector,
    EnvironmentManager,
    DependencyInstaller,
    HotReloadManager,
    CheckResult,
    DependencyResult,
    DepType,
    DepStatus,
)

# Minecraft server file acquisition
from engine.downloader import (
    DownloadResult,
    DownloadCache,
    MojangDownloader,
    FabricDownloader,
    ForgeDownloader,
    DownloadError,
    IntegrityCheckError,
    IntegrityChecker,
)
