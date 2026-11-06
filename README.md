# BedJet Nights

Schedules, a remote and night history for your BedJet, running in Docker next to Home Assistant.

![BedJet Nights screens](docs/screens.png)

## Why this exists

A BedJet accepts only one Bluetooth connection at a time. If Home Assistant holds it (through the
BedJet integration), a phone app such as [Lull](https://github.com/rkanjani/lull) or the official
BedJet app can't connect, and the other way round.

BedJet Nights brings Lull's best parts (schedules built from phases, the night curve, the remote
and the history) into a web app that works **through** Home Assistant. Home Assistant keeps the
one Bluetooth connection, and every phone, tablet and dashboard in the house can open the same
page at once.

## How it works

- **Home Assistant** talks to the BedJet over Bluetooth, as it does today, with the BedJet
  integration ([ha-bedjet](https://github.com/natekspencer/ha-bedjet) from HACS, or the built-in one).
- **BedJet Nights** (this container) connects to Home Assistant's WebSocket API with a long-lived
  token. It reads the BedJet's entities and calls their actions, the same way a dashboard would.
- **Your phone** opens the web UI at `http://<your-machine>:8099`. It can also live in Home
  Assistant's sidebar.
- **Optional:** with an MQTT broker, BedJet Nights adds "Start night", "End night", "Tonight's
  schedule" and more to your BedJet's device page in Home Assistant, for automations and dashboards.

## Features

- **Schedules.** Build a night from phases (Cool, Heat, Gentle heat, Turbo, Dry, Pause), each with its
  own temperature, fan speed and duration, and see the whole night as one curve. Starts with Lull's
  four starter schedules (Cool Drift, Warm Welcome, Hot Sleeper, Winter Cocoon).
- **The BedJet's own limits.** Temperatures stay inside what each mode allows, Turbo stops at 10
  minutes, and Heat follows the BedJet's safety table (hotter and faster air is cut off sooner).
- **Start Night**, or **Start at bedtime** on the nights you pick. Home Assistant plays the schedule
  phase by phase. Each phase also sets the BedJet's own shut-off timer to the end of that phase plus
  two minutes, so if Home Assistant or this app stopped mid-night the BedJet would still turn itself
  off instead of running on.
- **Remote.** Mode, temperature, fan, the shut-off timer, and the programs saved on your BedJet
  (M1 to M3 and biorhythms). LED and beeps are in the BedJet sheet.
- **History.** Every change through the night, wherever it was made (this app, Home Assistant, the
  BedJet's remote), with the time and the room temperature. Nights run noon to noon, so 3 AM belongs
  with the evening before, and each night is redrawn as a curve.
- **Several BedJets.** Every BedJet in Home Assistant shows up. Tap the name at the top to switch.
- **Lend to a phone.** Pauses the BedJet integration in Home Assistant for 15 minutes to 2 hours so
  the BedJet app or Lull can connect (for a firmware update, say), then takes the BedJet back by itself.

### How it differs from Lull

Lull writes tonight's schedule into the BedJet's memory and lets the BedJet run it, because a phone
can leave the room. Home Assistant's BedJet integration has no way to write programs, and the Home
Assistant machine never leaves, so BedJet Nights plays the schedule from Home Assistant instead. In
practice:

- Changes made during a night (here, in Home Assistant, or on the BedJet's remote) last until the
  next phase begins, as in Lull.
- Turning the BedJet off ends the night. Choosing a mode or a saved program in the Remote tab ends
  it too, as in Lull. The BedJet's own timer running out does not: the next phase picks up.
- If the container restarts mid-night, the night carries on from where it should be.

## Setup

You need Home Assistant with the BedJet integration working, and Docker on a machine that can reach
Home Assistant (the same machine is fine).

1. **Create a token.** In Home Assistant, open your profile, then the **Security** tab, then
   **Create token** under Long-lived access tokens. Use an administrator's account if you want
   "Lend to a phone", since it pauses and resumes an integration.

2. **Get the code and settings.**

   ```bash
   git clone https://github.com/dallasjanssen/bedjet_ha.git
   cd bedjet_ha
   cp .env.example .env
   ```

   Open `.env` and set `HA_TOKEN`. If Home Assistant runs in Docker on the same machine with host
   networking (the usual setup), leave `HA_URL=http://host.docker.internal:8123`. Otherwise use the
   address you open Home Assistant at, for example `http://192.168.1.20:8123`.

3. **Start it.**

   ```bash
   docker compose up -d --build
   ```

4. **Open it** at `http://<your-machine>:8099` on your phone, and use your browser's **Add to Home
   Screen** for an app-like icon.

5. **Optional: put it in Home Assistant's sidebar.** Go to **Settings**, **Dashboards**, **Add
   dashboard**, choose **Webpage**, and enter `http://<your-machine>:8099`. If you open Home
   Assistant over HTTPS (for example through Home Assistant Cloud), browsers block an `http://`
   page inside it, so use the direct address in that case.

To update later: `git pull` then `docker compose up -d --build`. Your schedules and history live in
`./data` and are kept.

## Home Assistant entities (optional, MQTT)

Set `MQTT_HOST` (and the username and password, if your broker has them) in `.env` and restart. Each
BedJet then gets these entities, added to the BedJet's existing device page:

| Entity | What it does |
| --- | --- |
| `select.<bedjet>_tonight` | Tonight's schedule |
| `button.<bedjet>_start_night` | Starts tonight's schedule |
| `button.<bedjet>_end_night` | Ends the night and turns the BedJet off |
| `switch.<bedjet>_start_at_bedtime` | Starts tonight's schedule by itself at its bedtime |
| `binary_sensor.<bedjet>_night_running` | On while a schedule is running |
| `sensor.<bedjet>_night` | For example "Cool Drift: Cooling, 45 min left (phase 1 of 4)" |
| `sensor.<bedjet>_night_ends` | When the night's schedule ends |

`<bedjet>` is the object ID of your BedJet's climate entity: for `climate.dallass_bedjet` it is
`dallass_bedjet`. To start the night from an automation or a script, use this action:

```yaml
action: button.press
target:
  entity_id: button.dallass_bedjet_start_night
```

## Troubleshooting

- **"Can't reach Home Assistant"**: check `HA_URL` and `HA_TOKEN` in `.env`, then
  `docker compose logs bedjet-nights`.
- **"Can't reach your BedJet"**: Home Assistant isn't connected to it. Make sure the BedJet app and
  Lull are closed on every phone, and that the BedJet integration shows the BedJet as available.
- **No BedJet found**: BedJet Nights looks for climate entities from the `bedjet` integration. If
  yours comes from somewhere else, name it in `BEDJET_ENTITIES`.
- **Lend to a phone fails**: the token needs to belong to an administrator.

The web UI has no login of its own, so keep port 8099 on your home network.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

No BedJet needed: `tests/fake_ha.py` is a pretend Home Assistant with one BedJet that behaves like
the ha-bedjet integration. Run it and point the app at it:

```bash
python tests/fake_ha.py --port 8123 &
HA_URL=http://127.0.0.1:8123 HA_TOKEN=test-token DATA_DIR=./data python -m bedjet_nights
```

Then open `http://127.0.0.1:8099`.

| Path | What it is |
| --- | --- |
| `bedjet_nights/model.py` | Phases, schedules, the BedJet's limits, and nights rebuilt from history |
| `bedjet_nights/ha.py` | Home Assistant WebSocket client |
| `bedjet_nights/bedjet.py` | One BedJet as Home Assistant shows it: reading state and sending actions |
| `bedjet_nights/nights.py` | Plays schedules, bedtime starts, history, lending |
| `bedjet_nights/mqtt.py` | Optional MQTT entities for Home Assistant |
| `bedjet_nights/web.py` | Web server, JSON API and live updates |
| `bedjet_nights/static/` | The web UI (plain HTML, CSS and JavaScript, no build step) |

## Credits

The phase model, the BedJet's limits and heat table, the starter schedules, the night curve and the
history view follow [Lull](https://github.com/rkanjani/lull) by Rohan Kanjani (MIT). See
[NOTICE](NOTICE). Home Assistant's side is [ha-bedjet](https://github.com/natekspencer/ha-bedjet)
by natekspencer.

BedJet Nights is an independent project, not affiliated with or endorsed by BedJet LLC or Lull.
