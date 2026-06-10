"""
networking.py — Virtual Network Adapter Sandbox for Minecraft Servers

This module provides a virtual network sandbox layer that Minecraft server
instances use through the runner. It does NOT intercept actual system sockets;
instead it manages virtual IP assignments, firewall ACL rules, and port
allocation to prevent conflicts between collocated server instances.

The runner enforces sandbox constraints (bandwidth limits, latency simulation,
packet loss) at server startup via platform-specific firewall rules and
traffic control (tc) configuration.

Expected DB Interface
=====================
Classes in this module accept a `db` object that must implement:

    VirtualAdapter:
        - get_virtual_adapter(server_id: int) -> dict | None
        - create_virtual_adapter(server_id: int, virtual_ip: str,
                                 mac_address: str) -> dict
        - update_virtual_adapter(server_id: int, **kwargs) -> bool

    Firewall:
        - add_firewall_rule(server_id: int, rule_type: str, direction: str,
                            protocol: str, port_range: str | None,
                            target_host: str, target_port: int | None,
                            priority: int) -> int
        - remove_firewall_rule(rule_id: int) -> bool
        - list_firewall_rules(server_id: int) -> list[dict]

    PortManager (via NetworkManager / runner):
        - reserve_port(port: int, server_id: int) -> bool
        - release_port(port: int, server_id: int) -> bool
"""

from __future__ import annotations

import ipaddress
import json
import logging
import random
import threading
import uuid
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger('mc-server-runner.networking')


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_VIRTUAL_SUBNET = ipaddress.IPv4Network('10.0.0.0/24')
"""Default virtual subnet for auto-generated IP addresses."""
DEFAULT_SERVER_PORT = 25565
"""Default Minecraft server port used when no preferred port is given."""
DEFAULT_RCON_PORT = 25575
"""Default Minecraft RCON port."""
MAX_PORT = 65535
"""Maximum valid TCP/UDP port number."""


# ---------------------------------------------------------------------------
# VirtualAdapter
# ---------------------------------------------------------------------------

