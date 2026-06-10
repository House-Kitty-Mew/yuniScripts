"""
server_profiles.py — Server Type Profiles & Environment Setup

Defines the environmental setup for each MC server type/loader:

  VANILLA   - Official Mojang server.jar
  FABRIC    - Fabric Loader + Fabric API via Modrinth cache
  QUILT     - Quilt Loader + QSL via Modrinth cache
  FORGE     - Forge via installer from maven.minecraftforge.net
  NEOFORGE  - NeoForge via installer from maven.neoforged.net
  PAPER     - PaperMC server from api.papermc.io (Bukkit/Spigot plugins)
  PURPUR    - Purpur server from api.purpurmc.org (Bukkit/Spigot plugins)
  SPIGOT    - Spigot via BuildTools or API
  BUKKIT    - CraftBukkit server (legacy)

Each profile defines:
  - server_jar: How to download/install the server JAR
  - mods_dir:  Where mods go (mods/ vs plugins/)
  - config_files: What config files to generate
  - startup_class: Main class (if different from default)
  - min_java_version: Minimum Java version
  - setup_steps: List of setup actions

The download helpers use the VFS + ModCache infrastructure.
"""

import json
import uuid
import logging
import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime

from engine.database import Database
from engine.vfs import VFS
from engine.converter import file_to_db_blob

logger = logging.getLogger('mc-server-runner.server_profiles')


