library(tidyverse)

library(tidyverse)

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
    
    ip_raw |>
      str_extract("(?<=inet\\s)\\d+(\\.\\d+){3}") |>
      na.omit() |>
      first()
  }
}

DB_hoste_name <- get_host_ipv4()

if (is.na(DB_hoste_name)) {
  stop("Could not determine host IPv4 address")
}

DB_hoste_name


# get host server IP
ip <- system("ipconfig", intern = TRUE)

re_ipv4_leading_zeros_in_text <- "(?<!\\d)(?:25[0-5]|2[0-4]\\d|1\\d\\d|0?\\d?\\d)(?:\\.(?:25[0-5]|2[0-4]\\d|1\\d\\d|0?\\d?\\d)){3}(?!\\d)"

ip[str_detect(ip,"IPv4")][1]


DB_hoste_name <- ip[str_detect(ip,"IPv4")][1]|>
  str_extract(re_ipv4_leading_zeros_in_text)

DB_hoste_name

# get credentials
c_file <- "credentials_template.py"
c_raw <- readLines(c_file)

c_new <- str_replace_all(
  c_raw,
  # regex for IPv4 inside http://
  "http://[0-9\\.]+",
  paste0("http://", DB_hoste_name))
c_new

writeLines(c_new)
# write updated file
writeLines(c_new, c_file)

