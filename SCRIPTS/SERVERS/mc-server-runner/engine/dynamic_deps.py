"""
dynamic_deps.py — Dynamic Dependency Detection & Runtime Environment Manager

Detects, validates, and manages runtime dependencies for Minecraft servers:

  - Java Detection: Locate Java runtime, parse version, check minimum requirements
  - Simulated Auto-Installation: Generates install instructions + state simulation
  - Hot-Reload After Install: Refreshes cached state without restarting the application
  - Environment Variable Updates: Manages JAVA_HOME, PATH, LD_LIBRARY_PATH
  - PATH Refresh: Updates in-memory PATH when new tools become available
  - Fallback on Failure: Graceful degradation with helpful error messages

This module is designed to be called BEFORE launching a server process,
giving proactive feedback about missing dependencies rather than
crashing at Java launch time.

Usage:
    from engine.dynamic_deps import DynamicDeps

    deps = DynamicDeps()
    result = deps.check_all()
    if not result.ready:
        print(result.report())
        deps.simulate_install(result.missing)
        result = deps.check_all()  # After simulated install
"""

import os
import re
import sys
import json
import logging
import subprocess
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger('mc-server-runner.dynamic_deps')


# ═══════════════════════════════════════════════════════════════════
# Enums & Data Classes
# ═══════════════════════════════════════════════════════════════════


class DepType(Enum):
    """Types of runtime dependencies the system can detect and manage."""

    JAVA_RUNTIME = "java_runtime"
    """Java Runtime Environment (JRE/JDK) needed to run Minecraft servers."""

    SYSTEM_LIBRARY = "system_library"
    """OS-level shared library (e.g., libstdc++, OpenAL)."""

    PATH_TOOL = "path_tool"
    """An executable that must be discoverable via PATH."""

    ENVIRONMENT_VAR = "environment_var"
    """An environment variable that must be set."""


class DepStatus(Enum):
    """Status of a dependency check."""

    OK = "ok"
    """Dependency is installed and meets requirements."""

    MISSING = "missing"
    """Dependency is not installed at all."""

    VERSION_MISMATCH = "version_mismatch"
    """Dependency is installed but version is insufficient."""

    NOT_IN_PATH = "not_in_path"
    """Dependency exists but is not in PATH."""

    SIMULATED = "simulated"
    """Dependency was auto-installed in simulation mode."""


