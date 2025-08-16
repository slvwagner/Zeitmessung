library(shiny)
library(DBI)
library(RMySQL)
library(tidyverse)
library(rebus)
library(hms)
library(DT)

message("Race track app startup")

source("source/SQL_Functions.R")

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
  # shiny::verbatimTextOutput("next_in_race"),
  shiny::htmlOutput("next_in_race", style = "margin: 20px;"),  # Added margin for spacing
  shiny::hr(),
  DTOutput("in_race"),
  shiny::actionButton("desqualified", "Ausgeschieden"),
  shiny::hr(),
  DTOutput("race_results")

  
)

# Server ####
server <- function(input, output, session) {
  
  ## SQL connection ####
  con <- DB_connect("wagnius", "zeitmessung", "race", "49rb61")
  
  ## reactive values ####
  ### Filters race results #### 
  last_user_filter_race_results <- shiny::reactiveVal(NULL)
  last_user_filter_in_race <- shiny::reactiveVal(NULL)
  ### currently rendered ####
  current_data_race_results <- shiny::reactiveVal(NULL)
  current_data_in_race <- shiny::reactiveVal(NULL)
  ### last rendered ####
  last_data_in_race <- shiny::reactiveVal(NULL)
  ### last selected row in tables ####
  last_selected_row_race_results <- shiny::reactiveVal(NULL)
  last_selected_row_in_race <- shiny::reactiveVal(NULL)
  last_selected_ID_in_race <- shiny::reactiveVal(NULL)
  
  # next in race ####
  next_nb_in_race <- shiny::reactiveVal(NULL)
  
  ## Store sorting state ####
  sorting_state_race_results <- reactiveVal(NULL)
  sorting_state_in_race <- reactiveVal(NULL)
  
  ## Disqualifiziern von Startnummern ####
  observeEvent(input$desqualified, {
    if(!is.null(last_selected_row_in_race())){
      DB_update_cell(con, "race", "id", last_selected_ID_in_race(), "race_status", "Disqualifiziert") 
      # update timestamp to enable in race table to update
      c_timestamp <- str_remove(Sys.time(), "CEST")
      DB_update_cell(con, "race", "id", last_selected_ID_in_race(), "timestamp", c_timestamp) 
    }
  })
  
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
  ## Poll DB in race ####
  ## Poll DB in race ####
  race_ongoing <- reactivePoll(
    intervalMillis = 3000,  # 3 seconds
    session = session,
    
    checkFunc = function() {
      dbGetQuery(con, "SELECT MAX(timestamp) AS last_ts FROM race")$last_ts
    },
    
    valueFunc = function() {
      # Get all race data
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
      
      # Calculate next start number
      active_racers <- df_race %>% 
        filter(race_status == "race_started") %>%
        arrange(Startnummer)
      
      if (nrow(active_racers) > 0) {
        # Case 1: There are active racers - next number is highest active + 1
        next_nb <- max(active_racers$Startnummer, na.rm = TRUE) + 1
      } else {
        # Case 2: No active racers - next number is highest existing + 1
        max_nb <- df_race %>%
          summarise(max_nb = max(Startnummer, na.rm = TRUE)) %>%
          pull(max_nb)
        
        next_nb <- ifelse(is.infinite(max_nb), 1, max_nb + 1)
      }
      
      # Update the reactive value
      next_nb_in_race(paste("Nächste Startnummer:", next_nb))
      
      # Return filtered data for the table
      active_racers
    }
  )
  
  # Render in race table ####
  output$in_race <- renderDT({
    df_temp <- race_ongoing()|>
      mutate(value = factor(value))
    # save for later use
    current_data_in_race(df_temp)
    
    datatable(
      df_temp, 
      rownames = FALSE,
      filter = "top",
      selection = "single", 
      options = 
        list(
          stateSave = FALSE,   # we'll handle restoring state manually
          order = sorting_state_in_race() %||% list(list(0, "asc")),  
          fixedHeader = TRUE,  # This keeps headers visible
          scrollX = TRUE,  # Enable horizontal scrolling
          pageLength = 5,
          dom = 'lftip',
          language = DT_language,
          searchCols = last_user_filter_in_race(),
          initComplete = JS(
            "function(settings, json) {",
            "  // Signal that table has been rendered",
            "  Shiny.setInputValue('in_race_rendered', new Date().getTime());",
            "}"
          )
        )
    )
  })
  
  # Render in results table ####
  output$race_results <- renderDT({
    df_temp <- race_data()|>
      arrange(Zeit)|>
      mutate(Rang = row_number(),
             Zeit = paste0(as.character(round(Zeit,3)), " Sekunden")
      )|>
      select(Rang, Startnummer, Zeit)
    
    current_data_race_results(df_temp)
    
    datatable(
      df_temp, 
      rownames = FALSE,
      filter = "top",
      selection = "single", 
      options = 
        list(
          stateSave = FALSE,   # we'll handle restoring state manually
          order = sorting_state_race_results() %||% list(list(0, "asc")),  
          scrollX = TRUE,  # Enable horizontal scrolling
          pageLength = 50,
          dom = 'lftip',
          language = DT_language,
          searchCols = last_user_filter_race_results(),
          initComplete = JS(
            "function(settings, json) {",
            "  // Signal that table has been rendered",
            "  Shiny.setInputValue('race_results_rendered', new Date().getTime());",
            "}"
          )
        )
    )
  })
  
  # Render nächste Startnummer ####
  output$next_in_race <- renderUI({
    if(!is.null(next_nb_in_race())) {
      shiny::tags$div(
          next_nb_in_race(), style = "font-size: 30px; font-weight: bold; color: #f4cccc;"
      )
    }
  })
  
  # Observe the sorting state for both tables
  shiny::observe({
    if (!is.null(input$race_results_state$order)) {
      sorting_state_race_results(input$race_results_state$order)
    }
    if (!is.null(input$in_race_state$order)) {
      sorting_state_in_race(input$in_race_state$order)
    }
  })
  
  ## Signal: race results table has been rendered ####
  shiny::observeEvent(input$race_results_rendered, {
    writeLines("Signal: Result table has been rendered")
    dataTableProxy('race_results')|>
      selectRows(last_selected_row_race_results())
  })
  
  ## Signal: in race table has been rendered ####
  shiny::observeEvent(input$in_race_rendered, {
    writeLines("Signal: In race table has been rendered")
    if(is.null(last_data_in_race())){
      last_data_in_race(current_data_in_race())
    }
    
    if(is.null(last_selected_row_in_race())) req(NULL) # early exit
    
    if(!identical(current_data_in_race(), last_data_in_race())){
      
      current_data_in_race()|>
        mutate(index = row_number())|>
        filter(id == last_selected_ID_in_race())|>
        select(index)|>
        last_selected_row_in_race()
            
      if(!is.null(last_selected_row_in_race())){
        dataTableProxy('in_race')|>
          selectRows(last_selected_row_in_race())
      }
    } else {
      if(!is.null(last_selected_row_in_race())){
        dataTableProxy('in_race')|>
          selectRows(last_selected_row_in_race())
      }
    }
    
  })
  
  ## get selected rows from table race results ####
  shiny::observeEvent(input$race_results_rows_selected, {
    last_selected_row_race_results(input$race_results_rows_selected)
  })
  
  ## get selected rows from table in race ####
  shiny::observeEvent(input$in_race_rows_selected, {
    last_selected_row_in_race(input$in_race_rows_selected)
    
    current_data_in_race()[input$in_race_rows_selected,]$id|>
      last_selected_ID_in_race() 
    
    print(current_data_in_race()[input$in_race_rows_selected,])
  })
  
  ## check if last user filter has been cleared for race results ####
  shiny::observeEvent(input$race_results_search_columns,{
    df_temp <- current_data_race_results()
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
      last_user_filter_race_results(column_filters_temp)
    } else {
      last_user_filter_race_results(NULL)
    }
  })
  
  ## check if last user filter has been cleared for in_race table ####
  shiny::observeEvent(input$in_race_search_columns,{
    df_temp <- current_data_in_race()
    column_filters = input$in_race_search_columns
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
      column_filters_temp <- input$in_race_search_columns|>
        lapply(function(x){
          if(nchar(x) > 0) {
            list(search = x)
          } 
          else {
            NULL
          }
        })
      last_user_filter_in_race(column_filters_temp)
    } else {
      last_user_filter_in_race(NULL)
    }
  })
  
  ## Close connection when session ends ####
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