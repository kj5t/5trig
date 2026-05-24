"""
5trig remote operation — shack server and remote client.

The remote module lets you run the 5trig UI on one machine while the
radio sits on another (the shack PC).  Communication uses a single TCP
connection with length-prefixed JSON frames.

Typical usage
-------------
Shack PC (next to radio):
  - Run full 5trig, connect to radio, click "Share 📡" in the toolbar.
  - A RemoteServer starts on port 5040 (configurable).

Remote machine (anywhere on LAN / VPN):
  - Open 5trig Settings → Remote → enable client mode, enter shack IP.
  - Click Connect — 5trig uses RemoteRadio instead of a local serial port.

Protocol
--------
All frames:  [ 4-byte big-endian uint32 length ][ UTF-8 JSON ]

Server → Client:
  {"t":"hello",  "model":"FTdx-10", "ver":1}
  {"t":"state",  "s":{…RadioState fields…}}
  {"t":"scope",  "d":"<base64 float32 dB array>"}

Client → Server:
  {"t":"set_freq", "vfo":"A"|"B", "hz":<int>}
  {"t":"set_mode", "code":"<Yaesu mode char>"}
  {"t":"set_ptt",  "on":<bool>}
  {"t":"cw_key",   "down":<bool>}     # dit/dah element; WinKeyer/Vail source
  {"t":"cat_raw",  "cmd":"<str>"}     # arbitrary CAT command (e.g. "SV")
"""

from .client import RemoteRadio
from .server import RemoteServer

__all__ = ["RemoteRadio", "RemoteServer"]
