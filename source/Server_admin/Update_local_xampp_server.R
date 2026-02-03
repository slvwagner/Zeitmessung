# Script to update htdocs in local xampp server
library(tidyverse)
library(rebus)
slvwagner::r_path

# The path to local xampp server and its subdirectory
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

# dashboard for all xampp files
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
