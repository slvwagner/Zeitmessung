# Script to update htdocs in local xampp server
library(tidyverse)
library(rebus)
slvwagner::r_path

# The path to local xampp server and its subdirectory for this project must be defined in environment variable
c_xampp_path <- chartr("\\", "/", Sys.getenv("xampp_server"))
c_xampp_path

if (c_xampp_path == "") {
  stop("Environment variable 'xampp_server' is not set")
}

if (file.access(c_xampp_path, 2) != 0) {
  stop("No write permission to: ", c_xampp_path)
}

# Source and xampp server sub directories 
c_paths <- c("www_check_registrations", "www_register","xampp")
c_paths

# Unlink or delete old data on xampp server for this project
unlink(c_xampp_path, recursive = TRUE)

# Recreate the project directory
dir.create(c_xampp_path)

# Recreate the sub directory structure
c_xampp_subdirectories <- (paste0(c_xampp_path, "/", c_paths))
c_xampp_subdirectories

for (ii in 1:length(c_xampp_subdirectories)) {
  dir.create(c_xampp_subdirectories[ii])  
}

# Source files 
source_files <- list.files(path = paste0( "source/",c_paths), full.names = TRUE)
source_files

# xampp server files
xampp_files <- str_remove(source_files, "source/")
xampp_files <- paste0(c_xampp_path,"/", xampp_files)
xampp_files

# Copy data 
for (ii in 1:length(source_files)) {
  file.copy(source_files, xampp_files)

}

message("The following files:\n", paste0(".../",source_files, collapse = "\n"),"\nhave been written to ", paste0(xampp_files, collapse = "\n"))

# dashboars