@dataclass
class DependencyResult:
    """Result of checking a single dependency."""

    dep_type: DepType
    name: str
    status: DepStatus
    found_path: Optional[str] = None
    current_version: Optional[str] = None
    required_version: Optional[str] = None
    message: str = ""
    suggestion: str = ""

    def to_dict(self) -> Dict:
        """Serialize to dictionary for logging or reporting."""
        return {
            "dep_type": self.dep_type.value,
            "name": self.name,
            "status": self.status.value,
            "found_path": self.found_path,
            "current_version": self.current_version,
            "required_version": self.required_version,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class CheckResult:
    """Aggregated result of a full dependency check."""

    checks: List[DependencyResult] = field(default_factory=list)
    java_home: Optional[str] = None
    path_dirs: List[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """True if all checks passed (no missing or mismatched deps)."""
        return all(
            r.status in (DepStatus.OK, DepStatus.SIMULATED)
            for r in self.checks
        )

    @property
    def missing(self) -> List[DependencyResult]:
        """List of dependencies that need attention."""
        return [r for r in self.checks if r.status != DepStatus.OK]

    @property
    def critical(self) -> List[DependencyResult]:
        """Dependencies that MUST be resolved before the server can start."""
        return [
            r for r in self.checks
            if r.status in (DepStatus.MISSING, DepStatus.VERSION_MISMATCH, DepStatus.NOT_IN_PATH)
        ]

    def report(self) -> str:
        """Generate a human-readable report of the check results."""
        lines = ["=== Dynamic Dependency Check Report ===", ""]

        if self.ready:
            lines.append("  All dependencies satisfied.")
            lines.append("")
            return "\n".join(lines)

        for dep in self.checks:
            status_icon = {
                DepStatus.OK: "  [OK]",
                DepStatus.MISSING: "  [MISS]",
                DepStatus.VERSION_MISMATCH: "  [WARN]",
                DepStatus.NOT_IN_PATH: "  [WARN]",
                DepStatus.SIMULATED: "  [SIM]",
            }.get(dep.status, "  [??]")

            lines.append(f"{status_icon} {dep.name}: {dep.message}")

            if dep.suggestion:
                lines.append(f"     Suggestion: {dep.suggestion}")

            if dep.found_path:
                lines.append(f"     Path: {dep.found_path}")

            if dep.current_version and dep.required_version:
                lines.append(
                    f"     Version: found={dep.current_version}, "
                    f"required>={dep.required_version}"
                )

        lines.append("")
        lines.append(f"  {len(self.critical)} critical issue(s) need resolution.")
        lines.append(f"  {len(self.missing)} total issue(s) found.")
        lines.append("")
        return "\n".join(lines)

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps({
            "ready": self.ready,
            "java_home": self.java_home,
            "checks": [c.to_dict() for c in self.checks],
        }, indent=2)


# ═══════════════════════════════════════════════════════════════════
# Java Detection
# ═══════════════════════════════════════════════════════════════════


class JavaDetector:
    """
    Locate and verify Java Runtime Environment installations.

    Searches in order:
      1. JAVA_HOME environment variable
      2. 'java' on PATH (via shutil.which)
      3. Common installation directories (/usr/lib/jvm, /usr/local, etc.)
      4. Manual candidate search for 'java' binary

    Once found, runs 'java -version' to parse the version string and
    determine major version, vendor, and architecture.
    """

    # Common Java installation prefixes on Linux
    _CANDIDATE_ROOTS = [
        "/usr/lib/jvm",
        "/usr/local",
        "/usr/lib/jvm/java",
        "/opt/java",
        "/opt/jdk",
        "/Library/Java/JavaVirtualMachines",  # macOS
    ]

    # Minimum Java versions required per Minecraft server type
    _MIN_JAVA_VERSIONS = {
        "vanilla": 17,
        "fabric": 17,
        "quilt": 17,
        "forge": 17,
        "neoforge": 17,
        "paper": 17,
        "purpur": 17,
        "spigot": 17,
        "bukkit": 8,
    }

    # Java binary names to search for
    _JAVA_BINARIES = ["java", "java17", "java21", "java11", "java8", "jre"]

    @classmethod
    def get_min_java_version(cls, server_type: str = "vanilla") -> int:
        """
        Get the minimum Java major version for a server type.

        Args:
            server_type: Minecraft server type (vanilla, paper, fabric, etc.)

        Returns:
            Minimum required Java major version (e.g., 17).
        """
        return cls._MIN_JAVA_VERSIONS.get(server_type.lower(), 17)

    @classmethod
    def find_java(cls) -> Optional[str]:
        """
        Locate the Java executable on the system.

        Search order:
          1. JAVA_HOME/java
          2. shutil.which('java')
          3. Scan common JVM directories
          4. Deep scan for java binaries

        Returns:
            Absolute path to 'java' executable, or None if not found.
        """
        # 1. Check JAVA_HOME
        java_home = os.environ.get("JAVA_HOME", "")
        if java_home:
            candidate = os.path.join(java_home, "bin", "java")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                logger.debug(f"Found Java via JAVA_HOME: {candidate}")
                return os.path.abspath(candidate)

        # 2. Check PATH
        path_java = shutil.which("java")
        if path_java:
            logger.debug(f"Found Java via PATH: {path_java}")
            return os.path.abspath(path_java)

        # 3. Scan common JVM directories
        for root in cls._CANDIDATE_ROOTS:
            if os.path.isdir(root):
                found = cls._scan_java_in_directory(root)
                if found:
                    return found

        # 4. Try alternative binary names via shutil
        for name in cls._JAVA_BINARIES:
            found = shutil.which(name)
            if found:
                logger.debug(f"Found Java via alternate name '{name}': {found}")
                return os.path.abspath(found)

        return None

    @classmethod
    def _scan_java_in_directory(cls, root: str) -> Optional[str]:
        """
        Recursively scan a directory for 'java' executable.

        Args:
            root: Directory to scan (e.g., /usr/lib/jvm)

        Returns:
            Path to java binary, or None.
        """
        try:
            for entry in os.listdir(root):
                entry_path = os.path.join(root, entry)
                if os.path.isdir(entry_path):
                    bin_java = os.path.join(entry_path, "bin", "java")
                    if os.path.isfile(bin_java) and os.access(bin_java, os.X_OK):
                        return os.path.abspath(bin_java)
        except PermissionError:
            logger.debug(f"Permission denied scanning: {root}")
        except OSError as e:
            logger.debug(f"Error scanning {root}: {e}")

        return None

    @classmethod
    def get_version(cls, java_path: str) -> Optional[Dict[str, object]]:
        """
        Run 'java -version' and parse the version string.

        Args:
            java_path: Absolute path to the java binary.

        Returns:
            Dict with keys: 'full_version', 'major_version', 'vendor', 'arch'
            or None if java cannot be executed.
        """
        try:
            result = subprocess.run(
                [java_path, "-version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            version_output = result.stderr or result.stdout

            if not version_output:
                return None

            return cls._parse_version_output(version_output)

        except FileNotFoundError:
            logger.warning(f"Java binary not found at: {java_path}")
            return None
        except subprocess.TimeoutExpired:
            logger.warning(f"Java version check timed out for: {java_path}")
            return None
        except PermissionError:
            logger.warning(f"Permission denied executing: {java_path}")
            return None
        except OSError as e:
            logger.warning(f"OS error checking Java version: {e}")
            return None

    @classmethod
    def _parse_version_output(cls, output: str) -> Dict[str, object]:
        """
        Parse 'java -version' stderr output.

        Handles formats:
          - openjdk version "17.0.9" 2023-10-17 LTS
          - java version "1.8.0_392"
          - OpenJDK 64-Bit Server VM (build 17.0.9+8, mixed mode)

        Args:
            output: stderr from 'java -version'.

        Returns:
            Parsed version dict.
        """
        info: Dict[str, object] = {
            "full_version": "unknown",
            "major_version": 0,
            "vendor": "unknown",
            "arch": "unknown",
        }

        # Extract vendor (first line, before "version" or "VM")
        vendor_match = re.match(
            r'^(openjdk|java|openjdk\s+\w+|java\s+\w+)', output, re.IGNORECASE
        )
        if vendor_match:
            vendor_raw = vendor_match.group(1).strip()
            if "openjdk" in vendor_raw.lower():
                info["vendor"] = "OpenJDK"
            elif "java" in vendor_raw.lower():
                info["vendor"] = "Oracle JDK"
            else:
                info["vendor"] = vendor_raw

        # Extract version string: "1.8.0_392" or "17.0.9"
        version_match = re.search(r'"([^"]+)"', output)
        if version_match:
            raw_version = version_match.group(1)
            info["full_version"] = raw_version

            # Parse major version
            if raw_version.startswith("1."):
                parts = raw_version.split(".")
                if len(parts) >= 2:
                    try:
                        info["major_version"] = int(parts[1])
                    except ValueError:
                        info["major_version"] = 0
            else:
                major_match = re.match(r'^(\d+)', raw_version)
                if major_match:
                    info["major_version"] = int(major_match.group(1))

        # Extract architecture (64-Bit / 32-Bit / ARM)
        # Check for aarch64/ARM first (more specific), then fall back to 64-Bit/32-Bit
        arch_match = re.search(r'aarch64|ARM\s+\w+', output, re.IGNORECASE)
        if not arch_match:
            arch_match = re.search(r'64-Bit|32-Bit', output)
        if arch_match:
            info["arch"] = arch_match.group(0)

        return info

    @classmethod
    def check_version_requirement(
        cls, java_path: str, min_major: int = 17
    ) -> Tuple[bool, Optional[Dict[str, object]], str]:
        """
        Check if a Java installation meets the minimum version requirement.

        Args:
            java_path: Path to java binary.
            min_major: Minimum required major version (default 17).

        Returns:
            Tuple of (is_sufficient, version_info, message).
        """
        version_info = cls.get_version(java_path)
        if version_info is None:
            return (
                False,
                None,
                f"Could not determine Java version for: {java_path}",
            )

        major = version_info.get("major_version", 0)
        full = version_info.get("full_version", "unknown")

        if not isinstance(major, int) or major < 1:
            return (
                False,
                version_info,
                f"Could not parse Java version from: {full}",
            )

        if major >= min_major:
            return (
                True,
                version_info,
                f"Java {full} (major {major}) meets minimum requirement of {min_major}.",
            )
        else:
            return (
                False,
                version_info,
                f"Java {full} (major {major}) is too old. "
                f"Minimum required is {min_major}.",
            )


# ═══════════════════════════════════════════════════════════════════
# Environment Manager
# ═══════════════════════════════════════════════════════════════════


class EnvironmentManager:
    """
    Manages environment variables for the Minecraft server runtime.

    Responsibilities:
      - Discover JAVA_HOME from a java path
      - Update PATH to include Java directories
      - Track environment modifications for hot-reload
      - Restore original environment on rollback
    """

    def __init__(self):
        self._original_env: Dict[str, str] = {}
        self._modified_keys: Set[str] = set()
        self._snapshot()

    def _snapshot(self):
        """Take a snapshot of current environment."""
        self._original_env = dict(os.environ)
        self._modified_keys.clear()

    def restore(self):
        """Restore the environment to its original state."""
        for key in self._modified_keys:
            if key in self._original_env:
                os.environ[key] = self._original_env[key]
            elif key in os.environ:
                del os.environ[key]
        self._modified_keys.clear()
        logger.info("Environment restored to original state")

    def set_java_home(self, java_path: str) -> str:
        """
        Set JAVA_HOME based on the location of the java binary.

        Given '/usr/lib/jvm/java-17-openjdk/bin/java',
        JAVA_HOME becomes '/usr/lib/jvm/java-17-openjdk'.

        Args:
            java_path: Absolute path to java binary.

        Returns:
            The computed JAVA_HOME path.
        """
        real_path = os.path.realpath(java_path)
        bin_dir = os.path.dirname(real_path)
        java_home = os.path.dirname(bin_dir)

        os.environ["JAVA_HOME"] = java_home
        self._modified_keys.add("JAVA_HOME")
        logger.info(f"JAVA_HOME set to: {java_home}")
        return java_home

    def update_path(self, java_bin_dir: str) -> bool:
        """
        Prepend a directory to PATH if it's not already there.

        Args:
            java_bin_dir: Directory to add to PATH.

        Returns:
            True if PATH was modified, False if dir was already in PATH.
        """
        current_path = os.environ.get("PATH", "")
        path_dirs = current_path.split(os.pathsep) if current_path else []

        normalized_dir = os.path.normpath(java_bin_dir)
        normalized_paths = [os.path.normpath(p) for p in path_dirs]

        if normalized_dir in normalized_paths:
            return False

        new_path = f"{java_bin_dir}{os.pathsep}{current_path}" if current_path else java_bin_dir
        os.environ["PATH"] = new_path
        self._modified_keys.add("PATH")
        logger.info(f"Added {java_bin_dir} to PATH")
        return True

    def set_env(self, key: str, value: str) -> None:
        """
        Set an arbitrary environment variable.

        Args:
            key: Environment variable name.
            value: Value to set.
        """
        os.environ[key] = value
        self._modified_keys.add(key)
        logger.debug(f"Set {key}={value}")

    def get_java_home_from_path(self, java_path: str) -> Optional[str]:
        """
        Determine JAVA_HOME from a java binary path.

        Traverses parent directories looking for a 'lib' directory
        (characteristic of a JDK/JRE installation).

        Args:
            java_path: Absolute path to java binary.

        Returns:
            JAVA_HOME path or None.
        """
        real_path = os.path.realpath(java_path)
        parent = os.path.dirname(os.path.dirname(real_path))

        indicators = ["lib", "include", "jre", "conf"]
        for indicator in indicators:
            candidate = os.path.join(parent, indicator)
            if os.path.isdir(candidate):
                logger.debug(f"Determined JAVA_HOME={parent} from java path")
                return parent

        bin_parent = os.path.dirname(os.path.dirname(real_path))
        logger.debug(f"Using fallback JAVA_HOME={bin_parent} from java path")
        return bin_parent

    @property
    def java_home(self) -> Optional[str]:
        """Current JAVA_HOME from environment."""
        return os.environ.get("JAVA_HOME")

    @property
    def modified_vars(self) -> Dict[str, str]:
        """Return all modified environment variables."""
        return {k: os.environ.get(k, "") for k in self._modified_keys}


# ═══════════════════════════════════════════════════════════════════
# Dependency Installer (Simulated)
# ═══════════════════════════════════════════════════════════════════


class DependencyInstaller:
    """
    Simulates or guides installation of missing dependencies.

    This class does NOT actually install packages on the host system.
    Instead, it:
      - Generates installation instructions
      - Simulates the install state for testing
      - Provides platform-specific guidance

    The simulation is important because:
      1. Installing Java without sudo is typically not possible
      2. The MC Server Runner runs in a sandboxed environment
      3. Users should be guided, not automated, for system-level changes
    """

    _INSTALL_COMMANDS: Dict[str, str] = {
        "arch": "sudo pacman -S jdk17-openjdk",
        "debian": "sudo apt install openjdk-17-jdk-headless",
        "ubuntu": "sudo apt install openjdk-17-jdk-headless",
        "fedora": "sudo dnf install java-17-openjdk-headless",
        "rhel": "sudo yum install java-17-openjdk-headless",
        "centos": "sudo yum install java-17-openjdk-headless",
        "suse": "sudo zypper install java-17-openjdk",
        "alpine": "sudo apk add openjdk17",
        "macos": "brew install openjdk@17",
        "freebsd": "sudo pkg install openjdk17",
        "windows": "winget install EclipseAdoptium.Temurin.17.JDK",
    }

    _SIMULATED_PATHS: Dict[str, str] = {}

    @classmethod
    def detect_distro(cls) -> str:
        """
        Detect the current OS/distribution.

        Uses /etc/os-release if available, falls back to platform detection.

        Returns:
            Distro identifier string (e.g., 'ubuntu', 'arch', 'macos').
        """
        os_release_paths = ["/etc/os-release", "/usr/lib/os-release"]
        for path in os_release_paths:
            if os.path.isfile(path):
                try:
                    with open(path) as f:
                        content = f.read()
                    id_match = re.search(r'^ID="?(\w+)"?', content, re.MULTILINE)
                    if id_match:
                        distro_id = id_match.group(1).lower()
                        distro_map = {
                            "debian": "debian",
                            "ubuntu": "ubuntu",
                            "linuxmint": "ubuntu",
                            "pop": "ubuntu",
                            "arch": "arch",
                            "manjaro": "arch",
                            "endeavouros": "arch",
                            "fedora": "fedora",
                            "rhel": "rhel",
                            "centos": "centos",
                            "opensuse": "suse",
                            "suse": "suse",
                            "alpine": "alpine",
                            "nixos": "nixos",
                        }
                        return distro_map.get(distro_id, distro_id)
                except (OSError, PermissionError):
                    pass

        if sys.platform == "darwin":
            return "macos"
        elif sys.platform == "win32":
            return "windows"
        elif sys.platform.startswith("linux"):
            return "linux"

        return sys.platform

    @classmethod
    def get_install_instructions(cls, dep_name: str) -> str:
        """
        Get installation instructions for a dependency.

        Args:
            dep_name: Name of the dependency (e.g., 'java_runtime').

        Returns:
            Human-readable installation instructions.
        """
        distro = cls.detect_distro()

        if dep_name == DepType.JAVA_RUNTIME.value:
            cmd = cls._INSTALL_COMMANDS.get(distro)
            if cmd:
                return (
                    f"Install OpenJDK 17 for your distribution:\n"
                    f"  {cmd}\n\n"
                    f"After installation, run:\n"
                    f"  export JAVA_HOME=/usr/lib/jvm/java-17-openjdk\n"
                    f"  export PATH=$JAVA_HOME/bin:$PATH"
                )
            return (
                "Install Java 17 or later from:\n"
                "  https://adoptium.net/\n"
                "  https://www.oracle.com/java/technologies/downloads/"
            )

        return f"No specific install instructions for '{dep_name}'."

    @classmethod
    def simulate_install(cls, dep_name: str, version: str = "17.0.9") -> Dict[str, str]:
        """
        Simulate installation of a dependency for testing purposes.

        Registers a simulated path and version that will be picked up
        by subsequent checks.

        Args:
            dep_name: Dependency type name to simulate (e.g., 'java_runtime').
            version: Version string to simulate (default '17.0.9').

        Returns:
            Dict with 'path' and 'version' of the simulated install.
        """
        sim_path = f"/simulated/{dep_name}/bin/java"
        cls._SIMULATED_PATHS[dep_name] = sim_path

        logger.info(
            f"Simulated install of {dep_name}: "
            f"path={sim_path}, version={version}"
        )

        return {"path": sim_path, "version": version}

    @classmethod
    def clear_simulated(cls) -> None:
        """Clear all simulated installations (for test cleanup)."""
        cls._SIMULATED_PATHS.clear()

    @classmethod
    def get_simulated_path(cls, dep_name: str) -> Optional[str]:
        """Get simulated path for a dependency, if any."""
        return cls._SIMULATED_PATHS.get(dep_name)


# ═══════════════════════════════════════════════════════════════════
# Hot-Reload Manager
# ═══════════════════════════════════════════════════════════════════


class HotReloadManager:
    """
    Manages hot-reload of runtime state after dependency changes.

    After a dependency is installed (or simulated), the HotReloadManager
    refreshes all cached state so the application can use the new
    dependency without restarting.

    Capabilities:
      - Clear cached paths so find operations re-scan
      - Refresh environment variables
      - Rebuild PATH
      - Track what was reloaded for audit
    """

    def __init__(self):
        self._reload_log: List[Dict[str, object]] = []
        self._java_detector_cache: Optional[Dict] = None

    def clear_caches(self) -> List[str]:
        """
        Clear all cached dependency information.

        This forces subsequent checks to re-detect dependencies
        rather than using cached results.

        Returns:
            List of cache keys that were cleared.
        """
        cleared = []
        self._java_detector_cache = None
        cleared.append("java_detector_cache")
        logger.debug("Cleared JavaDetector cache")
        return cleared

    def refresh_environment(
        self, env_manager: EnvironmentManager, java_path: str
    ) -> Dict[str, str]:
        """
        Refresh environment variables after a dependency change.

        Steps:
          1. Compute JAVA_HOME from java path
          2. Update PATH with Java bin directory
          3. Set any additional required vars

        Args:
            env_manager: EnvironmentManager instance.
            java_path: Path to newly installed java binary.

        Returns:
            Dict of environment changes made.
        """
        java_home = env_manager.set_java_home(java_path)

        java_bin_dir = os.path.dirname(os.path.realpath(java_path))
        env_manager.update_path(java_bin_dir)

        env_manager.set_env("JDK_HOME", java_home)
        env_manager.set_env("JRE_HOME", java_home)

        changes = {
            "JAVA_HOME": java_home,
            "JDK_HOME": java_home,
            "JRE_HOME": java_home,
            "PATH": os.environ.get("PATH", ""),
        }

        self._log_reload("environment", changes)
        return changes

    def _log_reload(self, component: str, details: object) -> None:
        """Log a reload action for audit."""
        entry = {
            "component": component,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "details": details,
        }
        self._reload_log.append(entry)
        logger.info(f"Hot-reload applied: {component}")

    @property
    def reload_history(self) -> List[Dict[str, object]]:
        """Return the history of reload actions."""
        return list(self._reload_log)


# ═══════════════════════════════════════════════════════════════════
# Main Orchestrator
# ═══════════════════════════════════════════════════════════════════


class DynamicDeps:
    """
    Top-level orchestrator for dynamic dependency management.

    Coordinates Java detection, environment management, simulated
    installation, and hot-reload into a single workflow.

    Usage:
        deps = DynamicDeps()

        # Full check
        result = deps.check_all(server_type="paper")

        # Report issues
        if not result.ready:
            print(result.report())

        # Simulate install + hot-reload
        deps.resolve_missing(result)

        # Verify
        result2 = deps.check_all(server_type="paper")
        assert result2.ready
    """

    def __init__(self):
        self.java_detector = JavaDetector()
        self.env_manager = EnvironmentManager()
        self.installer = DependencyInstaller()
        self.hot_reload = HotReloadManager()

    def check_all(
        self,
        server_type: str = "vanilla",
        min_java: Optional[int] = None,
    ) -> CheckResult:
        """
        Perform a comprehensive dependency check.

        Args:
            server_type: Minecraft server type for version requirements.
            min_java: Override minimum Java version.

        Returns:
            CheckResult with status for all dependencies.
        """
        result = CheckResult()
        result.path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        result.java_home = os.environ.get("JAVA_HOME")

        if min_java is None:
            min_java = JavaDetector.get_min_java_version(server_type)

        # Java Runtime Check
        java_result = self._check_java(min_java)
        result.checks.append(java_result)

        # JAVA_HOME Environment Variable
        env_result = self._check_java_home(java_result)
        result.checks.append(env_result)

        # Additional System Checks
        for tool_name in ["tar", "unzip", "curl"]:
            tool_result = self._check_tool_on_path(tool_name)
            result.checks.append(tool_result)

        return result

    def _check_java(self, min_java: int) -> DependencyResult:
        """
        Check Java availability and version.

        Args:
            min_java: Minimum required major version.

        Returns:
            DependencyResult for Java.
        """
        sim_path = DependencyInstaller.get_simulated_path(
            DepType.JAVA_RUNTIME.value
        )
        if sim_path:
            return DependencyResult(
                dep_type=DepType.JAVA_RUNTIME,
                name="Java Runtime",
                status=DepStatus.SIMULATED,
                found_path=sim_path,
                current_version="17.0.9",
                required_version=str(min_java),
                message="Java runtime is available (simulated install).",
                suggestion="",
            )

        java_path = self.java_detector.find_java()
        if not java_path:
            return DependencyResult(
                dep_type=DepType.JAVA_RUNTIME,
                name="Java Runtime",
                status=DepStatus.MISSING,
                found_path=None,
                current_version=None,
                required_version=str(min_java),
                message=(
                    "Java runtime not found. A Java Runtime Environment "
                    "(JRE) or Java Development Kit (JDK) version 17 or "
                    "later is required to run Minecraft servers."
                ),
                suggestion=self.installer.get_install_instructions(
                    DepType.JAVA_RUNTIME.value
                ),
            )

        sufficient, version_info, msg = (
            self.java_detector.check_version_requirement(java_path, min_java)
        )

        if sufficient:
            return DependencyResult(
                dep_type=DepType.JAVA_RUNTIME,
                name="Java Runtime",
                status=DepStatus.OK,
                found_path=java_path,
                current_version=str(version_info.get("full_version", "unknown"))
                if version_info else "unknown",
                required_version=str(min_java),
                message=msg,
                suggestion="",
            )
        else:
            return DependencyResult(
                dep_type=DepType.JAVA_RUNTIME,
                name="Java Runtime",
                status=DepStatus.VERSION_MISMATCH,
                found_path=java_path,
                current_version=str(version_info.get("full_version", "unknown"))
                if version_info else "unknown",
                required_version=str(min_java),
                message=msg,
                suggestion=self.installer.get_install_instructions(
                    DepType.JAVA_RUNTIME.value
                ),
            )

    def _check_java_home(
        self, java_result: DependencyResult
    ) -> DependencyResult:
        """
        Check if JAVA_HOME is set appropriately.

        Args:
            java_result: Result from Java check.

        Returns:
            DependencyResult for JAVA_HOME.
        """
        java_home = os.environ.get("JAVA_HOME")

        if java_home:
            return DependencyResult(
                dep_type=DepType.ENVIRONMENT_VAR,
                name="JAVA_HOME",
                status=DepStatus.OK,
                found_path=java_home,
                message=f"JAVA_HOME is set to: {java_home}",
                suggestion="",
            )

        if java_result.status in (DepStatus.OK, DepStatus.SIMULATED) and java_result.found_path:
            deduced_home = self.env_manager.get_java_home_from_path(
                java_result.found_path
            )
            return DependencyResult(
                dep_type=DepType.ENVIRONMENT_VAR,
                name="JAVA_HOME",
                status=DepStatus.OK,
                found_path=deduced_home,
                message=(
                    f"JAVA_HOME is not set, but can be deduced from "
                    f"java path: {deduced_home}"
                ),
                suggestion=(
                    f"Consider adding to shell profile:\n"
                    f"  export JAVA_HOME={deduced_home}"
                ),
            )

        return DependencyResult(
            dep_type=DepType.ENVIRONMENT_VAR,
            name="JAVA_HOME",
            status=DepStatus.MISSING,
            message="JAVA_HOME is not set and Java is not available.",
            suggestion="Install Java first, then set JAVA_HOME.",
        )

    def _check_tool_on_path(self, tool_name: str) -> DependencyResult:
        """
        Check if a tool is available on PATH.

        Args:
            tool_name: Executable name (e.g., 'tar', 'unzip').

        Returns:
            DependencyResult for the tool.
        """
        tool_path = shutil.which(tool_name)
        if tool_path:
            return DependencyResult(
                dep_type=DepType.PATH_TOOL,
                name=tool_name,
                status=DepStatus.OK,
                found_path=tool_path,
                message=f"'{tool_name}' is available at: {tool_path}",
                suggestion="",
            )
        else:
            return DependencyResult(
                dep_type=DepType.PATH_TOOL,
                name=tool_name,
                status=DepStatus.MISSING,
                message=(
                    f"'{tool_name}' is not found on PATH. "
                    f"It may be needed for mod/backup operations."
                ),
                suggestion=f"Install {tool_name} using your system package manager.",
            )

    def resolve_missing(self, result: CheckResult) -> CheckResult:
        """
        Attempt to resolve missing dependencies.

        For each critical missing dependency:
          1. Simulate installation
          2. Hot-reload environment
          3. Re-verify

        Args:
            result: CheckResult from check_all().

        Returns:
            Updated CheckResult after resolution attempts.
        """
        for dep in result.critical:
            if dep.dep_type == DepType.JAVA_RUNTIME:
                sim_info = self.installer.simulate_install(
                    DepType.JAVA_RUNTIME.value,
                    version=dep.required_version or "17.0.9",
                )

                self.hot_reload.refresh_environment(
                    self.env_manager, sim_info["path"]
                )

                dep.status = DepStatus.SIMULATED
                dep.found_path = sim_info["path"]
                dep.current_version = sim_info["version"]
                dep.message = (
                    f"Java runtime simulated at {sim_info['path']} "
                    f"(version {sim_info['version']})"
                )

        recheck = self.check_all()

        # Update existing checks with recheck results
        existing_names = {c.name for c in result.checks}
        for new_check in recheck.checks:
            if new_check.name in existing_names:
                # Update the existing check in-place
                for existing in result.checks:
                    if existing.name == new_check.name:
                        existing.status = new_check.status
                        existing.found_path = new_check.found_path
                        existing.current_version = new_check.current_version
                        existing.required_version = new_check.required_version
                        existing.message = new_check.message
                        existing.suggestion = new_check.suggestion
                        break
            else:
                result.checks.append(new_check)

        return result

    def get_path_snapshot(self) -> Dict[str, object]:
        """
        Get a snapshot of current PATH and environment.

        Returns:
            Dict with 'path_dirs' (list) and 'java' info.
        """
        path = os.environ.get("PATH", "")
        java_home = os.environ.get("JAVA_HOME", "")

        snapshot: Dict[str, object] = {
            "path_dirs": path.split(os.pathsep),
            "java_home": java_home,
        }

        java_path = self.java_detector.find_java()
        if java_path:
            version_info = self.java_detector.get_version(java_path)
            if version_info:
                snapshot["java_version"] = version_info

        return snapshot

    def reset(self) -> None:
        """
        Reset all state: clear simulated installs, restore environment,
        and clear caches.
        """
        self.installer.clear_simulated()
        self.env_manager.restore()
        self.hot_reload.clear_caches()
        logger.info("DynamicDeps fully reset")
