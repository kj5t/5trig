"""
5trig remote server — shack-side component.

RemoteServer attaches to an already-connected CATRadio and an optional
AudioScopeSource (for spectrum data).  It accepts TCP connections from
remote 5trig clients and:

  • pushes RadioState updates as {"t":"state"} frames
  • pushes FFT spectrum data as {"t":"scope"} frames
  • executes CAT/PTT/CW commands received from clients

The server is started by the shack-side 5trig app when the user enables
the "Share 📡" toggle.  It does NOT manage radio connection itself — the
main window owns the radio object.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING

import numpy as np

from .audio import AudioUDPServer
from .protocol import PROTOCOL_VERSION, configure_keepalive, encode_array, recv_msg, send_msg
from .txaudio import TxAudioReceiver, TxGate

if TYPE_CHECKING:
    from ..cat.base import CATRadio

logger = logging.getLogger(__name__)


class _PaddleUDP(asyncio.DatagramProtocol):
    """Receive single-byte paddle datagrams: bit 0 = dit, bit 1 = dah."""

    def __init__(self, winkeyer) -> None:
        self._winkeyer = winkeyer

    def datagram_received(self, data: bytes, addr) -> None:
        if len(data) >= 1 and self._winkeyer and self._winkeyer.is_open:
            b = data[0]
            self._winkeyer.paddle(bool(b & 0x01), bool(b & 0x02))


class RemoteServer:
    """Broadcasts radio state / spectrum to connected remote clients.

    Parameters
    ----------
    radio:
        The already-connected local CATRadio instance.
    host, port:
        TCP bind address.  Default ``0.0.0.0:5040`` accepts connections on
        all interfaces; use ``127.0.0.1`` to restrict to loopback only.
    """

    # A client that has sent a heartbeat ping must keep talking; silence for this long
    # means its network is gone (WiFi switch, sleep) and the connection is dropped.
    # Clients that never ping (older 5trig) are not timed out.
    CLIENT_IDLE_TIMEOUT = 15.0
    # A write that takes longer than this marks the client dead, so one stuck client
    # cannot stall state updates to the others.
    SEND_TIMEOUT = 2.0

    def __init__(
        self,
        radio: "CATRadio",
        host: str = "0.0.0.0",
        port: int = 5040,
        audio_port: int | None = 5041,
        audio_bitrate: int = 64_000,
        sample_rate: int = 48000,
        winkeyer=None,
        paddle_udp_port: int = 5042,
        tx_audio_port: int | None = None,
        tx_audio_sink: str = "",
    ) -> None:
        self._radio = radio
        self._loop = asyncio.get_event_loop()
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None
        self._winkeyer = winkeyer
        self._original_wk_callback = None
        self._original_wk_busy_callback = None
        self._paddle_udp_port = paddle_udp_port
        self._paddle_transport: asyncio.DatagramTransport | None = None
        # writers of currently connected clients
        self._clients: set[asyncio.StreamWriter] = set()
        # True between a client's "PTT on" and "PTT off"; gates remote TX audio
        self._ptt_requested = False

        self._tx_receiver: TxAudioReceiver | None = None
        if tx_audio_port is not None and tx_audio_sink:
            self._tx_receiver = TxAudioReceiver(
                host=host,
                port=tx_audio_port,
                sink=tx_audio_sink,
                allowed_ips=self._client_ips,
                gate=TxGate(lambda: self._ptt_requested or self._radio.state.ptt),
                sample_rate=sample_rate,
            )

        self._audio_server: AudioUDPServer | None = None
        if audio_port is not None:
            self._audio_server = AudioUDPServer(
                host=host,
                port=audio_port,
                sample_rate=sample_rate,
                bitrate=audio_bitrate,
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start listening for remote client connections."""
        self._server = await asyncio.start_server(
            self._handle_client,
            self._host,
            self._port,
            reuse_address=True,
        )
        if self._audio_server:
            self._audio_server.start()
        if self._tx_receiver:
            self._tx_receiver.start()
        if self._winkeyer and self._winkeyer.is_open:
            self._original_wk_callback = self._winkeyer._key_callback
            self._winkeyer.set_key_callback(self._on_wk_key)
            self._original_wk_busy_callback = self._winkeyer._busy_callback
            self._winkeyer.set_busy_callback(self._on_wk_busy)
            transport, _ = await asyncio.get_event_loop().create_datagram_endpoint(
                lambda: _PaddleUDP(self._winkeyer),
                local_addr=(self._host, self._paddle_udp_port),
            )
            self._paddle_transport = transport
        logger.info(
            "Remote server listening on %s:%d (audio UDP: %s, paddle UDP: %s)",
            self._host,
            self._port,
            self._audio_server.port if self._audio_server else "disabled",
            self._paddle_udp_port if self._paddle_transport else "disabled",
        )

    async def stop(self) -> None:
        """Close the server and disconnect all clients."""
        if self._paddle_transport:
            self._paddle_transport.close()
            self._paddle_transport = None
        if self._audio_server:
            self._audio_server.stop()
        if self._tx_receiver:
            self._tx_receiver.stop()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in list(self._clients):
            try:
                writer.close()
            except Exception:
                pass
        self._clients.clear()
        logger.info("Remote server stopped")

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def tx_audio_error(self) -> str | None:
        """Why remote transmit audio is unavailable (None if fine or not configured)."""
        return self._tx_receiver.error if self._tx_receiver else None

    def _client_ips(self) -> set[str]:
        ips: set[str] = set()
        for writer in list(self._clients):
            peer = writer.get_extra_info("peername")
            if peer:
                ips.add(peer[0])
        return ips

    # ------------------------------------------------------------------
    # Callbacks wired by the shack-side main window
    # ------------------------------------------------------------------

    def on_pcm(self, samples: np.ndarray) -> None:
        """Forward raw PCM to the UDP audio server.  Called from audio thread."""
        if self._audio_server:
            self._audio_server.on_pcm(samples)

    def on_scope_data(self, db_array: np.ndarray) -> None:
        """Called from the AudioScopeSource callback thread.

        Schedules a scope frame to be sent to all connected clients.
        Must NOT call asyncio coroutines directly — uses call_soon_threadsafe.
        """
        if not self._clients:
            return
        msg = {"t": "scope", "d": encode_array(db_array)}
        try:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._broadcast(msg))
            )
        except RuntimeError:
            pass

    async def on_state_changed(self, state) -> None:
        """Called (from main asyncio loop) when the radio state changes."""
        msg = {"t": "state", "s": self._state_to_dict(state)}
        await self._broadcast(msg)

    # ------------------------------------------------------------------
    # Client handler
    # ------------------------------------------------------------------

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        addr = writer.get_extra_info("peername")
        logger.info("Remote client connected: %s", addr)
        self._clients.add(writer)

        sock = writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        configure_keepalive(sock)

        # Greet the client with radio model info and optional audio UDP port
        hello: dict = {
            "t": "hello",
            "model": self._radio.state.model,
            "ver": PROTOCOL_VERSION,
            "heartbeat": True,      # we answer {"t":"ping"} with {"t":"pong"}
        }
        if self._audio_server:
            hello["audio_udp_port"] = self._audio_server.port
        if self._tx_receiver and self._tx_receiver.running:
            hello["tx_audio_udp_port"] = self._tx_receiver.port
        if self._winkeyer and self._winkeyer.is_open:
            hello["winkeyer"] = True
            if self._paddle_transport:
                hello["paddle_udp_port"] = self._paddle_udp_port
        try:
            await send_msg(writer, hello)
            # Send current state immediately so the client shows real data
            await send_msg(writer, {
                "t": "state",
                "s": self._state_to_dict(self._radio.state),
            })
        except Exception as exc:
            logger.warning("Failed to greet client %s: %s", addr, exc)
            self._clients.discard(writer)
            writer.close()
            return

        heartbeat = False
        try:
            while True:
                timeout = self.CLIENT_IDLE_TIMEOUT if heartbeat else None
                msg = await asyncio.wait_for(recv_msg(reader), timeout)
                if msg.get("t") == "ping":
                    heartbeat = True
                    await asyncio.wait_for(
                        send_msg(writer, {"t": "pong", "n": msg.get("n")}), self.SEND_TIMEOUT
                    )
                    continue
                await self._dispatch(msg)
        except (ConnectionResetError, asyncio.IncompleteReadError, EOFError):
            pass
        except asyncio.TimeoutError:
            logger.warning("Client %s went silent; dropping the connection", addr)
            self._abort(writer)
        except Exception as exc:
            logger.warning("Client %s error: %s", addr, exc)
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
            except Exception:
                pass
            logger.info("Remote client disconnected: %s", addr)
            if not self._clients and self._ptt_requested:
                # Nobody is left to unkey the radio.
                logger.warning("Last client left while transmitting; releasing PTT")
                self._ptt_requested = False
                self._radio.set_ptt(False)

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, msg: dict) -> None:
        t = msg.get("t")

        if t == "set_freq":
            from ..cat.base import VFO
            vfo = VFO.A if msg.get("vfo", "A") == "A" else VFO.B
            hz = int(msg["hz"])
            self._radio.set_freq(hz, vfo)

        elif t == "set_mode":
            from ..cat.base import Mode
            self._radio.set_mode(Mode.from_code(str(msg["code"])))

        elif t == "set_ptt":
            self._ptt_requested = bool(msg["on"])
            self._radio.set_ptt(self._ptt_requested)

        elif t == "cw_key":
            down = bool(msg["down"])
            if self._winkeyer and self._winkeyer.is_open:
                self._winkeyer.key_immediate(down)
            else:
                self._radio.cw_key(down)
                # Without a WinKeyer there is no key callback to relay the
                # state back, so send cw_key_state ourselves so remote
                # clients can play sidetone.
                await self._broadcast({"t": "cw_key_state", "down": down})

        elif t == "paddle":
            if self._winkeyer and self._winkeyer.is_open:
                self._winkeyer.paddle(bool(msg.get("dit")), bool(msg.get("dah")))

        elif t == "cw_text":
            if self._winkeyer and self._winkeyer.is_open:
                self._winkeyer.clear_buffer()
                self._winkeyer.send_text(str(msg["text"]))

        elif t == "cw_char":
            if self._winkeyer and self._winkeyer.is_open:
                self._winkeyer.send_text(str(msg["text"]))

        elif t == "cw_stop":
            if self._winkeyer and self._winkeyer.is_open:
                self._winkeyer.clear_buffer()

        elif t == "cat_raw":
            # Arbitrary CAT command string, e.g. "SV" to swap VFOs.
            from ..cat.base import CATCommand
            raw = str(msg["cmd"]).upper()
            if not raw.endswith(";"):
                raw += ";"
            self._radio.enqueue(CATCommand(raw=raw.encode(), reply_len=0))

        else:
            logger.debug("Unknown remote command: %r", t)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_wk_key(self, down: bool) -> None:
        """Called from the WinKeyer status thread on key state change."""
        # Chain the original callback (e.g. local sidetone) so sharing
        # doesn't silence the shack operator.
        if self._original_wk_callback:
            try:
                self._original_wk_callback(down)
            except Exception:
                pass
        if not self._clients:
            return
        msg = {"t": "cw_key_state", "down": down}
        try:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._broadcast(msg))
            )
        except RuntimeError:
            pass

    def _on_wk_busy(self, busy: bool) -> None:
        """Called from WinKeyer status thread on buffer busy/idle.

        Chains the original busy callback (e.g. local sidetone) and
        broadcasts as cw_key_state so remote clients see the indicator
        during macro text playback when breakin bytes may not fire.
        """
        if self._original_wk_busy_callback:
            try:
                self._original_wk_busy_callback(busy)
            except Exception:
                pass
        if not self._clients:
            return
        msg = {"t": "cw_key_state", "down": busy}
        try:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._broadcast(msg))
            )
        except RuntimeError:
            pass

    async def _broadcast(self, msg: dict) -> None:
        """Send *msg* to all connected clients; remove dead ones."""
        dead: set[asyncio.StreamWriter] = set()
        for writer in list(self._clients):
            try:
                await asyncio.wait_for(send_msg(writer, msg), self.SEND_TIMEOUT)
            except Exception:
                dead.add(writer)
                self._abort(writer)     # ends that client's handler, which does the cleanup
        self._clients -= dead

    @staticmethod
    def _abort(writer: asyncio.StreamWriter) -> None:
        """Drop a connection immediately, without waiting for a peer that may be gone."""
        try:
            writer.transport.abort()
        except Exception:
            pass

    @staticmethod
    def _state_to_dict(state) -> dict:
        return {
            "vfo_a": state.vfo_a_hz,
            "vfo_b": state.vfo_b_hz,
            "active_vfo": state.active_vfo.value,
            "mode": state.mode.value,
            "split": state.split,
            "rit_on": state.rit_on,
            "rit_hz": state.rit_hz,
            "xit_on": state.xit_on,
            "ptt": state.ptt,
            "meters": {
                k.value: v for k, v in state.meter_values.items()
            },
            "model": state.model,
        }
