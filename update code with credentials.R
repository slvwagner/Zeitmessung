library(tidyverse)

# get host server IP
ip <- system("ipconfig", intern = TRUE)

re_ipv4_leading_zeros_in_text <- "(?<!\\d)(?:25[0-5]|2[0-4]\\d|1\\d\\d|0?\\d?\\d)(?:\\.(?:25[0-5]|2[0-4]\\d|1\\d\\d|0?\\d?\\d)){3}(?!\\d)"

DB_hoste_name <- grep("IPv4", ip, value = TRUE)|>
  str_extract(re_ipv4_leading_zeros_in_text)

DB_hoste_name

# get credentials
c_file <- "credentials.py"
c_raw <- readLines(c_file)

c_new <- str_replace_all(
  c_raw,
  # regex for IPv4 inside http://
  "http://[0-9\\.]+",
  paste0("http://", DB_hoste_name))
c_new

# write updated file
writeLines(c_new, c_file)

