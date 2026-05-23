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
    port: int = 4532               # hamlib default rigctld port


@dataclass
class WaterfallConfig:
    """Waterfall display settings."""
    source: str = "audio"          # audio | cat
    audio_device: str | None = None
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
