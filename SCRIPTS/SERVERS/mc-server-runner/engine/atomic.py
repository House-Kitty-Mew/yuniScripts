"""
Atomic (journal-based) operation manager for safe VFS operations.

Provides transaction-like guarantees for operations that modify the VFS
database or file system: every mutation captures a *before* snapshot so
it can be rolled back if the higher-level operation fails.

Key components:
- :class:`AtomicOperation`:  A single operation with before/after state.

- :class:`AtomicJournal`:    Thread-safe stack of pending operations with
begin / commit / rollback lifecycle.
- :func:`get_journal`:       Module-level singleton accessor.
- :func:`atomic_write`:      Helper that captures file state before
modification and journalises the write.

Thread-safety is guaranteed via ``threading.Lock()`` on the journal.
"""

import json
import threading
import copy
import os
from datetime import datetime
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
)


# ---------------------------------------------------------------------------
# AtomicOperation
# ---------------------------------------------------------------------------

class AtomicOperation:
    """Represents a single atomic operation in the journal.

    Each operation captures its identity, type, the key/path it operates
    on, and the state *before* the operation (and optionally *after*
    on commit).

    Uses ``__slots__`` for memory efficiency when many operations are
    queued in the journal.

    Attributes:
    op_id:        Unique identifier within the journal session.

    op_type:      Operation category (e.g. ``"vfs_write"``, ``"vfs_delete"``,
    ``"db_update"``).
    op_key:       Key or path the operation targets (e.g.
    ``"/servers/my-server/server.properties"``).
    before_state: Snapshot of the state **before** the operation.
    after_state:  Snapshot of the state **after** the operation (set on commit).
    status:       ``"pending"``, ``"committed"``, or ``"rolled_back"``.
    timestamp:    ISO-8601 timestamp of creation.
    """

    __slots__ = (
        "op_id",
        "op_type",
        "op_key",
        "before_state",
        "after_state",
        "status",
        "timestamp",
    )

    def __init__(
        self,
        op_id: int,
        op_type: str,
        op_key: str,
        before_state: Dict[str, Any],
        after_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.op_id: int = op_id

        self.op_type: str = op_type
        self.op_key: str = op_key
        self.before_state: Dict[str, Any] = before_state
        self.after_state: Optional[Dict[str, Any]] = after_state
        self.status: str = "pending"
        self.timestamp: str = datetime.utcnow().isoformat() + "Z"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this operation to a plain dictionary.

        Returns:
        A dict suitable for JSON serialisation or database storage.

        """
        return {
            "op_id": self.op_id,
            "op_type": self.op_type,
            "op_key": self.op_key,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "status": self.status,
            "timestamp": self.timestamp,
        }

    def __repr__(self) -> str:
        return (
            f"AtomicOperation(id={self.op_id}, type={self.op_type!r}, "
            f"key={self.op_key!r}, status={self.status!r})"
        )


        # ---------------------------------------------------------------------------
        # AtomicJournal
        # ---------------------------------------------------------------------------

class AtomicJournal:
    """Manages atomic operations with full rollback capability.

    The journal maintains a **stack** of pending (uncommitted) operations.
    Each operation records its *before* state at creation time. On
    rollback, the operation is reversed using the before state. On commit,
    the *after* state is captured and the record is optionally persisted
    to a database.

    The journal is **thread-safe** — all public methods acquire
    ``self._lock`` (a ``threading.Lock()``).

    Args:
    db_instance:

    An optional database adaptor that provides ``insert_operation``

    and ``update_operation`` methods. If ``None``, operations are
    kept only in memory.

    Usage:

    .. code-block:: python


    journal = AtomicJournal(db)

    op1_id = journal.begin("vfs_write", "/etc/config.json",
    before_state={"exists": True, "size": 1024})

    # ... perform the write ...

    journal.commit(op1_id)   # persist

    # On failure:
    journal.rollback(op1_id)  # restore before_state

    """

    def __init__(self, db_instance: Optional[Any] = None) -> None:
        self._db: Optional[Any] = db_instance
        self._lock: threading.Lock = threading.Lock()
        self._pending: List[AtomicOperation] = []
        self._committed: List[AtomicOperation] = []
        self._next_id: int = 1

        # -- Lifecycle -------------------------------------------------------

    def begin(
        self,
        op_type: str,
        op_key: str,
        before_state: Dict[str, Any],
    ) -> int:
        """Start a new atomic operation and add it to the pending stack.


        Args:
        op_type:       Category label for the operation.

        op_key:        The key or path this operation targets.
        before_state:  Snapshot taken **before** the mutation.

        Returns:
        The assigned operation ID (integer). Use this ID with

        :meth:`commit` or :meth:`rollback`.

        If a *db_instance* was provided, the operation record is also
        inserted into the database immediately.
        """
        with self._lock:
            op_id = self._next_id
            self._next_id += 1

            operation = AtomicOperation(
                op_id=op_id,
                op_type=op_type,
                op_key=op_key,
                before_state=copy.deepcopy(before_state),
            )

            self._pending.append(operation)

            # Optional DB persistence
            if self._db is not None:
                self._db.insert_operation(operation.to_dict())

            return op_id

    def commit(self, op_id: int, after_state: Optional[Dict[str, Any]] = None) -> bool:
        """Mark an operation as committed.

        Args:
        op_id:        The operation ID returned by :meth:`begin`.

        after_state:  Optional snapshot **after** the mutation.
        If not provided, ``before_state`` is reused.

        Returns:
        ``True`` if the operation was found and committed.

        ``False`` if *op_id* was not in the pending stack.
        """
        with self._lock:
            for i, op in enumerate(self._pending):
                if op.op_id == op_id:
                    op.after_state = (
                        copy.deepcopy(after_state) if after_state is not None
                        else copy.deepcopy(op.before_state)
                    )
                    op.status = "committed"

                    # Move from pending to committed
                    self._committed.append(self._pending.pop(i))

                    # Update DB if attached
                    if self._db is not None:
                        self._db.update_operation(op.to_dict())

                    return True
            return False

        # -- Internal rollback (no locking -- caller MUST hold self._lock) ----

    def _rollback_unlocked(self, op_id: Optional[int] = None) -> Dict[str, Any]:
        """Roll back an operation **without** acquiring ``self._lock``.

        .. warning::
        The caller **must** hold ``self._lock`` before calling this

        method.  Public callers should use :meth:`rollback` instead.

        Args:
        op_id:

        ID of the operation to roll back. If ``None``, the **most

        recent** pending operation is rolled back.

        Returns:
        The ``before_state`` dict that was restored.


        Raises:
        ValueError: If *op_id* is not found or the pending stack is

        empty.
        """
        if op_id is None:

            if not self._pending:

                raise ValueError("No pending operations to roll back.")

            operation = self._pending.pop()

        else:

            idx = None
            for i, op in enumerate(self._pending):
                if op.op_id == op_id:
                    idx = i
                    break

            if idx is None:
                raise ValueError(
                    f"Operation {op_id} not found in pending stack."
                )
            operation = self._pending.pop(idx)

        operation.status = "rolled_back"

        if self._db is not None:
            self._db.update_operation(operation.to_dict())

        return copy.deepcopy(operation.before_state)

        # -- Public rollback (acquires self._lock) ---------------------------

    def rollback(self, op_id: Optional[int] = None) -> Dict[str, Any]:
        """Roll back an operation by restoring its *before_state*.

        Args:
        op_id:

        ID of the operation to roll back. If ``None``, the **most

        recent** pending operation is rolled back.

        Returns:
        The ``before_state`` dict that was restored — the caller is

        responsible for applying this state to revert the mutation.

        Raises:
        ValueError: If *op_id* is provided but not found in the

        pending stack, or if the pending stack is empty
        when ``op_id=None``.

        After rollback the operation's status is set to ``"rolled_back"``
        and it is removed from the pending stack.
        """
        with self._lock:
            return self._rollback_unlocked(op_id)

    def rollback_all(self) -> List[Dict[str, Any]]:
        """Roll back **all** pending operations in reverse (LIFO) order.

        Returns:
        A list of ``before_state`` dicts — one for each rolled-back

        operation — in the order they were rolled back. Callers
        should apply each state in the same order to restore the
        system.

        After this call the pending stack is empty.
        """
        restored: List[Dict[str, Any]] = []
        with self._lock:
            while self._pending:
                restored.append(self._rollback_unlocked())
            return restored

            # -- Queries ---------------------------------------------------------

    def get_pending(self) -> List[Dict[str, Any]]:
        """Return a serialisable list of all pending (uncommitted) operations.

        Returns:
        A list of :meth:`AtomicOperation.to_dict` dictionaries.

        """
        with self._lock:
            return [op.to_dict() for op in self._pending]

    def get_committed(self) -> List[Dict[str, Any]]:
        """Return a serialisable list of all committed operations.

        Returns:
        A list of :meth:`AtomicOperation.to_dict` dictionaries.

        """
        with self._lock:
            return [op.to_dict() for op in self._committed]

    def clear_committed(self) -> int:
        """Remove all committed operations from in-memory storage.

        If a *db_instance* was provided, the records remain persisted
        in the database — only the in-memory list is cleared.

        Returns:
        The number of entries that were cleared.

        """
        with self._lock:
            count = len(self._committed)
            self._committed.clear()
            return count

    def pending_count(self) -> int:
        """Return the number of pending operations."""
        with self._lock:
            return len(self._pending)

    def committed_count(self) -> int:
        """Return the number of committed (but not yet cleared) operations."""
        with self._lock:
            return len(self._committed)

            # -- Context manager -------------------------------------------------

    def __enter__(self) -> "AtomicJournal":
        """Enter the context manager — no action needed, journal is ready."""
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[object],
    ) -> bool:
        """Exit the context manager.


        - If an exception occurred, **roll back** all pending operations
        (the exception is re-raised).
        - If no exception, **commit** all pending operations.

        Returns:
        ``False`` so that any exception is propagated to the caller.

        """
        if exc_type is not None:
            # Exception in flight -> roll everything back
            self.rollback_all()
            return False  # re-raise

            # Success -> commit everything
            for op in list(self._pending):
                self.commit(op.op_id)
            return False


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_journal: Optional[AtomicJournal] = None
"""Module-scoped journal instance.  Use :func:`get_journal` to access."""

_journal_lock: threading.Lock = threading.Lock()
"""Lock guarding singleton initialisation."""


def get_journal(db: Optional[Any] = None) -> AtomicJournal:
    """Return the module-level :class:`AtomicJournal` singleton.

    The singleton is created on first call (lazy initialisation).  If
    *db* is provided on the first call it is passed to the journal
    constructor; subsequent calls ignore the *db* argument.

    Thread-safe with double-checked locking.

    Args:
    db: Optional database adaptor for operation persistence.

    Only used when the singleton is first created.

    Returns:
    The shared :class:`AtomicJournal` instance.

    """
    global _journal

    if _journal is None:
        with _journal_lock:
            if _journal is None:  # double-check
                _journal = AtomicJournal(db_instance=db)

    return _journal


def reset_journal() -> None:
    """Reset the journal singleton to ``None`` (for testing).

    After calling this, the next call to :func:`get_journal` will create
    a fresh instance.
    """
    global _journal
    with _journal_lock:
        _journal = None


# ---------------------------------------------------------------------------
# atomic_write helper
# ---------------------------------------------------------------------------

class AtomicWriteContext:
    """Context-like object returned by :func:`atomic_write`.

    The caller uses :meth:`set_new_content` to supply the after-state,
    then explicitly calls :meth:`commit` or :meth:`rollback`.

    Attributes:
    op_id: The journal operation ID.

    """

    def __init__(
        self,
        journal: AtomicJournal,
        op_id: int,
        file_path: str,
    ) -> None:
        self._journal: AtomicJournal = journal

        self._op_id: int = op_id
        self._file_path: str = file_path
        self._new_content: Optional[bytes] = None

    # -- Properties ------------------------------------------------------

    @property
    def op_id(self) -> int:
        """Return the underlying journal operation ID."""
        return self._op_id

    @property
    def file_path(self) -> str:
        """Return the file path being written."""
        return self._file_path

    # -- Content lifecycle -----------------------------------------------

    def set_new_content(self, content: bytes) -> None:
        """Supply the new content that was (or will be) written to disk.

        Args:
        content: The new file content as bytes.

        """
        self._new_content = content

    @property
    def new_content(self) -> Optional[bytes]:
        """Return the content previously set via :meth:`set_new_content`."""
        return self._new_content

    # -- Journal actions -------------------------------------------------

    def commit(self) -> bool:
        """Finalise the write operation in the journal.

        Returns:
        ``True`` if the commit succeeded.

        """
        after_state: Dict[str, Any] = {
            "file_path": self._file_path,
            "exists": self._new_content is not None,
            "size": len(self._new_content) if self._new_content is not None else 0,
        }
        return self._journal.commit(self._op_id, after_state=after_state)

    def rollback(self) -> Dict[str, Any]:
        """Roll back this write operation.

        Returns:
        The ``before_state`` dict so the caller can restore the file.

        """
        return self._journal.rollback(self._op_id)


def atomic_write(
    file_path: str,
    db: Any,
    vfs: Any,
) -> 'AtomicWriteContext':
    """Create an :class:`AtomicWriteContext` for a safe VFS write.


    Captures the current state of *file_path* **before** the write,
    journalises the operation, and returns a context object that the
    caller uses to supply new content.

    Usage:

    .. code-block:: python


    db = get_database()
    vfs = get_vfs()
    journal = atomic.get_journal(db)

    ctx = atomic.atomic_write("/data/file.txt", db, vfs)
    new_bytes = b"Hello, world!\\n"
    ctx.set_new_content(new_bytes)
    vfs.write(file_path, new_bytes)
    ctx.commit()
    Args:
    file_path: Path to the file being written (VFS or real path).

    db:        Database adaptor (used by :func:`get_journal`).
    vfs:       VFS handler that provides a ``read`` method for capturing
    before-state.

    Returns:
    An :class:`AtomicWriteContext` instance.


    Raises:
    OSError: If the before-state cannot be read.

    """
    journal = get_journal(db)

    # Capture before-state
    try:
        existing_data = vfs.read(file_path)
        before_state: Dict[str, Any] = {
            "file_path": file_path,
            "exists": True,
            "size": len(existing_data),
        }
    except FileNotFoundError:
        before_state = {
            "file_path": file_path,
            "exists": False,
            "size": 0,
        }

    op_id = journal.begin("vfs_write", file_path, before_state)
    return AtomicWriteContext(journal, op_id, file_path)


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "AtomicOperation",
    "AtomicJournal",
    "get_journal",
    "reset_journal",
    "atomic_write",
    "AtomicWriteContext",
]

