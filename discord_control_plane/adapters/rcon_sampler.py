"""RCON player-count sampler for the Arma Reforger BattlEye RCON protocol.

Implements the BattlEye RCON (BERCon) UDP protocol to query the connected
player count from the game server.

BERCon packet structure:
  Header: 'B' 'E' (2 bytes)
  Checksum: CRC32 of payload (4 bytes, little-endian)
  Payload: type-dependent bytes

Packet types (first byte of payload):
  0xFF - Login (client -> server: password; server -> client: 0x01 success / 0x00 fail)
  0x01 - Command (client -> server: seq + command)
  0x02 - Command response (server -> client: seq + body)
"""

from __future__ import annotations

import os
import re
import socket
import struct
import zlib


class RconError(Exception):
    """Raised when the RCON query fails."""

    pass


# ---------------------------------------------------------------------------
# BattlEye RCON packet helpers
# ---------------------------------------------------------------------------

_HEADER = b"BE"


def _checksum(payload: bytes) -> bytes:
    """Compute the CRC32 checksum for BERCon (little-endian, unsigned)."""
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return struct.pack("<I", crc)


def _build_packet(payload: bytes) -> bytes:
    """Build a full BERCon packet: header + checksum + payload."""
    return _HEADER + _checksum(payload) + payload


def _build_login_packet(password: str) -> bytes:
    """Build a login request packet."""
    payload = b"\xff" + password.encode("utf-8")
    return _build_packet(payload)


def _build_command_packet(seq: int, command: str) -> bytes:
    """Build a command request packet with a sequence number."""
    payload = b"\x01" + struct.pack("B", seq & 0xFF) + command.encode("utf-8")
    return _build_packet(payload)


def _validate_response(data: bytes) -> bytes:
    """Validate a BERCon response and return the payload.

    Raises RconError if the packet is malformed or the checksum is invalid.
    """
    if len(data) < 7:
        raise RconError(f"Response too short: {len(data)} bytes")
    if data[:2] != _HEADER:
        raise RconError("Invalid BERCon header")
    received_crc = data[2:6]
    payload = data[6:]
    expected_crc = _checksum(payload)
    if received_crc != expected_crc:
        raise RconError("Checksum mismatch in RCON response")
    return payload


def _parse_login_response(payload: bytes) -> bool:
    """Parse a login response payload. Returns True if login succeeded."""
    if len(payload) < 2:
        return False
    # payload[0] == 0xFF (login type), payload[1] == 0x01 success / 0x00 failure
    return payload[0] == 0xFF and payload[1] == 0x01


def _parse_player_count(response_text: str) -> int:
    """Parse the player count from a BERCon 'players' command response.

    The response typically looks like:
        Players on server:
        [#] [IP Address]:[Port] [Ping] [GUID] [Name]
        ------------------------------------------
        0   123.45.67.89:2304  42  <guid> PlayerName
        1   98.76.54.32:2304   31  <guid> AnotherPlayer
        (2 players in total)

    We look for the "(N players in total)" line first, then fall back to
    counting individual player lines.
    """
    # Strategy 1: Look for "(N players in total)" pattern
    total_match = re.search(r"\((\d+)\s+players?\s+in\s+total\)", response_text)
    if total_match:
        return int(total_match.group(1))

    # Strategy 2: Count lines that look like player entries (start with a number
    # followed by whitespace and an IP:port pattern)
    player_lines = re.findall(
        r"^\s*\d+\s+\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+",
        response_text,
        re.MULTILINE,
    )
    return len(player_lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def sample_player_count(
    host: str = "127.0.0.1",
    port: int = 1999,
    password: str | None = None,
    timeout: float = 5.0,
) -> int:
    """Query the RCON port for connected player count.

    Connects to the BattlEye RCON server via UDP, authenticates, sends the
    'players' command, and parses the response to extract the player count.

    Args:
        host: RCON server address (default: 127.0.0.1 for local server).
        port: RCON port (default: 1999 as configured in the deployment).
        password: RCON password. If None, reads from RCON_PASSWORD env var.
        timeout: Socket timeout in seconds.

    Returns:
        The number of connected players (>= 0).

    Raises:
        RconError: On connection failure, authentication failure, or parse error.
    """
    if password is None:
        password = os.environ.get("RCON_PASSWORD", "")

    if not password:
        raise RconError(
            "RCON password not provided and RCON_PASSWORD environment variable is not set"
        )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    try:
        # Step 1: Send login packet
        login_packet = _build_login_packet(password)
        sock.sendto(login_packet, (host, port))

        # Step 2: Receive login response
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            raise RconError(
                f"Timeout waiting for login response from {host}:{port}"
            )

        payload = _validate_response(data)
        if not _parse_login_response(payload):
            raise RconError("RCON authentication failed")

        # Step 3: Send 'players' command
        command_packet = _build_command_packet(0, "players")
        sock.sendto(command_packet, (host, port))

        # Step 4: Receive command response(s)
        # BERCon may split responses across multiple packets; collect until timeout
        response_parts: list[str] = []
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                break

            payload = _validate_response(data)
            if len(payload) < 3:
                continue

            # Command response: type 0x02, seq byte, then body
            if payload[0] == 0x02:
                # Skip type byte and sequence byte
                body = payload[2:].decode("utf-8", errors="replace")
                response_parts.append(body)
                # If we got a response with the total line, we're done
                if "players in total" in body or "Players on server" in body:
                    # Give a brief window for any trailing packets
                    sock.settimeout(0.5)

        if not response_parts:
            raise RconError("No response received for 'players' command")

        full_response = "".join(response_parts)
        return _parse_player_count(full_response)

    except socket.error as e:
        if isinstance(e, socket.timeout):
            raise RconError(f"Connection timed out to {host}:{port}") from e
        raise RconError(f"Socket error communicating with {host}:{port}: {e}") from e
    finally:
        sock.close()
