# 5trig

**Cross-platform Yaesu transceiver control**  
Python · PySide6 · asyncio · pyqtgraph

Primary target: Arch-based Linux (Manjaro, EndeavourOS, Arch). Also runs on macOS and Windows.

---

## Supported Radios

| Radio | CAT Port | Scope Source |
|---|---|---|
| Yaesu FT-710 AESS | USB-B → `/dev/ttyUSB0` | Audio FFT (USB audio) |
| Yaesu FTdx-10 | USB-B → `/dev/ttyUSB0` | CAT band scope + Audio FFT |
| Yaesu FTdx-101D / MP | USB-B → `/dev/ttyUSB0` | CAT band scope + Audio FFT |

---

## Features (MVP)

- **Frequency control** — VFO A/B display & entry, click-to-tune, scroll wheel step, band quick-select
- **Mode control** — LSB/USB/CW/CW-R/AM/FM/RTTY/DATA/C4FM buttons
- **Waterfall** — Real-time spectrum via CAT band scope (FTdx-10/101) or audio FFT (FT-710 / fallback)
- **Virtual CAT server** — TCP server (default port 4532) speaking Kenwood TS-2000 dialect → works with WSJT-X, JS8Call, fldigi, Log4OM out of the box
- **QSO log** — Quick-entry form, ADIF r/w, UDP broadcast to N1MM+/WSJT-X/Log4OM
- **DX cluster** — DX Spider telnet client with band/mode filter and click-to-tune

---

## Installation

### Arch / Manjaro (recommended)

```bash
# Install system Python and Qt
sudo pacman -S python python-pip qt6-base

# Create a virtual environment
python -m venv ~/.venvs/5trig
source ~/.venvs/5trig/bin/activate

# Install 5trig
pip install -e /path/to/5trig
```

### Any Linux / macOS / Windows

```bash
pip install -e ".[dev]"
```

### Serial port permissions (Linux)

Your user must be in the `uucp` (Arch) or `dialout` (Debian/Ubuntu) group:

```bash
# Arch / Manjaro
sudo usermod -aG uucp $USER
# Log out and back in, then verify:
groups | grep uucp
```

---

## Running

```bash
5trig
# or
python -m fivetr.main
```

---

## Configuration

Settings are stored at `~/.config/5trig/config.toml` (XDG).

Open the Settings dialog via **File → Settings** or `Ctrl+,`.

Key settings:
- **Radio Model** — FT-710 / FTdx-10 / FTdx-101
- **Serial Port** — `/dev/ttyUSB0` (check `ls /dev/ttyUSB*` after plugging in)
- **Baud Rate** — match radio's CAT RATE menu setting (default 38400)
- **Virtual CAT port** — default 4532 (hamlib rigctld port)

### Pointing WSJT-X to 5trig virtual CAT

In WSJT-X Settings → Radio:
- Rig: **Kenwood TS-2000**
- CAT port: **TCP** → `127.0.0.1 : 4532`
- PTT method: **CAT** or **RTS**

---

## Development

```bash
pip install -e ".[dev]"
pytest          # run tests
ruff check .    # lint
mypy src/       # type check
```

---

## Architecture

```
src/fivetr/
├── cat/          CAT engine — serial I/O + per-model command sets
├── vcat/         Virtual CAT TCP server (TS-2000 protocol)
├── spectrum/     Waterfall — audio FFT and CAT scope sources
├── logging/      ADIF log, UDP broadcast
├── cluster/      DX Spider telnet client
├── config/       TOML settings, XDG paths
└── ui/           PySide6 main window and widgets
```

---

## Roadmap

- [ ] RIT/XIT controls
- [ ] Memory channel manager
- [ ] Macro / CW keyer
- [ ] LoTW / QRZ.com QSL upload
- [ ] POTA spot integration
- [ ] AUR PKGBUILD package

---

## License

GPL-3.0-or-later
