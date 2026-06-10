"""
datagram_functions.py — Embedded function system for datagrams.

Allows datagrams to bundle Python functions that can be loaded and executed
at runtime. This enables self-extracting/self-executing datagrams.
"""

import sys
import types
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from .datagram_types import DatagramFunction, DatagramVersion


class FunctionLoadError(Exception):
    """Raised when an embedded function cannot be loaded."""
    pass


class FunctionRegistry:
    """
    Registry of available functions for a datagram.
    
    Functions can be:
      - EMBEDDED: Source code stored in the datagram
      - FILE-BASED: .py files in the Functions/ directory
      - REGISTERED: Loaded externally and registered
    """

    def __init__(self):
        self._functions: Dict[str, DatagramFunction] = {}
        self._loaded: Dict[str, Callable] = {}

    def register(self, func: DatagramFunction, callable_obj: Callable = None) -> None:
        """Register a function definition and optional callable."""
        self._functions[func.name] = func
        if callable_obj:
            self._loaded[func.name] = callable_obj

    def get_function(self, name: str) -> Optional[DatagramFunction]:
        """Get function metadata by name."""
        return self._functions.get(name)

    def get_callable(self, name: str) -> Optional[Callable]:
        """Get loaded callable by name."""
        return self._loaded.get(name)

    def has_function(self, name: str) -> bool:
        return name in self._functions

    def has_callable(self, name: str) -> bool:
        return name in self._loaded

    def load_embedded(self, func: DatagramFunction) -> Callable:
        """
        Load an embedded function from source code.
        The function source must define the entry_point callable.
        """
        if func.name in self._loaded:
            return self._loaded[func.name]

        if not func.source:
            raise FunctionLoadError(f"No source code for function '{func.name}'")

        try:
            # Compile and execute the source in a new module namespace
            module_name = f"_datagram_func_{func.name}"
            module = types.ModuleType(module_name)
            exec(compile(func.source, f"<datagram:{func.name}>", "exec"), module.__dict__)

            # Get the entry point
            if not hasattr(module, func.entry_point):
                raise FunctionLoadError(
                    f"Function '{func.name}' has no entry point '{func.entry_point}'"
                )

            callable_obj = getattr(module, func.entry_point)
            if not callable(callable_obj):
                raise FunctionLoadError(
                    f"Entry point '{func.entry_point}' in '{func.name}' is not callable"
                )

            self._loaded[func.name] = callable_obj
            return callable_obj

        except Exception as e:
            raise FunctionLoadError(
                f"Failed to load function '{func.name}': {e}"
            )

    def load_from_file(self, file_path: Path) -> DatagramFunction:
        """
        Load a function from a .py file in the Functions directory.
        The file must define the function callable and a __version__ string.
        """
        if not file_path.exists():
            raise FunctionLoadError(f"Function file not found: {file_path}")

        name = file_path.stem

        try:
            spec = importlib.util.spec_from_file_location(f"_datagram_file_{name}", file_path)
            if spec is None or spec.loader is None:
                raise FunctionLoadError(f"Cannot load spec for: {file_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            raise FunctionLoadError(f"Failed to load file '{file_path}': {e}")

        # Determine entry point (main function or name)
        entry_point = getattr(module, "main", None) or getattr(module, name, None)
        if entry_point is None:
            raise FunctionLoadError(
                f"No 'main' or '{name}' function in {file_path}"
            )
        if not callable(entry_point):
            raise FunctionLoadError(f"Entry point in {file_path} is not callable")

        # Get version from module
        version_str = getattr(module, "__version__", "1.0.0")
        version = DatagramVersion.parse(version_str)

        # Create function metadata
        func = DatagramFunction(
            name=name,
            version=version,
            language="python",
            source=file_path.read_text(encoding="utf-8"),
            entry_point=entry_point.__name__,
            description=getattr(module, "__doc__", "") or "",
        )

        self._functions[name] = func
        self._loaded[name] = entry_point
        return func

    def load_all_from_directory(self, func_dir: Path) -> List[DatagramFunction]:
        """Load all .py files from a Functions directory."""
        loaded = []
        if not func_dir.exists():
            return loaded

        for py_file in sorted(func_dir.glob("*.py")):
            try:
                func = self.load_from_file(py_file)
                loaded.append(func)
            except FunctionLoadError as e:
                print(f"[datagram] Warning: {e}")
        return loaded

    def execute(self, name: str, *args, **kwargs) -> Any:
        """Execute a loaded function by name."""
        callable_obj = self._loaded.get(name)
        if callable_obj is None:
            raise FunctionLoadError(f"Function '{name}' is not loaded")
        return callable_obj(*args, **kwargs)

    @property
    def function_count(self) -> int:
        return len(self._functions)

    @property
    def function_names(self) -> List[str]:
        return list(self._functions.keys())
