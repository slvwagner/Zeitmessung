library(shiny)
library(DBI)
library(RMariaDB)
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
  shiny::htmlOutput("next_in_race", style = "margin: 20px;"),  # Added margin for spacing
  shiny::hr(),
  shiny::actionButton("desqualified", "Ausgeschieden"),
  DTOutput("in_race"),
  shiny::hr(),
  shiny::actionButton("update_info", "Update racer info"),  # New button
  DTOutput("race_results")

  
)

# Server ####
server <- function(input, output, session) {
  ## SQL connection ####
  con <- dbConnect(
    RMariaDB::MariaDB(),
    dbname   = "zeitmessung",
    host     = "192.168.0.17",
    user     = "race",
    password = "49rb61"
  )
  
  # Set connection options to handle MySQL types better
  dbExecute(con, "SET SESSION sql_mode='ANSI'")
  
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
  
  ## observe button: Disqualifiziern von Startnummern ####
  observeEvent(input$desqualified, {
    if(!is.null(last_selected_row_in_race())){
      DB_update_cell(con, "race", "id", last_selected_ID_in_race(), "race_status", "Disqualifiziert") 
      # update timestamp to enable in race table to update
      c_timestamp <- str_remove(Sys.time(), "CEST")
      DB_update_cell(con, "race", "id", last_selected_ID_in_race(), "timestamp", c_timestamp) 
    }
  })
  
  ## Observe button: update info button ####
  observeEvent(input$update_info, {
    req(input$race_results_rows_selected)
    
    selected_row <- current_data_race_results()[input$race_results_rows_selected, ]
    showModal(updateInfoModal(selected_row))
  })
  
  ## Poll DB for Results ####
  race_data <- reactivePoll(
    intervalMillis = 3000,
    session = session,
    
    checkFunc = function() {
      # Use explicit type casting
      query <- "SELECT UNIX_TIMESTAMP(MAX(last_updated)) AS last_update_ts FROM race"
      dbGetQuery(con, query)$last_update_ts
    },
    
    valueFunc = function() {
      # Use explicit column selection
      df_race <- tbl(con, "race")|>
        collect()|>
        mutate(
          Startnummer = as.integer(str_extract(value, "\\d+$"))
        )
      
      # Calculate results
      c_Startnummern <- df_race |> distinct(Startnummer) |> pull()
      
      l_results <- map(c_Startnummern, function(sn) {
        df_test <- filter(df_race, Startnummer == sn)
        if (nrow(df_test) >= 2) {
          tibble(
            id = df_test$id,
            Startnummer = df_test[1, "Startnummer"][[1]],
            Zeit = df_test[2, ]$timestamp - df_test[1, ]$timestamp,
            Name = df_test$Name,
            Vorname = df_test$Vorname, 
            Phone = df_test$Phone,
            `E-mail` = df_test$`E-mail`
          )
        } else {
          NULL
        }
      })
      
      df_temp <- bind_rows(l_results)|>
        distinct(Startnummer, .keep_all = TRUE)|>
        arrange((Zeit))|>
        mutate(Rang = row_number())
      
      return(df_temp)
    }
  )
  
  ## Poll DB in race ####
  race_ongoing <- reactivePoll(
    intervalMillis = 3000,  # 3 seconds
    session = session,
    
    checkFunc = function() {
      dbGetQuery(con, "SELECT MAX(last_updated) AS last_update FROM race")$last_update
    },
    
    valueFunc = function() {
      # Get all race data
      df_race <- tbl(con, "race") |>
        collect() |>
        mutate(
          POSIXct = as.POSIXct(timestamp, format = "%Y-%m-%d %H:%M:%OS", tz = "UTC"),
          Startnummer = value
        ) |>
        separate(timestamp, into = c("Datum", "Zeit"), sep = " ") |>
        mutate(
          Startnummer = str_extract(Startnummer, one_or_more(DGT) %R% END) |> as.integer(),
          Datum = as.Date(Datum),
          Zeit = parse_hms(Zeit)
        ) |>
        arrange(race_status, POSIXct)
      
      # Calculate next start number
      active_racers <- df_race |> 
        filter(race_status == "race_started") |>
        arrange(Startnummer)
      
      if (nrow(active_racers) > 0) {
        # Case 1: There are active racers - next number is highest active + 1
        next_nb <- max(active_racers$Startnummer, na.rm = TRUE) + 1
      } else {
        # Case 2: No active racers - next number is highest existing + 1
        max_nb <- df_race |>
          summarise(max_nb = max(Startnummer, na.rm = TRUE)) |>
          pull(max_nb)
        
        next_nb <- ifelse(is.infinite(max_nb), 1, max_nb + 1)
      }
      
      # Update the reactive value
      next_nb_in_race(paste("Nächste Startnummer:", next_nb))
      
      # Return filtered data for the table
      active_racers
    }
  )
  
  ## Modal dialog for updating info ####
  updateInfoModal <- function(selected_row) {
    modalDialog(
      title = div(icon("user-edit"), "Update Racer-Inforamtionen"),
      size = "m",
      footer = tagList(
        modalButton("Abbrechen"),
        actionButton("save_info", "Speichern", icon = icon("save"), 
                     class = "btn-primary")
      ),
      fluidRow(
        column(6, textInput("update_vorname", "Vorname", 
                            value = selected_row$Vorname,
                            placeholder = "Max")),
        column(6, textInput("update_name", "Nachname", 
                            value = selected_row$Name,
                            placeholder = "Mustermann")),
        column(6, textInput("update_phone", "Telefon", 
                            value = selected_row$Phone,
                            placeholder = "+41 79 123 45 67")),
        column(6, textInput("update_email", "E-Mail", 
                            value = selected_row$`E-mail`,
                            placeholder = "max.mustermann@example.com"))
      )
    )
  }
  
  ## Enhanced Save Logic ####
  observeEvent(input$save_info, {
    req(input$race_results_rows_selected)
    
    selected_row <- current_data_race_results()[input$race_results_rows_selected, ]
    removeModal()
    
    # Show loading indicator
    showModal(modalDialog(
      title = "Updating Information",
      "Please wait while we update the racer information...",
      footer = NULL
    ))
    
    tryCatch({
      # Create a list of updates to perform
      updates <- list()
      
      if (!is.null(input$update_name) && input$update_name != selected_row$Name) {
        updates$Name <- input$update_name
      }
      
      if (!is.null(input$update_vorname) && input$update_vorname != selected_row$Vorname) {
        updates$Vorname <- input$update_vorname
      }
      
      if (!is.null(input$update_phone) && input$update_phone != selected_row$Phone) {
        updates$Phone <- input$update_phone
      }
      
      if (!is.null(input$update_email) && input$update_email != selected_row$`E-mail`) {
        updates$`E-mail` <- input$update_email
      }
      
      # Only proceed if there are actual changes
      if (length(updates) > 0) {
        # Update all changed fields in a single transaction
        dbWithTransaction(con, {
          for (field in names(updates)) {
            DB_update_cell(con, "race", "id", selected_row$id, field, updates[[field]])
          }
          # Update the refresh trigger
          dbExecute(con, sprintf("UPDATE race SET last_updated = NOW() WHERE id = %d", selected_row$id))
        })
        
        removeModal()
        showNotification("Racer information updated successfully!", 
                         type = "message",
                         duration = 5)
      } else {
        removeModal()
        showNotification("No changes were made.", 
                         type = "warning",
                         duration = 3)
      }
      
    }, error = function(e) {
      removeModal()
      showNotification(
        sprintf("Failed to update information: %s", e$message),
        type = "error",
        duration = NULL  # Persistent until dismissed
      )
    })
  })
  

  
  ## Save updated racer information ####
  observeEvent(input$save_info, {
    req(input$race_results_rows_selected)
    
    selected_row <- current_data_race_results()[input$race_results_rows_selected, ]
    
    # Validate inputs
    if (!is.null(input$update_email) && 
        nchar(input$update_email) > 0 &&
        !grepl("^[^@]+@[^@]+\\.[^@]+$", input$update_email)) {
      showNotification("Invalid email format", type = "error")
      return()
    }
    
    # Show processing modal
    showModal(modalDialog(
      title = "Processing Update",
      "Updating racer information...",
      footer = NULL
    ))
    
    tryCatch({
      # Build update statement dynamically
      updates <- list()
      if (!identical(input$update_name, selected_row$Name)) updates$Name <- input$update_name
      if (!identical(input$update_vorname, selected_row$Vorname)) updates$Vorname <- input$update_vorname
      if (!identical(input$update_phone, selected_row$Phone)) updates$Phone <- input$update_phone
      if (!identical(input$update_email, selected_row$`E-mail`)) updates$`E-mail` <- input$update_email
      
      if (length(updates) > 0) {
        # Generate parameterized SQL
        set_clauses <- paste(names(updates), "= ?", sep = "", collapse = ", ")
        query <- sprintf("UPDATE race SET %s, last_updated = NOW() WHERE id = ?", set_clauses)
        
        # Execute with parameters
        params <- c(unname(updates), selected_row$id)
        dbExecute(con, query, params = params)
        
        removeModal()
        showNotification("Update successful!", type = "message")
      } else {
        removeModal()
        showNotification("No changes detected", type = "warning")
      }
      
    }, error = function(e) {
      removeModal()
      showNotification(
        paste("Update failed:", e$message),
        type = "error",
        duration = NULL
      )
    })
  })
  
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
      arrange(Startnummer)|>
      mutate(Zeit = paste0(as.character(round(Zeit,3)), " Sekunden")
      )|>
      select(id, Rang, Startnummer, Zeit, Vorname, Name, Phone, `E-mail`)
    
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