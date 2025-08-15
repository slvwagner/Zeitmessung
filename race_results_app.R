library(shiny)
library(DBI)
library(RMySQL)
library(tidyverse)
library(rebus)
library(hms)
library(DT)

message("Race track app startup")

# DB connection function ####
DB_connect <- function(DB_host, DB_name, DB_user, DB_PW = NULL, max_attempts = 3) {
  con <- NULL
  attempt <- 1
  while (attempt <= max_attempts) {
    if (!is.null(con) && dbIsValid(con)) return(con)
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
      if (attempt == max_attempts) stop("Failed after ", max_attempts, " attempts")
      Sys.sleep(2^attempt)
      attempt <<- attempt + 1
    })
  }
}

# Serve the custom_styles directory
shiny::addResourcePath("custom_styles", "source/css")

# UI ####
ui <- fluidPage(
  shiny::tags$head(
    shiny::tags$link(rel = "stylesheet", type = "text/css", 
                     href = paste0("custom_styles/dark.css?v=", as.integer(Sys.time()))
    )
  ),
  
  titlePanel("Race Results (Live)"),
  shiny::hr(),
  DTOutput("in_race"),
  DTOutput("race_results")
)

# Server ####
server <- function(input, output, session) {
  
  ## SQL connection ####
  con <- DB_connect("wagnius", "zeitmessung", "race", "49rb61")
  

  
  # Poll DB every 3 seconds, reuse the same connection
  race_data <- reactivePoll(
    intervalMillis = 3000,  # 3 seconds
    session = session,      # ✅ Fix: explicitly pass session
    
    checkFunc = function() {
      dbGetQuery(con, "SELECT MAX(timestamp) AS last_ts FROM race")$last_ts
    },
    
    valueFunc = function() {
      df_race <- tbl(con, "race") %>%
        collect() %>%
        suppressWarnings() %>%
        mutate(
          POSIXct = as.POSIXct(timestamp, format = "%Y-%m-%d %H:%M:%OS", tz = "UTC"),
          Startnummer = value
        ) %>%
        separate(timestamp, into = c("Datum", "Zeit"), sep = " ") %>%
        mutate(
          Startnummer = str_extract(Startnummer, one_or_more(DGT) %R% END) %>% as.integer(),
          Datum = as.Date(Datum),
          Zeit = parse_hms(Zeit)
        ) %>%
        arrange(race_status, POSIXct)
      
      # Calculate results
      c_Startnummern <- df_race %>% distinct(Startnummer) %>% pull()
      
      l_results <- map(c_Startnummern, function(sn) {
        df_test <- filter(df_race, Startnummer == sn)
        if (nrow(df_test) >= 2) {
          tibble(
            Startnummer = df_test[1, "Startnummer"][[1]],
            Zeit = df_test[1, ]$POSIXct - df_test[2, ]$POSIXct
          )
        } else {
          NULL
        }
      })
      
      bind_rows(l_results)
    }
    
  )
  
  # Poll DB every 3 seconds, reuse the same connection
  race_ongoing <- reactivePoll(
    intervalMillis = 3000,  # 3 seconds
    session = session,      # ✅ Fix: explicitly pass session
    
    checkFunc = function() {
      dbGetQuery(con, "SELECT MAX(timestamp) AS last_ts FROM race")$last_ts
    },
    
    valueFunc = function() {
      df_race <- tbl(con, "race") %>%
        collect() %>%
        suppressWarnings() %>%
        mutate(
          POSIXct = as.POSIXct(timestamp, format = "%Y-%m-%d %H:%M:%OS", tz = "UTC"),
          Startnummer = value
        ) %>%
        separate(timestamp, into = c("Datum", "Zeit"), sep = " ") %>%
        mutate(
          Startnummer = str_extract(Startnummer, one_or_more(DGT) %R% END) %>% as.integer(),
          Datum = as.Date(Datum),
          Zeit = parse_hms(Zeit)
        ) %>%
        arrange(race_status, POSIXct)
      
      df_race|>
        filter(race_status == "race_started")
      
    }
    
  )
  
  # Render in race table ####
  output$in_race <- renderDT({
    datatable(
      race_ongoing(), 
      rownames = FALSE,
      options = 
        list(
          fixedHeader = TRUE,  # This keeps headers visible
          scrollX = TRUE,  # Enable horizontal scrolling
          pageLength = 5
          )
      )
  })
  
  # Render in results table ####
  output$race_results <- renderDT({
    datatable(
      race_data(), 
      rownames = FALSE,
      options = 
        list(
          fixedHeader = TRUE,  # This keeps headers visible
          scrollX = TRUE,  # Enable horizontal scrolling
          pageLength = 50
        )
      )
  })
  
  # Close connection when session ends ####
  session$onSessionEnded(function() {
    if (dbIsValid(con)) {
      dbDisconnect(con)
      message("\ndisconnected from SQL server")
    } else {
      message("\nconnection was already lost to SQL server")
    }
    message("The app has been closed by the user")
  })

}

shinyApp(ui, server)
