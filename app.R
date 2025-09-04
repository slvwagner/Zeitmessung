# app.R — Shiny app for zeitmessung_V2 (event-log model) — UPDATED FOR Startnummer

# --- Packages ---
library(shiny)
library(DBI)
library(pool)
library(RMariaDB)
library(tidyverse)
library(DT)

# Kategorie ####
c_categorie <- c("Standard", "Pimped")

# Data table in german ####
DT_language <- list(
  lengthMenu = "Zeige _MENU_ Zeilen pro Seite",
  search = "Suchen:",
  searchPlaceholder = "Suchbegriff eingeben...",
  zeroRecords = "Keine passenden Einträge gefunden",
  info = "Zeige _START_ bis _END_ von _TOTAL_ Einträgen",
  infoEmpty = "Zeige 0 bis 0 von 0 Einträgen",
  infoFiltered = "(gefiltert aus _MAX_ Einträgen)",
  paginate = list(
    first = "Erste Seite",
    last = "Letzte Seite",
    `next` = "Nächste Seite",
    previous = "Vorherige Seite"
  )
)

# Serve the custom_styles directory  ####
shiny::addResourcePath("custom_styles", "source/css")

# poll database ####
pool <- dbPool(
  drv = RMariaDB::MariaDB(),
  host = Sys.getenv("ZEIT_DB_HOST", "localhost"),
  dbname = Sys.getenv("ZEIT_DB_NAME", "zeitmessung_V2"),
  user = Sys.getenv("ZEIT_DB_USER", "root"),
  password = Sys.getenv("ZEIT_DB_PASS", ""),
  port = as.integer(Sys.getenv("ZEIT_DB_PORT", "3306")),
  bigint = "integer"
)


# connection to registration Database ####
DB_connect <- function(DB_host, DB_name, DB_user, DB_PW, con = NULL, max_attempts = 3) {
  library(RMySQL)
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

# Participants registrations ####
## Data base credentials from system variables for https://lx51.hoststar.hosting/ ####
DB_host <- Sys.getenv("DB_host")
DB_name <- "ch367079_race"
DB_user <- Sys.getenv("DB_user")
DB_pw <- Sys.getenv("DB_PASSWORD_KINOKLUB")

## database connection ####
con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)

df_registered <- tbl(con, "participant")|>
  collect()|>
  suppressWarnings()|>
  mutate(Geburtsdatum = as.Date(Geburtsdatum))
df_registered

DBI::dbDisconnect(con)

## close database connection ####
onStop(function() {
  poolClose(pool)
})


# Helpers ####
now_ms <- function() {
  format(Sys.time(), "%Y-%m-%d %H:%M:%OS3")
}

# Create/refresh a summary view that pairs started/finished per run — UPDATED for Startnummer
ensure_summary_view <- function(pool) {
  sql <- "CREATE OR REPLACE VIEW race_summary AS
  SELECT 
    p.Startnummer,
    p.Name,
    p.Vorname,
    s.run,
    s.start_time,
    f.finish_time,
    CASE 
      WHEN f.finish_time IS NOT NULL THEN TIMESTAMPDIFF(MICROSECOND, s.start_time, f.finish_time) / 1000.0
      ELSE NULL
    END AS duration_ms
  FROM
    (SELECT Startnummer, run, MIN(timestamp_ms) AS start_time
     FROM race WHERE race_status = 'started'
     GROUP BY Startnummer, run) s
  LEFT JOIN
    (SELECT Startnummer, run, MAX(timestamp_ms) AS finish_time
     FROM race WHERE race_status = 'finished'
     GROUP BY Startnummer, run) f
  ON s.Startnummer = f.Startnummer AND s.run = f.run
  LEFT JOIN participant p ON p.Startnummer = s.Startnummer;"
  dbExecute(pool, sql)
}

# Initialize view
try(ensure_summary_view(pool), silent = TRUE)

# UI ####
ui <- function()fluidPage(
  shiny::tags$head(
    shiny::tags$link(rel = "stylesheet", type = "text/css", 
                     href = paste0("custom_styles/dark.css?v=", as.integer(Sys.time())))
  ),
  titlePanel("Zeitmessung"),
  tabsetPanel(
    tabPanel("Registrierung importieren",
             fluidRow(
               column(3,
                      h3("Registrierung"),
                      actionButton("import_participant", "Teilnehmer importieren", class = "btn-success"),
                      h3("Aktualisieren"),
                      actionButton("update_participant", "Teilnehmerliste aktualisieren", class = "btn-success"),
               ),
               column(8,
                      h3("Registrierungen"),
                      DTOutput("registered_tbl"),
                      br()
               )
             )
             
    ),
    tabPanel("Teilnehmer",
             fluidRow(
               column(3,
                      h3("Teilnehmer"),
                      actionButton("add_participant", "Teilnehmer hinzufügen", class = "btn-success"),
                      actionButton("open_edit_modal", "Teilnehmer editieren", class = "btn-primary"),
                      actionButton("delete_participant", "Teilnehmer löschen", class = "btn-warning"),
                      h3("Startreihenfolge"),
                      actionButton("race_order_up", "Startreihenfolge", 
                                   class = "btn btn-success", icon = icon("arrow-up")),
                      actionButton("race_order_down", "Startreihenfolge", 
                                   class = "btn btn-success", icon = icon("arrow-down")
                      ),
                      h3("RFID suchen"),
                      textInput("edit_rfid_dec", "RFID (vom USB-Reader, Dezimal)", value = ""),
                      textInput("edit_rfid_le", "RFID UID (LE, gespeichert)", value = "", placeholder = "AA:BB:CC:DD"),
                      actionButton("find_RFID", "RFID UID (LE, gespeichert)", 
                                   class = "btn btn-success")
               ),
               column(8,
                      h3("Startreihenfolge"),
                      DTOutput("participants_tbl"),
               )
             )
             
    ),
    tabPanel("Messungen",
             fluidRow(
               column(3,
                      h3("Insert event"),
                      uiOutput("participant_select_ui"),
                      numericInput("run_number", "Run number", value = NA, min = 1),
                      selectInput("race_status", "Status", choices = c("started", "interim 1", "interim 2", "finished", "disqualify"), selected = "started"),
                      textInput("device_id", "Device ID", value = "chip001"),
                      textInput("device_name", "Device name", value = "Gate"),
                      checkboxInput("use_now", "Timestamp = NOW (ms)", value = TRUE),
                      textInput("timestamp_free", "Custom timestamp (YYYY-mm-dd HH:MM:SS.mmm)", value = now_ms()),
                      actionButton("insert_event", "Insert event", class = "btn btn-success"),
                      br(),
                      h4("Quick demo for selected participant"),
                      actionButton("demo_sequence", "Insert demo: start → interim → finish", class = "btn btn-secondary")
               ),
               column(8,
                      h3("Events (race)"),
                      DTOutput("events_tbl")
               )
             )
    ),
    tabPanel("Rangliste",
             fluidRow(
               column(3,
                      h3("Filters"),
                      uiOutput("participant_filter_ui")
               ),
               column(8,
                      h3("Rangliste"),
                      DTOutput("summary_tbl")
               )
             )
    )
  )
)

