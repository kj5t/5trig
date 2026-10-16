#!/usr/bin/env bash
# Virtual PipeWire sinks for running WSJT-X against a remote radio through 5trig.
# Run this on the machine where WSJT-X runs (the laptop).
#
#   wsjtx-audio.sh up       create the sinks (safe to run twice)
#   wsjtx-audio.sh down     remove them
#   wsjtx-audio.sh status   show whether they exist
#
# fivetr_rx   5trig plays the shack's receive audio into this sink.
#             In WSJT-X choose  Input:  "Monitor of 5trig-RX".
# fivetr_tx   WSJT-X plays its transmit audio into this sink; 5trig records it
#             and sends it to the shack.  In WSJT-X choose  Output: "5trig-TX".
set -euo pipefail

declare -A SINKS=([fivetr_rx]="5trig-RX" [fivetr_tx]="5trig-TX")
ORDER=(fivetr_rx fivetr_tx)

has_sink() { pactl list short sinks | cut -f2 | grep -qx "$1"; }

case "${1:-status}" in
  up)
    for name in "${ORDER[@]}"; do
      if has_sink "$name"; then
        echo "$name already exists"
      else
        pactl load-module module-null-sink "sink_name=$name" \
          "sink_properties=device.description=${SINKS[$name]}" >/dev/null
        echo "created $name"
      fi
    done
    ;;
  down)
    for name in "${ORDER[@]}"; do
      ids=$(pactl list short modules | awk -v n="sink_name=$name" 'index($0, n) {print $1}')
      if [[ -n "$ids" ]]; then
        echo "$ids" | xargs -r -n1 pactl unload-module
        echo "removed $name"
      else
        echo "$name not present"
      fi
    done
    ;;
  status)
    for name in "${ORDER[@]}"; do
      has_sink "$name" && echo "$name: present" || echo "$name: missing"
    done
    ;;
  *)
    echo "usage: $0 up|down|status" >&2
    exit 2
    ;;
esac
