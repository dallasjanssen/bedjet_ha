#!/usr/bin/env bash
# Runs Home Assistant with BedJet Nights and a simulated BedJet, for trying changes without hardware.
#
#   pip install homeassistant home-assistant-frontend
#   dev/run-ha.sh
#
# Then open http://127.0.0.1:8123, finish onboarding, and add "BedJet (simulated)" and
# "BedJet Nights" under Settings, Devices & services.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
config="$here/config"
mkdir -p "$config/custom_components"
ln -sfn "$repo/custom_components/bedjet_nights" "$config/custom_components/bedjet_nights"
ln -sfn "$here/fake_bedjet/custom_components/bedjet" "$config/custom_components/bedjet"
if [ ! -f "$config/configuration.yaml" ]; then
  cat > "$config/configuration.yaml" <<'YAML'
homeassistant:
  unit_system: us_customary
  time_zone: America/Chicago
frontend:
config:
logger:
  default: warning
  logs:
    custom_components.bedjet_nights: debug
YAML
fi
exec hass -c "$config"