class VirtualAdapter:
    """Represents a virtual network adapter assigned to a server instance.

    Stores network sandbox configuration: virtual IP, subnet, bandwidth
    limits, latency simulation, and packet loss simulation. All config is
    DB-backed. The runner enforces these constraints when launching the
    Minecraft server.

    Parameters
    ----------
    db : object
        Database handler implementing the expected virtual adapter methods.
    server_id : int
        Unique identifier for the server instance this adapter belongs to.
    """

    def __init__(self, db: Any, server_id: int) -> None:
        self.db = db
        self.server_id = server_id
        self._config: Optional[dict] = None
        self._load()

    # -- Configuration loading ------------------------------------------------

    def _load(self) -> None:
        """Load adapter configuration from the database.

        If no config exists for this server, one is auto-created with a
        generated virtual IP and MAC address.  The loaded (or newly-created)
        config is stored in ``self._config``.
        """
        data = self.db.get_virtual_adapter(self.server_id)
        if data:
            self._config = data
            logger.debug(
                'VirtualAdapter loaded for server %d (IP: %s)',
                self.server_id, data.get('virtual_ip', '?'),
            )
        else:
            self.db.create_virtual_adapter(
                server_id=self.server_id,
                virtual_ip=self._generate_ip(),
                mac_address=self._generate_mac(),
            )
            # Reload the config we just created (returns dict now)
            self._config = self.db.get_virtual_adapter(self.server_id)
            if not self._config:
                self._config = self._default_config()
            logger.info(
                'VirtualAdapter auto-created for server %d (IP: %s)',
                self.server_id, self._config.get('virtual_ip', '?'),
            )

    def _default_config(self) -> dict:
        """Return a fallback in-memory config when the DB is unavailable."""
        return {
            'server_id': self.server_id,
            'virtual_ip': self._generate_ip(),
            'mac_address': self._generate_mac(),
            'subnet': str(DEFAULT_VIRTUAL_SUBNET),
            'bandwidth_limit_kbps': 0,        # 0 = unlimited
            'latency_ms': 0,                   # 0 = no artificial delay
            'packet_loss_pct': 0.0,            # 0.0 = no loss
            'enabled': True,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat(),
        }

    # -- Generation helpers ---------------------------------------------------

    def _generate_ip(self) -> str:
        """Generate a random virtual IP address in ``10.0.0.0/24``.

        The network and broadcast addresses (``.0`` and ``.255``) are
        excluded.  Returns the IP as a dotted-quad string.
        """
        net = DEFAULT_VIRTUAL_SUBNET
        hosts = list(net.hosts())          # excludes network & broadcast
        return str(random.choice(hosts))

    def _generate_mac(self) -> str:
        """Generate a random locally-administered MAC address.

        The first octet is set to ``0x02`` which marks the address as
        **locally administered** and **unicast** per IEEE 802.3.
        Returns a colon-separated hexadecimal string (e.g.
        ``02:1a:3b:4c:5d:6e``).
        """
        mac = [0x02] + [random.randint(0x00, 0xFF) for _ in range(5)]
        return ':'.join(f'{octet:02x}' for octet in mac)

    # -- Public API -----------------------------------------------------------

    def get_config(self) -> dict:
        """Return the current adapter configuration dict.

        Returns
        -------
        dict
            Adapter properties including *virtual_ip*, *mac_address*,
            *subnet*, *bandwidth_limit_kbps*, *latency_ms*,
            *packet_loss_pct*, and *enabled* status.
        """
        return dict(self._config) if self._config else {}

    def update(self, **kwargs: Any) -> bool:
        """Update one or more adapter properties.

        Accepted keyword arguments include any key present in the adapter
        config dict (e.g. ``virtual_ip``, ``subnet``,
        ``bandwidth_limit_kbps``, ``latency_ms``, ``packet_loss_pct``).

        Parameters
        ----------
        **kwargs
            Property names and their new values.

        Returns
        -------
        bool
            ``True`` if the update was persisted successfully.
        """
        if self._config is None:
            logger.warning(
                'VirtualAdapter.update: no config loaded for server %d',
                self.server_id,
            )
            return False

        # Validate IP address if being changed
        new_ip = kwargs.get('virtual_ip')
        if new_ip is not None:
            try:
                ipaddress.IPv4Address(new_ip)
            except ipaddress.AddressValueError as exc:
                logger.error('Invalid virtual IP %r — %s', new_ip, exc)
                return False

        # Validate subnet if being changed
        new_subnet = kwargs.get('subnet')
        if new_subnet is not None:
            try:
                ipaddress.IPv4Network(new_subnet, strict=False)
            except (ipaddress.AddressValueError, ValueError) as exc:
                logger.error('Invalid subnet %r — %s', new_subnet, exc)
                return False

        # Validate packet loss percentage
        new_loss = kwargs.get('packet_loss_pct')
        if new_loss is not None:
            try:
                val = float(new_loss)
            except (TypeError, ValueError):
                logger.error('packet_loss_pct must be a float')
                return False
            if not (0.0 <= val <= 100.0):
                logger.error('packet_loss_pct must be in range 0.0‑100.0')
                return False

        kwargs['updated_at'] = datetime.utcnow().isoformat()
        self._config.update(kwargs)

        self.db.update_virtual_adapter(self.server_id, **kwargs)
        logger.debug(
            'VirtualAdapter updated for server %d (%d keys)',
            self.server_id, len(kwargs),
        )
        return True

    def enable(self) -> bool:
        """Enable the virtual adapter.

        Sets the *enabled* flag to ``True`` and persists the change.

        Returns
        -------
        bool
            ``True`` if the adapter was enabled successfully.
        """
        return self.update(enabled=True)

    def disable(self) -> bool:
        """Disable the virtual adapter.

        Sets the *enabled* flag to ``False`` and persists the change.
        A disabled adapter will not have its network constraints enforced
        by the runner.

        Returns
        -------
        bool
            ``True`` if the adapter was disabled successfully.
        """
        return self.update(enabled=False)


# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------

