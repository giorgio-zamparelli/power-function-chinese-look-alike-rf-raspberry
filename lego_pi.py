#!/usr/bin/env python3
"""
Raspberry Pi controller for a Chinese clone of the LEGO(R) Power Functions RF receiver.

The Pi drives an NRF24L01(+PA+LNA) directly over SPI (no Arduino) and serves a web
control panel over WiFi. It emulates the receiver's scrambled XN297 protocol.

Protocol summary (full decode in the sibling repo
  github.com/giorgio-zamparelli/power-function-chinese-look-alike-rf-controller-receiver):
  * scrambled XN297, 1 Mbps. Per channel-switch position the RF channels + address differ;
    the address embeds the two RF channel numbers:  addr = 55 [lowRF] [highRF] 34.
  * Payload byte carries BOTH outputs: high nibble = Output A, low nibble = Output B.
    Per nibble: low 2 bits = direction (01 CW / 10 CCW / 11 brake / 00 float),
    high 2 bits = the wheel's quadrature encoder (speed is incremental -> send a phase
    sequence: A 0x50->0x90, B 0x05->0x09).

WIRING  (NRF24 adapter -> Raspberry Pi 40-pin header, BCM in parens):
  VCC -> 5V  (pin 2)        GND -> GND (pin 6)
  SCK -> pin 23 (GPIO11)    MOSI-> pin 19 (GPIO10)    MISO-> pin 21 (GPIO9)
  CSN -> pin 24 (CE0/GPIO8) CE  -> pin 22 (GPIO25)
  (feed the adapter 5V; add a 10-100uF cap across the module VCC/GND for the PA spikes)

SETUP:  sudo raspi-config -> Interface Options -> SPI -> enable, then reboot.
        sudo apt install -y python3-spidev python3-gpiozero   (or: pip3 install spidev gpiozero)
RUN:    python3 lego_pi.py          then open  http://<pi-ip>:8080
"""
import time, json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import spidev
from gpiozero import OutputDevice

HTTP_PORT = 8080
CE_GPIO   = 25          # BCM pin for the NRF24 CE line (pin 22)

# ---- nRF24 commands / registers ----
W_REGISTER=0x20; R_REGISTER=0x00; W_TX_PAYLOAD=0xA0; FLUSH_TX=0xE1
R_CONFIG=0x00; R_EN_AA=0x01; R_EN_RXADDR=0x02; R_SETUP_AW=0x03; R_SETUP_RETR=0x04
R_RF_CH=0x05; R_RF_SETUP=0x06; R_STATUS=0x07; R_TX_ADDR=0x10; R_DYNPD=0x1C; R_FEATURE=0x1D
PWR_UP=0x02; TX_DS=0x20

spi = spidev.SpiDev()
ce  = OutputDevice(CE_GPIO, initial_value=False)
radio_ok = False

def wreg(r, v): spi.xfer2([W_REGISTER | (r & 0x1F), v & 0xFF])
def rreg(r):    return spi.xfer2([R_REGISTER | (r & 0x1F), 0xFF])[1]
def strobe(c):  spi.xfer2([c])

# ---- XN297 emulation (adapted from goebish/nrf24_multipro, GPLv3) ----
SCRAMBLE = [0xe3,0xb1,0x4b,0xea,0x85,0xbc,0xe5,0x66,0x0d,0xae,0x8c,0x88,0x12,0x69,0xee,0x1f,
            0xc7,0x62,0x97,0xd5,0x0b,0x79,0xca,0xcc,0x1b,0x5d,0x19,0x10,0x24,0xd3,0xdc,0x3f,0x8e,0xc5,0x2f]
XOROUT   = [0x0000,0x3448,0x9BA7,0x8BBB,0x85E1,0x3E8C,0x451E,0x18E6,0x6B24,0xE7AB,0x3828,0x814B,
            0xD461,0xF494,0x2503,0x691D,0xFE8B,0x9BA7,0x8B17,0x2920,0x8B5F,0x61B1,0xD391,0x7401,0x2138,0x129F,0xB3A0,0x2988]
def brev(b):
    r = 0
    for _ in range(8): r = (r << 1) | (b & 1); b >>= 1
    return r
