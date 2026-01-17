# Zeitmessung

`Zeitmessung` is a lap / race time measurement system using Pico2 W microcontrollers. It consists of **StartGates** and **FinishGates**, lasers/light barriers, RFID for start-nummer, status displays, and a backend (PHP) + UI (Shiny / Web) components.

---

## Architecture / Components

| Component | Purpose |
|---|---|
| **StartGate** | Triggers race start: detects beam break, reads RFID, connects via WiFi to backend, logs start time. Enforces headway / cooldown. |
| **FinishGate** | Detects beam break when racer finishes, sends finish time to backend. |
| **LCD / OLED Display** | Shows status / instructions / big digits, such as locked startnummer or finish time. |
| **RFID** | Each racer has an RFID tag; StartGate reads tag as part of starting process. |
| **Backend (PHP + MySQL)** | Collects start/finish data, stores participants, parameters. |
| **Frontend (Shiny / Web, registration UI)** | For registering participants / managing races / disqualifications etc. |

---

## GPIO / Wiring Map (Pico2 W)

Here are the pins in use. *(If you have made changes recently, double-check them and adjust accordingly.)*

| Function | Pin / Interface | Microcontroller Pin (Pico2 W) |
|---|---|---|
| Beam sensor (Start / Finish) | GPIO input, pull-up, trigger on falling edge | **GP2** |
| Cancel / Stop button | GPIO input, pull-up | **GP3** |
| On-board Status LED | GPIO output | `"LED"` (special alias in MicroPython) |
| Optional External LED | GPIO output | **GP15** |
| OLED Display (SSD1306, I²C) | I²C interface | SDA = **GP4**, SCL = **GP5**; typical address `0x3C` |
| RFID Reader (RC522) | SPI interface | SCK = **GP10**, MOSI = **GP11**, MISO = **GP12**, CS = **GP13**, RST = **GP22** |

---

## Behaviour / Logic Highlights

- Beam inputs are **HIGH** when idle; a **FALLING** edge means the beam is broken.  
- The Cancel / Stop button is used for short-press (unlock / cancel) and long-press for additional functionality (shutdown, show log, etc.).  
- The system enforces a configurable **global headway** (delay between a locked start) to avoid racers starting too close to each other.  
- Device parameters (e.g. headway, server endpoints) are pulled from backend (via `device_params.php`) so they can be centrally managed.  
- Time synchronization relies on internet / WiFi; resolution is millisecond scale.

---

## Software Structure

- **MicroPython firmware** under `source/`  

   - StartGate code  
   - FinishGate code  
   - Low-level drivers (RC522, OLED, LEDs etc.)  
- **Backend** (PHP + MySQL) for data storage & APIs  
- **Frontend / UI** (Web / Shiny / registration) for user interaction  

---

## Setup / Deployment

1. Wire up hardware according to the GPIO mapping above.  
2. Flash MicroPython firmware onto Pico2 W devices.  
3. Configure WiFi credentials in firmware.  
4. Make sure backend server endpoints are reachable & correct in device configuration.  
5. Set up database tables (participants, runs, parameters).  
6. Deploy front-end / registration app.

---

## Configuration Parameters

Some settings you might want to check / change:

- **Headway / cooldown interval** between locked starts  
- **Server URLs / API endpoints**  
- Timeout for beam or button events  
- Display address / I²C bus if you have a different model  

---


