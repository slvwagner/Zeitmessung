library(DBI)
library(RMariaDB)
library(tidyverse)

message("Race track app startup")
source("source/SQL_Functions.R")
source("update code with credentials.R")

Sys.setenv(
  ZEIT_DB_HOST = "localhost",
  ZEIT_DB_NAME = "zeitmessung_V2",
  ZEIT_DB_USER = "race",
  ZEIT_DB_PASS = "49rb61",
  ZEIT_DB_PORT = "3306"
)


## SQL connection ####
con <- dbConnect(
  RMariaDB::MariaDB(),
  dbname   = "zeitmessung_V2",
  host     = DB_hoste_name,
  user     = "race",
  password = "49rb61"
)

paritcipant <- tbl(con, "participant")
paritcipant
race <- tbl(con, "race")
race

joined_data <- left_join(
  race,
  paritcipant|>
    select(-last_updated, -created_at),
  by = c("participant_id" = "id")
)

joined_data|>
  show_query()

joined_data