def crc16(crc, a):
    crc ^= (a << 8) & 0xffff
    for _ in range(8): crc = ((crc << 1) ^ 0x1021) & 0xffff if crc & 0x8000 else (crc << 1) & 0xffff
    return crc

ADDR = [0x55, 0x05, 0x44, 0x34]   # logical address; bytes 1,2 = the two RF channels
ALEN = 4

def xn_set_tx_addr():
    # the chip's TX address is the fixed XN297 preamble; the real device address goes in the payload
    wreg(R_SETUP_AW, ALEN - 2)
    spi.xfer2([W_REGISTER | R_TX_ADDR, 0x55, 0x0F, 0x71, 0x0C, 0x00])

def xn_write_payload(msg):
    buf = []
    for i in range(ALEN):       buf.append(ADDR[ALEN - 1 - i] ^ SCRAMBLE[i])
    for i in range(len(msg)):    buf.append(brev(msg[i]) ^ SCRAMBLE[ALEN + i])
    crc = 0xb5d2
    for b in buf: crc = crc16(crc, b)
    crc ^= XOROUT[ALEN - 3 + len(msg)]
    buf.append((crc >> 8) & 0xff); buf.append(crc & 0xff)
    spi.xfer2([W_TX_PAYLOAD] + buf)

def radio_init():
    global radio_ok
    spi.open(0, 0); spi.max_speed_hz = 8000000; spi.mode = 0
    ce.off(); time.sleep(0.1)
    wreg(R_CONFIG, PWR_UP); time.sleep(0.005)
    wreg(R_EN_AA, 0x00); wreg(R_EN_RXADDR, 0x01); wreg(R_SETUP_RETR, 0x00)
    wreg(R_RF_SETUP, 0x06)                       # 1 Mbps, max power
    wreg(R_STATUS, 0x70); wreg(R_DYNPD, 0x00); wreg(R_FEATURE, 0x00)
    strobe(FLUSH_TX)
    xn_set_tx_addr()
    radio_ok = (rreg(R_EN_RXADDR) == 0x01)        # SPI read-back sanity check
    print("radio_ok =", radio_ok)
    return radio_ok

def tx_one(ch, byte):
    ce.off()
    wreg(R_CONFIG, PWR_UP)
    wreg(R_RF_CH, ch)
    strobe(FLUSH_TX)
    wreg(R_STATUS, 0x70)
    xn_write_payload([byte & 0xFF])
    ce.on(); time.sleep(0.00003); ce.off()
    t = time.time()
    while not (rreg(R_STATUS) & TX_DS):
        if time.time() - t > 0.003: break
    wreg(R_STATUS, 0x70)

# ---- protocol state (web handlers set this; the TX thread sends it continuously) ----
CH_LO = [5, 7, 17, 12]            # low  RF channel per switch position (sniff-confirmed)
CH_HI = [68, 70, 65, 75]          # high RF channel per switch position
CHANS = [5, 68]
curChannel = 1
aState = 0                        # Output A nibble (high)
bState = 0                        # Output B nibble (low)

def set_channel(n):
    global ADDR, CHANS, curChannel
    if n < 1 or n > 4: return
    lo, hi = CH_LO[n - 1], CH_HI[n - 1]
    ADDR[1] = lo; ADDR[2] = hi
    CHANS = [lo, hi]; curChannel = n

def set_ch(ch, v):
    global aState, bState
    if ch == "a": aState = v & 0xF0
    else:         bState = v & 0x0F

def seq_ch(ch, vals, gap=0.06):
    global aState, bState
    for v in vals:
        if ch == "a": aState = v & 0xF0
        else:         bState = v & 0x0F
        time.sleep(gap)

def stop_all():
    global aState, bState
    aState, bState = 0x30, 0x03

def raw_send(v):
    global aState, bState
    v &= 0xFF; aState = v & 0xF0; bState = v & 0x0F

def tx_loop():
    while True:
        if radio_ok:
            byte = (aState | bState) & 0xFF
            for ch in CHANS:
                tx_one(ch, byte)
            time.sleep(0.012)
        else:
            time.sleep(0.2)

