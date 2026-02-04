# Script to update htdocs in local xampp server ####
library(tidyverse)
library(rebus)
slvwagner::r_path

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

# Source and xampp server sub directories
c_paths <- c("www_check_registrations", "www_register", "xampp")
c_paths


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

# copy and edit config.php`s ####

## Upddate check registrations with credential ####
c_path <- "source/Server_admin/www_check_registrations/"
file.copy(paste0(c_path, "config_template.php"), paste0(c_path, "config.php"))

c_raw <- readLines(paste0(c_path, "config.php"))
c_raw <- readLines(paste0(c_path, "config_template.php"))
c_raw

c_raw[str_detect(c_raw, "DB_HOST")] <-
  str_replace(c_raw[str_detect(c_raw, "DB_HOST")], "localhost",  Sys.getenv("DB_host"))
c_raw

c_raw[str_detect(c_raw, "DB_NAME")] <-
  str_replace(c_raw[str_detect(c_raw, "DB_NAME")], "data_base_name",  Sys.getenv("DB_name_register"))
c_raw


c_raw[str_detect(c_raw, "DB_USER")] <-
  str_replace(c_raw[str_detect(c_raw, "DB_USER")], "register_user",  Sys.getenv("DB_user_register"))
c_raw

c_raw[str_detect(c_raw, "DB_PASSWORD")][1] <-
  str_replace(c_raw[str_detect(c_raw, "DB_PASSWORD")][1], "your_password_here",  Sys.getenv("DB_password_register"))
c_raw


c_raw == readLines(paste0(c_path, "config.php"))

## update config for register page  with credentials ####
c_path <- "source/Server_admin/www_register/"
file.copy(paste0(c_path, "config_template.php"), paste0(c_path, "config.php"))



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
