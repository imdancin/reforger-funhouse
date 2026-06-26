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
_PAYLOAD_PREFIX = b"\xff"  # All BERCon packets have 0xFF after the CRC


def _checksum(payload: bytes) -> bytes:
    """Compute the CRC32 checksum for BERCon (little-endian, unsigned).

    The checksum covers the 0xFF prefix byte plus the type-specific payload.
    """
    crc = zlib.crc32(_PAYLOAD_PREFIX + payload) & 0xFFFFFFFF
    return struct.pack("<I", crc)


def _build_packet(payload: bytes) -> bytes:
    """Build a full BERCon packet: 'BE' + CRC32(0xFF + payload) + 0xFF + payload."""
    return _HEADER + _checksum(payload) + _PAYLOAD_PREFIX + payload


def _build_login_packet(password: str) -> bytes:
    """Build a login request packet (type 0x00 + password)."""
    payload = b"\x00" + password.encode("utf-8")
    return _build_packet(payload)


def _build_command_packet(seq: int, command: str) -> bytes:
    """Build a command request packet with a sequence number (type 0x01 + seq + command)."""
    payload = b"\x01" + struct.pack("B", seq & 0xFF) + command.encode("utf-8")
    return _build_packet(payload)


def _validate_response(data: bytes) -> bytes:
    """Validate a BERCon response and return the payload (after 0xFF prefix).

    Packet format: 'BE' (2) + CRC32 (4) + 0xFF (1) + payload
    The CRC covers 0xFF + payload.

    Raises RconError if the packet is malformed or the checksum is invalid.
    """
    if len(data) < 7:
        raise RconError(f"Response too short: {len(data)} bytes")
    if data[:2] != _HEADER:
        raise RconError("Invalid BERCon header")
    received_crc = data[2:6]
    # Everything after the CRC (0xFF + payload)
    suffix = data[6:]
    if not suffix or suffix[0:1] != _PAYLOAD_PREFIX:
        raise RconError("Missing 0xFF prefix in BERCon response")
    # Payload is everything after the 0xFF
    payload = suffix[1:]
    # CRC covers 0xFF + payload
    expected_crc = _checksum(payload)
    if received_crc != expected_crc:
        raise RconError("Checksum mismatch in RCON response")
    return payload


def _parse_login_response(payload: bytes) -> bool:
    """Parse a login response payload. Returns True if login succeeded.

    Payload format: 0x00 (login type) + 0x01 (success) or 0x00 (failure).
    """
    if len(payload) < 2:
        return False
    return payload[0] == 0x00 and payload[1] == 0x01


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
        # Command ack: type 0x01 + seq + optional header + response body
        # Server message: type 0x02 + seq + message (needs ack from client)
        response_parts: list[str] = []
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                break

            payload = _validate_response(data)
            if len(payload) < 2:
                continue

            # Command response (ack): type 0x01, seq byte, then optional body
            if payload[0] == 0x01:
                if len(payload) <= 2:
                    # Empty ack (no response body) — command acknowledged
                    continue
                # Check for multi-part header: 0x00 + num_packets + index
                body_start = 2
                if len(payload) > 4 and payload[2] == 0x00:
                    # Multi-part: skip the 3-byte sub-header
                    body_start = 5
                body = payload[body_start:].decode("utf-8", errors="replace")
                response_parts.append(body)
                # If we got a response with the total line, we're done
                if "players in total" in body or "Players on server" in body:
                    # Give a brief window for any trailing packets
                    sock.settimeout(0.5)

            # Server message: type 0x02, seq byte, then message
            # We need to acknowledge these to stay connected
            elif payload[0] == 0x02 and len(payload) >= 2:
                seq_byte = payload[1:2]
                # Send ack: type 0x02 + received seq
                ack_payload = b"\x02" + seq_byte
                ack_packet = _build_packet(ack_payload)
                sock.sendto(ack_packet, (host, port))

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