# ---- web UI (same look + behaviour as the Arduino version) ----
STYLE = """
 :root{--bg:#0f1115;--fg:#e8eaed;--mut:#8b909a;--grn:#2ecc71;--blu:#3aa0ff;--red:#ff4d4f;--ylw:#f5c451}
 *{box-sizing:border-box;-webkit-user-select:none;user-select:none}
 body{margin:0;font:16px/1.4 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg);padding:18px;max-width:620px;margin:auto;transition:opacity .2s}
 a{color:var(--blu);text-decoration:none;font-size:13px}
 h1{font-size:19px;font-weight:600;margin:8px 0 2px}.sub{color:var(--mut);font-size:13px;margin-bottom:14px}
 button{color:#fff;border:0;border-radius:13px;cursor:pointer;touch-action:manipulation;font:700 16px system-ui}
 button:active{transform:scale(.96)}
 h2{font-size:14px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;margin:18px 0 6px}
 .conn{display:inline-block;padding:4px 11px;border-radius:8px;font:700 12px system-ui;margin-bottom:12px}
 .conn.ok{background:#11331f;color:var(--grn)} .conn.no{background:#3a1416;color:var(--red)}
 .blk{border:1px solid #232733;border-radius:14px;padding:12px;margin-bottom:12px}
 .row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}.row button{padding:20px 0;font-size:15px}
 .cw{background:var(--grn)}.ccw{background:var(--blu)}.st{background:#555c6b}
 .meter{font:700 14px ui-monospace,monospace;color:var(--ylw);margin-bottom:8px}
 .stopall{background:var(--red);width:100%;padding:20px 0;font-size:19px;margin-top:4px}
 .byte{text-align:center;font:700 14px ui-monospace,monospace;color:var(--mut);margin-top:12px}
 .byte b{color:var(--fg)}.nav{text-align:right}
 .chanrow{display:flex;align-items:center;gap:8px;margin-bottom:14px;color:var(--mut);font-size:13px}
 .ch{padding:9px 16px;background:#2a2f3a;border-radius:9px;font-size:15px}.ch.on{background:var(--ylw);color:#0b0d10}
 .read{text-align:center;font:600 15px ui-monospace,monospace;margin:8px 0}.read b{font-size:24px;color:var(--ylw)}.read .bin{color:var(--blu)}
 .legend{font-size:12px;color:var(--mut);text-align:center;margin-bottom:8px}.legend i{font-style:normal;padding:2px 6px;border-radius:5px;color:#0b0d10;font-weight:700}
 .grid{display:grid;grid-template-columns:repeat(16,1fr);gap:3px}
 .grid button{font:600 10px ui-monospace,monospace;padding:8px 0;border-radius:5px;color:#0b0d10}
 .c1{background:#2ecc71}.c2{background:#3aa0ff}.c3{background:#ff6b6d}.c0{background:#454b57;color:#9aa0aa}
 .stop{background:var(--red);width:100%;padding:16px 0;margin-top:10px;font-size:17px}
"""
POLL = """
 function poll(){fetch('/status').then(r=>r.json()).then(s=>{var c=document.getElementById('conn');
   c.textContent=s.connected?('\\u25CF radio ready \\u00B7 ch '+s.channel):'\\u25CF radio not found \\u2014 check wiring / SPI';
   c.className='conn '+(s.connected?'ok':'no');document.body.style.opacity=s.connected?'1':'.55';})
   .catch(function(){var c=document.getElementById('conn');c.textContent='\\u25CF server unreachable';c.className='conn no';document.body.style.opacity='.55';});}
 setInterval(poll,2000);poll();
"""
CONTROL_HTML = ("""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>LEGO Pi control</title>
<style>__STYLE__</style></head><body>
<div class=nav><a href="/grid">number grid &rarr;</a></div>
<h1>LEGO motor control (Raspberry Pi)</h1>
<div class=sub>Output A + Output B, sent together (e.g. steering + drive)</div>
<div id=conn class="conn no">&#9679; connecting&hellip;</div>
<div class=chanrow><span>channel switch:</span><span id=chbtns></span></div>
<div id=gp class=sub style="margin-top:-8px">&#127918; gamepad: detecting&hellip;</div>

<div class=blk>
 <h2>Output A (high nibble)</h2>
 <div class=meter>A: <span id=am>stop</span></div>
 <div class=row>
  <button class=cw id=acw>&#9650; CW</button>
  <button class=ccw id=accw>&#9660; CCW</button>
  <button class=st id=astop>&#9632; stop</button>
 </div>
</div>
<div class=blk>
 <h2>Output B (low nibble)</h2>
 <div class=meter>B: <span id=bm>stop</span></div>
 <div class=row>
  <button class=cw id=bcw>&#9650; CW</button>
  <button class=ccw id=bccw>&#9660; CCW</button>
  <button class=st id=bstop>&#9632; stop</button>
 </div>
</div>
<button class=stopall id=stopall>&#9632; STOP BOTH (brake)</button>
<div class=byte>byte sent: <b id=byte>0x00</b></div>
<script>
 const $=id=>document.getElementById(id);
 let actx=0,bctx=0,al=0,bl=0;
 function disp(){$('am').textContent=al>0?('CW \\u00B7 '+al):al<0?('CCW \\u00B7 '+(-al)):'stop';
   $('bm').textContent=bl>0?('CW \\u00B7 '+bl):bl<0?('CCW \\u00B7 '+(-bl)):'stop';}
 const get=u=>fetch(u).then(r=>r.text()).then(t=>{if(t&&t[0]==='@')$('byte').textContent=t.slice(1);}).catch(()=>{});
 function aCW(){if(actx!==1){get('/set?ch=a&v=16');actx=1;}get('/seq?ch=a&v=80,144');al++;disp();}
 function aCCW(){if(actx!==2){get('/set?ch=a&v=32');actx=2;}get('/seq?ch=a&v=96,160');al--;disp();}
 function aStop(){get('/set?ch=a&v=48');actx=0;al=0;disp();}
 function bCW(){if(bctx!==1){get('/set?ch=b&v=1');bctx=1;}get('/seq?ch=b&v=5,9');bl++;disp();}
 function bCCW(){if(bctx!==2){get('/set?ch=b&v=2');bctx=2;}get('/seq?ch=b&v=6,10');bl--;disp();}
 function bStop(){get('/set?ch=b&v=3');bctx=0;bl=0;disp();}
 function stopAll(){get('/stop');actx=bctx=0;al=bl=0;disp();}
 $('acw').onclick=aCW;$('accw').onclick=aCCW;$('astop').onclick=aStop;
 $('bcw').onclick=bCW;$('bccw').onclick=bCCW;$('bstop').onclick=bStop;$('stopall').onclick=stopAll;
 function setChan(n){get('/channel?n='+n);[...document.querySelectorAll('.ch')].forEach(b=>b.classList.toggle('on',+b.dataset.n===n));}
 (function(){const h=$('chbtns');for(let n=1;n<=4;n++){const b=document.createElement('button');b.className='ch'+(n===1?' on':'');b.dataset.n=n;b.textContent=n;b.onclick=()=>setChan(n);h.appendChild(b);}})();
 const MAXN=7, DZ=0.20;
 window.addEventListener('gamepadconnected',function(){});
 function activePad(){var ps=navigator.getGamepads?navigator.getGamepads():[];
   for(var i=0;i<ps.length;i++){if(ps[i]&&ps[i].connected&&ps[i].axes&&ps[i].axes.length>=4)return ps[i];}return null;}
 function tgt(v){return Math.abs(v)<DZ?0:Math.round(-v*MAXN);}
 function gpTick(){var g=$('gp');var p=activePad();
   if(!p){var ps=navigator.getGamepads?navigator.getGamepads():[];var n=0;for(var i=0;i<ps.length;i++)if(ps[i])n++;
     if(g)g.textContent='\\uD83C\\uDFAE no gamepad ('+n+' detected) \\u2014 press a button on the controller';return;}
   var ta=tgt(p.axes[1]||0),tb=tgt(p.axes[3]||0);
   if(ta===0){if(al!==0||actx!==0)aStop();}else if(al<ta)aCW();else if(al>ta)aCCW();
   if(tb===0){if(bl!==0||bctx!==0)bStop();}else if(bl<tb)bCW();else if(bl>tb)bCCW();
   if(p.buttons[0]&&p.buttons[0].pressed)stopAll();
   if(g)g.textContent='\\uD83C\\uDFAE '+(p.id||'pad').slice(0,16)+'  A '+al+'\\u2192'+ta+'  B '+bl+'\\u2192'+tb;}
 setInterval(gpTick,180);
__POLL__
</script></body></html>""").replace("__STYLE__", STYLE).replace("__POLL__", POLL)