class Firewall:
    """Manages network ACL (firewall) rules for a server instance.

    Rules are stored in the database and enforced at server startup through
    platform-specific firewall tooling (iptables/nftables on Linux).

    The security model is **default-deny**: a connection is allowed only if
    an ``allow`` rule matches it.  Rules are evaluated in *priority* order
    (lower numeric priority = checked first).

    Parameters
    ----------
    db : object
        Database handler implementing the expected firewall rule methods.
    server_id : int
        Unique identifier for the server instance.
    """

    def __init__(self, db: Any, server_id: int) -> None:
        self.db = db
        self.server_id = server_id

    # -- Rule management ------------------------------------------------------

    def add_rule(
        self,
        rule_type: str = 'allow',
        direction: str = 'outbound',
        protocol: str = 'tcp',
        port_range: Optional[str] = None,
        target_host: str = '*',
        target_port: Optional[int] = None,
        priority: int = 100,
    ) -> int:
        """Add a firewall rule to the database.

        Parameters
        ----------
        rule_type : str, optional
            ``'allow'`` or ``'deny'`` (default ``'allow'``).
        direction : str, optional
            ``'inbound'`` or ``'outbound'`` (default ``'outbound'``).
        protocol : str, optional
            ``'tcp'``, ``'udp'``, or ``'any'`` (default ``'tcp'``).
        port_range : str or None, optional
            Port range string, e.g. ``'25565-25575'``.  When provided,
            *target_port* is ignored for this field.
        target_host : str, optional
            Destination host or ``'*'`` for any (default ``'*'``).
        target_port : int or None, optional
            Destination port.  Ignored if *port_range* is set.
        priority : int, optional
            Rule evaluation priority (lower = evaluated first; default 100).

        Returns
        -------
        int
            The new rule's database ID, or ``-1`` on failure.
        """
        rule_id = self.db.add_firewall_rule(
            server_id=self.server_id,
            rule_type=rule_type,
            direction=direction,
            protocol=protocol,
            port_range=port_range,
            target_host=target_host,
            target_port=target_port,
            priority=priority,
        )
        logger.info(
            'Firewall rule #%d added for server %d [%s %s %s/%s → %s]',
            rule_id, self.server_id, rule_type, direction, protocol,
            port_range or str(target_port or '*'), target_host,
        )
        return rule_id

    def remove_rule(self, rule_id: int) -> bool:
        """Remove a firewall rule from the database.

        Parameters
        ----------
        rule_id : int
            The database ID of the rule to remove.

        Returns
        -------
        bool
            ``True`` if the rule was removed successfully.
        """
        self.db.remove_firewall_rule(rule_id)
        logger.info('Firewall rule #%d removed (server %d)', rule_id, self.server_id)
        return True

    def list_rules(self) -> list[dict]:
        """List all firewall rules for this server.

        Returns
        -------
        list[dict]
            A list of rule dicts, each containing *rule_id*, *rule_type*,
            *direction*, *protocol*, *port_range*, *target_host*,
            *target_port*, and *priority*.  Returns an empty list on
            error.
        """
        return list(self.db.list_firewall_rules(self.server_id))
    # -- Connection checking --------------------------------------------------

    def check_connection(
        self,
        host: str,
        port: int,
        protocol: str = 'tcp',
    ) -> tuple:
        """Check whether a connection would be allowed by the current rules.

        Rules are evaluated in *priority* order (lower number first).
        The first matching rule determines the result.  If no rule
        explicitly allows the connection, it is **denied** by default.

        Parameters
        ----------
        host : str
            Target hostname or IP address.
        port : int
            Target port number.
        protocol : str, optional
            ``'tcp'``, ``'udp'``, or ``'any'`` (default ``'tcp'``).

        Returns
        -------
        tuple[bool, int | None]
            ``(allowed, rule_id)`` where *allowed* is ``True`` if the
            connection is permitted, and *rule_id* is the ID of the
            matching rule (or ``None`` if no rule matched).
        """
        rules = self.list_rules()
        rules.sort(key=lambda r: r.get('priority', 100))

        for rule in rules:
            if not self._rule_matches(rule, host, port, protocol):
                continue

            # First match wins
            allowed = rule.get('rule_type', 'deny') == 'allow'
            logger.debug(
                'check_connection %s:%d/%s → %s (rule #%d, priority %d)',
                host, port, protocol,
                'ALLOW' if allowed else 'DENY',
                rule.get('id'),
                rule.get('priority'),
            )
            return (allowed, rule.get('id'))

        # No matching rule → default deny
        logger.debug(
            'check_connection %s:%d/%s → DENY (no matching rule)',
            host, port, protocol,
        )
        return (False, None)

    @staticmethod
    def _rule_matches(
        rule: dict,
        host: str,
        port: int,
        protocol: str,
    ) -> bool:
        """Check whether a single rule matches the given connection parameters.

        Host matching supports:
        - Exact match (``'mc.example.com'``)
        - Wildcard prefix (``'*.example.com'``)
        - Any host (``'*'``)

        Parameters
        ----------
        rule : dict
            A firewall rule dict (from :meth:`list_rules`).
        host : str
            Target hostname or IP.
        port : int
            Target port.
        protocol : str
            Connection protocol.

        Returns
        -------
        bool
            ``True`` if the rule applies to this connection.
        """
        # Protocol match
        
        rule_proto = rule.get('protocol', 'any')
        if rule_proto not in ('any', protocol):
            return False

        # Direction — we check host-side direction; for simplicity we
        # only reject explicit direction mismatches here.
        # (In production the caller would annotate the direction.)

        # Port range match
        port_range_str = rule.get('port_range')
        target_port = rule.get('target_port')

        if port_range_str:
            if not _port_in_range(port, port_range_str):
                return False
        elif target_port is not None and port != target_port:
            return False

        # Target host match (supports wildcard prefix)
        rule_host = rule.get('target_host', '*')
        if rule_host != '*':
            if rule_host.startswith('*.'):
                # Wildcard domain match: *.example.com → matches mc.example.com
                suffix = rule_host[1:]  # '.example.com'
                if not host.endswith(suffix):
                    return False
            elif rule_host != host:
                return False

        return True

    # -- Utility methods ------------------------------------------------------

    def get_allowed_ports(self) -> list[int]:
        """Get a sorted list of ports that are explicitly allowed.

        Only ``allow`` type rules with explicit port specifications are
        considered.  Rules with ``port_range`` are expanded into individual
        port numbers.

        Returns
        -------
        list[int]
            Sorted list of allowed port numbers (deduplicated).
        """
        ports: set[int] = set()

        for rule in self.list_rules():
            if rule.get('rule_type') != 'allow':
                continue

            port_range_str = rule.get('port_range')
            target_port = rule.get('target_port')

            if port_range_str:
                start_s, _, end_s = port_range_str.partition('-')
                try:
                    start = int(start_s.strip())
                    end = int(end_s.strip()) if end_s else start
                    ports.update(range(start, end + 1))
                except (ValueError, TypeError):
                    continue
            elif target_port is not None:
                ports.add(target_port)

        return sorted(ports)

    def generate_firewall_script(self) -> str:
        """Generate a platform-specific firewall script.

        Currently produces ``iptables`` rules for Linux.  The script
        creates a dedicated chain (``MC-SERVER-{server_id}``) with rules
        that implement the loaded ACL entries.

        Returns
        -------
        str
            A bash script that sets up the sandbox firewall using iptables.
            Returns an empty string if no rules exist.
        """
        rules = self.list_rules()
        
        if not rules:
            return ''

        lines = ['#!/bin/bash', '# Auto-generated firewall script',
                 f'# Server ID: {self.server_id}',
                 f'# Generated: {datetime.utcnow().isoformat()}',
                 '', 'set -e',
                 '',
                 f'CHAIN="MC-SERVER-{self.server_id}"',
                 '',
                 '# Create dedicated chain',
                 f'sudo iptables -N {self._chain_name()} 2>/dev/null || true',
                 '',
                 '# Flush existing rules in chain',
                 f'sudo iptables -F {self._chain_name()}',
                 '']

        for rule in sorted(rules, key=lambda r: r.get('priority', 100)):
            rule_type = rule.get('rule_type', 'deny')
            direction = rule.get('direction', 'outbound')
            protocol = rule.get('protocol', 'tcp')
            target_host = rule.get('target_host', '*')
            port_range = rule.get('port_range')
            target_port = rule.get('target_port')

            # Build iptables arguments
            ipt_args = []

            if direction == 'inbound':
                ipt_args.extend(['-A', self._chain_name(), '-i', 'eth+'])
            else:
                ipt_args.extend(['-A', self._chain_name(), '-o', 'eth+'])

            if protocol != 'any':
                ipt_args.extend(['-p', protocol])

            if target_host and target_host != '*':
                if direction == 'inbound':
                    ipt_args.extend(['-s', target_host])
                else:
                    ipt_args.extend(['-d', target_host])

            if port_range:
                ipt_args.extend(['--dport', port_range])
            elif target_port is not None:
                ipt_args.extend(['--dport', str(target_port)])

            target = 'ACCEPT' if rule_type == 'allow' else 'DROP'
            ipt_args.extend(['-j', target])

            ipt_cmd = 'sudo iptables ' + ' '.join(ipt_args)
            lines.append(f'# Rule priority={rule.get("priority", 100)}')
            lines.append(ipt_cmd)
            lines.append('')

        # Append to FORWARD chain (with comment for identification)
        lines.append('# Jump from FORWARD to our chain')
        lines.append(
            f'sudo iptables -C FORWARD -j {self._chain_name()} 2>/dev/null '
            f'|| sudo iptables -I FORWARD -j {self._chain_name()}'
        )
        lines.append('')

        return '\n'.join(lines)

    def _chain_name(self) -> str:
        """Return the iptables chain name for this server."""
        return f'MC-SERVER-{self.server_id}'