# Server ####
server <- function(input, output, session) {
  
  ## Rective values ####
  ### Track last known database tables ####
  last_db_participant_update <- reactiveVal(NULL)
  last_db_race_update <- reactiveVal(NULL)
  last_race_summary <- reactiveVal(NULL)
  last_user_filter_in_race <- reactiveVal(NULL)
  
  ### last selected in data table ####
  last_selected_row <- reactiveVal(NULL)
  last_selected_page <- reactiveVal(NULL)
  
  df_registered <- reactiveVal(df_registered)
  
  ## Helper functions ####

  ### get date type for each column from a data frame ####
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
  
  # Normalize "5a:91:a7:af" / "5A91A7AF" → "5A:91:A7:AF"
  norm_uid_le <- function(x) {
    x <- toupper(gsub("[^0-9A-F]", "", x))
    if (nchar(x) != 8) return(NA_character_)
    paste(substring(x, c(1,3,5,7), c(2,4,6,8)), collapse=":")
  }
  
  # DB lookup: RFID (LE) → Startnummer
  lookup_startnummer_by_uid <- function(pool, uid_le) {
    if (is.na(uid_le) || !nzchar(uid_le)) return(NA_integer_)
    res <- DBI::dbGetQuery(pool,
                           "SELECT Startnummer FROM participant WHERE rfid_uid_le = ? LIMIT 1",
                           params = list(uid_le)
    )
    if (nrow(res) == 1) as.integer(res$Startnummer[1]) else NA_integer_
  }
  
  # 8-digit, zero-padded, uppercase hex from unsigned 32-bit (no overflow)
  to_hex32 <- function(n) {
    stopifnot(!is.na(n), n >= 0, n < 2^32)
    bytes <- integer(4)
    for (i in 1:4) {
      bytes[i] <- n %% 256L
      n <- n %/% 256L
    }
    paste(sprintf("%02X", rev(bytes)), collapse = "")
  }
  
  # Accepts either:
  #  - a pure decimal string from your USB reader (e.g. "1514672170"), or
  #  - an already-formatted hex like "5A:91:A7:AF" (any case)
  # Returns LE hex "AA:BB:CC:DD" (11 chars) or NA_character_ if invalid.
  rfid_to_le_hex <- function(s) {
    if (is.null(s)) return(NA_character_)
    s <- trimws(as.character(s))
    if (!nzchar(s)) return(NA_character_)
    
    # Case 1: looks like hex with colons already?
    if (grepl("^([0-9A-Fa-f]{2}:){3}[0-9A-Fa-f]{2}$", s)) {
      # Normalize to uppercase
      return(toupper(s))
    }
    
    # Case 2: looks decimal – keep digits only (USB readers sometimes add CR/LF)
    dec <- gsub("[^0-9]", "", s)
    if (!nzchar(dec)) return(NA_character_)
    # Needs to fit in 32-bit
    suppressWarnings({
      x <- as.numeric(dec)
    })
    if (is.na(x) || x < 0 || x >= 2^32) return(NA_character_)
    
    hx <- to_hex32(x)                           # e.g. "5A91A7AF"
    bytes <- substring(hx, c(1,3,5,7), c(2,4,6,8))
    le <- paste(rev(bytes), collapse=":")       # -> "AF:A7:91:5A"
    toupper(le)
  }
  
  # DB check for uniqueness (optionally ignore one Startnummer when editing)
  rfid_exists_elsewhere <- function(pool, le, ignore_startnummer = NULL) {
    if (is.na(le) || !nzchar(le)) return(FALSE)
    sql <- "SELECT Startnummer FROM participant WHERE rfid_uid_le = ?"
    hit <- DBI::dbGetQuery(pool, sql, params = list(le))
    if (nrow(hit) == 0) return(FALSE)
    if (!is.null(ignore_startnummer)) {
      return(any(hit$Startnummer != ignore_startnummer))
    }
    TRUE
  }
  
  
  print_RFID_tag <- function(dec_str) {
    dec_str <- gsub("[^0-9]", "", dec_str)
    if (nchar(dec_str) == 0) return()
    
    x <- suppressWarnings(as.numeric(dec_str))
    if (is.na(x) || x >= 2^32) {
      cat("⚠️ Ungültiger Code:", dec_str, "\n")
      return()
    }
    
    hx <- to_hex32(x)
    bytes <- substring(hx, c(1,3,5,7), c(2,4,6,8))
    be <- paste(bytes, collapse=":")
    le <- paste(rev(bytes), collapse=":")
    
    cat("\n--- Neuer Tag ---\n")
    cat("Decimal     :", dec_str, "\n")
    cat("Hex (BE)    :", be, "\n")
    cat("Hex (LE)    :", le, "\n")
    cat("LE string   :", le, "\n")   # 👈 explicit paste(rev(bytes), collapse=":")
    return(le)
  }
  
  check_participants_update <- function() {
    max_update <- dbGetQuery(pool, "SELECT MAX(last_updated) as max_update FROM participant")$max_update
    if (is.na(max_update)) return("")
    as.character(max_update)
  }
   
  check_race_update <- function() {
    max_update <- dbGetQuery(pool, "SELECT MAX(last_updated) as max_update FROM race")$max_update
    if (is.na(max_update)) return("")
    as.character(max_update)
  }

  get_participants <- function(){
    data <- tbl(pool, "participant")|>
      collect()|> 
      arrange(race_order)

    bind_rows(
      data|>
        filter(is.na(last_run))|>
        arrange(race_order),
      data|>
        filter(!is.na(last_run))|>
        arrange(last_run, race_order)
    )
  }
  
  update_race_order <- function(pool, ids, new_race_order) {
    # Build CASE statements for race_order
    case_parts <- paste0("WHEN ", ids, " THEN ", new_race_order, collapse = " ")
    in_list    <- paste(ids, collapse = ", ")
    
    sql <- sprintf("
    UPDATE participant
    SET race_order = CASE Startnummer
        %s
      END,
        last_updated = NOW(3)
    WHERE Startnummer IN (%s)
  ", case_parts, in_list)
    
    dbExecute(pool, sql)
  }
  
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
  
  # Escape regex literals
  escape_regex <- function(pattern) {
    gsub("([][{}()+*^$|\\\\?.])", "\\\\\\1", pattern)
  }
  
  ms_to_hms <- function(ms) {
    total_seconds <- ms / 1000
    
    hours   <- total_seconds %/% 3600
    minutes <- (total_seconds %% 3600) %/% 60
    seconds <- round(total_seconds %% 60, 3)  # keep milliseconds if wanted
    
    sprintf("%02d:%02d:%06.3f", hours, minutes, seconds)
  }
  
  # In ADD / IMPORT dialog: convert raw -> LE on the fly
  observeEvent(input$edit_rfid_dec, {
    le <- rfid_to_le_hex(input$edit_rfid_dec)
    updateTextInput(session, "edit_rfid_le", value = ifelse(is.na(le), "", le))
  }, ignoreInit = TRUE)
  
  # Also normalize manual edits to edit_rfid_le (uppercasing and format validation)
  observeEvent(input$edit_rfid_le, {
    v <- input$edit_rfid_le
    if (is.null(v) || !nzchar(v)) return()
    if (grepl("^([0-9A-Fa-f]{2}:){3}[0-9A-Fa-f]{2}$", v)) {
      updateTextInput(session, "edit_rfid_le", value = toupper(v))
    } else {
      showNotification("⚠️ Ungültiges RFID-Format. Erwartet: AA:BB:CC:DD", type = "warning")
    }
  }, ignoreInit = TRUE)
  
  observeEvent(input$find_RFID, {
    req(input$edit_rfid_le)
    df_test <- tbl(pool, "participant")|>
      filter(rfid_uid_le == input$edit_rfid_le)|>
      collect()
    
    df_temp <- df_test
    
    table_search_columns <- input$participants_tbl_search_columns
    table_search_columns[2] <- input$edit_rfid_le
    
    # get user filters
    column_filters = table_search_columns
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
      column_filters_temp <- table_search_columns|>
        lapply(function(x){
          if(nchar(x) > 0) {
            list(search = x)
          } 
          else {
            NULL
          }
        })
    }
    
    # apply the filters
    last_user_filter_in_race(column_filters_temp)
    
    showNotification("Teilnehmer gefunden", type = "message")

  })
  
  ## Signal: last selected row ####
  observeEvent(input$participants_tbl_rows_selected, {
    last_selected_row(input$participants_tbl_rows_selected)
    writeLines(paste("Last selected row:", last_selected_row()))
  })
  
  
  ## Signal: Datatable participants has been rendered ####
  observeEvent(input$participants_tbl_signal, {
    writeLines("Signal: paticipants has been rendered")
    
    if(is.null(last_selected_row()) ) req(NULL) # early exit because not initalized
    
    # select in table
    dataTableProxy('participants_tbl')|>
      selectRows(last_selected_row())
    
  })
  
  ## change participant race order up ####
  observeEvent(input$race_order_up, {
    req(input$participants_tbl_rows_selected)
    
    df_test <- get_participants()
    c_startnummer <- participants_data()[input$participants_tbl_rows_selected,]$Startnummer
    
    head(df_test, n = 20)|>
      print()
    
    # row selected in table
    selected_row <- df_test|>
      mutate(race_order = row_number())|>
      filter(Startnummer == c_startnummer)|>
      select(race_order)|>
      pull()
    selected_row
    
    if(selected_row == 1){
      req(NULL) # early exit
    } else if (selected_row == 2){
      df_new <- bind_rows(
        df_test[selected_row,],
        df_test[(selected_row - 1),],
        df_test[(selected_row + 1):nrow(df_test),]
      )|>
        mutate(race_order = row_number())
      df_new|>
        print()
    } else if (selected_row == nrow(df_test)) {
      df_new <- bind_rows(
        df_test[1:(selected_row - 2),],
        df_test[selected_row,],
        df_test[(selected_row - 1),]
      )|>
        mutate(race_order = row_number())
      df_new|>
        print()
    } else {
      df_new <- bind_rows(
        df_test[(1:(selected_row - 2)),],
        df_test[selected_row,],
        df_test[(selected_row - 1),],
        df_test[(selected_row + 1):nrow(df_test),]
      )|>
        mutate(race_order = row_number())
      df_new|>
        print()
    }
    
    # Update race order
    update_race_order(pool, ids = df_new$Startnummer, new_race_order = df_new$race_order)
    
    # update last selected row
    (last_selected_row() - 1)|>
      last_selected_row()
    
  })
  
  ## change participant race order down ####
  observeEvent(input$race_order_down, {
    req(input$participants_tbl_rows_selected)
    
    df_test <- get_participants()
    c_startnummer <- participants_data()[input$participants_tbl_rows_selected,]$Startnummer
    
    head(df_test, n = 20)|>
      print()
    
    # row selected in table
    selected_row <- df_test|>
      mutate(race_order = row_number())|>
      filter(Startnummer == c_startnummer)|>
      select(race_order)|>
      pull()
    selected_row
    
    if(selected_row == nrow(df_test)){
      req(NULL) # early exit
    } else if (selected_row == (nrow(df_test) - 1)){
      df_new <- bind_rows(
        df_test[1:(nrow(df_test) - 2),],
        df_test[nrow(df_test),],
        df_test[selected_row,]
      )|>
        mutate(race_order = row_number())
      df_new|>
        print()
    } else if (selected_row == (nrow(df_test) - 1)){
      df_new <- bind_rows(
        df_test[(1:(selected_row - 1)),],
        df_test[(selected_row + 1),],
        df_test[selected_row,],
        df_test[(selected_row + 2):nrow(df_test),]
      )|>
        mutate(race_order = row_number())
      df_new|>
        print()
    } else {
      df_new <- bind_rows(
        df_test[(selected_row - 1),],
        df_test[(selected_row + 1),],
        df_test[selected_row,],
        df_test[(selected_row + 2):nrow(df_test),]
      )|>
        mutate(race_order = row_number())
      df_new|>
        print()
    }
    
    # Update race order
    update_race_order(pool, ids = df_new$Startnummer, new_race_order = df_new$race_order)
    
    # update last selected row
    (last_selected_row() + 1)|>
      last_selected_row()
    
  })
  
  observeEvent(input$update_participant, {
    
    # Participants registrations ####
    ## Data base credentials from system variables for https://lx51.hoststar.hosting/ ####
    DB_host <- Sys.getenv("DB_host")
    DB_name <- "ch367079_race"
    DB_user <- Sys.getenv("DB_user")
    DB_pw <- Sys.getenv("DB_PASSWORD_KINOKLUB")
    
    ## database connection ####
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
    
    tbl(con, "participant")|>
      collect()|>
      suppressWarnings()|>
      mutate(Geburtsdatum = as.Date(Geburtsdatum))|>
      as_tibble()|>
      df_registered()
    
    DBI::dbDisconnect(con)
    
    showNotification("Teilnehmerliste aktualisiert", type = "message")
  })
  
  
  ## Import participant####
  observeEvent(input$import_participant, {
    req(input$registered_tbl_rows_selected)
    df_registered
    df <- participants_data()
    
    if (is.null(df) || nrow(df) == 0) {
      sel <- 1L
    } else if (all(is.na(df$Startnummer))) {
      sel <- 1L
    } else {
      sel <- max(df$Startnummer, na.rm = TRUE) + 1L
    }
    
    cat("Calculated Startnummer:", sel, "\n")
    
    # Modal to add participant 
    showModal(modalDialog(
      title = paste0("Teilnehmer hinzufügen: Startnummer ", sel),
      size = "m",
      shiny::tagList(
        textInput("edit_Vorname", "Vorname", value = df_registered()$Vorname[input$registered_tbl_rows_selected]),
        textInput("edit_Name", "Name", value = df_registered()$Name[input$registered_tbl_rows_selected]),
        textInput("edit_Nickname", "Nickname", value = df_registered()$Nickname[input$registered_tbl_rows_selected]),
        textInput("edit_Phone", "Phone", value = df_registered()$Phone[input$registered_tbl_rows_selected]),
        textInput("edit_Email", "E-mail", value = df_registered()$`E-mail`[input$registered_tbl_rows_selected] ),
        shiny::selectInput("edit_Kategorie", "Kategorie",
                           choices = c_categorie, 
                           selected = df_registered()$Kategorie[input$registered_tbl_rows_selected]),
        shiny::dateInput("edit_geburtstag", "Geburtstag", value = df_registered()$Geburtsdatum[input$registered_tbl_rows_selected],
                         format = "dd.mm.yyyy",
                         language = "de",
                         weekstart = 1),
        textInput("edit_rfid_dec", "RFID (vom USB-Reader, Dezimal oder Hex)", value = "", placeholder = "z.B. 1514672170 oder 5A:91:A7:AF"),
        textInput("edit_rfid_le", "RFID UID (LE, gespeichert)", value = "", placeholder = "AA:BB:CC:DD")
        
      ),
      footer = tagList(
        modalButton("Abbrechen"),
        actionButton("save_add_participant", "Save", class = "btn-primary")
      ),
      easyClose = FALSE,
    ))
  })
  
  
  ## Add participant via modal ####
  # open modal when button clicked (requires a row selection)
  observeEvent(input$add_participant, {
    # Calculate next Startnummer with better error handling
    df <- participants_data()
    
    if (is.null(df) || nrow(df) == 0) {
      sel <- 1L
    } else if (all(is.na(df$Startnummer))) {
      sel <- 1L
    } else {
      sel <- max(df$Startnummer, na.rm = TRUE) + 1L
    }
    
    cat("Calculated Startnummer:", sel, "\n")
    
    # Modal to add participant with predicted Startnummer
    showModal(modalDialog(
      title = paste0("Teilnehmer hinzufügen: Startnummer ", sel),
      size = "m",
      shiny::tagList(
        textInput("edit_Name", "Name", value = ""),
        textInput("edit_Vorname", "Vorname", value = ""),
        textInput("edit_Nickname", "Nickname", value = ""),
        textInput("edit_Phone", "Phone", value = ""),
        textInput("edit_Email", "E-mail", value = ""),
        textInput("edit_Kategorie", "Kategorie", value = ""),
        textInput("edit_rfid_dec", "RFID (vom USB-Reader, Dezimal oder Hex)", value = "", placeholder = "z.B. 1514672170 oder 5A:91:A7:AF"),
        textInput("edit_rfid_le", "RFID UID (LE, gespeichert)", value = "", placeholder = "AA:BB:CC:DD")
      ),
      footer = tagList(
        modalButton("Cancel"),
        actionButton("save_add_participant", "Save", class = "btn-primary")
      ),
      easyClose = FALSE,
    ))
  })
  
  # Save changes for ADDING participant
  observeEvent(input$save_add_participant, {
    df_test <- tbl(pool, "participant") |> collect() |> as_tibble()
    
    nm  <- trimws(input$edit_Name %||% "")
    vn  <- trimws(input$edit_Vorname %||% "")
    nn  <- trimws(input$edit_Nickname %||% "")
    ph  <- trimws(input$edit_Phone %||% "")
    em  <- trimws(input$edit_Email %||% "")
    ka  <- trimws(input$edit_Kategorie %||% "")
    ro  <- ifelse(nrow(df_test) == 0 || all(is.na(df_test$race_order)), 1L, max(df_test$race_order, na.rm = TRUE) + 1L)
    
    # RFID: prefer explicit LE field; otherwise convert from DEC field
    rfid_le <- trimws(input$edit_rfid_le %||% "")
    if (!nzchar(rfid_le)) {
      rfid_le <- rfid_to_le_hex(input$edit_rfid_dec)
    }
    if (!is.na(rfid_le) && nzchar(rfid_le) && rfid_exists_elsewhere(pool, rfid_le)) {
      showNotification("❌ RFID ist bereits vergeben.", type = "error"); return()
    }
    
    sql <- "
    INSERT INTO participant 
      (race_order, Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, rfid_uid_le, next_run, last_updated)
    VALUES 
      (?, ?, ?, ?, ?, ?, ?, ?, 1, NOW(3))
  "
    
    tryCatch({
      dbExecute(pool, sql, params = list(
        ro, nm, vn, nn, ph, em, ka,
        if (!is.na(rfid_le) && nzchar(rfid_le)) rfid_le else NA_character_
      ))
      removeModal()
      showNotification("Teilnehmer hinzugefügt", type = "message")
    }, error = function(e) {
      showNotification(paste("Teilnehmer konnte nicht hinzugefügt werden:", e$message), type = "error")
    })
  })
  
  
  
  ## Delete participant via modal ####
  observeEvent(input$delete_participant, {
    req(input$participants_tbl_rows_selected)
    sel <- input$participants_tbl_rows_selected
    
    df <- participants_data()
    row <- df[sel, , drop = FALSE]
    req(nrow(row) == 1)
    
    showModal(modalDialog(
      title = paste0("Willst du den Teilnehmer wirklich löschen: Startnummer ", row$Startnummer),
      size = "m",
      shiny::tagList(
        renderText(row$Name),
        renderText(row$Vorname),
        renderText(row$Nickname),
        renderText(row$Phone),
        renderText(row$`E-mail`),
        renderText(row$Kategorie)
      ),
      footer = tagList(
        modalButton("Abbrechen"),
        actionButton("delete_participant_confirm", "Teilnehmer löschen", class = "btn-danger")
      ),
      easyClose = FALSE,
    ))
  })
  
  ## Delete participant via modal — confirm handler (pool-safe transaction) ####
  observeEvent(input$delete_participant_confirm, {
    req(input$participants_tbl_rows_selected)
    sel <- input$participants_tbl_rows_selected
    
    df  <- participants_data()
    row <- df[sel, , drop = FALSE]
    req(nrow(row) == 1)
    
    sn <- as.integer(row$Startnummer)
    
    tryCatch({
      pool::poolWithTransaction(pool, function(conn) {
        # If you don't have ON DELETE CASCADE, delete child rows first:
        DBI::dbExecute(conn, "DELETE FROM race WHERE Startnummer = ?", params = list(sn))
        # Then delete the participant:
        DBI::dbExecute(conn, "DELETE FROM participant WHERE Startnummer = ?", params = list(sn))
      })
      
      removeModal()
      showNotification(
        sprintf("Teilnehmer %d (%s %s) wurde gelöscht.", sn, row$Vorname %||% "", row$Name %||% ""),
        type = "message"
      )
      
      dataTableProxy("participants_tbl") |> selectRows(NULL)
      last_selected_row(NULL)
      
    }, error = function(e) {
      showNotification(paste("Löschen fehlgeschlagen:", e$message), type = "error")
    })
  })
  
  
  
  
  ## Edit participant via modal ####
  # Safe null/NA helper
  `%||%` <- function(a, b) if (!is.null(a) && !is.na(a) && nzchar(as.character(a))) a else b
  
  ## Open edit modal ####
  observeEvent(input$open_edit_modal, {
    sel <- input$participants_tbl_rows_selected
    req(sel)
    df <- participants_data()
    row <- df[sel, , drop = FALSE]
    req(nrow(row) == 1)
    
    showModal(modalDialog(
      title = paste0("Edit participant: Startnummer ", row$Startnummer),
      size = "m",
      shiny::tagList(
        textInput("edit_Name", "Name", value = row$Name),
        textInput("edit_Vorname", "Vorname", value = row$Vorname),
        textInput("edit_Nickname", "Nickname", value = row$Nickname),
        textInput("edit_Phone", "Phone", value = row$Phone),
        textInput("edit_Email", "E-mail", value = row$`E-mail`),
        shiny::selectInput("edit_Kategorie", "Kategorie",
                           choices = c_categorie, 
                           selected = row$Kategorie
                           ),
        numericInput("edit_race_order", "Race order", value = ifelse(is.na(row$race_order), -1, row$race_order)),
        textInput("edit_rfid_dec", "RFID (vom USB-Reader, Dezimal oder Hex)", value = "", placeholder = "Zum Überschreiben scannen/eingeben"),
        textInput("edit_rfid_le", "RFID UID (LE, gespeichert)", value = row$rfid_uid_le %||% "", placeholder = "AA:BB:CC:DD"),
        
      ),
      footer = tagList(
        modalButton("Cancel"),
        actionButton("save_edit_participant", "Save", class = "btn-primary")
      ),
      easyClose = FALSE,
    ))
  })
  
  ## Save changes for editing participant ####
  observeEvent(input$save_edit_participant, {
    sel <- input$participants_tbl_rows_selected
    req(sel)
    df <- participants_data()
    row <- df[sel, , drop = FALSE]
    req(nrow(row) == 1)
    sn <- as.integer(row$Startnummer)
    
    nm  <- trimws(input$edit_Name %||% "")
    vn  <- trimws(input$edit_Vorname %||% "")
    nn  <- trimws(input$edit_Nickname %||% "")
    ph  <- trimws(input$edit_Phone %||% "")
    em  <- trimws(input$edit_Email %||% "")
    ka  <- trimws(input$edit_Kategorie %||% "")
    ro  <- if (!length(input$edit_race_order) || is.na(input$edit_race_order)) NA_integer_ else as.integer(input$edit_race_order)
    
    # RFID: prefer explicit LE; otherwise convert from DEC
    rfid_le <- trimws(input$edit_rfid_le %||% "")
    if (!nzchar(rfid_le)) {
      rfid_le <- rfid_to_le_hex(input$edit_rfid_dec)
    }
    if (!is.na(rfid_le) && nzchar(rfid_le) && rfid_exists_elsewhere(pool, rfid_le, ignore_startnummer = sn)) {
      showNotification("❌ RFID ist bereits einem anderen Teilnehmer zugeordnet.", type = "error"); return()
    }
    
    sql <- "
    UPDATE participant
       SET race_order   = ?,
           Name         = ?,
           Vorname      = ?,
           Nickname     = ?,
           Phone        = ?,
           `E-mail`     = ?,
           Kategorie    = ?,
           rfid_uid_le  = ?,
           last_updated = NOW(3)
     WHERE Startnummer  = ?;
  "
    
    dbExecute(pool, sql, params = list(
      ro, nm, vn, nn, ph, em, ka,
      if (!is.na(rfid_le) && nzchar(rfid_le)) rfid_le else NA_character_,
      sn
    ))
    
    removeModal()
    showNotification("Teilnehmer aktualisiert", type = "message")
  })
  
  observeEvent(input$insert_event_by_rfid, {
    uid <- norm_uid_le(input$rfid_le)
    if (is.na(uid)) { showNotification("RFID-Format ungültig.", type="error"); return() }
    sn <- lookup_startnummer_by_uid(pool, uid)
    if (is.na(sn)) { showNotification("RFID unbekannt.", type="error"); return() }
    
    ts <- if (isTRUE(input$use_now)) now_ms() else input$timestamp_free
    
    ins_sql <- "INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name, last_updated)
              VALUES (?, ?, ?, ?, ?, ?, NOW(3))"
    dbExecute(pool, ins_sql, params = list(
      sn,
      as.integer(input$run_number %||% 1L),
      ts, input$race_status,
      input$device_id, input$device_name
    ))
    
    if (identical(input$race_status, "started")) {
      dbExecute(pool, "UPDATE participant SET last_run = ?, last_updated = NOW(3) WHERE Startnummer = ?",
                params = list(as.integer(input$run_number %||% 1L), sn))
    }
    if (input$race_status %in% c("finished", "disqualify")) {
      dbExecute(pool, "UPDATE participant SET next_run = next_run + 1, last_updated = NOW(3) WHERE Startnummer = ?",
                params = list(sn))
    }
    
    showNotification(sprintf("Event für RFID %s (SN %d) eingefügt.", uid, sn), type = "message")
  })
  
  
  observeEvent(input$use_rfid, {
    uid <- norm_uid_le(input$rfid_le)
    if (is.na(uid)) {
      showNotification("RFID-Format ungültig. Erwartet z. B. 5A:91:A7:AF", type = "error")
      return()
    }
    sn <- lookup_startnummer_by_uid(pool, uid)
    if (is.na(sn)) {
      showNotification(paste("Kein Teilnehmer mit RFID", uid, "gefunden."), type = "warning")
      return()
    }
    
    # Set the selectInput to this Startnummer
    updateSelectInput(session, "participant_id", selected = sn)
    
    # Also update run number from participant.next_run
    run_no <- dbGetQuery(pool, "SELECT next_run FROM participant WHERE Startnummer = ?",
                         params = list(sn))$next_run
    updateNumericInput(session, "run_number", value = ifelse(length(run_no), run_no, 1))
    
    showNotification(paste("Teilnehmer", sn, "per RFID gewählt."), type = "message")
  })
  
  ## Reactive: participants data with smart polling ####
  participants_data <- 
    reactivePoll(
      1000,
      session,
      checkFunc = function() {
        check_participants_update()
      },
      valueFunc = function() {
        # Your existing valueFunc code here
        tryCatch({
          data <- get_participants()
          
          if (!is.null(data) && nrow(data) > 0) {
            data 
          } else {
            # Return empty data frame with correct structure
            data.frame(
              Startnummer = integer(),
              created_at = as.POSIXct(character()),
              last_updated = as.POSIXct(character()),
              race_order = integer(),
              last_run = integer(),
              next_run = integer(),
              Name = character(),
              Vorname = character(),
              Nickname = character(),
              Phone = character(),
              `E-mail` = character(),
              Kategorie = character(),
              Gewicht = numeric()
            )
          }
        }, error = function(e) {
          showNotification(paste("Database error:", e$message), type = "error")
          # Return empty data frame
          data.frame(
            Startnummer = integer(),
            created_at = as.POSIXct(character()),
            last_updated = as.POSIXct(character()),
            race_order = integer(),
            last_run = integer(),
            next_run = integer(),
            Name = character(),
            Vorname = character(),
            Nickname = character(),
            Phone = character(),
            `E-mail` = character(),
            Kategorie = character(),
            Gewicht = numeric()
          )
        })
      }
    )
  
  ## Reactive: events data with smart polling ####
  events_data <- reactivePoll(3000, session,
                              checkFunc = function() {
                                check_race_update()
                              },
                              valueFunc = function() {
                                tbl(pool, "race") |> collect()|> arrange(desc(id))
                              }
  )
  
  ## Reactive: summary data (depends on both tables) ####
  summary_data <- reactivePoll(3000, session,
                               checkFunc = function() {
                                 # Combine both timestamps to detect changes in either table
                                 paste0(check_participants_update(), "|", check_race_update())
                               },
                               valueFunc = function() {
                                 ensure_summary_view(pool)
                                 
                                 df_test <- tbl(pool, "race_summary")|> 
                                   collect()|> 
                                   arrange(Startnummer, run)
                                 
                                 df_test <- df_test|>
                                   group_by(Startnummer, Name, Vorname)|>
                                   reframe(`Durchschnitliche Laufzeit [ms]` = mean(duration_ms),
                                           `Anzahl Läufe` = n())
                                 
                               }
  )
  
  ## Store current participant selection ####
  current_participant <- reactiveVal(NULL)
  observeEvent(input$participant_id, {
    current_participant(input$participant_id)
  })
  
  ## UI: participant select (uses Startnummer) ####
  output$participant_select_ui <- renderUI({
    df <- participants_data()
    choices <- setNames(df$Startnummer, paste0(df$Startnummer, ": ", df$Vorname, " ", df$Name, " (", df$Nickname,")"))
    selected <- if (!is.null(current_participant()) && current_participant() %in% df$Startnummer) {
      current_participant()
    } else if (nrow(df) > 0) {
      df$Startnummer[1]
    } else {
      NULL
    }
    selectInput("participant_id", "Participant", choices = choices, selected = selected)
  })
  
  ## UI: participant filter (uses Startnummer) ####
  output$participant_filter_ui <- renderUI({
    df <- participants_data()
    selectInput("participant_filter", "Participant (optional)",
                choices = c("All" = "", setNames(df$Startnummer, paste0(df$Startnummer, ": ", df$Name, " ", df$Vorname))),
                selected = "")
  })
  
  ## Populate run number from participant.next_run based on Startnummer ####
  observeEvent(input$participant_id, {
    req(input$participant_id)
    run_no <- dbGetQuery(pool, "SELECT next_run FROM participant WHERE Startnummer = ?",
                         params = list(as.integer(input$participant_id)))$next_run
    updateNumericInput(session, "run_number", value = ifelse(length(run_no), run_no, 1))
  }, ignoreInit = TRUE)

  
  ## Render: Registrierungen ####
  output$registered_tbl <- renderDT({
    
    df_test <- df_registered()
    df_test <- df_test|>
      mutate(Datum_display = format(Geburtsdatum, "%d.%m.%Y")
             )|>
      select(-Startnummer, -last_updated, -race_order, -last_run, -next_run, -Gewicht)
    
    datatable(
      df_test, 
      rownames = FALSE,
      selection = "single",
      filter = "top",
      options = 
        list(
          pageLength = 10,
          scrollX = TRUE,  # Enable horizontal scrolling
          language = DT_language,
          columnDefs = list(
            # list(targets = 3,visible = F),
            list(targets = 7, visible = FALSE), # Hide the 'Datum' column
            list(targets = 8, orderData = 7)    # Use the 'Datum' column for sorting 'Datum_display'
          ),
          initComplete = JS(
            "function(settings, json) {",
            "// One-time header/body styles",
            "  $(this.api().table().header()).css({",
            "    'background-color': '#2d3e50',",
            "    'color': '#ffffff'",
            "  });",
            "  $(this.api().table().body()).css({",
            "    'background-color': '#34495e',",
            "    'color': '#ecf0f1'",
            "  });",
            "  // One-time search/length styling",
            "  $('div.dataTables_filter input').css({",
            "    'background-color': '#2c3e50',",
            "    'color': '#ecf0f1',",
            "    'border': '1px solid #7f8c8d'",
            "  });",
            "  $('div.dataTables_length select').css({",
            "    'background-color': '#2c3e50',",
            "    'color': '#ecf0f1',",
            "    'border': '1px solid #7f8c8d'",
            "  });",
            "  // Signal that table has been rendered",
            "  Shiny.setInputValue('events_tbl', new Date().getTime());",
            "}"
          ),
          drawCallback = JS(
            "function(settings) {",
            "$('a.paginate_button').css({",
            "'background-color': '#7898b6',",
            "'color': '#ffffff',",
            "'border': '1px solid #7f8c8d',",
            "'padding': '5px 10px',",
            "'margin': '0 2px',",
            "'border-radius': '4px',",
            "'text-decoration': 'none'",
            "});",
            
            "$('a.paginate_button.current').css({",
            "'background-color': '#e67e22',",
            "'color': '#ffffff',",
            "'font-weight': 'bold'",
            "});",
            
            "$('a.paginate_button').hover(",
            "function() {",
            "if (!$(this).hasClass('current')) {",
            "$(this).css('background-color', '#5d7d9a');",
            "}",
            "},",
            "function() {",
            "if (!$(this).hasClass('current')) {",
            "$(this).css('background-color', '#7898b6');",
            "}",
            "}",
            ");",
            "}"
          )
        ),
      colnames = c(Datum = "Datum_display")
    )
  })
  
  ## Render: Table participant ####
  output$participants_tbl <- renderDT({
    df_temp <- participants_data()|>
      select(-created_at, -last_updated)|>
      rename(
        RFID = rfid_uid_le,
        Startreihenfolge = race_order,
        `Letzter Lauf` = last_run,
        `Nächster Lauf` = next_run)
    
    df_temp <-  df_temp|>
      mutate(
             Startreihenfolge = factor(Startreihenfolge),
             `Letzter Lauf` = factor(`Letzter Lauf`),
             `Nächster Lauf` = factor(`Nächster Lauf`))|>
      select(Startnummer, RFID, Vorname, Name, Nickname, `E-mail`, Kategorie,  Geburtsdatum )
    writeLines("Render datatable:")
    df_temp|>
      print()
    
    datatable(
      df_temp,
      rownames = FALSE,
      selection = "single",
      filter = "top",
      options = 
        list(
          scrollX = TRUE,  # Enable horizontal scrolling
          language = DT_language,
          pageLength = nrow(df_temp),
          searchCols = last_user_filter_in_race(),
          initComplete = JS(
            "function(settings, json) {",
            "// One-time header/body styles",
            "  $(this.api().table().header()).css({",
            "    'background-color': '#2d3e50',",
            "    'color': '#ffffff'",
            "  });",
            "  $(this.api().table().body()).css({",
            "    'background-color': '#34495e',",
            "    'color': '#ecf0f1'",
            "  });",
            "  // One-time search/length styling",
            "  $('div.dataTables_filter input').css({",
            "    'background-color': '#2c3e50',",
            "    'color': '#ecf0f1',",
            "    'border': '1px solid #7f8c8d'",
            "  });",
            "  $('div.dataTables_length select').css({",
            "    'background-color': '#2c3e50',",
            "    'color': '#ecf0f1',",
            "    'border': '1px solid #7f8c8d'",
            "  });",
            "  // Signal that table has been rendered",
            "  Shiny.setInputValue('participants_tbl_signal', new Date().getTime());",
            "}"
          ),
          drawCallback = JS(
            "function(settings) {",
            "$('a.paginate_button').css({",
            "'background-color': '#7898b6',",
            "'color': '#ffffff',",
            "'border': '1px solid #7f8c8d',",
            "'padding': '5px 10px',",
            "'margin': '0 2px',",
            "'border-radius': '4px',",
            "'text-decoration': 'none'",
            "});",
            
            "$('a.paginate_button.current').css({",
            "'background-color': '#e67e22',",
            "'color': '#ffffff',",
            "'font-weight': 'bold'",
            "});",
            
            "$('a.paginate_button').hover(",
            "function() {",
            "if (!$(this).hasClass('current')) {",
            "$(this).css('background-color', '#5d7d9a');",
            "}",
            "},",
            "function() {",
            "if (!$(this).hasClass('current')) {",
            "$(this).css('background-color', '#7898b6');",
            "}",
            "}",
            ");",
            "}"
          )
        )
    )
  })
  
  ## Render: Table events ####
  output$events_tbl <- renderDT({
    showNotification(paste("New Events found"), type = "message")
    df_test <- events_data()|>
      mutate(Startnummer = factor(Startnummer),
             run = factor(run))|>
      rename(ID = id,
             Lauf = run, 
             Zeitstempel = timestamp_ms,
             `Geräte ID` = device_id, 
             `Gerätename` = device_name, 
             Rennstatus = race_status)|>
      select(,-created_at, -last_updated)
    
    datatable(
      df_test, 
      rownames = FALSE,
      selection = "single",
      filter = "top",
      options = 
        list(
          pageLength = 10,
          scrollX = TRUE,  # Enable horizontal scrolling
          language = DT_language,
          initComplete = JS(
            "function(settings, json) {",
            "// One-time header/body styles",
            "  $(this.api().table().header()).css({",
            "    'background-color': '#2d3e50',",
            "    'color': '#ffffff'",
            "  });",
            "  $(this.api().table().body()).css({",
            "    'background-color': '#34495e',",
            "    'color': '#ecf0f1'",
            "  });",
            "  // One-time search/length styling",
            "  $('div.dataTables_filter input').css({",
            "    'background-color': '#2c3e50',",
            "    'color': '#ecf0f1',",
            "    'border': '1px solid #7f8c8d'",
            "  });",
            "  $('div.dataTables_length select').css({",
            "    'background-color': '#2c3e50',",
            "    'color': '#ecf0f1',",
            "    'border': '1px solid #7f8c8d'",
            "  });",
            "  // Signal that table has been rendered",
            "  Shiny.setInputValue('events_tbl', new Date().getTime());",
            "}"
          ),
          drawCallback = JS(
            "function(settings) {",
            "$('a.paginate_button').css({",
            "'background-color': '#7898b6',",
            "'color': '#ffffff',",
            "'border': '1px solid #7f8c8d',",
            "'padding': '5px 10px',",
            "'margin': '0 2px',",
            "'border-radius': '4px',",
            "'text-decoration': 'none'",
            "});",
            
            "$('a.paginate_button.current').css({",
            "'background-color': '#e67e22',",
            "'color': '#ffffff',",
            "'font-weight': 'bold'",
            "});",
            
            "$('a.paginate_button').hover(",
            "function() {",
            "if (!$(this).hasClass('current')) {",
            "$(this).css('background-color', '#5d7d9a');",
            "}",
            "},",
            "function() {",
            "if (!$(this).hasClass('current')) {",
            "$(this).css('background-color', '#7898b6');",
            "}",
            "}",
            ");",
            "}"
          )
        )
    )
  })
  
  ## Render: Table summary ####
  output$summary_tbl <- renderDT({
    df <- summary_data()
    if (!is.null(input$participant_filter) && nzchar(input$participant_filter)) {
      df <- df |> filter(Startnummer == as.integer(input$participant_filter))
    }
    df
    
    df <- df|>
      arrange(`Durchschnitliche Laufzeit [ms]`)|>
      mutate(`Durchschnitliche Laufzeit [ms]` = if_else(is.na(`Durchschnitliche Laufzeit [ms]`), NA, ms_to_hms(`Durchschnitliche Laufzeit [ms]`)),
             Zwischenrang = row_number())
    df
    
    datatable(
      df, 
      options = 
        list(
          pageLength = 10,
          scrollX = TRUE,  # Enable horizontal scrolling
          language = DT_language,
          initComplete = JS(
            "function(settings, json) {",
            "// One-time header/body styles",
            "  $(this.api().table().header()).css({",
            "    'background-color': '#2d3e50',",
            "    'color': '#ffffff'",
            "  });",
            "  $(this.api().table().body()).css({",
            "    'background-color': '#34495e',",
            "    'color': '#ecf0f1'",
            "  });",
            "  // One-time search/length styling",
            "  $('div.dataTables_filter input').css({",
            "    'background-color': '#2c3e50',",
            "    'color': '#ecf0f1',",
            "    'border': '1px solid #7f8c8d'",
            "  });",
            "  $('div.dataTables_length select').css({",
            "    'background-color': '#2c3e50',",
            "    'color': '#ecf0f1',",
            "    'border': '1px solid #7f8c8d'",
            "  });",
            "  // Signal that table has been rendered",
            "  Shiny.setInputValue('events_tbl', new Date().getTime());",
            "}"
          ),
          drawCallback = JS(
            "function(settings) {",
            "$('a.paginate_button').css({",
            "'background-color': '#7898b6',",
            "'color': '#ffffff',",
            "'border': '1px solid #7f8c8d',",
            "'padding': '5px 10px',",
            "'margin': '0 2px',",
            "'border-radius': '4px',",
            "'text-decoration': 'none'",
            "});",
            
            "$('a.paginate_button.current').css({",
            "'background-color': '#e67e22',",
            "'color': '#ffffff',",
            "'font-weight': 'bold'",
            "});",
            
            "$('a.paginate_button').hover(",
            "function() {",
            "if (!$(this).hasClass('current')) {",
            "$(this).css('background-color', '#5d7d9a');",
            "}",
            "},",
            "function() {",
            "if (!$(this).hasClass('current')) {",
            "$(this).css('background-color', '#7898b6');",
            "}",
            "}",
            ");",
            "}"
          )
        )
    )
  })
  
  ## Insert event (race row) — uses Startnummer ####
  observeEvent(input$insert_event, {
    req(input$participant_id, input$run_number)
    ts <- if (isTRUE(input$use_now)) now_ms() else input$timestamp_free
    
    ins_sql <- "INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, NOW(3))"
    dbExecute(pool, ins_sql, params = list(as.integer(input$participant_id),
                                           as.integer(input$run_number),
                                           ts, input$race_status,
                                           input$device_id, input$device_name))
    
    
    if (identical(input$race_status, "started")) {
      dbExecute(pool, "UPDATE participant SET last_run = ?, last_updated = NOW(3) WHERE Startnummer = ?", 
                params = list(as.integer(input$run_number), as.integer(input$participant_id)))

    }
    if (input$race_status %in% c("finished", "disqualify")) {
      dbExecute(pool, "UPDATE participant SET next_run = next_run + 1, last_updated = NOW(3) WHERE Startnummer = ?", 
                params = list(as.integer(input$participant_id)))

    }
    
    showNotification(sprintf("Event '%s' inserted", input$race_status), type = "message")
  })
  
  ## Demo sequence: start → interim → finish — uses Startnummer ####
  observeEvent(input$demo_sequence, {
    req(input$participant_id)
    ts0 <- as.POSIXct(Sys.time())
    ts1 <- ts0 + runif(1, min = 40, max = 55)
    ts2 <- ts1 + runif(1, min = 41.2, max = 55.7)
    
    sn <- as.integer(input$participant_id)
    run_no <- dbGetQuery(pool, "SELECT next_run FROM participant WHERE Startnummer = ?", params = list(sn))$next_run
    
    # started
    dbExecute(pool, "INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name, last_updated)
                     VALUES (?, ?, ?, 'started', 'chip001', 'StartGate', NOW(3))",
              params = list(sn, run_no, format(ts0, "%Y-%m-%d %H:%M:%OS3")))
    dbExecute(pool, "UPDATE participant SET last_run = ?, last_updated = NOW(3) WHERE Startnummer = ?", 
              params = list(run_no, sn))
    
    # interim 1
    dbExecute(pool, "INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name, last_updated)
                     VALUES (?, ?, ?, 'interim 1', 'chip002', 'InterimGate 1', NOW(3))",
              params = list(sn, run_no, format(ts1, "%Y-%m-%d %H:%M:%OS3")))
    
    # finished
    dbExecute(pool, "INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name, last_updated)
                     VALUES (?, ?, ?, 'finished', 'chip003', 'FinishGate', NOW(3))",
              params = list(sn, run_no, format(ts2, "%Y-%m-%d %H:%M:%OS3")))
    dbExecute(pool, "UPDATE participant SET next_run = next_run + 1, last_updated = NOW(3) WHERE Startnummer = ?", 
              params = list(sn))
    
    showNotification("Demo events inserted (start → interim → finish)", type = "message")
  })
}

# shinyApp(ui, server)

# Run the shiny app ####
shiny::runApp(
  shiny::shinyApp(ui = ui, server = server),
  launch.browser = TRUE
)
