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
sudo apt update && sudo apt install -y git python3-spidev python3-gpiozero
git clone https://github.com/giorgio-zamparelli/power-function-chinese-look-alike-rf-raspberry.git
cd power-function-chinese-look-alike-rf-raspberry
python3 lego_pi.py
```

Then from any phone/laptop on the same WiFi open **`http://<pi-ip>:8080`**
(find the Pi's address with `hostname -I`). Controls page at `/`, raw payload grid at `/grid`.

The top badge shows **radio ready / not found** (SPI read-back check) so you can tell if the
wiring/SPI is right.

## Controls

- **Output A / Output B** each: ▲ CW · ▼ CCW · ■ stop, with incremental speed; **STOP BOTH** brakes.
- **Channel switch 1–4** selector — set it to match the receiver's physical switch.
- **Gamepad:** open the page on a device with a controller connected; **left stick → Output A,
  right stick → Output B** (up = forward, down = reverse, centre = brake), **A button = STOP**.

## How it works

`lego_pi.py` is self-contained: it implements the **XN297 emulation** (scramble + bit-reverse +
CRC, adapted from goebish/nrf24_multipro, GPLv3) and the receiver's protocol (dual-output byte,
incremental quadrature speed, the per-channel RF/address table), talks to the NRF24 via `spidev`
(+ `gpiozero` for the CE pin), and a background thread transmits the current state continuously on
both of the channel's RF frequencies while the web server updates that state.

## License

Incorporates GPLv3 code (goebish XN297 emulation), so this project is **GPL-3.0** — see `LICENSE`.