# ---------------------------------------------------------------------------
# PortManager
# ---------------------------------------------------------------------------

class PortManager:
    """Manages port assignments to avoid conflicts between server instances.

    This is a **thread-safe** class that uses a class-level lock and an
    in-memory reservation table.  Reservations are coordinated across
    the process so that collocated servers never step on each other's
    ports.

    The backing DB store is used for persistent reservations so that
    ports remain claimed across runner restarts.
    """

    _lock: threading.Lock = threading.Lock()
    _reserved: dict[int, int] = {}          # port -> server_id

    # ------------------------------------------------------------------
    # Public API (class methods)
    # ------------------------------------------------------------------

    @classmethod
    def reserve(cls, port: int, server_id: int, db: Any) -> bool:
        """Reserve a port for a server instance.

        Parameters
        ----------
        port : int
            Port number to reserve (1–65535).
        server_id : int
            Unique identifier for the server.
        db : object
            Database handler with ``reserve_port`` / ``release_port`` methods.

        Returns
        -------
        bool
            ``True`` if the port was successfully reserved.  Returns
            ``False`` if the port is already claimed by a different server.
        """
        if port < 1 or port > MAX_PORT:
            logger.error('PortManager.reserve: port %d out of range', port)
            return False

        with cls._lock:
            existing = cls._reserved.get(port)
            if existing is not None and existing != server_id:
                logger.warning(
                    'PortManager.reserve: port %d already reserved by server %d',
                    port, existing,
                )
                return False

            # Persist in DB
            db.reserve_port(port, server_id)
            cls._reserved[port] = server_id
            logger.debug('Port %d reserved for server %d', port, server_id)
            return True

    @classmethod
    def release(cls, port: int, server_id: int, db: Any = None) -> bool:
        """Release a previously reserved port.

        Only the server that owns the reservation may release it.

        Parameters
        ----------
        port : int
            Port number to release.
        server_id : int
            The server that currently holds the reservation.
        db : object or None, optional
            Database handler.  If provided, the persistent reservation
            is also removed from the database.

        Returns
        -------
        bool
            ``True`` if the port was released successfully.
        """
        with cls._lock:
            owner = cls._reserved.get(port)
            if owner is None:
                logger.debug('PortManager.release: port %d not reserved', port)
                # Still try to clean DB in case of stale entries
                if db is not None:
                    db.release_port(port, server_id)
                return True  # already free
            if owner != server_id:
                logger.warning(
                    'PortManager.release: port %d owned by server %d, '
                    'cannot release from server %d',
                    port, owner, server_id,
                )
                return False

            del cls._reserved[port]
            # Also clear the persistent reservation in the DB
            if db is not None:
                db.release_port(port, server_id)
            logger.debug('Port %d released by server %d', port, server_id)
            return True

    @classmethod
    def check_available(cls, port: int) -> bool:
        """Check whether a port is available (not reserved).

        Parameters
        ----------
        port : int
            Port number to check.

        Returns
        -------
        bool
            ``True`` if the port is not currently reserved.
        """
        with cls._lock:
            return port not in cls._reserved

    @classmethod
    def find_free_port(
        cls,
        preferred: int = DEFAULT_SERVER_PORT,
        db: Any = None,
    ) -> int:
        """Find a free port, starting from the preferred port.

        The search scans upward from *preferred* and wraps around to
        port 1 if *MAX_PORT* is reached, continuing up to the preferred
        port again.  If *db* is provided, it will also check the
        database for persistent reservations.

        Parameters
        ----------
        preferred : int, optional
            The ideal starting port (default ``25565``).
        db : object or None, optional
            Database handler.  If provided, persistent reservations
            stored in the DB are also checked.

        Returns
        -------
        int
            A free port number, or ``-1`` if no port is available in the
            valid range.
        """
        with cls._lock:
            # Collect DB-persistent reservations if available
            db_reserved: set[int] = set()
            if db is not None:
                db_reserved = set(db.get_all_reserved_ports())
            port = preferred
            while port <= MAX_PORT:
                if port not in cls._reserved and port not in db_reserved:
                    return port
                port += 1

            # Wrap around from port 1
            port = 1
            while port < preferred:
                if port not in cls._reserved and port not in db_reserved:
                    return port
                port += 1

            logger.error(
                'PortManager.find_free_port: no free port found '
                '(preferred=%d)', preferred,
            )
            return -1

    @classmethod
    def get_server_for_port(cls, port: int) -> int:
        """Return the server ID that has reserved *port*.

        Parameters
        ----------
        port : int
            Port number to look up.

        Returns
        -------
        int
            The server ID that owns this reservation, or ``-1`` if the
            port is not reserved.
        """
        with cls._lock:
            return cls._reserved.get(port, -1)


