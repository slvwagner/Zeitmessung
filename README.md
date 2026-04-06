# [Zeitmessung] Project Version: 0.1.2

Zeitmessung is a modular lap and race timing system built around Raspberry Pi Pico 2 W microcontrollers. It is designed for sports events where accurate measurement and structured management of race activities on a defined track are required.

Racers begin by registering with an RFID card, which activates time tracking for their session. Each run is measured using a dual-beam sensing system at both the start and finish gates. This redundant sensing approach reduces the likelihood of false detections and ensures precise identification of crossing events. In addition, it enables speed calculation.

The system supports multiple runs per racer, making it suitable for both training and competitive scenarios. To maintain measurement integrity, overtaking on the track is not permitted; therefore, removing racers from the track must be handled manually.

Key features include:

- RFID-based racer identification and session control  
- High-precision dual-beam timing at start and finish gates, including speed measurements  
- Support for multiple runs per participant  
- Database-based race administration and data management  


---
## Components Overview

### Table of Contents

- [Shiny App (Frontend)](#shiny-app-frontend)
- [Version Automation](#version-automation)
- [System Overview](#system-overview)
	- [Application Startup & Autostart (startup app.R)](#application-startup--autostart-startup-appr)
	- [Local XAMPP Server Sync (Update_local_xampp_server.R)](#local-xampp-server-sync-update_local_xampp_serverr)
	- [Admin Dashboard (dashboard.php)](#admin-dashboard-dashboardphp)
- [Configuration Management (configuration.R)](#configuration-management-configurationr)
- [Hardware (Pico 2 W)](#hardware-pico-2-w)
- [Key Features](#key-features)
- [Quick Start](#quick-start)
- [Software Structure](#software-structure)
- [Firmware Build & Deployment](#firmware-build--deployment)
- [Configuration](#configuration)

---

- **Firmware:** Custom MicroPython build for Pico 2 W, with native C modules for timing, DMX, and RFID.
- **Backend:** PHP/MySQL (XAMPP) for race management, data collection, and configuration.
- **Frontend:** R/Shiny app for registration, dashboards, and result display.


- **StartGates** and **FinishGates** with dual-beam laser/light barriers for precise timing
- **RFID** for racer identification at the start
- **OLED displays** for real-time status and feedback
- **WiFi** connectivity for time sync and backend communication
- **Backend** (PHP/MySQL) for data collection, race management, and results
- **Frontend** (Shiny/Web) for registration, live dashboards, and result display

The system is highly configurable and supports robust, low-latency timing using custom MicroPython firmware and native C modules for hardware control.

---
## Shiny App (Frontend)
[⬆️ Back to Table of Contents](#components-overview)

The R/Shiny app provides a web-based interface for registration, live dashboards, and result display.

### Requirements

- R (>= 4.0)
- The following R packages:
	- shiny
	- DBI
	- pool
	- RMariaDB
	- tidyverse
	- DT
	- httr
	- jsonlite
	- shinyWidgets
	- RMySQL

Install all required packages in R:

```r
install.packages(c("shiny", "DBI", "pool", "RMariaDB", "tidyverse", "DT", "httr", "jsonlite", "shinyWidgets", "RMySQL"))
```

### Environment Variables

Set the following environment variables for database access (in your .Renviron or system environment):

- ZEIT_DB_HOST
- ZEIT_DB_NAME
- ZEIT_DB_USER
- ZEIT_DB_PW
- DB_host
- DB_user
- DB_PASSWORD_KINOKLUB
- API_KEY (if required)

### Running the App

From the project root:

```r
shiny::runApp("app.R")
```

or simply open `app.R` in RStudio and click "Run App".


---
## Version Automation
[⬆️ Back to Table of Contents](#components-overview)

The script `update_version.R` updates all project version tags in relevant files (firmware, SQL, PHP, R, etc.).

To update the version everywhere:

```r
source("update_version.R")
```

---

## System Overview
[⬆️ Back to Table of Contents](#components-overview)

### Application Startup & Autostart (`startup app.R`)

The `startup app.R` script automates the creation of desktop shortcuts and autostart entries for the Zeitmessung Shiny app across Windows, Linux, and macOS:

- Detects your operating system
- Creates a desktop shortcut to launch the app (with icon, minimized window, etc.)
- Optionally adds or removes the app from system autostart (login items/startup folder)
- Handles Windows, Linux (with .desktop files), and macOS (with .command and .app bundles)
- Sets a system environment variable (`Zeitmessung_wd`) for the app working directory (Windows)
- Prompts the user for autostart setup/removal interactively

**Usage:**
1. Run `source('source/OS_support/startup app.R')` in R
2. Follow the prompts to create shortcuts and configure autostart
3. The script will place a shortcut on your desktop and optionally add/remove the app from autostart

This makes it easy for users to launch Zeitmessung and ensures it can start automatically on login if desired.



### Local XAMPP Server Sync (`Update_local_xampp_server.R`)
[⬆️ Back to Table of Contents](#components-overview)

The `Update_local_xampp_server.R` script automates the process of updating and copying configuration and web files to your local XAMPP server:

- Reads configuration/credentials from your Google Sheet (same as `configuration.R`)
- Updates PHP config files (`config.php`) for registration and dashboard web apps
- Copies all relevant web app folders (`www_register`, `www_check_registrations`, `xampp`) to the XAMPP server directory specified by the `xampp_server` environment variable
- Copies dashboard and index files to the XAMPP root
- Ensures directory structure and permissions are correct

**Usage:**
1. Set the `xampp_server` environment variable to your local XAMPP root path
2. Run `source('source/Server_admin/Update_local_xampp_server.R')` in R
3. The script will update config files and copy all necessary web files to your XAMPP server

This ensures your local server always has the latest configuration and web interface for testing or development.


### Admin Dashboard (`dashboard.php`)
[⬆️ Back to Table of Contents](#components-overview)

The `dashboard.php` file provides a web-based admin dashboard for managing and viewing participant registrations:

- Secure login required (session-based authentication)
- Search, filter, and view all registered participants
- Filter by name, email, category, etc.
- See total participant count and latest registration date
- Export all participant data as CSV (with one click)
- Responsive, modern UI with dark mode styling
- Category badges and role display
- User info and logout button
- All data is loaded from the backend MySQL database

**Location:**
`source/Server_admin/www_check_registrations/dashboard.php`

**Usage:**
1. Deploy to your XAMPP or web server (with PHP and MySQL access)
2. Log in with your admin credentials
3. Use the dashboard to monitor, filter, and export participant data

This dashboard is the main tool for event admins to manage registrations and monitor event status in real time.

---
## Configuration Management (`configuration.R`)
[⬆️ Back to Table of Contents](#components-overview)

The `configuration.R` script automates project configuration by:

- Authenticating with Google Drive/Sheets (first run opens a browser for OAuth)
- Listing and locating the credentials/configuration spreadsheet in your Google Drive
- Downloading configuration data from the sheet
- (Optionally) Setting Windows environment variables from the sheet (commented code)
- Copying configuration files to the local XAMPP server (calls `Update_local_xampp_server.R`)

**Usage:**

1. Install required R packages: `googlesheets4`, `googledrive`, `tidyverse`
2. Run `source('configuration.R')` in R
3. Follow authentication prompts if needed
4. Configuration data is loaded and local server is updated automatically

See the script for advanced options (e.g., setting environment variables from the sheet).

The Zeitmessung system consists of firmware (Pico 2 W), backend (PHP/MySQL), and frontend (R/Shiny). The firmware communicates with the backend for configuration and data upload. The Shiny app provides a user interface for race management and results.

The Zeitmessung system consists of:

- **StartGate**: Detects beam break, reads RFID, logs start time, enforces headway
- **FinishGate**: Detects finish beam break, logs finish time
- **RFID (RC522)**: Identifies racers at the start
- **OLED Display**: Shows status, racer info, or finish time
- **Backend**: Collects and manages all race data
- **Frontend**: Registration, management, and results

For a detailed technical description of the firmware, hardware architecture, and timing logic, **see the [MicroPython Project README](source/pico2%20W/micropython/project/README.md)**.

---
## Hardware (Pico 2 W)
[⬆️ Back to Table of Contents](#components-overview)

| Function | Interface | Pin |
|---|---|---|
| Beam 1 (timing) | GPIO input, pull-down | **GP2** |
| Beam 2 (debounce) | GPIO input, pull-down | **GP3** |
| Cancel/Stop button | GPIO input, pull-up | **GP14** |
| On-board LED | GPIO output | `LED` |
| External LED | GPIO output | **GP15** |
| OLED Display | I²C (0x3C) | **GP4** / **GP5** |
| RFID Reader | SPI | **GP10/11/12/13/22** |


---
## Key Features
[⬆️ Back to Table of Contents](#components-overview)

- Dual-beam timing with PIO and DMA for microsecond accuracy
- Native C modules for DMX and RFID (RC522) support
- Automatic WiFi/NTP time sync
- Centralized configuration via backend
- One-step firmware build, flash, and Python file sync


---
## Quick Start
[⬆️ Back to Table of Contents](#components-overview)

All firmware and deployment scripts are in `source/pico2 W/micropython/project/`. See the [detailed project README](source/pico2%20W/micropython/project/README.md) for build, update, and architecture details.


---

## Software Structure
[⬆️ Back to Table of Contents](#components-overview)

```
source/
├── pico2 W/
│   ├── create credentials.R
│   ├── credentials_template.py
│   └── micropython/
│       └── project/
│           ├── build_firmware.sh           # Build custom MicroPython UF2
│           ├── common.py                   # Shared helpers
│           ├── DMX_controller.py           # DMX output support
│           ├── DMX_native_wrapper.py       # Native DMX wrapper
│           ├── DMX_PIO_DMA.py              # PIO/DMA timing
│           ├── finish_gate.py              # FinishGate logic
│           ├── full_update.sh              # Build + flash + sync in one step
│           ├── OLED.py                     # Display driver
│           ├── pico_sdk_import.cmake       # Pico SDK import
│           ├── rc522_lowlevel.py           # RFID driver
│           ├── README.md                   # MicroPython project docs
│           ├── squarewave generator.py     # Squarewave generator
│           ├── start_gate.py               # StartGate logic
│           ├── sync_pico.sh                # Upload Python files via mpremote
│           └── native_modules/             # Native C modules
│               ├── dmx_native/
│               ├── dualbeam_native/
│               ├── rc522_native/
│               └── zeitmessung.cmake
├── OS_support/                  # R helper scripts and templates
├── Server_admin/
│   ├── xampp/                   # PHP API endpoints
│   ├── www_register/            # Participant registration web app
│   └── www_check_registrations/ # Race dashboard
└── SQL/                         # Database scripts
```

---

## Firmware Build & Deployment

[⬆️ Back to Table of Contents](#components-overview)

All scripts live in `source/pico2 W/micropython/project/`. Disconnect any serial monitor / VS Code Pico extension before running.

### One-shot full update (recommended)

```bash
./full_update.sh
```

This builds the firmware, flashes it via USB mass storage, and syncs all Python files — automatically detecting the serial port.

**Options:**

| Flag | Effect |
|---|---|
| `--no-flash` | Skip firmware flash; only build and sync Python files |
| `--core` | Sync only `DMX_controller.py` and `DMX_native_wrapper.py` |
| `--port=/dev/ttyACM0` | Use a specific serial port instead of auto-detect |

### Build firmware only

```bash
./build_firmware.sh
```

Builds a custom MicroPython UF2 for `RPI_PICO2_W` with native C modules (`dmx_native`, `rc522_native`). Output is placed in `project/firmware/`.

### Sync Python files only

```bash
./sync_pico.sh [--all-py|--core] [--port=auto|/dev/ttyACM0]
```

Uploads `.py` files to the board filesystem using `mpremote`.

**Install mpremote if missing:**

```bash
/usr/bin/python3 -m pip install --user --break-system-packages mpremote
```

---
## Configuration
[⬆️ Back to Table of Contents](#components-overview)

- **WiFi credentials:** copy `source/pico2 W/credentials_template.py` → `source/pico2 W/credentials.py` and fill in SSID / password.
- **Server endpoints & headway:** managed centrally via `Server_admin/xampp/device_params.php` on the backend.
- **I²C address / bus:** adjust in `source/pico2 W/micropython/project/OLED.py` if using a different display model.

