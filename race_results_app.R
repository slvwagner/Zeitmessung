library(shiny)
library(DBI)
library(RMySQL)
library(tidyverse)
library(rebus)
library(hms)
library(DT)

message("Race track app startup")

# Data table in german ####
DT_language <- list(
  lengthMenu = "Zeige _MENU_ Zeilen pro Seite", # Text für das Dropdown-Menü
  search = "Suchen:", # Text für das Suchfeld
  searchPlaceholder = "Suchbegriff eingeben...", # Platzhaltertext für das Suchfeld
  zeroRecords = "Keine passenden Einträge gefunden", # Text, wenn keine Einträge gefunden wurden
  info = "Zeige _START_ bis _END_ von _TOTAL_ Einträgen", # Info-Text
  infoEmpty = "Zeige 0 bis 0 von 0 Einträgen", # Info-Text, wenn keine Einträge vorhanden sind
  infoFiltered = "(gefiltert aus _MAX_ Einträgen)", # Info-Text bei Filterung
  paginate = list(
    first = "Erste Seite", # Text für die erste Seite
    last = "Letzte Seite", # Text für die letzte Seite
    `next` = "Nächste Seite", # Text für die nächste Seite
    previous = "Vorherige Seite" # Text für die vorherige Seite
  )
)

# get date type for each column from a data frame ####
get_data_type <- function(df){
  1:ncol(df)|>
    lapply(function(x){
      c_temp <- df|>
        select(all_of(x))|>
        pull()
      class(c_temp)[1] # only use the first class
    })|>
    unlist()
}

# Escape regex literals ####
escape_regex <- function(pattern) {
  gsub("([][{}()+*^$|\\\\?.])", "\\\\\\1", pattern)
}

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
  
  titlePanel("Race results (Live)"),
  shiny::hr(),
  DTOutput("in_race"),
  shiny::hr(),
  DTOutput("race_results")
)

# Server ####
server <- function(input, output, session) {
  
  ## SQL connection ####
  con <- DB_connect("wagnius", "zeitmessung", "race", "49rb61")
  
  ## reactive values ####
  ### Filters race results #### 
  last_user_filter <- shiny::reactiveVal(NULL)
  current_data <- shiny::reactiveVal(NULL)
  
  ## Poll DB for Results ####
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
      
      return(bind_rows(l_results))
    }
  )
  
  ## Poll DB in race ####
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
          pageLength = 5,
          language = DT_language
          )
      )
  })
  
  # Render in results table ####
  output$race_results <- renderDT({
    
    df_temp <-race_data()|>
      arrange(Zeit)|>
      mutate(Rang = row_number(),
             # Stunden = lubridate::hour(Zeit),
             # Minuten = lubridate::minute(Zeit),
             # Sekunden = lubridate::second(Zeit),
             Zeit = paste0(as.character(round(Zeit,3)), " Sekunden")
             )|>
      select(Rang, Startnummer, Zeit)
    
    current_data(df_temp)
    
    datatable(
      df_temp, 
      rownames = FALSE,
      filter = "top",
      options = 
        list(
          fixedHeader = TRUE,  # This keeps headers visible
          scrollX = TRUE,  # Enable horizontal scrolling
          pageLength = 50,
          dom = 'lftip',
          language = DT_language,
          searchCols = last_user_filter()
        )
      )
  })
  
  
  ## check if last user filter has been cleared ####
  observeEvent(input$race_results_search_columns,{
    
    # last rendered data 
    df_temp <- current_data()
    
    # get user filters
    column_filters = input$race_results_search_columns
    column_filters <- column_filters|>
      str_remove_all("\"")|>
      str_remove_all("\\[")|>
      str_remove_all("\\]")
    column_filters <- str_split(column_filters,",")
    
    # Update last user filter
    c_test <- lapply(column_filters, function(x){
      nchar(x) > 0
    })|>
      unlist()
    
    # get column data type
    c_class <- get_data_type(df_temp)
    
    ### extract data from column filters ####
    for (ii in 1:length(column_filters)) {
      col_filter <- column_filters[[ii]]
      if(nchar(col_filter[1]) > 0){
        if(c_class[ii] %in% c("Date", "hms")){
          c_date <- pull(df_temp[,ii])|>
            as.character()
          df_temp <- df_temp[str_detect(c_date, col_filter),]
          df_temp <- df_temp[!is.na(pull(df_temp[,ii])),]
        } 
        else if(c_class[ii] == "integer"){
          p1 <- "^[\\d]+"
          p2 <- "[\\d]+$"
          start <- str_extract(col_filter, p1)|>
            as.integer()
          end <- str_extract(col_filter, p2)|>
            as.integer()
          c_select <- start:end
          df_temp <- df_temp[pull(df_temp[,ii]) %in% c_select,]
        } else if (c_class[ii] == "factor"){
          if(length(col_filter) > 1){
            df_temp <- df_temp[pull(df_temp[,ii]) %in% col_filter,] 
          } else {
            c_select <- str_detect(pull(df_temp[,ii]), col_filter)
            c_select <- ifelse(is.na(c_select), FALSE, c_select)
            df_temp <- df_temp[c_select,]
          }
        }
        # character 
        else { 
          col_filter <- col_filter|>
            tolower()|>
            escape_regex()
          
          df_temp <- df_temp[str_detect(pull(df_temp[,ii])|>tolower(), col_filter),] 
          df_temp <- df_temp[!is.na(pull(df_temp[,ii])),]
        }
      }
    }
    
    ### if column filters are present update column filters #####
    if(sum(!c_test) != length(column_filters)) {
      column_filters_temp <- input$race_results_search_columns|>
        lapply(function(x){
          if(nchar(x) > 0) {
            list(search = x)
          } 
          else {
            NULL
          }
        })
      last_user_filter(column_filters_temp)
    } else {
      last_user_filter(NULL)
    }
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
