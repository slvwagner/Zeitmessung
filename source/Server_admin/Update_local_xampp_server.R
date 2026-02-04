# Script to update htdocs in local xampp server ####
library(rebus)
library(googlesheets4)
library(googledrive)
library(tidyverse)

# The path to local xampp server and its sub directories
# for this project must be defined in environment variable
c_xampp_path <- chartr("\\", "/", Sys.getenv("xampp_server"))
c_xampp_path

if (c_xampp_path == "") {
  stop("Environment variable 'xampp_server' is not set")
}

if (file.access(c_xampp_path, 2) != 0) {
  stop("No write permission to: ", c_xampp_path)
}

# Create config.php files ####

# Update credentials in config files ####
r_is_defined <- function(sym) {
  sym <- deparse(substitute(sym))
  env <- parent.frame()
  exists(sym, env)
}

# only load google sheet if not already available
if(!r_is_defined(df_config) | !drive_has_token()){
  # Load the library
  library(tidyverse)
  
  # Authenticate with Google (opens browser the first time)
  gs4_auth()
  
  # List all spreadsheets in your Drive
  df_spreadsheets <- drive_find(type = "spreadsheet")
  df_spreadsheets
  
  id <- df_spreadsheets|>
    filter(str_detect(name,"credentials"))|>
    select(id)|>
    pull()
  
  
  # Or by Sheet ID
  df_config <- read_sheet(id)
  df_config
}

## Source directories ####
c_paths <- c("www_check_registrations", "www_register", "xampp")
c_paths

## Updadate www_register with credentials ####
c_path <- paste0("source/Server_admin/", c_paths[2], "/")
c_path

c_raw <- readLines(paste0(c_path, "config_template.php"))
c_raw

c_value <- df_config|>
  filter(str_detect(name, "DB_host"))|>
  select(value)|>
  pull()
c_raw[str_detect(c_raw, "DB_HOST")] <-
  str_replace(c_raw[str_detect(c_raw, "DB_HOST")], "localhost", c_value)
c_raw

c_value <- df_config|>
  filter(str_detect(name, "DB_name_register"))|>
  select(value)|>
  pull()
c_value
c_raw[str_detect(c_raw, "DB_NAME")] <-
  str_replace(c_raw[str_detect(c_raw, "DB_NAME")], "data_base_name",  c_value)
c_raw

c_value <- df_config|>
  filter(str_detect(name, "DB_user_register"))|>
  select(value)|>
  pull()
c_value
c_raw[str_detect(c_raw, "DB_USER")] <-
  str_replace(c_raw[str_detect(c_raw, "DB_USER")], "register_user",  c_value)
c_raw

c_value <- df_config|>
  filter(str_detect(name, "DB_password_register"))|>
  select(value)|>
  pull()
c_value
c_raw[str_detect(c_raw, "DB_PASSWORD")][1] <-
  str_replace(c_raw[str_detect(c_raw, "DB_PASSWORD")][1], "your_password_here",  c_value)
c_raw

c_value <- df_config|>
  filter(str_detect(name, "RECAPTCHA_SITE_KEY"))|>
  select(value)|>
  pull()
c_value
c_raw[str_detect(c_raw, "RECAPTCHA_SITE_KEY")] <-
  str_replace(c_raw[str_detect(c_raw, "RECAPTCHA_SITE_KEY")], "c_site_key",  c_value)
c_raw

c_value <- df_config|>
  filter(str_detect(name, "RECAPTCHA_SECRET_KEY"))|>
  select(value)|>
  pull()
c_value
c_raw[str_detect(c_raw, "RECAPTCHA_SECRET_KEY")] <-
  str_replace(c_raw[str_detect(c_raw, "RECAPTCHA_SECRET_KEY")], "c_site_sec",  c_value)
c_raw

### write config file ####
writeLines(c_raw,paste0(c_path,"config.php"))

writeLines(paste0("Config.php has been updated in the folder: ", getwd(), "/", c_path))

##  Update www_check_register with credentials ####
c_path <- paste0("source/Server_admin/", c_paths[1], "/")
c_path

c_raw <- readLines(paste0(c_path, "config_template.php"))
c_raw

c_value <- df_config|>
  filter(str_detect(name, "DB_host"))|>
  select(value)|>
  pull()
c_raw[str_detect(c_raw, "DB_HOST")] <-
  str_replace(c_raw[str_detect(c_raw, "DB_HOST")], "localhost", c_value)
c_raw

c_value <- df_config|>
  filter(str_detect(name, "DB_name_register"))|>
  select(value)|>
  pull()
c_value
c_raw[str_detect(c_raw, "DB_NAME")] <-
  str_replace(c_raw[str_detect(c_raw, "DB_NAME")], "data_base_name",  c_value)
c_raw

c_value <- df_config|>
  filter(str_detect(name, "DB_user_register"))|>
  select(value)|>
  pull()
c_value
c_raw[str_detect(c_raw, "DB_USER")] <-
  str_replace(c_raw[str_detect(c_raw, "DB_USER")], "register_user",  c_value)
c_raw

c_value <- df_config|>
  filter(str_detect(name, "DB_password_register"))|>
  select(value)|>
  pull()
c_value
c_raw[str_detect(c_raw, "DB_PASSWORD")][1] <-
  str_replace(c_raw[str_detect(c_raw, "DB_PASSWORD")][1], "your_password_here",  c_value)
c_raw

### write config file ####
writeLines(c_raw,paste0(c_path,"config.php"))
writeLines(paste0("Config.php has been updated in the folder: ", getwd(), "/", c_path))

# Copy config.php files to local xampp folder ####
for (ii in paste0(c_xampp_path, "/",c_paths)) {
  # Unlink or delete old data on xampp server for this project
  unlink(ii, recursive = TRUE)
  # Recreate the file structure
  dir.create(ii)
  print(ii)
}

# Source files
source_files <- list.files(path = paste0("source/Server_admin/", c_paths), full.names = TRUE)
source_files

# xampp server files
xampp_files <- str_remove(source_files, "source/Server_admin/")
xampp_files <- paste0(c_xampp_path, "/", xampp_files)
xampp_files

# Copy data
for (ii in seq_along(source_files)) {
  file.copy(source_files, xampp_files)
}

message("The following files:\n",
  paste0(".../", source_files, collapse = "\n"), "\nhave been written to ", paste0(xampp_files, collapse = "\n")
)


# dashboard for all xampp files ####
dashboard_source <- "source/Server_admin/dashboard.html"

file.copy(
  dashboard_source,
  paste0(c_xampp_path, "/dashboard.html"), overwrite = TRUE
)
message(
  "\nThe file ", c_xampp_path, "/" , dashboard_source, " has been written to ",
  paste0(c_xampp_path, "/dashboard.html")
)

# favicon
source_file <- "index.php"
target_file <- paste0(c_xampp_path, "/", source_file)
source_file <- paste0(getwd(), "/source/Server_admin/", source_file)

file.copy(
  source_file,
  target_file, overwrite = TRUE
)
message(
  "\nThe file ", source_file, " has been written to ", target_file
)

# index.php
source_file <- "index.php"
target_file <- paste0(c_xampp_path, "/", source_file)
source_file <- paste0(getwd(), "/source/Server_admin/", source_file)

file.copy(
  source_file,
  target_file, overwrite = TRUE
)
message(
  "\nThe file ", source_file, " has been written to ", target_file
)
