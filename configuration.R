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

source("source/Server_admin/Update_local_xampp_server.R")
