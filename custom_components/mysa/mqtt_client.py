"""MQTT-over-WebSocket client for Mysa real-time device status subscriptions."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# MQTT 3.1.1 packet types (high nibble of fixed header byte)
_PTYPE_CONNACK = 2
_PTYPE_PUBLISH = 3
_PTYPE_SUBACK = 9
_PTYPE_PINGRESP = 13


class MysaMqttClient:
    """Persistent MQTT-over-WebSocket client for Mysa device status subscriptions.

    Connects to AWS IoT via a SigV4-signed WebSocket URL, subscribes to device
    output topics, and calls ``on_message`` for every incoming status message.

    Credential refresh is handled transparently: on each (re)connect the
    ``url_factory`` callable is awaited to obtain a freshly signed URL,
    which embeds current temporary credentials.
    """

    def __init__(
        self,
        url_factory: Callable[[], Awaitable[str]],
        on_message: Callable[[str, dict[str, Any]], None],
    ) -> None:
        """Initialize the client.

        Args:
            url_factory: Async callable that returns a fresh SigV4-signed WSS URL.
            on_message: Callback invoked as ``on_message(device_id, parsed_msg)``
                        for each received MQTT PUBLISH message.
        """
        self._url_factory = url_factory
        self._on_message = on_message
        self._topics: set[str] = set()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._packet_id: int = 0

    async def start(self) -> None:
        """Start the persistent MQTT connection background task."""
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._async_run(), name="mysa_mqtt")

    async def stop(self) -> None:
        """Stop the MQTT connection and release resources."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._ws and not self._ws.closed:
            try:
                await self._ws.send_bytes(b"\xe0\x00")  # MQTT DISCONNECT
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass

        if self._session and not self._session.closed:
            await self._session.close()

        self._ws = None
        self._session = None
        self._task = None

    async def subscribe(self, device_id: str) -> None:
        """Subscribe to real-time status updates for a device.

        Safe to call before or after ``start()``. If already connected, sends
        the SUBSCRIBE packet immediately; otherwise the topic is queued and
        subscribed on the next (re)connect.
        """
        topic = f"/v1/dev/{device_id}/out"
        if topic in self._topics:
            return
        self._topics.add(topic)

        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.send_bytes(self._build_subscribe([topic]))
                _LOGGER.debug("Mysa MQTT subscribed to %s", topic)
            except Exception:  # noqa: BLE001
                pass  # Will be subscribed on next reconnect

    # ------------------------------------------------------------------
    # Internal connection loop
    # ------------------------------------------------------------------

    async def _async_run(self) -> None:
        """Persistent reconnect loop with exponential back-off."""
        backoff = 1.0
        while True:
            try:
                url = await self._url_factory()
                assert self._session is not None  # noqa: S101
                async with self._session.ws_connect(
                    url,
                    protocols=("mqtt",),
                    ssl=True,
                    max_msg_size=0,
                    compress=0,
                ) as ws:
                    self._ws = ws
                    try:
                        await self._async_session(ws)
                    finally:
                        self._ws = None
                    backoff = 1.0  # reset on a session that ended cleanly

            except asyncio.CancelledError:
                return
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Mysa MQTT connection error: %s; reconnecting in %.0fs", err, backoff
                )
            finally:
                self._ws = None

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _async_session(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Manage one MQTT session: CONNECT → SUBSCRIBE → read loop."""
        client_id = f"mysa-ha-{uuid.uuid4().hex[:12]}"

        # --- CONNECT ---
        await ws.send_bytes(_build_connect(client_id, keep_alive=60))

        connack_msg = await asyncio.wait_for(ws.receive(), timeout=15.0)
        if connack_msg.type != aiohttp.WSMsgType.BINARY:
            raise ConnectionError(
                f"Mysa MQTT expected BINARY CONNACK, got {connack_msg.type}"
            )
        ptype, pdata = _parse_packet(connack_msg.data)
        if ptype != _PTYPE_CONNACK:
            raise ConnectionError(
                f"Mysa MQTT expected CONNACK packet type {_PTYPE_CONNACK}, got {ptype}"
            )
        if len(pdata) >= 2 and pdata[1] != 0:
            raise ConnectionError(
                f"Mysa MQTT CONNACK refused, return code={pdata[1]}"
            )
        _LOGGER.debug("Mysa MQTT connected (client_id=%s)", client_id)

        # --- SUBSCRIBE to all known topics ---
        if self._topics:
            await ws.send_bytes(self._build_subscribe(list(self._topics)))
            _LOGGER.debug("Mysa MQTT subscribed to %d topic(s)", len(self._topics))

        # --- Read loop ---
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=25.0)
            except asyncio.TimeoutError:
                # Send PINGREQ to keep the connection alive
                await ws.send_bytes(b"\xc0\x00")
                continue

            if msg.type == aiohttp.WSMsgType.BINARY:
                self._handle_packet(msg.data)
            elif msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            ):
                _LOGGER.debug("Mysa MQTT WebSocket closed (type=%s)", msg.type)
                break
            # PING, TEXT, etc. are silently ignored

    def _handle_packet(self, data: bytes) -> None:
        """Parse an incoming MQTT packet and dispatch to callbacks."""
        try:
            ptype, pdata = _parse_packet(data)
        except ValueError as err:
            _LOGGER.debug("Mysa MQTT malformed packet: %s", err)
            return

        if ptype == _PTYPE_PUBLISH:
            flags = data[0] & 0x0F
            qos = (flags >> 1) & 0x3
            try:
                topic, payload_bytes = _parse_publish(pdata, qos)
            except (ValueError, IndexError) as err:
                _LOGGER.debug("Mysa MQTT malformed PUBLISH: %s", err)
                return

            device_id = _device_id_from_topic(topic)
            if device_id:
                try:
                    msg = json.loads(payload_bytes)
                    self._on_message(device_id, msg)
                except (json.JSONDecodeError, ValueError) as err:
                    _LOGGER.debug(
                        "Mysa MQTT JSON decode error for device %s: %s", device_id, err
                    )

        elif ptype == _PTYPE_SUBACK:
            _LOGGER.debug("Mysa MQTT SUBACK received")

        elif ptype == _PTYPE_PINGRESP:
            pass  # keepalive acknowledged, nothing to do

    def _build_subscribe(self, topics: list[str]) -> bytes:
        """Build a MQTT SUBSCRIBE packet for the given topics at QoS 0."""
        self._packet_id = (self._packet_id % 65535) + 1
        payload = self._packet_id.to_bytes(2, "big")
        for topic in topics:
            enc = topic.encode()
            payload += len(enc).to_bytes(2, "big") + enc + b"\x00"  # QoS 0
        return b"\x82" + _encode_remaining_length(len(payload)) + payload


# ------------------------------------------------------------------
# MQTT 3.1.1 packet building helpers
# ------------------------------------------------------------------


def _build_connect(client_id: str, keep_alive: int = 60) -> bytes:
    """Build a MQTT 3.1.1 CONNECT packet (clean session, no will/auth)."""
    cid = client_id.encode()
    var_header = (
        b"\x00\x04MQTT"  # protocol name length + "MQTT"
        b"\x04"  # protocol level = 3.1.1
        b"\x02"  # connect flags: clean session only
        + keep_alive.to_bytes(2, "big")
    )
    payload = len(cid).to_bytes(2, "big") + cid
    remaining = var_header + payload
    return b"\x10" + _encode_remaining_length(len(remaining)) + remaining


def _encode_remaining_length(n: int) -> bytes:
    """Encode the MQTT variable-length remaining-length field."""
    result = bytearray()
    while True:
        byte = n % 128
        n //= 128
        if n:
            byte |= 0x80
        result.append(byte)
        if not n:
            break
    return bytes(result)


# ------------------------------------------------------------------
# MQTT 3.1.1 packet parsing helpers
# ------------------------------------------------------------------


def _decode_remaining_length(data: bytes, offset: int) -> tuple[int, int]:
    """Decode MQTT remaining length starting at ``offset``.

    Returns ``(value, new_offset)`` where ``new_offset`` points to the
    first byte after the remaining-length field.
    """
    multiplier = 1
    value = 0
    while True:
        if offset >= len(data):
            raise ValueError("Truncated MQTT remaining-length field")
        byte = data[offset]
        offset += 1
        value += (byte & 0x7F) * multiplier
        multiplier *= 128
        if not (byte & 0x80):
            break
    return value, offset


def _parse_packet(data: bytes) -> tuple[int, bytes]:
    """Parse an MQTT fixed header.

    Returns ``(packet_type, remaining_bytes)`` where ``packet_type`` is the
    high nibble of the first byte.
    """
    if not data:
        raise ValueError("Empty MQTT packet")
    packet_type = (data[0] >> 4) & 0xF
    remaining_len, offset = _decode_remaining_length(data, 1)
    payload = data[offset : offset + remaining_len]
    return packet_type, payload


def _parse_publish(remaining: bytes, qos: int) -> tuple[str, bytes]:
    """Parse the remaining bytes of a MQTT PUBLISH packet.

    Returns ``(topic, payload_bytes)``.
    """
    if len(remaining) < 2:
        raise ValueError("PUBLISH packet too short")
    topic_len = (remaining[0] << 8) | remaining[1]
    topic = remaining[2 : 2 + topic_len].decode("utf-8")
    body_start = 2 + topic_len
    if qos > 0:
        body_start += 2  # skip 2-byte packet identifier
    return topic, remaining[body_start:]


def _device_id_from_topic(topic: str) -> str | None:
    """Extract a device ID from a topic of the form ``/v1/dev/{id}/out``."""
    parts = topic.split("/")
    # Expected split: ['', 'v1', 'dev', '{device_id}', 'out']
    if (
        len(parts) == 5
        and parts[1] == "v1"
        and parts[2] == "dev"
        and parts[4] == "out"
    ):
        return parts[3]
    return None
