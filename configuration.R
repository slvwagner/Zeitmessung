# Load the library
library(googlesheets4)
library(googledrive)
library(tidyverse)

if(!drive_has_token()){
  # Authenticate with Google (opens browser the first time)
  gs4_auth()
}

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

# # Copy all file to local xammp server
# source("source/Server_admin/Update_local_xampp_server.R")
# 
# # Create named vector
# env_vec <- df_config$value
# names(env_vec) <- df_config$name
# 
# # set user variables 
# set_windows_env <- function(name, value, scope = "user") {
#   # scope: "user" (default) or "machine" (requires admin)
#   
#   value_escaped <- gsub('"', '""', value)  # Escape double quotes
#   value_escaped <- gsub('%', '%%', value_escaped)  # Escape percent signs
#   
#   if (scope == "user") {
#     cmd <- sprintf('setx "%s" "%s"', name, value_escaped)
#   } else if (scope == "machine") {
#     cmd <- sprintf('setx "%s" "%s" /M', name, value_escaped)
#   } else {
#     stop("scope must be 'user' or 'machine'")
#   }
#   
#   result <- system(cmd, intern = TRUE, ignore.stderr = FALSE)
#   return(result)
# }
# 
# # Set them 
# for (ii in 1:length(env_vec)) {
#   set_windows_env(names(env_vec)[ii], env_vec[ii])
# }
# 
# # check them 
# Sys.getenv(names(env_vec))
# 
# 
