# Power Functions Chinese look-alike RF — Raspberry Pi controller

Drive a **Chinese clone of the LEGO® Power Functions** receiver from a **Raspberry Pi**, fully
wireless: the Pi drives an **NRF24L01(+PA+LNA)** directly over SPI and serves a web control panel
over its built-in **WiFi** — no Arduino, no host computer. (A gamepad can drive it too, via the
browser Gamepad API, or — later — paired straight to the Pi over Bluetooth.)

This is the Pi port of the Arduino project where the protocol was reverse-engineered:
👉 **[power-function-chinese-look-alike-rf-controller-receiver](https://github.com/giorgio-zamparelli/power-function-chinese-look-alike-rf-controller-receiver)** (full protocol decode, sniffer, docs).

> ⚠️ Not affiliated with the LEGO Group. "LEGO" and "Power Functions" are trademarks of the LEGO
> Group. Independent interoperability / reverse-engineering project for hardware the author owns.

## Wiring — NRF24 adapter → Raspberry Pi 40-pin header

| NRF24 adapter | Pi physical pin | BCM |
|---|---|---|
| **VCC** | **5V** — pin 2 | — |
| **GND** | GND — pin 6 | — |
| **SCK** | pin 23 | GPIO11 |
| **MOSI (M0)** | pin 19 | GPIO10 |
| **MISO (M1)** | pin 21 | GPIO9 |
| **CSN** | pin 24 | GPIO8 (CE0) |
| **CE** | pin 22 | GPIO25 |
| IRQ | — (unused) | — |

Feed the adapter **5 V** (its regulator makes 3.3 V); for range/reliability add a **10–100 µF**
decoupling cap across the module's VCC/GND (the PA draws current spikes).

## Setup (on the Pi)

```bash
sudo raspi-config        # Interface Options → SPI → Enable, then reboot
sudo apt update && sudo apt install -y git python3-spidev python3-gpiozero python3-evdev
git clone https://github.com/giorgio-zamparelli/power-function-chinese-look-alike-rf-raspberry.git
cd power-function-chinese-look-alike-rf-raspberry
python3 lego_pi.py
```

Then from any phone/laptop on the same WiFi open **`http://<pi-ip>:8080`**
(find the Pi's address with `hostname -I`). Controls page at `/`, raw payload grid at `/grid`.

The top badge shows **radio ready / not found** (SPI read-back check) so you can tell if the
wiring/SPI is right.

### Run on boot (auto-start)

```bash
DIR="$HOME/power-function-chinese-look-alike-rf-raspberry"
sudo tee /etc/systemd/system/legopi.service >/dev/null <<EOF
[Unit]
Description=LEGO Power Functions RF controller
After=network.target
[Service]
ExecStart=/usr/bin/python3 $DIR/lego_pi.py
WorkingDirectory=$DIR
Restart=always
User=root
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now legopi
```

Now the Pi boots, joins WiFi, and starts the controller by itself — open `http://<pi-ip>:8080`.

## Controls

- **Output A / Output B** each: ▲ CW · ▼ CCW · ■ stop, with incremental speed; **STOP BOTH** brakes.
- **Channel switch 1–4** selector — set it to match the receiver's physical switch.
- **Gamepad:** open the page on a device with a controller connected; **left stick → Output A,
  right stick → Output B** (up = forward, down = reverse, centre = brake), **A button = STOP**.

## Direct Bluetooth gamepad (controller paired straight to the Pi)

For a fully standalone rig (controller → Pi, no browser), `lego_pi.py` also reads a controller
directly via **evdev** — same mapping. Set up Bluetooth on the Pi:

```bash
sudo apt install -y python3-evdev
echo "options bluetooth disable_ertm=1" | sudo tee /etc/modprobe.d/disable_ertm.conf   # Xbox pads need this
echo uhid | sudo tee /etc/modules-load.d/uhid.conf
sudo reboot
```

Then pair it (put the controller in pairing mode first):

```bash
bluetoothctl
  power on
  agent on
  scan on            # find the controller's MAC
  pair <MAC>
  trust <MAC>
  connect <MAC>
```

Once it appears as a `/dev/input/event*` with axes, `lego_pi.py` auto-detects it and the `/status`
endpoint reports its name. No restart or "mode switch" needed — it just picks up whatever gamepad
is connected to the Pi.

### ⚠️ Controller compatibility (important)

- **Xbox Series X\|S (model 1914), PS4 DualShock 4, 8BitDo** — work out of the box on Linux. ✅
- **Xbox One S (model 1708, USB/BT id `045E:02FD`)** — its older firmware ships a **malformed HID
  report descriptor** that the Linux kernel refuses to parse (`unbalanced collection / parse
  failed`), so it *pairs* but yields **no usable gamepad device** (a [well-known, widespread
  issue](https://github.com/atar-axis/xpadneo/issues/100)). Options, in order of reliability:
  use one of the controllers above; **update the controller firmware** via the Xbox Accessories
  app on Windows/Xbox (helps many, not guaranteed); or install
  [**xpadneo**](https://github.com/atar-axis/xpadneo).
- **Always-works fallback:** open the web UI in a browser on a device that has the controller
  connected — it reads the pad via the Gamepad API and drives the Pi over WiFi. *(On macOS use
  Safari; Chrome doesn't expose Xbox pads over Bluetooth.)*

## How it works

`lego_pi.py` is self-contained: it implements the **XN297 emulation** (scramble + bit-reverse +
CRC, adapted from goebish/nrf24_multipro, GPLv3) and the receiver's protocol (dual-output byte,
incremental quadrature speed, the per-channel RF/address table), talks to the NRF24 via `spidev`
(+ `gpiozero` for the CE pin), and a background thread transmits the current state continuously on
both of the channel's RF frequencies while the web server updates that state.

## License

Incorporates GPLv3 code (goebish XN297 emulation), so this project is **GPL-3.0** — see `LICENSE`.
