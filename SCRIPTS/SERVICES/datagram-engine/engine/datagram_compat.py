"""
datagram_compat.py — Version compatibility checking and function capability negotiation.

Implements the forward/backward compatibility system from the original Datagram spec.
"""

from typing import Dict, List, Optional, Tuple
from .datagram_types import DatagramVersion, DatagramFunction


class CompatibilityResult:
    """
    Result of a compatibility check between a required version
    and an available version (function loader, viewer, etc.)
    """

    def __init__(self, compatible: bool, required: DatagramVersion,
                 available: DatagramVersion, component: str = "",
                 message: str = ""):
        self.compatible = compatible
        self.required = required
        self.available = available
        self.component = component
        self.message = message

    def to_dict(self) -> dict:
        return {
            "compatible": self.compatible,
            "required": str(self.required),
            "available": str(self.available),
            "component": self.component,
            "message": self.message,
        }


class CompatibilityChecker:
    """
    Checks compatibility between required and available component versions.
    
    Supports:
      - Function versions (loader, viewer, buttons, etc.)
      - Datagram format versions
      - Embedded function requirements
    """

    # Default component version requirements
    DEFAULT_LOADER_VERSION = DatagramVersion(1, 0, 0)
    DEFAULT_VIEWER_VERSION = DatagramVersion(1, 0, 0)
    DEFAULT_BUTTONS_VERSION = DatagramVersion(1, 0, 0)

    def __init__(self, engine_version: DatagramVersion = None):
        self.engine_version = engine_version or DatagramVersion(1, 0, 0)
        self._component_versions: Dict[str, DatagramVersion] = {}
        self._results: List[CompatibilityResult] = []

    def register_component(self, name: str, version: DatagramVersion) -> None:
        """Register an available component version."""
        self._component_versions[name] = version

    def check_required_version(self, component: str, required: DatagramVersion,
                                available: Optional[DatagramVersion] = None) -> CompatibilityResult:
        """
        Check if an available version meets a required version.
        Uses registered version if available is not provided.
        """
        if available is None:
            available = self._component_versions.get(component, DatagramVersion(0, 0, 0))

        compatible = available.is_compatible_with(required)
        msg = ""
        if not compatible:
            msg = f"Component '{component}' requires v{required} but has v{available}"

        result = CompatibilityResult(
            compatible=compatible,
            required=required,
            available=available,
            component=component,
            message=msg,
        )
        self._results.append(result)
        return result

    def check_datagram_compatibility(self, datagram_version: DatagramVersion) -> CompatibilityResult:
        """Check if the engine can load a datagram of the given version."""
        return self.check_required_version("datagram_format", self.engine_version,
                                            datagram_version)

    def check_function_requirements(self, functions: List[DatagramFunction]) -> List[CompatibilityResult]:
        """Check all embedded function version requirements."""
        results = []
        for func in functions:
            available = self._component_versions.get(f"func_{func.name}")
            if available is None:
                # If not registered, check if it's embedded (always compatible)
                if func.source:
                    available = func.version
                else:
                    available = DatagramVersion(0, 0, 0)
            result = self.check_required_version(
                f"function:{func.name}", func.version, available
            )
            results.append(result)
        return results

    @property
    def all_compatible(self) -> bool:
        """Check if all checks passed."""
        return all(r.compatible for r in self._results)

    @property
    def failures(self) -> List[CompatibilityResult]:
        return [r for r in self._results if not r.compatible]

    def clear(self) -> None:
        self._results.clear()
