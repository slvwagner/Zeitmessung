library(DBI)
library(RMariaDB)
library(tidyverse)

message("Race track app startup")
source("source/SQL_Functions.R")
source("update code with credentials.R")

## SQL connection ####
con <- dbConnect(
  RMariaDB::MariaDB(),
  dbname   = "zeitmessung_V2",
  host     = DB_hoste_name,
  user     = "race",
  password = "49rb61"
)

paritcipant <- tbl(con, "participant")
race <- tbl(con, "race")

left_join(, by = c("paticipant_id" = "id"))