class ServerProfile:
    """Describes how to set up and run a specific server type."""

    def __init__(self, server_type: str, name: str, mc_version: str,
                 loader_version: str = None):
        self.server_type = server_type.lower()
        self.name = name
        self.mc_version = mc_version
        self.loader_version = loader_version or self._default_loader_version()

    def _default_loader_version(self) -> str:
        """Get best loader version for this type + MC version."""
        defaults = {
            'vanilla': 'latest',
            'fabric': '0.19.3',
            'quilt': '0.26.1',
            'forge': 'latest',
            'neoforge': 'latest',
            'paper': 'latest',
            'purpur': 'latest',
            'spigot': 'latest',
            'bukkit': 'latest',
        }
        return defaults.get(self.server_type, 'latest')

    # ── Profile methods ───────────────────────────────────────

    def setup(self, db: Database, vfs: VFS, server_id: int,
              install_dir: str) -> dict:
        """
        Run the full environmental setup for this server type.

        Returns setup log with steps performed.
        """
        setup_log = {
            'server_type': self.server_type,
            'name': self.name,
            'mc_version': self.mc_version,
            'steps': [],
        }

        # Each profile overrides this method
        handler = getattr(self, f'_setup_{self.server_type}', None)
        if handler:
            handler(db, vfs, server_id, install_dir, setup_log)
        else:
            setup_log['steps'].append(f"Unknown server type: {self.server_type}")

        return setup_log

    # ═══════════════════════════════════════════════════════════
    # VANILLA
    # ═══════════════════════════════════════════════════════════

    def _setup_vanilla(self, db, vfs, server_id, install_dir, log):
        """Vanilla: Download from Mojang manifest, place server.jar."""
        log['steps'].append("Fetching Mojang version manifest...")
        manifest = self._mojang_manifest()
        if not manifest:
            log['steps'].append("FAILED: Could not fetch Mojang manifest")
            return

        version_info = self._mojang_version_info(manifest, self.mc_version)
        if not version_info:
            log['steps'].append(f"FAILED: MC version {self.mc_version} not found in manifest")
            return

        server_url = version_info.get('downloads', {}).get('server', {}).get('url')
        if not server_url:
            log['steps'].append(f"FAILED: No server download URL for {self.mc_version}")
            return

        import requests as _r
        try:
            resp = _r.get(server_url, timeout=120)
            resp.raise_for_status()
            jar_data = resp.content
            vfs.write(f"/servers/{self.name}/server.jar", jar_data, atomic=True,
                     content_type='application/java-archive')
            log['steps'].append(f"Downloaded vanilla server.jar ({len(jar_data)} bytes)")
        except Exception as e:
            log['steps'].append(f"FAILED: Download failed: {e}")

        self._write_eula(vfs, self.name)
        log['steps'].append("eula=true written")

    # ═══════════════════════════════════════════════════════════
    # FABRIC
    # ═══════════════════════════════════════════════════════════

    def _setup_fabric(self, db, vfs, server_id, install_dir, log):
        """
        Fabric: Use the Fabric Installer to download and set up the server.

        Uses the Fabric Installer (from maven.fabricmc.net) which:
          1. Resolves the correct Fabric Loader version for the requested MC version
          2. Downloads the vanilla Minecraft server JAR
          3. Downloads all required libraries (mixin, ASM, etc.)
          4. Generates fabric-server-launch.jar (the bootstrap JAR)

        The generated files (server.jar, fabric-server-launch.jar, libraries/)
        are imported into the VFS under /servers/<name>/.

        Main class: net.fabricmc.loader.impl.launch.knot.KnotServer
        """
        log['steps'].append(f"Setting up Fabric server for MC {self.mc_version}...")

        import requests as _r
        import subprocess
        import tempfile
        import os
        import shutil

        try:
            # Step 1: Get the latest Fabric installer version from meta API
            log['steps'].append("Querying Fabric installer versions...")
            meta_resp = _r.get(
                "https://meta.fabricmc.net/v2/versions/installer",
                timeout=15
            )
            meta_resp.raise_for_status()
            installers = meta_resp.json()
            if not installers:
                log['steps'].append("FAILED: No Fabric installer versions found")
                return
            installer_info = installers[0]
            installer_version = installer_info['version']
            installer_url = installer_info['url']
            log['steps'].append(f"Latest Fabric installer: v{installer_version}")

            # Step 2: Download the Fabric installer JAR
            log['steps'].append("Downloading Fabric installer...")
            jar_resp = _r.get(installer_url, timeout=120)
            jar_resp.raise_for_status()
            installer_data = jar_resp.content
            log['steps'].append(f"Downloaded Fabric installer ({len(installer_data)} bytes)")

            # Step 3: Save installer to a temp location and run it
            with tempfile.TemporaryDirectory(prefix='fabric_setup_') as tmpdir:
                installer_jar = os.path.join(tmpdir, 'fabric-installer.jar')
                with open(installer_jar, 'wb') as f:
                    f.write(installer_data)

                # Run the installer
                log['steps'].append("Running Fabric installer (downloading MC server + libraries)...")
                java_bin = shutil.which('java') or os.environ.get('JAVA_HOME', '')
                if java_bin and os.path.isdir(java_bin):
                    java_bin = os.path.join(java_bin, 'bin/java')
                if not java_bin or not os.path.isfile(java_bin):
                    # Try JavaDetector
                    try:
                        from engine.dynamic_deps import JavaDetector
                        found = JavaDetector.find_java()
                        if found:
                            java_bin = found
                    except Exception:
                        pass
                if not java_bin or not os.path.isfile(java_bin):
                    java_bin = 'java'  # Let system resolve it

                result = subprocess.run(
                    [java_bin, '-jar', installer_jar, 'server',
                     '-mcversion', self.mc_version,
                     '-dir', tmpdir,
                     '-downloadMinecraft'],
                    capture_output=True, text=True, timeout=180
                )
                if result.returncode != 0:
                    log['steps'].append(f"FAILED: Installer exit code {result.returncode}")
                    log['steps'].append(f"  stderr: {result.stderr[-300:]}")
                    return
                log['steps'].append("Fabric installer completed successfully")

                # Step 4: Import generated files into VFS
                # The installer creates:
                #   - server.jar (vanilla MC server, ~49MB)
                #   - fabric-server-launch.jar (Fabric bootstrap, small)
                #   - libraries/ (all dependencies)
                #   - versions/ (versioned MC jar)
                server_jar_path = os.path.join(tmpdir, 'server.jar')
                fabric_launch_jar = os.path.join(tmpdir, 'fabric-server-launch.jar')

                # Import fabric-server-launch.jar as itself (runner finds this first)
                if os.path.isfile(fabric_launch_jar):
                    with open(fabric_launch_jar, 'rb') as f:
                        vfs.write(
                            f"/servers/{self.name}/fabric-server-launch.jar",
                            f.read(), atomic=True,
                            content_type='application/java-archive'
                        )
                    log['steps'].append(f"Imported fabric-server-launch.jar")

                # Import server.jar (vanilla MC) as the main server jar
                if os.path.isfile(server_jar_path):
                    with open(server_jar_path, 'rb') as f:
                        data = f.read()
                        vfs.write(
                            f"/servers/{self.name}/server.jar",
                            data, atomic=True,
                            content_type='application/java-archive'
                        )
                    log['steps'].append(f"Imported vanilla server.jar ({len(data)} bytes)")

                # Import libraries/ directory recursively
                libs_dir = os.path.join(tmpdir, 'libraries')
                if os.path.isdir(libs_dir):
                    lib_count = 0
                    for root, dirs, files in os.walk(libs_dir):
                        for filename in files:
                            filepath = os.path.join(root, filename)
                            rel_path = os.path.relpath(filepath, tmpdir)
                            vfs_path = f"/servers/{self.name}/{rel_path}"
                            with open(filepath, 'rb') as f:
                                vfs.write(vfs_path, f.read(), atomic=False)
                            lib_count += 1
                    log['steps'].append(f"Imported {lib_count} library files")

                # Import versions/ directory
                versions_dir = os.path.join(tmpdir, 'versions')
                if os.path.isdir(versions_dir):
                    ver_count = 0
                    for root, dirs, files in os.walk(versions_dir):
                        for filename in files:
                            filepath = os.path.join(root, filename)
                            rel_path = os.path.relpath(filepath, tmpdir)
                            vfs_path = f"/servers/{self.name}/{rel_path}"
                            with open(filepath, 'rb') as f:
                                vfs.write(vfs_path, f.read(), atomic=False)
                            ver_count += 1
                    log['steps'].append(f"Imported {ver_count} version files")

        except _r.exceptions.RequestException as e:
            log['steps'].append(f"FAILED: Network error: {e}")
            return
        except subprocess.TimeoutExpired:
            log['steps'].append("FAILED: Fabric installer timed out (180s)")
            return
        except Exception as e:
            log['steps'].append(f"FAILED: {type(e).__name__}: {e}")
            return

        self._write_eula(vfs, self.name)
        log['steps'].append("eula=true written")

    # ═══════════════════════════════════════════════════════════
    # QUILT
    # ═══════════════════════════════════════════════════════════

    def _setup_quilt(self, db, vfs, server_id, install_dir, log):
        """
        Quilt: Uses quilt-installer API + Quilt Loader.
        
        Quilt is compatible with Fabric mods but uses its own loader.
        Server JAR: quilt-server-launch.jar
        Main class: org.quiltmc.loader.impl.launch.knot.KnotServer
        """
        log['steps'].append(f"Setting up Quilt server (loader v{self.loader_version})...")

        meta_url = f"https://meta.quiltmc.org/v3/versions/loader/{self.mc_version}"
        import requests as _r
        try:
            resp = _r.get(meta_url, timeout=15)
            resp.raise_for_status()
            entries = resp.json()
            if entries:
                entry = entries[0]
                loader_info = entry.get('loader', entry)
                log['steps'].append(f"Quilt loader version: {loader_info.get('version', 'unknown')}")
                log['steps'].append("Download Quilt installer from: https://quiltmc.org/install/")
            else:
                log['steps'].append(f"No Quilt loader found for MC {self.mc_version}")
        except _r.exceptions.RequestException as e:
            log['steps'].append(f"Failed to query Quilt meta: {e}")

        self._write_eula(vfs, self.name)

    # ═══════════════════════════════════════════════════════════
    # FORGE
    # ═══════════════════════════════════════════════════════════

    def _setup_forge(self, db, vfs, server_id, install_dir, log):
        """
        Forge: Uses installer jar from maven.minecraftforge.net.
        
        Forge requires running the installer:
          java -jar forge-{mc}-{forge_version}-installer.jar --installServer
        
        After installation:
          - forge-{mc}-{forge_version}.jar (server jar)
          - libraries/ (dependency cache)
          - run.sh / run.bat (launch scripts)
        """
        log['steps'].append(f"Setting up Forge server for MC {self.mc_version}...")

        # Try to find the installer URL from Forge maven
        forge_maven = "https://maven.minecraftforge.net/net/minecraftforge/forge/"
        # Common pattern: forge/{mc_version}-{forge_version}/forge-{mc_version}-{forge_version}-installer.jar
        log['steps'].append(f"Forge requires installer download from:")
        log['steps'].append(f"  {forge_maven}")
        log['steps'].append(f"  Search for: forge-{self.mc_version}-*.jar")
        log['steps'].append("  Then run: java -jar forge-*-installer.jar --installServer")
        log['steps'].append("  The server.jar will be: forge-{mc}-{ver}.jar")

        # Store setup hint
        hint = {
            'installer_url_base': forge_maven,
            'mc_version': self.mc_version,
            'setup_command': 'java -jar forge-*-installer.jar --installServer',
        }
        vfs.write(f"/servers/{self.name}/.forge-setup.json",
                 json.dumps(hint, indent=2).encode(), atomic=False)
        self._write_eula(vfs, self.name)

    # ═══════════════════════════════════════════════════════════
    # NEOFORGE
    # ═══════════════════════════════════════════════════════════

    def _setup_neoforge(self, db, vfs, server_id, install_dir, log):
        """
        NeoForge: Uses installer from maven.neoforged.net.
        
        Similar to Forge but from NeoForge's own maven.
        Version format for 1.20.x: 20.2.x-beta
        For newer versions: {major}.{minor}.{patch} (no -beta)
        """
        log['steps'].append(f"Setting up NeoForge server for MC {self.mc_version}...")

        neoforge_maven = "https://maven.neoforged.net/releases/net/neoforged/neoforge/"
        log['steps'].append(f"NeoForge versions at: {neoforge_maven}")
        log['steps'].append("  Download neoforge-{version}-installer.jar")
        log['steps'].append("  Run: java -jar neoforge-*-installer.jar --installServer")
        log['steps'].append("  Server jar: neoforge-{version}.jar")

        hint = {
            'installer_url_base': neoforge_maven,
            'mc_version': self.mc_version,
            'setup_command': 'java -jar neoforge-*-installer.jar --installServer',
        }
        vfs.write(f"/servers/{self.name}/.neoforge-setup.json",
                 json.dumps(hint, indent=2).encode(), atomic=False)
        self._write_eula(vfs, self.name)

    # ═══════════════════════════════════════════════════════════
    # PAPER
    # ═══════════════════════════════════════════════════════════

    def _setup_paper(self, db, vfs, server_id, install_dir, log):
        """
        PaperMC: Downloads directly from api.papermc.io.
        
        Paper uses Bukkit/Spigot plugins (not Fabric/Forge mods).
        JAR is self-contained (no installer needed).
        """
        log['steps'].append(f"Setting up Paper server for MC {self.mc_version}...")

        import requests as _r
        api_base = "https://api.papermc.io/v2/projects/paper"

        try:
            # Get version info
            version_resp = _r.get(f"{api_base}/versions/{self.mc_version}", timeout=15)
            version_resp.raise_for_status()
            version_data = version_resp.json()
            builds = version_data.get('builds', [])
            if not builds:
                log['steps'].append(f"No builds found for Paper {self.mc_version}")
                return

            latest_build = builds[-1]
            jar_name = f"paper-{self.mc_version}-{latest_build}.jar"
            dl_url = f"{api_base}/versions/{self.mc_version}/builds/{latest_build}/downloads/{jar_name}"

            jar_resp = _r.get(dl_url, timeout=120)
            jar_resp.raise_for_status()
            vfs.write(f"/servers/{self.name}/server.jar", jar_resp.content, atomic=True,
                     content_type='application/java-archive')
            log['steps'].append(f"Downloaded Paper server build #{latest_build} ({len(jar_resp.content)} bytes)")

            # Store build info
            vfs.write(f"/servers/{self.name}/.paper-info.json",
                     json.dumps({'build': latest_build, 'version': self.mc_version}).encode(),
                     atomic=False)

        except _r.exceptions.RequestException as e:
            log['steps'].append(f"FAILED: Paper download failed: {e}")

        self._write_eula(vfs, self.name)

    # ═══════════════════════════════════════════════════════════
    # PURPUR
    # ═══════════════════════════════════════════════════════════

    def _setup_purpur(self, db, vfs, server_id, install_dir, log):
        """Purpur: Downloads from api.purpurmc.org."""
        log['steps'].append(f"Setting up Purpur server for MC {self.mc_version}...")

        import requests as _r
        dl_url = f"https://api.purpurmc.org/v2/purpur/{self.mc_version}/latest/download"
        try:
            resp = _r.get(dl_url, timeout=120, allow_redirects=True)
            resp.raise_for_status()
            vfs.write(f"/servers/{self.name}/server.jar", resp.content, atomic=True,
                     content_type='application/java-archive')
            log['steps'].append(f"Downloaded Purpur server ({len(resp.content)} bytes)")
        except _r.exceptions.RequestException as e:
            log['steps'].append(f"FAILED: Purpur download failed: {e}")

        self._write_eula(vfs, self.name)

    # ═══════════════════════════════════════════════════════════
    # SPIGOT
    # ═══════════════════════════════════════════════════════════

    def _setup_spigot(self, db, vfs, server_id, install_dir, log):
        """
        Spigot: Requires BuildTools.jar compilation.
        
        Spigot is not distributed as a pre-built JAR due to license.
        Must use BuildTools:
          git config --global --unset core.autocrlf
          java -jar BuildTools.jar --rev {mc_version}
        """
        log['steps'].append("Spigot setup requires BuildTools:")
        log['steps'].append(f"  1. Download BuildTools.jar from https://hub.spigotmc.org/jenkins/job/BuildTools/")
        log['steps'].append(f"  2. Run: java -jar BuildTools.jar --rev {self.mc_version}")
        log['steps'].append(f"  3. Copy spigot-{self.mc_version}.jar to server directory")
        log['steps'].append(f"  NOTE: Spigot is NOT pre-compiled - manual step required")
        self._write_eula(vfs, self.name)

    # ═══════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════

    def _write_eula(self, vfs, server_name):
        """Write eula=true for the server."""
        vfs.write(f"/servers/{server_name}/eula.txt", b"eula=true\n", atomic=False)

    def _mojang_manifest(self) -> Optional[dict]:
        """Fetch Mojang version manifest JSON."""
        import requests as _r
        try:
            resp = _r.get("https://piston-meta.mojang.com/mc/game/version_manifest.json",
                         timeout=15)
            resp.raise_for_status()
            return resp.json()
        except _r.exceptions.RequestException as e:
            logger.error(f"Failed to fetch Mojang manifest: {e}")
            return None

    def _mojang_version_info(self, manifest: dict, mc_version: str) -> Optional[dict]:
        """Get version info for a specific MC version from the manifest."""
        for v in manifest.get('versions', []):
            if v['id'] == mc_version:
                import requests as _r
                try:
                    resp = _r.get(v['url'], timeout=15)
                    resp.raise_for_status()
                    return resp.json()
                except _r.exceptions.RequestException as e:
                    logger.error(f"Failed to fetch version info for {mc_version}: {e}")
                    return None
        return None

    @staticmethod
    def get_default_min_java(server_type: str) -> int:
        """Get minimum Java version for a server type."""
        java_vers = {
            'vanilla': 17,   # 1.18+ requires Java 17
            'fabric': 17,
            'quilt': 17,
            'forge': 17,
            'neoforge': 17,
            'paper': 17,
            'purpur': 17,
            'spigot': 17,
            'bukkit': 8,     # Legacy
        }
        return java_vers.get(server_type, 17)

    def to_dict(self) -> dict:
        """Return profile as serializable dict."""
        return {
            'server_type': self.server_type,
            'name': self.name,
            'mc_version': self.mc_version,
            'loader_version': self.loader_version,
            'min_java_version': self.get_default_min_java(self.server_type),
            'mods_dir': 'mods/' if self.server_type in ('fabric', 'quilt', 'forge', 'neoforge', 'vanilla') else 'plugins/',
            'supports_mods': self.server_type in ('fabric', 'quilt', 'forge', 'neoforge'),
            'supports_plugins': self.server_type in ('paper', 'purpur', 'spigot', 'bukkit'),
        }


# ═══════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════

SERVER_TYPE_LOADER_MAP = {
    'fabric': 1,
    'forge': 2,
    'cauldron': 3,
    'liteloader': 4,
    'fabric': 5,  # Actually 4 in CF system
    'quilt': 6,
    'neoforge': 7,
}

# Download URLs for quick reference
SERVER_DOWNLOADS = {
    'vanilla':  'https://piston-meta.mojang.com/mc/game/version_manifest.json',
    'fabric':   'https://meta.fabricmc.net/v2/versions/loader/{mc}',
    'quilt':    'https://meta.quiltmc.org/v3/versions/loader/{mc}',
    'paper':    'https://api.papermc.io/v2/projects/paper/versions/{mc}',
    'purpur':   'https://api.purpurmc.org/v2/purpur/{mc}/latest/download',
    'neoforge': 'https://maven.neoforged.net/releases/net/neoforged/neoforge/',
    'forge':    'https://maven.minecraftforge.net/net/minecraftforge/forge/',
}
