library(RMySQL)
library(DBI)
library(tidyverse)

# connection to Database ####
DB_connect <- function(DB_host, DB_name, DB_user, DB_PW = NULL, con = NULL, max_attempts = 3) {
  attempt <- 1
  
  while(attempt <= max_attempts) {
    # Check if connection exists and is valid
    if (!is.null(con) && dbIsValid(con)) {
      return(con)
    }
    
    tryCatch({
      con <- dbConnect(
        MySQL(),
        host = DB_host,
        user = DB_user,
        password = DB_PW,
        dbname = DB_name,
        port = 3306
      )
      return(con)
    }, error = function(e) {
      message(sprintf("Connection attempt %d failed: %s", attempt, e$message))
      if(attempt == max_attempts) {
        stop("Failed to connect after ", max_attempts, " attempts")
      }
      Sys.sleep(2^attempt) # Exponential backoff
      attempt <<- attempt + 1
    })
  }
}

# DB_name <- "ch367079_GUI_testing_envir"
DB_host <-"localhost";
DB_name <-"zeitmessung";
DB_user <- "root";


con <- DB_connect(DB_host, DB_name, DB_user)

library(rebus)

df_race <- tbl(con,"race")|>
  collect()|>
  suppressWarnings()
df_race

df_race <- df_race|>
  mutate(POSIXct = as.POSIXct(timestamp, format = "%Y-%m-%d %H:%M:%OS", tz = "UTC"))|>
  rename(Startnummer = value)|>
  separate(col = timestamp, into = c("Datum", "Zeit"), sep = " ")|>
  mutate(Startnummer = str_extract(Startnummer, one_or_more(DGT)%R%END)|>as.integer(),
         Datum = as.Date(Datum),
         Zeit = hms::parse_hms(Zeit)
         )

df_race|>
  arrange(race_status, POSIXct)

# calculate the results 
c_Startnummern <- df_race|>
  distinct(Startnummer)|>
  pull()

l_results <- list()
ii <- 10
for (ii in 1:length(c_Startnummern)) {
  df_test <- df_race|>
    filter(Startnummer == c_Startnummern[ii])
  df_test
  
  l_results[[ii]] <- 
    tibble(
      df_test[1,"Startnummer"],
      Zeit = df_test[2,]$POSIXct - df_test[1,]$POSIXct,
      Zeit2 = df_test[2,]$Zeit - df_test[1,]$Zeit
    )
  
}

l_results|>
  bind_rows()|>
  print()
