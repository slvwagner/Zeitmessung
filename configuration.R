# Load the library
library(googlesheets4)
library(googledrive)
library(tidyverse)

# Authenticate with Google (opens browser the first time)
gs4_auth()

# Read a sheet by URL
df_config <- read_sheet("https://docs.google.com/spreadsheets/d/1giX8P4z-sPaArBX6QtJb0lKKEugx_rlB28Wi1UGjusM/edit?gid=0#gid=0")

df_config
  

# List all spreadsheets in your Drive
df_spreadsheets <- drive_find(type = "spreadsheet")
df_spreadsheets

id <- df_spreadsheets|>
  filter(str_detect(name,"Zeitmessung"))|>
  select(id)|>
  pull()


# Or by Sheet ID
df_config <- read_sheet(id)
df_config

# Read a specific sheet/tab
df <- read_sheet("your-sheet-id", sheet = "Sheet1")

# Write data to Google Sheets
write_sheet(mtcars, ss = "your-sheet-id", sheet = "mtcars_data")

# Create a new spreadsheet
new_ss <- gs4_create("my-new-spreadsheet", sheets = list(data = iris))