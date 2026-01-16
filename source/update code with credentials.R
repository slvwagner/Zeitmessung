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
    re_ipv4_leading_zeros_in_text <- "(?<!\\d)(?:25[0-5]|2[0-4]\\d|1\\d\\d|0?\\d?\\d)(?:\\.(?:25[0-5]|2[0-4]\\d|1\\d\\d|0?\\d?\\d)){3}(?!\\d)"
    
    ip_raw[6:length(ip_raw)] |>
      str_extract(re_ipv4_leading_zeros_in_text) |>
      na.omit() |>
      first()
  }
}

# get host server IP
DB_hoste_name <- get_host_ipv4()
DB_hoste_name

# get credentials
c_file <- "credentials.py"

if(length(list.files(pattern = ".py")) == 0) {
  warning("File: credentials.py cannot be found")
  warning("A new credentials.py will be created from credentials_template.py")
  file.copy("source/credentials_template.py", c_file)
}

# get file with credentials
c_raw <- readLines(c_file)
c_raw

# update host ip
c_new <- str_replace_all(
  c_raw,
  # regex for IPv4 inside http://
  "http://[0-9\\.]+",
  paste0("http://", DB_hoste_name))
c_new

# get wifi connection
SSID <- Sys.getenv("SSID")
if(str_length(SSID)>0){
  c_new[str_detect(c_new,"SSID")] <- paste0("SSID = ", SSID)
}else{
  warning("API_KEY not found in system variables")
}

SSID_PW <- Sys.getenv("SSID_PW")
if(str_length(SSID_PW)>0){
  c_new[str_detect(c_new,"PASSWORD")] <- paste0("PASSWORD = ", SSID_PW)
}else{
  warning("API_KEY not found in system variables")
}

# Get API key to secure the communication
API_KEY <- Sys.getenv("API_KEY")
if(str_length(API_KEY)>0){
  c_new[str_detect(c_new,"API_KEY")] <- paste0("API_KEY = ", API_KEY)
}else{
  warning("API_KEY not found in system variables")
}



writeLines(c_new)
# write updated file
writeLines(c_new, c_file)

