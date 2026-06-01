"""
Application settings.

Config file location follows the XDG Base Directory Specification:
  ~/.config/5trig/config.toml   (Linux)
  ~/Library/Application Support/5trig/config.toml  (macOS)
  %APPDATA%\\5trig\\config.toml  (Windows)
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomli_w
from platformdirs import user_config_dir

APP_NAME = "5trig"
APP_AUTHOR = "5tmuzak"


def _config_path() -> Path:
    config_dir = Path(user_config_dir(APP_NAME, APP_AUTHOR))
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.toml"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RadioConfig:
    """Per-radio connection settings."""
    model: str = "FT-710"          # FT-710 | FTdx-10 | FTdx-101
    port: str = "/dev/ttyUSB0"
    baud: int = 38400
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 2
    poll_interval: float = 0.1     # seconds


@dataclass
class VCATConfig:
    """Virtual CAT server settings."""
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 4535               # avoid clashing with rigctld on 4532


@dataclass
class WaterfallConfig:
    """Waterfall display settings."""
    source: str = "audio"          # audio | cat
    audio_device: str | None = None
    # When set, audio is captured via `pw-record --target=<pw_node>` instead of
    # sounddevice.  This avoids the exclusive ALSA lock that sounddevice takes
    # when opening hw:X,0 directly, which would evict PipeWire and silence the
    # monitoring path.  Set to the PipeWire node name (e.g. the
    # alsa_input.usb-... name shown by `pactl list sources short`).
    # Leave empty to use sounddevice (may break speaker monitoring on Linux).
    pw_node: str = ""
    sample_rate: int = 48000
    fft_size: int = 4096
    db_min: float = -120.0
    db_max: float = -60.0
    color_map: str = "plasma"      # plasma | inferno | turbo | grayscale


@dataclass
class LoggingConfig:
    """QSO log settings."""
    log_dir: str = str(Path.home() / "Documents" / "5trig")
    adif_file: str = "qso_log.adi"
    udp_enabled: bool = False
    udp_host: str = "127.0.0.1"
    udp_port: int = 2333           # N1MM+/WSJT-X log port
    lotw_user: str = ""
    qrz_user: str = ""
    clublog_callsign: str = ""


@dataclass
class ClusterConfig:
    """DX cluster connection."""
    host: str = "dxc.nc7j.com"
    port: int = 7373
    callsign: str = ""
    auto_connect: bool = False


@dataclass
class KeyerConfig:
    """CW keyer settings."""
    enabled: bool = False
    midi_port: str = ""        # empty = auto-select first non-passthrough port
    dit_note: int = 36         # MIDI note for dit paddle  (C2, Vail/WinKeyer default)
    dah_note: int = 37         # MIDI note for dah paddle  (C#2)
    mode: str = "iambic_a"     # straight | iambic_a | iambic_b
    wpm: int = 20              # words per minute


@dataclass
class RemoteConfig:
    """Remote operation settings.

    client_enabled:
        When True, 5trig connects to a remote shack server instead of a
        local serial port.  The radio model shown in Settings is ignored;
        the actual model is reported by the server on connect.
    server_host:
        IP address or hostname of the shack machine (client mode only).
    server_port:
        TCP port for both client connections and the server listener.
    share_enabled:
        When True, the shack-side 5trig automatically starts the remote
        server when the radio connects (equivalent to clicking Share 📡).
    share_host:
        Bind address for the server.  ``0.0.0.0`` = all interfaces.
        Use ``127.0.0.1`` to restrict to loopback only.
    """
    client_enabled: bool = False
    server_host: str = ""
    server_port: int = 5040
    share_enabled: bool = False
    share_host: str = "0.0.0.0"
    audio_stream: bool = True       # stream audio over UDP when sharing
    audio_udp_port: int = 5041      # UDP port for Opus audio (separate from TCP control)
    audio_bitrate: int = 64_000     # Opus encoder bitrate in bps


@dataclass
class AppConfig:
    """Top-level application configuration."""
    callsign: str = ""
    grid_locator: str = ""
    country: str = ""
    theme: str = "dark"            # dark | light
    radio: RadioConfig = field(default_factory=RadioConfig)
    vcat: VCATConfig = field(default_factory=VCATConfig)
    waterfall: WaterfallConfig = field(default_factory=WaterfallConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    remote: RemoteConfig = field(default_factory=RemoteConfig)
    keyer: KeyerConfig = field(default_factory=KeyerConfig)


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def _dict_to_config(d: dict) -> AppConfig:
    cfg = AppConfig()
    cfg.callsign = d.get("callsign", cfg.callsign)
    cfg.grid_locator = d.get("grid_locator", cfg.grid_locator)
    cfg.country = d.get("country", cfg.country)
    cfg.theme = d.get("theme", cfg.theme)

    if "radio" in d:
        r = d["radio"]
        cfg.radio = RadioConfig(
            model=r.get("model", cfg.radio.model),
            port=r.get("port", cfg.radio.port),
            baud=r.get("baud", cfg.radio.baud),
            bytesize=r.get("bytesize", cfg.radio.bytesize),
            parity=r.get("parity", cfg.radio.parity),
            stopbits=r.get("stopbits", cfg.radio.stopbits),
            poll_interval=r.get("poll_interval", cfg.radio.poll_interval),
        )

    if "vcat" in d:
        v = d["vcat"]
        cfg.vcat = VCATConfig(
            enabled=v.get("enabled", cfg.vcat.enabled),
            host=v.get("host", cfg.vcat.host),
            port=v.get("port", cfg.vcat.port),
        )

    if "waterfall" in d:
        w = d["waterfall"]
        cfg.waterfall = WaterfallConfig(
            source=w.get("source", cfg.waterfall.source),
            audio_device=w.get("audio_device", cfg.waterfall.audio_device),
            pw_node=w.get("pw_node", cfg.waterfall.pw_node),
            sample_rate=w.get("sample_rate", cfg.waterfall.sample_rate),
            fft_size=w.get("fft_size", cfg.waterfall.fft_size),
            db_min=w.get("db_min", cfg.waterfall.db_min),
            db_max=w.get("db_max", cfg.waterfall.db_max),
            color_map=w.get("color_map", cfg.waterfall.color_map),
        )

    if "logging" in d:
        lg = d["logging"]
        cfg.logging = LoggingConfig(
            log_dir=lg.get("log_dir", cfg.logging.log_dir),
            adif_file=lg.get("adif_file", cfg.logging.adif_file),
            udp_enabled=lg.get("udp_enabled", cfg.logging.udp_enabled),
            udp_host=lg.get("udp_host", cfg.logging.udp_host),
            udp_port=lg.get("udp_port", cfg.logging.udp_port),
            lotw_user=lg.get("lotw_user", cfg.logging.lotw_user),
            qrz_user=lg.get("qrz_user", cfg.logging.qrz_user),
            clublog_callsign=lg.get("clublog_callsign", cfg.logging.clublog_callsign),
        )

    if "cluster" in d:
        cl = d["cluster"]
        cfg.cluster = ClusterConfig(
            host=cl.get("host", cfg.cluster.host),
            port=cl.get("port", cfg.cluster.port),
            callsign=cl.get("callsign", cfg.cluster.callsign),
            auto_connect=cl.get("auto_connect", cfg.cluster.auto_connect),
        )

    if "keyer" in d:
        ky = d["keyer"]
        cfg.keyer = KeyerConfig(
            enabled=ky.get("enabled", cfg.keyer.enabled),
            midi_port=ky.get("midi_port", cfg.keyer.midi_port),
            dit_note=ky.get("dit_note", cfg.keyer.dit_note),
            dah_note=ky.get("dah_note", cfg.keyer.dah_note),
            mode=ky.get("mode", cfg.keyer.mode),
            wpm=ky.get("wpm", cfg.keyer.wpm),
        )

    if "remote" in d:
        rm = d["remote"]
        cfg.remote = RemoteConfig(
            client_enabled=rm.get("client_enabled", cfg.remote.client_enabled),
            server_host=rm.get("server_host", cfg.remote.server_host),
            server_port=rm.get("server_port", cfg.remote.server_port),
            share_enabled=rm.get("share_enabled", cfg.remote.share_enabled),
            share_host=rm.get("share_host", cfg.remote.share_host),
            audio_stream=rm.get("audio_stream", cfg.remote.audio_stream),
            audio_udp_port=rm.get("audio_udp_port", cfg.remote.audio_udp_port),
            audio_bitrate=rm.get("audio_bitrate", cfg.remote.audio_bitrate),
        )

    return cfg


def load_config() -> AppConfig:
    path = _config_path()
    if not path.exists():
        return AppConfig()
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return _dict_to_config(raw)


def save_config(cfg: AppConfig) -> None:
    path = _config_path()
    d = asdict(cfg)
    with path.open("wb") as f:
        tomli_w.dump(d, f)
