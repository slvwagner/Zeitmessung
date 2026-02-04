# commonly used helper functions


# ------------------------------------------------------------
# Get host IPv4 address (cross-platform)
# ------------------------------------------------------------
get_host_ipv4 <- function() {
  os <- Sys.info()[["sysname"]]
  
  if (os == "Windows") {
    ip_raw <- system("ipconfig", intern = TRUE)
    
    # Windows output contains "IPv4 Address"
    ip_raw |>
      str_subset("IPv4") |>
      str_extract("\\d+\\.\\d+\\.\\d+\\.\\d+") |>
      first()
    
  } else {
    # Linux / Ubuntu / macOS
    ip_raw <- system("ip -4 addr show", intern = TRUE)
    re_ipv4_leading_zeros_in_text <- "(?<!\\d)(?:25[0-5]|2[0-4]\\d|1\\d\\d|0?\\d?\\d)(?:\\.(?:25[0-5]|2[0-4]\\d|1\\d\\d|0?\\d?\\d)){3}(?!\\d)"
    
    ip_raw[6:length(ip_raw)] |>
      str_extract(re_ipv4_leading_zeros_in_text) |>
      na.omit() |>
      first()
  }
}