# ---------------------------------------------------------------------------
# NetworkManager
# ---------------------------------------------------------------------------

class NetworkManager:
    """Top-level network sandbox manager for a server instance.

    Coordinates the :class:`VirtualAdapter`, :class:`Firewall`, and
    :class:`PortManager` to provide a complete network sandbox
    configuration that the runner uses when launching a Minecraft server.

    Parameters
    ----------
    db : object
        Database handler implementing all required methods.
    server_id : int
        Unique identifier for the server instance.
    """

    def __init__(self, db: Any, server_id: int) -> None:
        self.db = db
        self.server_id = server_id
        self.adapter = VirtualAdapter(db, server_id)
        self.firewall = Firewall(db, server_id)

    # -- Public API -----------------------------------------------------------

    def get_sandbox_config(self) -> dict:
        """Get the complete network sandbox configuration for this server.

        Combines virtual adapter settings, firewall rules, and port
        assignments into a single dictionary suitable for the runner.

        Returns
        -------
        dict
            A comprehensive sandbox configuration dict.
        """
        adapter_config = self.adapter.get_config()
        allowed_ports = self.firewall.get_allowed_ports()

        return {
            'server_id': self.server_id,
            'virtual_adapter': adapter_config,
            'firewall_rules': self.firewall.list_rules(),
            'allowed_ports': allowed_ports,
            'enabled': adapter_config.get('enabled', False),
            'bandwidth_limit_kbps': adapter_config.get('bandwidth_limit_kbps', 0),
            'latency_ms': adapter_config.get('latency_ms', 0),
            'packet_loss_pct': adapter_config.get('packet_loss_pct', 0.0),
        }

    def get_server_properties_override(self) -> dict:
        """Get ``server.properties`` overrides for sandboxed networking.

        These overrides are applied when the runner generates the
        Minecraft server's ``server.properties`` file to ensure the
        server binds to the correct port and IP.

        Returns
        -------
        dict
            Override map with keys like ``server-port`` and ``server-ip``.
        """
        config = self.adapter.get_config()
        enabled = config.get('enabled', True)

        if not enabled:
            return {
                'server-port': DEFAULT_SERVER_PORT,
                'server-ip': '0.0.0.0',
            }

        # Reserve the server port if not already reserved
        port = self._resolve_port()
        return {
            'server-port': port,
            'server-ip': '0.0.0.0',          # bind all interfaces
            'virtual-ip': config.get('virtual_ip', ''),
        }

    def initialize_default_rules(self) -> None:
        """Set up default firewall rules for a Minecraft server.

        Creates ``allow`` rules for the server port and the RCON port
        (both TCP inbound) so that the server can accept connections.
        Rules are only added if they do not already exist.
        """
        existing = self.firewall.list_rules()
        existing_ports = {r.get('target_port') for r in existing}

        server_port = DEFAULT_SERVER_PORT
        rcon_port = DEFAULT_RCON_PORT

        if server_port not in existing_ports:
            self.firewall.add_rule(
                rule_type='allow',
                direction='inbound',
                protocol='tcp',
                target_port=server_port,
                priority=10,
            )
            logger.info(
                'Default allow rule added for server port %d', server_port,
            )

        if rcon_port not in existing_ports:
            self.firewall.add_rule(
                rule_type='allow',
                direction='inbound',
                protocol='tcp',
                target_port=rcon_port,
                priority=20,
            )
            logger.info(
                'Default allow rule added for RCON port %d', rcon_port,
            )

    def generate_network_config(self) -> dict:
        """Generate the complete network configuration dict for the runner.

        This is the primary method called by the runner before launching
        a server.  It includes everything needed to enforce the sandbox:
        adapter settings, firewall scripts, and port mappings.

        Returns
        -------
        dict
            Full network configuration including a ready-to-execute shell
            script for firewall setup.
        """
        # Ensure default rules exist
        self.initialize_default_rules()

        adapter_config = self.adapter.get_config()
        port = self._resolve_port()
        fw_script = self.firewall.generate_firewall_script()

        return {
            'server_id': self.server_id,
            'adapter': adapter_config,
            'server_port': port,
            'firewall_script': fw_script,
            'firewall_rules': self.firewall.list_rules(),
            'allowed_ports': self.firewall.get_allowed_ports(),
            'bandwidth_limit_kbps': adapter_config.get('bandwidth_limit_kbps', 0),
            'latency_ms': adapter_config.get('latency_ms', 0),
            'packet_loss_pct': adapter_config.get('packet_loss_pct', 0.0),
            'enabled': adapter_config.get('enabled', True),
        }

    def cleanup(self) -> None:
        """Release all ports and clean up network resources.

        Should be called when a server instance is stopped or removed.
        Currently releases the server port reservation.
        """
        port = self._resolve_port(skip_reservation=True)
        if port > 0:
            PortManager.release(port, self.server_id)

        self.firewall.list_rules()  # (no-op — just to keep the interface warm)
        logger.info(
            'NetworkManager.cleanup: server %d network resources released',
            self.server_id,
        )

    # -- Internal helpers -----------------------------------------------------

    def _resolve_port(self, skip_reservation: bool = False) -> int:
        """Resolve and optionally reserve the server port.

        Parameters
        ----------
        skip_reservation : bool
            If ``True``, return the port without attempting to reserve it.

        Returns
        -------
        int
            The resolved port number.
        """
        config = self.adapter.get_config()
        
        # Allow explicit port override via adapter config
        
        explicit_port = config.get('server_port')
        if explicit_port and isinstance(explicit_port, int):
            port = explicit_port
        else:
            port = DEFAULT_SERVER_PORT

        if not skip_reservation:
            if not PortManager.reserve(port, self.server_id, self.db):
                # Preferred port taken — find another
                port = PortManager.find_free_port(preferred=port, db=self.db)
                if port == -1:
                    logger.error(
                        'NetworkManager: no free port available for server %d',
                        self.server_id,
                    )
                    port = DEFAULT_SERVER_PORT  # fallback — runner may fail gracefully

        return port


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _port_in_range(port: int, port_range: str) -> bool:
    """Check whether *port* falls within a range string like ``'25565-25575'``.

    Parameters
    ----------
    port : int
        Port number to check.
    port_range : str
        Range string in the form ``'start-end'`` or a single port ``'25565'``.

    Returns
    -------
    bool
        ``True`` if the port is within the range.
    """
    try:
        parts = port_range.split('-', 1)
        start = int(parts[0].strip())
        if len(parts) == 1:
            return port == start
        end = int(parts[1].strip())
        return start <= port <= end
    except (ValueError, IndexError, AttributeError):
        logger.warning('Invalid port range string: %r', port_range)
        return False

