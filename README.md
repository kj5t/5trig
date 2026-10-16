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
- **Virtual CAT server** — one TCP port (default `127.0.0.1:4535`) that speaks both the Hamlib **rigctld** protocol ("Hamlib NET rigctl" in WSJT-X, JS8Call, fldigi) and Kenwood TS-2000 → works with WSJT-X, JS8Call, fldigi, Log4OM
- **Remote operation** — run WSJT-X on a laptop while the radio sits on another computer: CAT, receive audio and transmit audio travel through 5trig, no remote desktop needed (see [Remote operation](#remote-operation-wsjt-x-on-a-laptop-radio-in-the-shack))
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
- **Virtual CAT port** — default 4535 (chosen so it does not clash with a real `rigctld` on 4532)

### Pointing WSJT-X to 5trig virtual CAT

In WSJT-X Settings → Radio:
- Rig: **Hamlib NET rigctl**
- Network Server: `127.0.0.1:4535`
- PTT method: **CAT**
- Mode: **Data/Pkt**
- Split Operation: **None** (5trig does not implement split; with "Rig" or "Fake It" WSJT-X's frequency changes will not reach the radio)

Programs that only offer a Kenwood TS-2000 can use the same port: Rig **Kenwood TS-2000**, network `127.0.0.1:4535`.
5trig tells the two dialects apart from the first command a client sends.

The virtual CAT server is controlled by `[vcat] enabled` in `config.toml` (or the **Virtual CAT** tab in Settings).

## Remote operation: WSJT-X on a laptop, radio in the shack

You can run WSJT-X on one computer while the radio is connected to another, with no remote-desktop session.
One copy of 5trig runs at the shack and shares the radio; a second copy runs on the laptop, and WSJT-X talks to that.

```
  LAPTOP                                              SHACK
  ┌────────┐                ┌──────────────┐          ┌──────────────┐          ┌───────┐
  │ WSJT-X │◄── rigctld ───►│ 5trig        │◄── TCP ─►│ 5trig        │◄── CAT ─►│ Radio │
  │        │   127.0.0.1    │ (client)     │   5040   │ (Share on)   │   USB    │       │
  │        │     :4535      │              │          │              │          │       │
  │        │◄─ fivetr_rx ◄──│              │◄─ UDP ───│              │◄─ audio ─│       │
  │        │                │              │   5041   │              │  (USB    │       │
  │        │─► fivetr_tx ──►│              │── UDP ──►│              │──codec)─►│       │
  └────────┘                └──────────────┘   5043   └──────────────┘          └───────┘
              receive audio: UDP 5041 (Opus)      transmit audio: UDP 5043 (raw PCM)
```

### Requirements

- 5trig on both machines (same version).
- **PipeWire** on both machines, with the `pw-play` and `pw-record` tools (Arch/Manjaro package `pipewire-audio`) and `pactl` (`libpulse`).
- The `opuslib` Python package (installed with 5trig; needs the system `libopus`) for receive audio.
- An accurate clock on the laptop. FT8 and FT4 need it within about a second; make sure NTP is on (`timedatectl` should say `System clock synchronized: yes`).

### Ports

| Port | Protocol | Direction | Purpose |
|---|---|---|---|
| 4535 | TCP, `127.0.0.1` only | WSJT-X → laptop 5trig | Virtual CAT (rigctld / Kenwood) |
| 5040 | TCP | laptop → shack | 5trig control link: CAT, state, PTT |
| 5041 | UDP | shack → laptop | Receive audio (Opus) |
| 5042 | UDP | laptop → shack | CW paddle events |
| 5043 | UDP | laptop → shack | Transmit audio (raw 16-bit PCM, 48 kHz) |

### Security

**Anyone who can reach port 5040 can key your transmitter.** There is no password.
- Use it on your home LAN only. **Never forward these ports on your router.**
- Away from home, connect through a VPN (WireGuard, Tailscale) or an SSH tunnel and point the client at the tunnel address.
- Transmit audio is accepted only from IP addresses of currently connected 5trig clients, and is passed to the radio only while PTT is on (plus a half-second tail). That keeps stray packets off the air, but an IP address is not authentication.
- If the last client disconnects while transmitting, the shack releases PTT.
- To restrict the shack to loopback (for an SSH tunnel), set **Bind address** to `127.0.0.1` in Settings → Remote.

### 1. Radio settings (Yaesu FTdx-10)

These are the settings that were needed for transmit audio over USB. Menu names are as they appear on the radio; see the FTdx-10 manual for where to find them.

| Setting | Value | Why |
|---|---|---|
| Operating mode | **DATA-U** (DATA-USB) | In plain USB the radio modulates from the microphone and ignores USB audio |
| **DATA MOD SOURCE** | **REAR** | Takes DATA-mode modulation from the rear-panel/USB audio path |
| **REAR SELECT** | **USB** | Makes "REAR" mean the USB audio codec rather than the rear DATA jack. **If this is wrong, PTT keys the radio but there is no power output** |
| CAT RATE | matches the baud rate in 5trig Settings (default 38400) | Serial control |

If the radio keys but shows no output power, check **REAR SELECT** first.

### 2. The shack computer

1. Plug the radio in over USB and make sure its CAT port and USB sound card both show up.
2. Find the radio's sound-card sink:
   ```bash
   pactl list short sinks | grep -i usb
   ```
   (on an FTdx-10 it is named like `alsa_output.usb-BurrBrown_from_Texas_Instruments_USB_AUDIO_CODEC-00.analog-stereo`)
3. In 5trig → Settings → Remote, under **Server Mode**:
   - **Radio TX audio sink**: the sink name from step 2. Leave it empty to disable remote transmit audio.
   - **TX audio UDP port**: 5043 (default).
   - Optionally tick auto-start sharing.
4. Connect the radio, then switch **Share 📡** on.
   The status bar shows `📡 Sharing: <ip>:5040 + audio UDP:5041 + TX audio UDP:5043`.
   If it shows `⚠ no TX audio`, the sink name is wrong or missing.
5. For the receive waterfall and audio stream, set the waterfall source to audio and give it the radio's USB capture device (Settings → Waterfall).

### 3. The laptop

1. Create the two virtual audio devices:
   ```bash
   scripts/wsjtx-audio.sh up        # creates fivetr_rx ("5trig-RX") and fivetr_tx ("5trig-TX")
   scripts/wsjtx-audio.sh status
   ```
   They disappear when PipeWire restarts; run `up` again after a reboot. Do not run `down` while 5trig or WSJT-X is using them.
2. In 5trig → Settings → Remote, under **Client Mode**:
   - tick the client option and set **Shack server IP / host** and port 5040,
   - **Play shack audio into PipeWire sink**: `fivetr_rx`,
   - **Send audio from PipeWire sink (TX)**: `fivetr_tx`.
3. Make sure **Virtual CAT** is enabled (Settings → Virtual CAT, port 4535).
4. Start 5trig and connect. The status bar should show `🛰 Remote: <shack>:5040` and `vCAT: 127.0.0.1:4535`.
   A `⚠` warning for RX or TX audio means a sink name could not be found.

> **Editing `config.toml` by hand:** close 5trig first. It writes its settings back when it exits and will overwrite your edit.

### 4. WSJT-X settings

| Where | Setting | Value |
|---|---|---|
| Radio | Rig | **Hamlib NET rigctl** |
| Radio | Network Server | `127.0.0.1:4535` |
| Radio | PTT Method | **CAT** |
| Radio | Mode | **Data/Pkt** |
| Radio | Split Operation | **None** |
| Audio | Input | **Monitor of 5trig-RX** |
| Audio | Output | **5trig-TX** |
| Audio | Output channel | Mono |
| Radio | Transmit audio source | Rear/Data |

Press **Test CAT**: it should turn green and the frequency should follow the radio.

### 5. First transmit

1. Use a dummy load at about 5 W.
2. Press **Tune**. Adjust WSJT-X's **Pwr** slider (and, if needed, the radio's USB audio level) until the ALC barely moves. FT8 needs a clean signal; do not drive it into ALC.
3. Check the radio shows output power and a clean waveform before going on the air.

### Troubleshooting

Both programs log what they do. Start 5trig from a terminal to see it. For each transmit, both ends print a line such as
`TX audio over finished: sent 123 packets (2.5 s), peak level 13106/32767`.

| Symptom | Likely cause and fix |
|---|---|
| WSJT-X: "Connection refused" on `127.0.0.1:4535` | The virtual CAT server is off. Enable it (Settings → Virtual CAT, or `[vcat] enabled = true` in `config.toml` with 5trig closed). In client mode it starts only after 5trig has connected to the shack. |
| Test CAT is green but changing band in WSJT-X does not move the radio | Split Operation is not None. Set it to **None**. |
| No decodes, level meter flat | `⚠ no RX audio` in the status bar, usually a typo in the sink name. Check `scripts/wsjtx-audio.sh status` and that WSJT-X's input is "Monitor of 5trig-RX". 5trig plays nothing rather than falling back to your speakers. |
| `⚠ TX audio not available: the shack is not offering it` | The shack's **Radio TX audio sink** is empty or missing, or the shack is running an older 5trig. Fix it and restart **Share**. |
| Radio keys (TX light) but no power output | Check the radio first: mode **DATA-U**, **DATA MOD SOURCE = REAR**, **REAR SELECT = USB**. |
| Log: `TX audio was silent: nothing is playing into 'fivetr_tx'` | WSJT-X is not playing into the TX sink. Re-select Output "5trig-TX" in WSJT-X → Settings → Audio. |
| Shack log: `TX audio ... dropped: not a connected 5trig client` | The packets come from an address other than the laptop's connected one (NAT, VPN, a second network card). |
| Shack log: `TX audio ... dropped: the radio is not keyed` | No PTT request reached the shack. Check PTT Method is CAT and the control link is up. |
| Decodes show a large time offset (DT) | Fix the laptop clock (NTP). |

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
├── vcat/         Virtual CAT TCP server (Hamlib rigctld + Kenwood TS-2000)
├── remote/       Shack server / laptop client, RX and TX audio over UDP
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
- [x] POTA spot integration
- [ ] AUR PKGBUILD package

---

## License

GPL-3.0-or-later
