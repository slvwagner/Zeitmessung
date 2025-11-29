# test_insert_race.R
# Simple tester for insert_race.php

library(httr)
library(jsonlite)

# Helper: create timestamp "YYYY-MM-DD HH:MM:SS.mmm"
make_timestamp_ms <- function(time = Sys.time(), tz = "Europe/Zurich") {
  old_opts <- options(digits.secs = 3)
  on.exit(options(old_opts), add = TRUE)
  
  t <- as.POSIXct(time, tz = tz)
  format(t, "%Y-%m-%d %H:%M:%OS3", tz = tz)
}

# --- CONFIG ---
url <- "http://127.0.0.1/insert_race.php"

# This should match your TZ_H in start_gate.py (e.g. +1 for CET)
timezone_offset <- 1L

# Test payload (change values as you like)
payload <- list(
  Startnummer      = 1L,
  run              = 1L,
  timestamp_ms     = make_timestamp_ms(),
  timezone_offset  = timezone_offset,
  device_id        = "R_TEST_DEVICE_01",
  device_name      = "R_Test_Client",
  race_status      = "started",
  # optional dual-beam fields:
  speed_mps        = 12.34,
  speed_kmh        = 44.4,
  beam_distance_mm = 43.18
)

cat("Sending JSON payload:\n")
print(payload)

# If you later enable API key in PHP, add this header:
# res <- POST(
#   url,
#   body = payload,
#   encode = "json",
#   add_headers("X-API-Key" = "change_me")
# )

res <- POST(
  url,
  body   = payload,
  encode = "json"
)

cat("\nHTTP status:", status_code(res), "\n")

txt <- content(res, as = "text", encoding = "UTF-8")
cat("Raw response:\n", txt, "\n")

cat("\nParsed JSON:\n")
print(fromJSON(txt))