GRID_HTML = ("""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>payload grid</title>
<style>__STYLE__ .nav{text-align:left}</style></head><body>
<div class=nav><a href="/">&larr; controls</a></div>
<h1>raw payload grid</h1>
<div class=sub>click any 0&ndash;255 &middot; high nibble=Output A, low nibble=Output B</div>
<div id=conn class="conn no">&#9679; connecting&hellip;</div>
<div class=chanrow><span>channel switch:</span><span id=chbtns></span></div>
<div class=read>value <b id=ev>0</b> <span id=eh>0x00</span> <span class=bin id=eb>0000&nbsp;0000</span></div>
<div class=legend>low-bits: <i class=c1>01 fwd</i> <i class=c2>10 rev</i> <i class=c3>11 stop</i> <i class=c0>00 float</i></div>
<div class=grid id=grid></div>
<button class=stop id=stop>&#9632; STOP / BRAKE (3)</button>
<script>
 const $=id=>document.getElementById(id);
 function bin(v){return v.toString(2).padStart(8,'0').replace(/(....)(....)/,'$1&nbsp;$2');}
 function send(v){fetch('/send?v='+v).catch(()=>{});$('ev').textContent=v;$('eh').textContent='0x'+v.toString(16).padStart(2,'0').toUpperCase();$('eb').innerHTML=bin(v);}
 const g=$('grid');for(let v=0;v<256;v++){const b=document.createElement('button');b.textContent=v;b.className='c'+(v&3);b.onclick=()=>send(v);g.appendChild(b);}
 $('stop').onclick=()=>send(3);
 function setChan(n){fetch('/channel?n='+n).catch(()=>{});[...document.querySelectorAll('.ch')].forEach(b=>b.classList.toggle('on',+b.dataset.n===n));}
 (function(){const h=$('chbtns');for(let n=1;n<=4;n++){const b=document.createElement('button');b.className='ch'+(n===1?' on':'');b.dataset.n=n;b.textContent=n;b.onclick=()=>setChan(n);h.appendChild(b);}})();
__POLL__
</script></body></html>""").replace("__STYLE__", STYLE).replace("__POLL__", POLL)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _html(self, html):
        body = html.encode()
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def _ok(self):
        body = ("@0x%02X" % ((aState | bState) & 0xFF)).encode()
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/":            self._html(CONTROL_HTML)
        elif u.path == "/grid":      self._html(GRID_HTML)
        elif u.path == "/status":
            body = json.dumps({"connected": radio_ok, "channel": curChannel}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif u.path == "/set":
            try: set_ch(q.get("ch", ["b"])[0], int(q.get("v", ["0"])[0]))
            except Exception as e: print("set err", e)
            self._ok()
        elif u.path == "/seq":
            try: seq_ch(q.get("ch", ["b"])[0], [int(x) for x in q.get("v", [""])[0].split(",") if x != ""])
            except Exception as e: print("seq err", e)
            self._ok()
        elif u.path == "/stop":      stop_all(); self._ok()
        elif u.path == "/send":
            try: raw_send(int(q.get("v", ["0"])[0]))
            except Exception as e: print("send err", e)
            self._ok()
        elif u.path == "/channel":
            try: set_channel(int(q.get("n", ["1"])[0]))
            except Exception as e: print("chan err", e)
            self._ok()
        else:                        self.send_response(404); self.end_headers()

if __name__ == "__main__":
    radio_init()
    set_channel(1)
    threading.Thread(target=tx_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), H)
    print("controls: http://<pi-ip>:%d/   grid: /grid" % HTTP_PORT)
    srv.serve_forever()
