# Kohler Konnect for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/kenyonj/kohler-konnect-ha.svg)](https://github.com/kenyonj/kohler-konnect-ha/releases)

An unofficial Home Assistant integration for **Kohler Konnect** devices, with full support for the **Anthem shower (GCS)**.

> ⚠️ This is an unofficial integration, reverse-engineered from the Kohler Konnect Android app. It is not affiliated with or endorsed by Kohler Co. The API may change at any time.

---

## Features

| Feature | Status |
|---|---|
| 🚿 Shower warmup (pre-heat) | ✅ Working |
| ▶️ Start preset / experience (select entity + service) | ✅ Working |
| ⏹️ Stop shower | ✅ Working |
| ⏸️ Pause shower (keeps session active) | ✅ Working |
| 🌡️ Target temperature (get/set, live while running) | ✅ Working |
| 💦 Flow percentage (set, live while running) | ✅ Working |
| 🚰 Outlet selection (showerhead / handshower / tub) | ✅ Working |
| 📶 Connection state sensor | ✅ Working |
| 🔄 Warmup state sensor | ✅ Working |
| 🎛️ Active preset sensor (by name) | ✅ Working |
| 💧 Current outlet temperature | ✅ Working |
| 🌊 Water running binary sensor | ✅ Working |
| ⚠️ Valve problem binary sensor (error codes) | ✅ Working |
| 📊 Session volume / system state sensors | ✅ Working |

---

## Supported Devices

- **Kohler Anthem Shower (GCS)** — full read/write support
- Other Kohler Konnect devices (EVO, DTV+, SFC) — partial state read (PRs welcome)

---

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** → ⋮ → **Custom repositories**
3. Add `https://github.com/kenyonj/kohler-konnect-ha` as an **Integration**
4. Install **Kohler Konnect**
5. Restart Home Assistant

### Manual

1. Copy `custom_components/kohler/` into your HA `custom_components/` directory
2. Restart Home Assistant

---

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for **Kohler Konnect**
3. Enter your Kohler Konnect email and password (same credentials as the official app)

---

## Entities

Each Anthem shower gets:

| Entity | Type | What it does |
|---|---|---|
| `water_heater.anthem_shower` | Water heater | Start/stop/pause the shower, warmup, target temperature |
| `select.*_preset` | Select | Start a preset or experience by name; `none` stops it |
| `select.*_outlet` | Select | Which outlet runs when starting (showerhead, handshower, tub filler, tub + handheld) |
| `number.*_flow` | Number | Water flow percentage; applies live if water is running |
| `switch.*_shower_warmup` | Switch | Start/stop warmup |
| `binary_sensor.*_water_running` | Binary sensor | On while any valve is flowing |
| `binary_sensor.*_valve_problem` | Binary sensor | On when a valve reports an error (codes in attributes) |
| `sensor.*` | Sensors | Connection state, target temperature, warmup state, active preset, system state, session volume, last connected |

The outlet and flow selections are held locally (the Kohler API has no "set
without running water" command) and are applied when the shower starts — or
immediately, if water is already running.

---

## Services

### `kohler.start_warmup`
Pre-heats the shower to your target temperature — no water flows until you get in.

### `kohler.start_preset`
Starts a saved preset or experience by ID (1–5 are presets, 17+ are experiences).

```yaml
service: kohler.start_preset
target:
  entity_id: water_heater.anthem_shower
data:
  preset_id: 1
```

### `kohler.stop_shower`
Immediately stops all water flow (and clears any running preset or warmup).

### `kohler.pause_shower`
Pauses the water while keeping the shower session active, so it can be resumed.

---

## Automations

### Pre-heat shower 10 minutes before your alarm
```yaml
automation:
  trigger:
    - platform: time
      at: "06:50:00"
  action:
    - service: kohler.start_warmup
      target:
        entity_id: water_heater.anthem_shower
```

### Start shower when you wake up
```yaml
automation:
  trigger:
    - platform: state
      entity_id: input_boolean.morning_routine
      to: "on"
  action:
    - service: kohler.start_preset
      target:
        entity_id: water_heater.anthem_shower
      data:
        preset_id: "1"
```

---

## How It Works

This integration uses the undocumented Kohler Konnect REST API:

1. **Service token** — mTLS request to Kohler's Azure APIM to get a runtime API key
2. **User token** — Azure B2C ROPC flow with your email/password → JWT bearer token
3. **API calls** — all device state and commands sent to `api-kohler-us.kohler.io` with both headers

State is polled every 10 seconds (presets every ~5 minutes). Commands are sent immediately.

---

## Contributing

PRs welcome! Especially interested in:
- Support for EVO / DTV+ / SFC devices
- Azure IoT Hub real-time state updates (instead of polling)
- Multiple shower / valve support

---

## Support

If this integration saved you some time (or a cold shower), consider buying me a coffee ☕

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/kenyonj)

---

## License

MIT
