# app.R — Shiny app for zeitmessung (event-log model) — UPDATED FOR Startnummer

# --- Packages ---
library(shiny)
library(DBI)
library(pool)
library(RMariaDB)
library(tidyverse)
library(DT)
library(httr)
library(jsonlite)

# shinyApp(ui, server)
library(shinyWidgets)

source("source/OS_support/helper.R")

# get host server IP
DB_hoste_name <- get_host_ipv4()
DB_hoste_name

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
  host = Sys.getenv("ZEIT_DB_HOST"),
  dbname = Sys.getenv("ZEIT_DB_NAME"),
  user = Sys.getenv("ZEIT_DB_USER"),
  password = Sys.getenv("ZEIT_DB_PW"),
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

df_registered <- tbl(con, "participants")|>
  collect()|>
  suppressWarnings()|>
  mutate(Geburtsdatum = as.Date(Geburtsdatum))|>
  arrange(desc(created_at))
df_registered

DBI::dbDisconnect(con)

## close database connection ####
onStop(function() {
  poolClose(pool)
})


## Race mangement configuration ####
php_url_racemanagement <- paste0("http://", Sys.getenv("ZEIT_DB_HOST"), "/zeitmessung/xampp/update_racemanagement.php")
api_key <- Sys.getenv("API_KEY")  # if required

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

shiny::addResourcePath("Server_admin", "source/Server_admin")

# UI ####
ui <- function() fluidPage(
  shiny::tags$head(
    
    # Reference via the added resource path
    tags$link(rel = "icon", href = "Server_admin/favicon.ico"),
    
    # In your UI (anywhere inside fluidPage but not inside renderUI)
    tags$div(style = "display:none;",
             selectizeInput(".__selectize_dep_loader__", NULL, choices = c("x"), selected = "x")
    ),
    shiny::tags$link(rel = "stylesheet", type = "text/css", 
                     href = paste0("custom_styles/dark.css?v=", as.integer(Sys.time()))),
    
    # JavaScript for background color updates ONLY
    tags$script(HTML("
      // Update background color based on race status
      Shiny.addCustomMessageHandler('updateBackgroundColor', function(color) {
        document.body.style.backgroundColor = color;
        document.body.style.transition = 'background-color 0.5s ease';
      });
      
      // Set initial color
      $(document).ready(function() {
        document.body.style.backgroundColor = '#2c3e50';
      });
    ")),
    
    # Existing JavaScript for RFID handling (keep this)
    tags$script(HTML("
      // Press ENTER in the RFID decimal field to trigger the search button
      $(document).on('keydown', '#edit_rfid_dec', function(e){
        if (e.key === 'Enter') {
          e.preventDefault();
          $('#find_RFID').click();
        }
      });
    
      // Helper: focus an input by id (from server via sendCustomMessage)
      Shiny.addCustomMessageHandler('focus', function(id){
        var el = document.getElementById(id);
        if (el) { el.focus(); if (el.select) el.select(); }
      });
    ")),
    
    # force browsers not to auto complete RFID reader stuff
    tags$script(HTML("
      (function () {
        function hardStop(id, opts) {
          var el = document.getElementById(id);
          if (!el) return;
          el.setAttribute('autocomplete', opts.autocomplete || 'off');
          el.setAttribute('autocorrect', 'off');
          el.setAttribute('autocapitalize', 'off');
          el.setAttribute('spellcheck', 'false');
          // Optional: numeric keypad for scanners that type digits
          if (opts.numeric) el.setAttribute('inputmode', 'numeric');
        }
        function applyAll() {
          hardStop('edit_rfid_dec', { numeric: true });
          hardStop('edit_rfid_le',  { });
        }
        // Initial apply when Shiny connects
        document.addEventListener('shiny:connected', applyAll);
        // Re-apply whenever a Bootstrap modal is shown (your inputs appear in modals too)
        $(document).on('shown.bs.modal', applyAll);
        // Safety net: watch DOM changes (e.g., re-rendered UI)
        new MutationObserver(function(){ applyAll(); })
          .observe(document.body, { childList: true, subtree: true });
      })();
    "))
  ),
  
  titlePanel("Zeitmessung"),
  
  # Simple race control buttons
  div(
    actionButton("race_stop", "Rennen stopen", class = "btn-danger"),
    actionButton("race_run", "Start ermöglichen", class = "btn-success"),
    shiny::uiOutput("msg_ui"),
    style = "margin-bottom: 5px;"
  ),

  tabsetPanel(
    id = "main_tabs",    
    tabPanel("Registrierung importieren", value = "import",
             fluidRow(
               column(3,
                      h3("Aktualisieren"),
                      actionButton("update_registered", "Registrierung aktualisieren", class = "btn-success"),
                      h3("Registrierung"),
                      actionButton("import_participant", "Registrierung importieren", class = "btn-success")
               ),
               column(8,
                      h3("Registrierungen"),
                      DTOutput("registered_tbl"),
                      br()
               )
             )
             
    ),
    tabPanel("Teilnehmer", value = "participants",
             fluidRow(
               column(3,
                      h3("Teilnehmer"),
                      actionButton("add_participant", "Teilnehmer hinzufügen", class = "btn-success"),
                      actionButton("open_edit_modal", "Teilnehmer editieren", class = "btn-primary"),
                      actionButton("delete_participant", "Teilnehmer löschen", class = "btn-warning"),
                      h3("RFID"),
                      actionButton("edit_rfid_Teilnehmer", "RFID ändern", class = "btn-danger"),
                      h3("RFID suchen"),
                      textInput("find_rfid_dec", "RFID (vom USB-Reader, Dezimal)", value = ""),
                      textInput("find_rfid_le", "RFID HEX"),
                      actionButton("find_RFID", "RFID suchen",
                                   class = "btn btn-success")
               ),
               column(8,
                      h3("Startreihenfolge"),
                      DTOutput("participants_tbl"),
               )
             )
    ),
    tabPanel("Messungen / Disqualifizierung", value = "events",
             fluidRow(
               column(3,
                      h3("Disqualifizierungen"),
                      uiOutput("participant_select_ui")
                      # actionButton("remove_disqulification", "Disqualifizierung aufheben", class = "btn-primary")
               ),
               column(8,
                      h3("Events (race)"),
                      DTOutput("events_tbl")
               )
             )
    ),
    tabPanel("Mitteilungen", value = "msg_system",
             fluidRow(
               column(3,
                      h3("Mitteilungen bearbeiten"),
                      actionButton("add_msg", "Mitteilung erstellen", class = "btn-success"),
                      actionButton("edit_msg", "Mitteilung editieren", class = "btn-primary"),
                      actionButton("del_msg", "Mitteilung löschen", class = "btn-danger"),
                      h3("Publizieren"),
                      actionButton("publish_msg", "Mitteilung Publizieren", class = "btn-success"),
                      actionButton("unpublish_msg", "Mitteilung stoppen", class = "btn-warning"),
               ),
               column(8,
                      h3("Mitteilungen"),
                      DTOutput("msg_tbl")
               )
             )
    ),
    tabPanel("Laufzeiten", value = "summary",
             fluidRow(
               column(3,
                      h3("Filters"),
                      uiOutput("participant_filter_ui")
               ),
               column(8,
                      h3("Laufzeiten"),
                      DTOutput("summary_tbl")
               )
             )
    ),
    tabPanel("Einstellungen", value = "settings",
             fluidRow(
               column(3,
                      h3("Einstellungen"),
                      actionButton("edit_settings", "Einstellungen ändern")
                      
               ),
               column(8,
                      h3("Einstellungen"),
                      DTOutput("settings_tbl")
               )
             )
    ),
    tabPanel("Rennablauf testen", value = "testing",
             fluidRow(
               column(3,
                      h3("Testen"),
                      actionButton("test_start_race", "Zeitmessung Start"),
                      actionButton("test_finish_race", "Zeitmessung Ziel")
                      
               ),
               column(8,
                      h3("Einstellungen"),
                      DTOutput("events_tbl_test")
               )
             )
    ),
    tabPanel("Picologs", value = "picologs",
             fluidRow(
               column(3,
                      h3("Logs"),
                      actionButton("delete_picologs", "Delete All Logs", 
                                   class = "btn-danger",
                                   onclick = "return confirm('Are you sure you want to delete ALL logs? This cannot be undone!');")
                      
               ),
               column(8,
                      h3("Pico Logs"),
                      DTOutput("picologs_tbl")
               )
             )
    ),
  )
)

# Server ####
server <- function(input, output, session) {
  
  ## Rective values ####
  ### Database data ####
  df_registered <- reactiveVal(df_registered)
  current_participant <- reactiveVal(NULL)
  last_user_filter_in_race <- reactiveVal(NULL)
  
  ### last selected in data table ####
  last_selected_row <- reactiveVal(NULL)
  last_selected_page <- reactiveVal(NULL)
  
  ### last scanned RFID ####
  last_scanned_RFID <- reactiveVal(NULL)
  
  ### Message system ####
  msg_typ <- shiny::reactiveVal(NULL)
  last_rendered_msg <- shiny::reactiveVal(NULL)
  
  ### Message queue system ####
  message_queue <- reactiveVal(data.frame())
  current_message <- reactiveVal(NULL)
  queue_index <- reactiveVal(1)
  message_start_time <- reactiveVal(NULL)
  n_time_counter <- reactiveVal(list())  # Track counters for n-time messages
  
  ### settings ####
  last_selected_setting <- reactiveVal(NULL)
  df_temp <- tbl(pool, "system_settings") |> 
    collect() 
  last_rendered_setting <-  reactiveVal(df_temp)
  
  ## Track race status for background color ####
  race_is_running <- reactiveVal(TRUE)  # Default to running
  
  # Update background color based on race status
  observe({
    invalidateLater(2000, session)  # Check every 2 seconds
    
    # Get current race status
    is_running <- tryCatch({
      result <- dbGetQuery(pool, "SELECT value FROM race_management WHERE name = 'Rennstatus'")
      nrow(result) > 0 && result$value[1] == "1"
    }, error = function(e) {
      TRUE  # Default to running on error
    })
    
    # Update background color
    if (is_running) {
      session$sendCustomMessage("updateBackgroundColor", "#322f3b")  # Dark blue for running
    } else {
      session$sendCustomMessage("updateBackgroundColor", "#991b1b")  # Dark red for stopped
    }
  })
  
  ## Helper functions ####
  
  ### Function to build message queue ####
  build_message_queue <- function(df_data) {
    queue <- data.frame()
    
    # Check if df_data has rows
    if (nrow(df_data) == 0) {
      cat("Debug - No published messages\n")
      return(queue)
    }
    
    cat("Debug - Starting to build queue with", nrow(df_data), "published messages\n")
    
    # Process static messages (always in queue)
    static_msgs <- df_data |> filter(typ == "statisch")
    cat("Debug - Found", nrow(static_msgs), "static messages\n")
    
    if (nrow(static_msgs) > 0) {
      static_queue <- static_msgs |>
        mutate(
          queue_id = paste0("static_", id),
          display_duration = as.numeric(msg_time_s),
          display_count = 1,
          max_displays = 1,
          expires_at = as.POSIXct(NA),
          type = "static"
        ) |>
        filter(!is.na(display_duration), display_duration > 0) |>
        select(queue_id, id, msg, display_duration, display_count, max_displays, expires_at, type)
      
      cat("Debug - Added", nrow(static_queue), "static messages to queue\n")
      queue <- bind_rows(queue, static_queue)
    }
    
    # Process n-time messages
    n_time_msgs <- df_data |> filter(typ == "n mal")
    cat("Debug - Found", nrow(n_time_msgs), "n-time messages\n")
    
    if (nrow(n_time_msgs) > 0) {
      # Get current counter values
      current_counters <- n_time_counter()
      
      n_time_queue <- n_time_msgs |>
        mutate(
          queue_id = paste0("ntime_", id),
          display_duration = as.numeric(msg_time_s),
          # Get current count or initialize
          current_count = sapply(id, function(x) {
            if (!is.null(current_counters[[as.character(x)]])) {
              current_counters[[as.character(x)]]
            } else {
              0
            }
          }),
          # Ensure display_n_times is numeric
          display_n_times_num = as.numeric(display_n_times),
          remaining_displays = pmax(0, display_n_times_num - current_count),
          max_displays = display_n_times_num,
          expires_at = as.POSIXct(NA),
          type = "n_time"
        ) |>
        filter(!is.na(display_duration), 
               display_duration > 0, 
               !is.na(display_n_times_num),
               display_n_times_num > 0,
               remaining_displays > 0)
      
      cat("Debug - After filtering,", nrow(n_time_queue), "n-time messages remain\n")
      
      # Add multiple entries for remaining displays
      if (nrow(n_time_queue) > 0) {
        n_time_expanded <- data.frame()
        for (i in 1:nrow(n_time_queue)) {
          row <- n_time_queue[i, ]
          remaining <- as.integer(row$max_displays - row$current_count)
          
          if (remaining > 0) {
            for (j in 1:remaining) {
              n_time_expanded <- bind_rows(n_time_expanded, 
                                           data.frame(
                                             queue_id = paste0(row$queue_id, "_", j),
                                             id = row$id,
                                             msg = row$msg,
                                             display_duration = row$display_duration,
                                             display_count = row$current_count + j,
                                             max_displays = row$max_displays,
                                             expires_at = as.POSIXct(NA),
                                             type = "n_time",
                                             stringsAsFactors = FALSE
                                           )
              )
            }
          }
        }
        
        cat("Debug - Added", nrow(n_time_expanded), "n-time expanded messages to queue\n")
        queue <- bind_rows(queue, n_time_expanded)
      }
    }
    
    # Process timed messages
    timed_msgs <- df_data |> filter(typ == "zeitlich")
    cat("Debug - Processing", nrow(timed_msgs), "timed messages\n")
    
    if (nrow(timed_msgs) > 0) {
      current_utc <- lubridate::with_tz(Sys.time(), "UTC")
      
      cat("Debug - Current UTC time:", format(current_utc, "%Y-%m-%d %H:%M:%S %Z"), "\n")
      cat("Debug - Current CET time:", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"), "\n")
      
      timed_queue <- timed_msgs |>
        mutate(
          # Compare UTC times
          is_expired = !is.na(display_till_utc) & display_till_utc < current_utc
        )
      
      # Show expiration status
      cat("Debug - Message expiration status:\n")
      for (i in 1:nrow(timed_queue)) {
        if (!is.na(timed_queue$display_till_utc[i])) {
          cat(sprintf("  ID %d: Expires at %s UTC (%s CET) - Expired: %s\n",
                      timed_queue$id[i],
                      format(timed_queue$display_till_utc[i], "%H:%M:%S"),
                      format(timed_queue$display_till_local[i], "%H:%M:%S"),
                      timed_queue$is_expired[i]))
        }
      }
      
      # Filter out expired
      timed_queue <- timed_queue |>
        filter(!is_expired) |>
        mutate(
          queue_id = paste0("timed_", id),
          display_duration = as.numeric(msg_time_s),
          display_count = 1,
          max_displays = 1,
          expires_at = display_till_utc,  # Store UTC time for comparison
          type = "timed"
        ) |>
        filter(!is.na(display_duration), display_duration > 0)
      
      cat("Debug - Kept", nrow(timed_queue), "unexpired timed messages\n")
      
      queue <- bind_rows(queue, timed_queue)
    }
    
    # Shuffle queue for random order
    if (nrow(queue) > 0) {
      cat("Debug - Final queue has", nrow(queue), "messages\n")
      
      # Safe shuffle - only if we have more than 1 message
      if (nrow(queue) > 1) {
        tryCatch({
          # Create a random permutation of row indices
          row_order <- sample(seq_len(nrow(queue)))
          queue <- queue[row_order, ]
          cat("Debug - Successfully shuffled queue\n")
        }, error = function(e) {
          cat("Debug - Shuffle failed, keeping original order:", e$message, "\n")
        })
      } else {
        cat("Debug - Only 1 message, no shuffle needed\n")
      }
    } else {
      cat("Debug - Queue is empty after processing all message types\n")
    }
    
    return(queue)
  }
  

  ### Emergency fix: Force skip expired timed messages ####
  observe({
    invalidateLater(5000, session)  # Check every 5 seconds
    
    current_msg <- current_message()
    
    if (!is.null(current_msg) && current_msg$type == "timed") {
      # Get the message from database to check expiration
      msg_id <- current_msg$id
      sql <- "SELECT display_till FROM msg WHERE id = ?"
      db_time <- dbGetQuery(pool, sql, params = list(msg_id))$display_till[1]
      
      if (!is.na(db_time)) {
        # Interpret as local time (CET)
        expires_at <- as.POSIXct(db_time, tz = Sys.timezone())
        current_time <- Sys.time()
        
        if (expires_at < current_time) {
          cat("\n!!! EMERGENCY SKIP - Message", msg_id, "has expired !!!\n")
          cat("Expired at:", format(expires_at, "%Y-%m-%d %H:%M:%S %Z"), "\n")
          cat("Current time:", format(current_time, "%Y-%m-%d %H:%M:%S %Z"), "\n")
          
          # Force skip
          current_message(NULL)
          message_start_time(NULL)
          
          # Rebuild queue to remove expired message
          df_data <- msg_tbl() |> filter(publish_msg == 1)
          new_queue <- build_message_queue(df_data)
          message_queue(new_queue)
          queue_index(1)
        }
      }
    }
  })
  
  # Add this function to check raw database values
  check_database_times_raw <- function() {
    cat("\n=== Checking Raw Database Times ===\n")
    
    # Get raw SQL result
    sql <- "SELECT id, typ, display_till FROM msg WHERE typ = 'zeitlich' AND publish_msg = 1"
    result <- dbGetQuery(pool, sql)
    
    if (nrow(result) > 0) {
      for (i in 1:nrow(result)) {
        cat(sprintf("\nMessage ID: %d\n", result$id[i]))
        cat("Raw from DB (as character):", as.character(result$display_till[i]), "\n")
        
        # Try different interpretations
        as_no_tz <- as.POSIXct(result$display_till[i])
        cat("As POSIXct (no tz):", format(as_no_tz, "%Y-%m-%d %H:%M:%S"), "\n")
        cat("  Attributes - tzone:", attr(as_no_tz, "tzone"), "\n")
        
        as_utc <- as.POSIXct(result$display_till[i], tz = "UTC")
        cat("As POSIXct (UTC):", format(as_utc, "%Y-%m-%d %H:%M:%S %Z"), "\n")
        
        as_local <- as.POSIXct(result$display_till[i], tz = Sys.timezone())
        cat("As POSIXct (Local):", format(as_local, "%Y-%m-%d %H:%M:%S %Z"), "\n")
        
        # What R thinks vs actual
        cat("\nCurrent time (Local):", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"), "\n")
        cat("Current time (UTC):", format(lubridate::with_tz(Sys.time(), "UTC"), "%Y-%m-%d %H:%M:%S %Z"), "\n")
        
        # Check if expired with different interpretations
        cat("\nIs expired (no tz)?", as_no_tz < Sys.time(), "\n")
        cat("Is expired (as UTC)?", as_utc < lubridate::with_tz(Sys.time(), "UTC"), "\n")
        cat("Is expired (as Local)?", as_local < Sys.time(), "\n")
      }
    } else {
      cat("No timed messages found\n")
    }
    cat("====================================\n")
  }
  
  # Run it: check_database_times_raw()
  
  
  ### Observer to clean expired messages from queue ####
  observe({
    # Check every 30 seconds for expired messages
    invalidateLater(30000, session)
    
    queue <- message_queue()
    if (nrow(queue) > 0) {
      current_time <- Sys.time()
      
      # Check if current message is expired
      if (!is.null(current_message())) {
        msg <- current_message()
        if (msg$type == "timed" && !is.na(msg$expires_at) && msg$expires_at < current_time) {
          cat("Debug - Current timed message has expired, skipping to next\n")
          # Force move to next message
          current_message(NULL)
          message_start_time(NULL)
        }
      }
      
      # Clean expired messages from queue
      new_queue <- clean_expired_messages(queue)
      if (nrow(new_queue) < nrow(queue)) {
        cat("Debug - Removed", nrow(queue) - nrow(new_queue), "expired messages from queue\n")
        message_queue(new_queue)
        
        # Reset index if it's now out of bounds
        idx <- queue_index()
        if (idx > nrow(new_queue)) {
          queue_index(1)
        }
      }
    }
  })

  clean_expired_messages <- function(queue) {
    if (nrow(queue) == 0) return(queue)
    
    current_utc <- lubridate::with_tz(Sys.time(), "UTC")
    
    # Remove timed messages that have expired (using UTC comparison)
    queue <- queue |>
      mutate(
        is_expired = ifelse(type == "timed" & !is.na(expires_at), 
                            expires_at < current_utc, 
                            FALSE)
      ) |>
      filter(!is_expired) |>
      select(-is_expired)
    
    # Remove n-time messages that have reached their limit
    if (any(queue$type == "n_time")) {
      current_counters <- n_time_counter()
      
      queue <- queue |>
        rowwise() |>
        filter(!(type == "n_time" && 
                   !is.na(id) && 
                   !is.null(current_counters[[as.character(id)]]) &&
                   current_counters[[as.character(id)]] >= max_displays)) |>
        ungroup()
    }
    
    return(queue)
  }
  
  # Function to update n-time counters
  update_n_time_counter <- function(msg_id) {
    counters <- n_time_counter()
    current <- ifelse(!is.null(counters[[as.character(msg_id)]]), 
                      counters[[as.character(msg_id)]], 0)
    counters[[as.character(msg_id)]] <- current + 1
    n_time_counter(counters)
  }
  
  
  # get date type for each column from a data frame 
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
    cat("LE string   :", le, "\n")   # explicit paste(rev(bytes), collapse=":")
    return(le)
  }
  
  check_participants_update <- function() {
    row_count <- dbGetQuery(pool, "SELECT COUNT(*) as row_count FROM participant")$row_count
    max_update <- dbGetQuery(pool, "SELECT MAX(last_updated) as max_update FROM participant")$max_update
    result <- paste0("nrow = ", row_count, ", max_update = ", max_update)
    if (is.na(max_update)) return("")
    else result
  }
  
  check_race_update <- function() {
    row_count <- dbGetQuery(pool, "SELECT COUNT(*) as row_count FROM race")$row_count
    max_update <- dbGetQuery(pool, "SELECT MAX(last_updated) as max_update FROM race")$max_update
    result <- paste0("nrow = ", row_count, ", max_update = ", max_update)
    if (is.na(max_update)) return("")
    else result 
  }
  
  get_participants <- function(){
    data <- tbl(pool, "participant")|>
      collect()
    
    data <- bind_rows(
      data|>
        filter(is.na(last_run)),
      data|>
        filter(!is.na(last_run))
    )
    return(data)
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
  
  # Escape regex literals
  escape_regex <- function(pattern) {
    gsub("([][{}()+*^$|\\\\?.])", "\\\\\\1", pattern)
  }
  
  ms_to_hms <- function(ms) {
    total_seconds <- as.double(ms)
    
    hours   <- total_seconds %/% 3600
    minutes <- (total_seconds %% 3600) %/% 60
    seconds <- round(total_seconds %% 60, 3)  # keep milliseconds if wanted
    
    sprintf("%02d:%02d:%06.3f", hours, minutes, seconds)
  }
  
  ### Function to update a value in race_management ####
  update_race_value <- function(name, value, url, api_key = NULL) {
    
    # Prepare the request body
    body <- list(
      name = name,
      value = value
    )
    
    # Add API key if needed
    if (!is.null(api_key)) {
      body$api_key <- api_key
    }
    
    # Make the POST request
    response <- POST(
      url = url,
      body = body,
      encode = "json"
    )
    
    # Check for HTTP errors
    if (status_code(response) != 200) {
      stop("Request failed with status: ", status_code(response))
    }
    
    # Parse the response
    result <- content(response, "parsed")
    
    # Check for API-level errors
    if (result$status == "error") {
      stop("API error: ", result$data$message)
    }
    
    # Return the successful result
    return(result$data)
  }
  
  ## React to tab changes ####
  observeEvent(input$main_tabs, {
    cat("Switched to tab:", input$main_tabs, "\n")
    last_selected_row <- reactiveVal(NULL)
    last_selected_page <- reactiveVal(NULL)
    
    switch(input$main_tabs,
           import = {
             last_user_filter_in_race()
           },
           participants = {
             # focus the RFID search field when entering "Teilnehmer"
             session$sendCustomMessage("focus", "find_rfid_dec")
           },
           events = {
             # e.g., refresh events table or whatever you need
           },
           summary = { 
             # showNotification("Rangliste", type = "message")  
           },
           testing = {
             #
           }
    )
  }, ignoreInit = TRUE)
  
  ## Edit participant by RFID and check if for duplicated: convert raw -> LE on the fly ####
  observeEvent(input$edit_rfid_dec, {
    if(is.na(input$edit_rfid_dec) || is.null(input$edit_rfid_dec)) req(NULL) #early exit
    le <- rfid_to_le_hex(input$edit_rfid_dec)
    last_scanned_RFID(le)
    updateTextInput(session, "edit_rfid_le", value = ifelse(is.na(le), "", le))
  }, ignoreInit = TRUE)
  
  ## Find participant by RFID and check if for duplicated: convert raw -> LE on the fly ####
  observeEvent(input$find_rfid_dec, {
    if(is.na(input$find_rfid_dec) || is.null(input$find_rfid_dec)) req(NULL) #early exit
    le <- rfid_to_le_hex(input$find_rfid_dec)
    last_scanned_RFID(le)
    updateTextInput(session, "find_rfid_le", value = ifelse(is.na(le), "", le))
  }, ignoreInit = TRUE)
  
  ## find scanned RFID ####
  observeEvent(input$find_RFID, {
    if(is.null(last_scanned_RFID()) || is.na(last_scanned_RFID())|| (last_scanned_RFID() == "")) req(NULL) #early exit
    
    df_temp <- tbl(pool, "participant")|>
      collect()
    df_temp <- df_temp|>
      filter(rfid_uid_le == last_scanned_RFID()) 
    df_temp  
    
    table_search_columns <- input$participants_tbl_search_columns
    table_search_columns[2] <- last_scanned_RFID()
    
    # get user filters
    column_filters = table_search_columns
    column_filters <- column_filters |>
      str_remove_all("\"") |>
      str_remove_all("\\[") |>
      str_remove_all("\\]")
    column_filters <- str_split(column_filters, ",")
    
    # Update last user filter
    c_test <- lapply(column_filters, function(x){
      nchar(x) > 0
    }) |>
      unlist()
    
    # get column data type
    c_class <- get_data_type(df_temp)
    
    # extract data from column filters 
    for (ii in 1:length(column_filters)) {
      col_filter <- column_filters[[ii]]
      if(nchar(col_filter[1]) > 0){
        if(c_class[ii] %in% c("Date", "hms")){
          c_date <- pull(df_temp[,ii]) |> as.character()
          df_temp <- df_temp[str_detect(c_date, col_filter),]
          df_temp <- df_temp[!is.na(pull(df_temp[,ii])),]
        } else if(c_class[ii] == "integer"){
          p1 <- "^[\\d]+"
          p2 <- "[\\d]+$"
          start <- str_extract(col_filter, p1) |> as.integer()
          end   <- str_extract(col_filter, p2) |> as.integer()
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
        } else { 
          col_filter <- col_filter |> tolower() |> escape_regex()
          df_temp <- df_temp[str_detect(pull(df_temp[,ii])|>tolower(), col_filter),]
          df_temp <- df_temp[!is.na(pull(df_temp[,ii])),]
        }
      }
    }
    
    # if column filters are present update column filters
    if(sum(!c_test) != length(column_filters)) {
      column_filters_temp <- table_search_columns |>
        lapply(function(x){
          if(nchar(x) > 0) list(search = x) else NULL
        })
    }
    
    # apply the filters
    last_user_filter_in_race(column_filters_temp)
    
    # --- NEW: clear scanner field(s) & refocus for next scan ---
    updateTextInput(session, "find_rfid_dec", value = "")
    updateTextInput(session, "find_rfid_le",  value = "")
    session$sendCustomMessage("focus", "find_rfid_dec")
  })
  
  ## Signal: last selected row registered_tbl ####
  observeEvent(input$registered_tbl_rows_selected, {
    last_selected_row(input$registered_tbl_rows_selected)
    writeLines(paste("Last selected row registed:", last_selected_row()))
  })
  
  ## Signal: last selected row participants_tbl ####
  observeEvent(input$participants_tbl_rows_selected, {
    last_selected_row(input$participants_tbl_rows_selected)
    writeLines(paste("Last selected row participants:", last_selected_row()))
  })
  
  ## Signal: last selected row events_tbl ####
  observeEvent(input$events_tbl_rows_selected, {
    last_selected_row(input$events_tbl_rows_selected)
    writeLines(paste("Last selected row race:", last_selected_row()))
  })
  
  ## Signal: last selected row summary_tbl ####
  observeEvent(input$summary_tbl_rows_selected, {
    last_selected_row(input$summary_tbl_rows_selected)
    writeLines(paste("Last selected row summary:", last_selected_row()))
  })
  
  ## Signal: registered_tbl has been rendered ####
  observeEvent(input$registered_tbl_signal, {
    writeLines("Registrierungen")
    
    if(is.null(last_selected_row()) ) req(NULL) # early exit because not initalized
    
    # select in table
    dataTableProxy('registered_tbl')|>
      selectRows(last_selected_row())
    
  })
  
  ## Signal: participants_tbl has been rendered ####
  observeEvent(input$participants_tbl_signal, {
    writeLines("Teilnehmer")
    
    if(is.null(last_selected_row()) ) req(NULL) # early exit because not initalized
    
    # select in table
    dataTableProxy('participants_tbl')|>
      selectRows(last_selected_row())
    
  })
  
  ## Signal: Datatable race has been rendered ####
  observeEvent(input$events_tbl_signal, {
    writeLines("Messungen / Disqualifizierungen")
    
    if(is.null(last_selected_row()) ) req(NULL) # early exit because not initalized
    
    # select in table
    dataTableProxy('events_tbl')|>
      selectRows(last_selected_row())
    
  })
  
  ## Signal: Datatable Rennablauf testen has been rendered ####
  observeEvent(input$modal_participants_tbl_signal, {
    writeLines("Rennablauf testen")
    
    if(is.null(last_selected_row()) ) req(NULL) # early exit because not initalized
    
    # select in table
    dataTableProxy('modal_participants_tbl_signal')|>
      selectRows(last_selected_row())
    
  })
  
  ## Signal: Datatable summary_tbl has been rendered ####
  observeEvent(input$summary_tbl_signal, {
    writeLines("Rangliste")
    
    if(is.null(last_selected_row()) ) req(NULL) # early exit because not initalized
    
    # select in table
    dataTableProxy('summary_tbl')|>
      selectRows(last_selected_row())
    
  })
  
  ## race stop ####
  observeEvent(input$race_stop,{
    showModal(
      modalDialog(
        title = paste0("Rennen stoppen"),
        tagList(
          renderText("Soll das Rennen gestoppt werden?"),
        ),
        easyClose = FALSE,
        footer = tagList(
          actionButton("race_stop_exe", "Rennen stoppen", class = "bnt-danger"),
          actionButton("abort", "Abbrechen")
        )
      )
    )
  })  
  
  ## race stop execute ####
  observeEvent(input$race_stop_exe,{    
    # Update "Rennstatus"
    result <- update_race_value(
      name = "Rennstatus", 
      value = "0",  # 0 to stop the rece
      url = php_url_racemanagement
    )
    removeModal()
  })  
  
  ## race run ####
  observeEvent(input$race_run,{
    showModal(
      modalDialog(
        title = paste0("Rennen freigeben"),
        tagList(
          renderText("Soll das Rennen wieder gestartet werden?"),
        ),
        easyClose = FALSE,
        footer = tagList(
          actionButton("race_restart_exe", "Rennen starten", class = "bnt-danger"),
          actionButton("abort", "Abbrechen")
        )
      )
    )
  })  
  
  ## race run execute ####
  observeEvent(input$race_restart_exe,{
    # Update "Rennstatus"
    result <- update_race_value(
      name = "Rennstatus", 
      value = "1",  # 1 to restart the race
      url = php_url_racemanagement
    )
    removeModal()
  })
  
  ## Disqualify a participant ####
  observeEvent(input$disqulification, {
    c_Startnummer <- as.integer(current_participant())
    
    df_started <- tbl(pool, "race")|>
      filter(race_status == "started",
             Startnummer == c_Startnummer,
      )|>
      arrange(desc(run))|>
      collect()
    df_started
    
    df_finished <- tbl(pool, "race")|>
      filter(race_status == "finished",
             Startnummer == c_Startnummer,
      )|>
      arrange(desc(run))|>
      collect()
    df_finished
    
    if (nrow(df_started) != nrow(df_finished)) {
      
      # Find the started and edit 
      row <- anti_join(df_started, df_finished)
      tryCatch({
        
        dbExecute(
          pool, 
          "INSERT INTO race ( Startnummer, run, timestamp_ms, race_status, device_id, device_name, last_updated)
          VALUES (?, ?, NOW(3), 'disqualified', 'R app', 'RaceControl', NOW(3))",
          params = list(row$Startnummer, row$run )
        )
        
        showNotification(paste("Die Startnummer", c_Startnummer, 
                               "ist wurde disqualifiziert."), 
                         type = "message")
        
      }, error = function(e) {
        showNotification(paste("Teilnehmer wurde nicht disqualifiziert", e$message), type = "error")
        writeLines(e)
      })
      
    } else {
      showNotification(paste("Die Startnummer", c_Startnummer, 
                             "ist schon disqualifiziert. Keine Änderungen vorgenommen."), 
                       type = "message")
    }
    
    
  })
  
  ## remove disqualification for a participant ####
  observeEvent(input$remove_disqulification, {
    c_Startnummer <- as.integer(current_participant())
    row <- tbl(pool, "race")|>
      filter(race_status == "disqualified",
             Startnummer == c_Startnummer)|>
      collect()
    row         
    
    
    if (nrow(row) > 0) {
      tryCatch({
        DBI::dbExecute(pool, "DELETE FROM race WHERE id = ?", params = list(row$id))
        
        df_started <- tbl(pool, "race")|>
          filter(race_status == "started",
                 Startnummer == c_Startnummer,
                 run == row$run)|>
          collect()
        DBI::dbExecute(pool, "DELETE FROM race WHERE id = ?", params = list(df_started$id))
      }, error = function(e) {
        showNotification(paste("Löschen fehlgeschlagen:", e$message), type = "error")
      })
      
    } else {
      showNotification(paste("Die Startnummer", c_Startnummer, 
                             "ist derzeit nicht disqualifiziert. Keine Änderungen vorgenommen."), 
                       type = "message")
    }
  })
  
  ## Update registrations ####
  observeEvent(input$update_registered, {
    # Data base credentials from system variables for https://lx51.hoststar.hosting/ 
    DB_host <- Sys.getenv("DB_host")
    DB_name <- "ch367079_race"
    DB_user <- Sys.getenv("DB_user")
    DB_pw <- Sys.getenv("DB_PASSWORD_KINOKLUB")
    
    # database connection
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
    
    df_temp <- tbl(con, "participants")|>
      collect()|>
      suppressWarnings()|>
      mutate(Geburtsdatum = as.Date(Geburtsdatum),
             Registrierungsnummer = as.factor(Registrierungsnummer))|>
      as_tibble()|>
      arrange(desc(created_at))
    
    # update
    df_temp|>
      df_registered()
    
    # print updated
    df_registered()|>
      print()
    
    DBI::dbDisconnect(con)
    
    showNotification("Registrierungen aktualisiert", type = "message")
  })
  
  ## Import participant####
  observeEvent(input$import_participant, {
    req(input$registered_tbl_rows_selected)
    
    df <- participants_smart_poll()
    
    if (is.null(df) || nrow(df) == 0) {
      sel <- 1L
    } else if (all(is.na(df$Startnummer))) {
      sel <- 1L
    } else {
      sel <- max(df$Startnummer, na.rm = TRUE) + 1L
    }
    
    # Modal to add participant 
    showModal(modalDialog(
      title = paste0("Teilnehmer hinzufügen: Startnummer ", sel),
      size = "m",
      shiny::tagList(
        textInput("edit_Vorname", "Vorname", value = df_registered()$Vorname[input$registered_tbl_rows_selected]),
        textInput("edit_Name", "Name", value = df_registered()$Name[input$registered_tbl_rows_selected]),
        textInput("edit_Nickname", "Nickname", value = df_registered()$Nickname[input$registered_tbl_rows_selected]),
        textInput("edit_Phone", "Phone", value = df_registered()$Phone[input$registered_tbl_rows_selected]),
        textInput("edit_Email", "E-mail", value = df_registered()$`E-mail`[input$registered_tbl_rows_selected]),
        shiny::selectInput("edit_Kategorie", "Kategorie",
                           choices = c_categorie, 
                           selected = df_registered()$Kategorie[input$registered_tbl_rows_selected]),
        shiny::dateInput("edit_geburtstag", "Geburtstag", value = df_registered()$Geburtsdatum[input$registered_tbl_rows_selected],
                         format = "dd.mm.yyyy",
                         language = "de",
                         weekstart = 1),
        textInput("edit_rfid_dec", "RFID (vom USB-Reader, Dezimal oder Hex)", value = ""),
        textInput("edit_rfid_le", "RFID HEX", placeholder = "AA:BB:CC:DD")
      ),
      footer = tagList(
        modalButton("Abbrechen"),
        actionButton("save_add_participant", "Save", class = "btn-primary")
      ),
      easyClose = FALSE,
      
      # JavaScript to focus on edit_rfid_dec field when modal opens
      tags$script(
        HTML("
      $(document).on('shown.bs.modal', function(e) {
        setTimeout(function() {
          $('#edit_rfid_dec').focus();
        }, 100);
      });
    ")
      )
    ))
  })
  
  ## Edit RFID participant ####
  observeEvent(input$edit_rfid_Teilnehmer, {
    req(input$participants_tbl_rows_selected)
    df_registered
    df <- participants_smart_poll()
    
    select_startnummer <- df[input$participants_tbl_rows_selected,]$Startnummer
    
    df <- df|>
      filter(Startnummer == select_startnummer)
    df
    
    # Modal to add participant 
    showModal(modalDialog(
      title = paste0("Achtung: RFID eines Teilnehmers ändern?"),
      size = "m",
      shiny::tagList(
        renderText(paste("Vorname:", df$Vorname)),
        renderText(paste("Name:", df$Name)),
        renderText(paste("Nickname:", df$Nickname)),
        renderText(paste("E-Mail:", df$`E-mail`)),
        textInput("edit_rfid_dec", "RFID (vom USB-Reader, Dezimal oder Hex)"),
        textInput("edit_rfid_le", "RFID HEX", placeholder = "AA:BB:CC:DD")
      ),
      footer = tagList(
        modalButton("Abbrechen"),
        actionButton("save_RFID_edit_participant", "Save", class = "btn-primary")
      ),
      easyClose = FALSE,
    ))
  })
  
  ## Add participant via modal ####
  # open modal when button clicked (requires a row selection)
  observeEvent(input$add_participant, {
    # Calculate next Startnummer with better error handling
    df <- participants_smart_poll()
    
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
  
  # Save changes RFID edit participant
  observeEvent(input$save_RFID_edit_participant, {
    df_test <- tbl(pool, "participant") |> collect() |> as_tibble()
    removeModal()
    # RFID: prefer explicit LE field; otherwise convert from DEC field
    rfid_le <- trimws(input$edit_rfid_le %||% "")
    if (!nzchar(rfid_le)) {
      rfid_le <- rfid_to_le_hex(input$edit_rfid_dec)
    }
    if (!is.na(rfid_le) && nzchar(rfid_le) && rfid_exists_elsewhere(pool, rfid_le)) {
      showNotification("❌ RFID ist bereits vergeben.", type = "error"); return()
    }
    
    sel <- input$participants_tbl_rows_selected
    req(sel)
    df <- participants_smart_poll()
    row <- df[sel, , drop = FALSE]
    req(nrow(row) == 1)
    sn <- as.integer(row$Startnummer)
    
    sql <- 
      " UPDATE participant
      SET rfid_uid_le = ?,
      last_updated = NOW(3)
      WHERE Startnummer = ?;
    "
    
    dbExecute(pool, sql, params = list(rfid_le,sn))
    
    removeModal()
    
    last_user_filter_in_race(NULL)
    
    showNotification("Teilnehmer aktualisiert", type = "message")
    
    
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
      (Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, rfid_uid_le, next_run, last_updated)
    VALUES 
      (?, ?, ?, ?, ?, ?, ?, 1, NOW(3))
  "
    
    tryCatch({
      dbExecute(pool, sql, params = list(
        nm, vn, nn, ph, em, ka,
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
    
    df <- participants_smart_poll()
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
    
    df  <- participants_smart_poll()
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
      
      last_user_filter_in_race(NULL)
      
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
    df <- participants_smart_poll()
    row <- df[sel, , drop = FALSE]
    req(nrow(row) == 1)
    
    showModal(modalDialog(
      title = paste0("Teilnehmer Daten ändern: Startnummer ", row$Startnummer),
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
        )
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
    df <- participants_smart_poll()
    row <- df[sel, , drop = FALSE]
    req(nrow(row) == 1)
    sn <- as.integer(row$Startnummer)
    
    nm  <- trimws(input$edit_Name %||% "")
    vn  <- trimws(input$edit_Vorname %||% "")
    nn  <- trimws(input$edit_Nickname %||% "")
    ph  <- trimws(input$edit_Phone %||% "")
    em  <- trimws(input$edit_Email %||% "")
    ka  <- trimws(input$edit_Kategorie %||% "")
    
    sql <- "
    UPDATE participant
       SET 
           Name         = ?,
           Vorname      = ?,
           Nickname     = ?,
           Phone        = ?,
           `E-mail`     = ?,
           Kategorie    = ?,
           last_updated = NOW(3)
     WHERE Startnummer  = ?;
  "
    
    dbExecute(pool, sql, params = list(
      nm, vn, nn, ph, em, ka,
      sn
    ))
    
    removeModal()
    showNotification("Teilnehmer aktualisiert", type = "message")
  })
  
  ## Reactive: participants data with smart polling ####
  participants_smart_poll <-
    reactivePoll(
      3000,
      session,
      checkFunc = function() {
        check_participants_update()
      },
      valueFunc = function() {
        print("paritcipants update")
        # Your existing valueFunc code here
        tryCatch({
          data <- get_participants()|>
            arrange(desc(Startnummer))
          
          if (!is.null(data) && nrow(data) > 0) {
            data
          } else {
            # Return empty data frame with correct structure
            tibble(
              Startnummer = integer(),
              created_at = as.POSIXct(character()),
              last_updated = as.POSIXct(character()),
              last_run = integer(),
              next_run = integer(),
              Name = character(),
              Vorname = character(),
              Nickname = character(),
              Phone = character(),
              `E-mail` = character(),
              Kategorie = character(),
              Geburtsdatum = Date(),
              rfid_uid_le = character() 
            )
          }
        }, error = function(e) {
          showNotification(paste("Database error:", e$message), type = "error")
          # Return empty data frame
          tibble(
            Startnummer = integer(),
            created_at = as.POSIXct(character()),
            last_updated = as.POSIXct(character()),
            last_run = integer(),
            next_run = integer(),
            Name = character(),
            Vorname = character(),
            Nickname = character(),
            Phone = character(),
            `E-mail` = character(),
            Kategorie = character(),
            Geburtsdatum = Date(),
            rfid_uid_le = character() 
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
                                print("Reactive: events data with smart polling")
                                df_temp <- tbl(pool, "race") |> collect()|>arrange(desc(id))
                                print(df_temp)
                                return(df_temp)
                              }
  )
  
  ## Reactive: summary data (depends on both tables) ####
  summary_data <- reactivePoll(3000, session,
                               checkFunc = function() {
                                 # Combine both timestamps to detect changes in either table
                                 paste0(check_participants_update(), "|", check_race_update())
                                 
                                 paste0(check_race_update())
                               },
                               valueFunc = function() {
                                 ensure_summary_view(pool)
                                 print("Reactive: summary data (depends on both tables)")
                                 df_test <- tbl(pool, "v_race_summary_completed")|> 
                                   collect()|> 
                                   arrange(duration_hms)
                                 print(df_test)
                                 
                                 return(df_test)
                                 
                               }
  )
  
  ## Reactive: Pico logs to render ####
  picologs_tbl <- reactivePoll(3000, session,
                               checkFunc = function() {
                                 paste(dbGetQuery(pool, "SELECT COUNT(*) as row_count FROM Picolog")$row_count)
                               },
                               valueFunc = function() {
                                 print("Reactive: logs")
                                 df_test <- tbl(pool, "Picolog")|> 
                                   collect()
                                 print(df_test)
                                 return(df_test)
                               }
  )
  
  ## Update race status periodically ####
  observe({
    invalidateLater(2000, session)  # Check every 2 seconds
    
    # Get current status
    current_status <- tryCatch({
      dbGetQuery(pool, "SELECT value FROM race_management WHERE name = 'Rennstatus'")
    }, error = function(e) {
      data.frame(value = "1")  # Default to running on error
    })
    
    if (nrow(current_status) > 0) {
      race_is_running(current_status$value[1] == "1")
    }
  })
  
  ## Store current participant selection ####
  observeEvent(input$participant_id, {
    current_participant(input$participant_id)
  })
  
  # Distinct Startnummer that are currently disqualified (reacts to events_data())
  disqualified_sn <- reactive({
    df <- events_data()
    if (is.null(df) || !nrow(df)) return(integer(0))
    df |> 
      dplyr::filter(race_status == "disqualified") |>
      dplyr::arrange(dplyr::desc(id)) |>
      dplyr::distinct(Startnummer) |>
      dplyr::pull(Startnummer) |>
      as.integer()
  })
  
  ## UI: participant select (uses Startnummer) ####
  output$participant_select_ui <- renderUI({
    # Make it reactive to race changes
    sn_disq <- disqualified_sn()
    df <- participants_smart_poll() |> dplyr::filter(Startnummer %in% sn_disq)
    
    if (!nrow(df)) {
      return(div(class = "text-muted", "Es sind keine Disqualifizierungen vorhanden."))
    }
    
    # Keep previous selection if still valid
    sel <- if (!is.null(current_participant()) && current_participant() %in% df$Startnummer) {
      current_participant()
    } else {
      df$Startnummer[1]
    }
    
    choices <- setNames(
      df$Startnummer,
      paste0(df$Startnummer, ": ", df$Vorname, " ", df$Name,
             ifelse(nzchar(df$Nickname), paste0(" (", df$Nickname, ")"), ""))
    )
    
    tagList(
      selectInput("participant_id", "Teilnehmer", choices = choices, selected = sel),
      actionButton("remove_disqulification", "Disqualifizierung aufheben", class = "btn-primary")
    )
  })
  
  ## UI: participant filter (uses Startnummer) ####
  output$participant_filter_ui <- renderUI({
    df <- participants_smart_poll()
    if (nrow(df) == 0) {
      shiny::renderText("Keine Teilnehmer vorhanden")
    } else {
      selectInput("participant_filter", "Participant (optional)",
                  choices = c("All" = "", setNames(df$Startnummer, 
                                                   paste0(df$Startnummer, ": ", df$Name, " ", df$Vorname, 
                                                          ifelse(df$Nickname == "", "", paste(" (",df$Nickname,")")))
                  )
                  ),
                  selected = "")
    }
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
      mutate(
        Datum_to_sort = Geburtsdatum,
        Geburtsdatum = format(Geburtsdatum, "%d.%m.%Y"),
        Registrierungsnummer  = factor(Registrierungsnummer),
        Gewicht = NULL
      )|>
      rename(Erstellungsdatum = created_at,
      )
    
    # Log
    print("registered dataframe rendered")
    df_test|>
      print()
    
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
            list(targets = 9,visible = F),
            list(targets = 8, orderData = 9)    #
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
            "  Shiny.setInputValue('registered_tbl_signal', new Date().getTime());",
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
  
  ## Render: (Teilnehmer) Table participant ####
  output$participants_tbl <- renderDT({
    
    if(is.null(participants_smart_poll())) {
      print("not yet initialized")
      df_temp <- tibble(Teilnehmer = "Bitte Teilnehmerliste aktualisieren")
      
      datatable(
        df_temp,
        rownames = FALSE
      )
      
    } else {
      df_temp <- participants_smart_poll()|>
        as_tibble()
      
      df_temp <- df_temp|>
        rename(
          RFID = rfid_uid_le,
          `Letzter Lauf` = last_run,
          `Nächster Lauf` = next_run)
      
      df_temp <-  df_temp|>
        mutate(Startnummer = factor(Startnummer),
               `Letzter Lauf` = factor(`Letzter Lauf`),
               `Nächster Lauf` = factor(`Nächster Lauf`))|>
        select(Startnummer, RFID, Vorname, Name, Nickname, `E-mail`, Kategorie,  Geburtsdatum, `Letzter Lauf`, `Nächster Lauf` )
      
      # Log
      print("participants dataframe rendered")
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
    } 
  })
  
  ## Render: Table events ####
  output$events_tbl <- renderDT({
    showNotification(paste("New Events found"), type = "message")
    df_test <- events_data()|>
      mutate(Startnummer = factor(Startnummer),
             run = factor(run),
             device_name = factor(device_name),
             race_status = factor(race_status))
    
    df_test <- df_test|>
      rename(Lauf = run, 
             Zeitstempel = timestamp_ms,
             `Geräte ID` = device_id, 
             `Gerätename` = device_name, 
             Rennstatus = race_status)|>
      select(id,Startnummer, Lauf, Rennstatus, Gerätename, Zeitstempel, 
             `Geräte ID`, created_at, last_updated, timezone_offset, speed_mps, speed_kmh, beam_distance_mm )
    
    print(df_test)
    
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
            "  Shiny.setInputValue('events_tbl_signal', new Date().getTime());",
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
  
  ## Render: Laufzeiten / Rangliste ####
  output$summary_tbl <- renderDT({
    df <- summary_data()
    if (!is.null(input$participant_filter) && nzchar(input$participant_filter)) {
      df <- df |> filter(Startnummer == as.integer(input$participant_filter))
    }
    
    df <- df|>
      mutate(Startnummer = factor(Startnummer))|>
      rename(Laufzeit = duration_hms)
    df
    
    datatable(
      df, 
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
            "  Shiny.setInputValue('summary_tbl_signal', new Date().getTime());",
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
  
  ## Render: Table settings ####
  output$settings_tbl <- renderDT({
    df <- last_rendered_setting()
    
    datatable(
      df, 
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
            "  Shiny.setInputValue('settings_tbl_signal', new Date().getTime());",
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
  
  ## Render: Pico logs ####
  output$picologs_tbl <- renderDT({
    df_temp <- picologs_tbl()|>
      arrange(desc(created_at))
    df_temp <- df_temp|>
      mutate(Device_ID = factor(Device_ID), 
             Device_Name = factor(Device_Name))
    
    datatable(
      df_temp, 
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
            "  Shiny.setInputValue('pico_logs_signal', new Date().getTime());",
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
  
  ## edit a setting Modal ####
  observeEvent(input$edit_settings, {
    print("here")
    req(input$settings_tbl_rows_selected)
    df_temp <- tbl(pool, "system_settings")|>
      collect()
    df_temp <- df_temp[input$settings_tbl_rows_selected,]
    last_selected_setting(df_temp)
    
    # Modal 
    showModal(modalDialog(
      title = paste("Einstellung:", df_temp$name),
      size = "m",
      shiny::tagList(
        shiny::textInput("edit_settings_value", "Wert:", value = df_temp$value),
        renderText(df_temp$unit)
      ),
      footer = tagList(
        modalButton("Abbrechen"),
        actionButton("save_setting", "Save", class = "btn-primary")
      ),
      easyClose = FALSE,
    ))
  })
  
  ## Save a setting (from the modal) ####
  observeEvent(input$save_setting, {
    req(input$settings_tbl_rows_selected)
    
    # Read currently selected setting row
    df_all <- tbl(pool, "system_settings") |> collect()
    row    <- df_all[input$settings_tbl_rows_selected, , drop = FALSE]
    req(nrow(row) == 1)
    
    key_name <- as.character(row$name[[1]])
    new_val  <- trimws(input$edit_settings_value %||% "")
    
    # Update query
    sql <- "
    UPDATE system_settings
       SET value = ?
     WHERE name  = ?
  "
    tryCatch({
      DBI::dbExecute(pool, sql, params = list(new_val, key_name))
      
      removeModal()
      showNotification(sprintf("Einstellung '%s' gespeichert.", key_name), type = "message")
      
      # Refresh the visible table
      df_refreshed <- tbl(pool, "system_settings") |> 
        collect()
      
      last_rendered_setting(df_refreshed)
      
    }, error = function(e) {
      showNotification(paste("Speichern fehlgeschlagen:", e$message), type = "error")
    })
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
  
  ## Button: Abort, do nothing! ####
  observeEvent(input$abort,{
    removeModal()
  })
  
  ## Message system ####
  
  # Message typ`s
  c_msg_typ <- c("statisch", "zeitlich", "n mal")
  
  ### Add message ####
  observeEvent(input$add_msg, {
    showModal(
      modalDialog(
        title = paste0("Mitteilung erstellen"),
        tagList(
          shiny::actionButton("static_msg", "Statische Mitteilung"),
          shiny::actionButton("timed_msg", "Zeitliche Mitteilung"),
          shiny::actionButton("n_time_msg", "Anzahl Mitteilungen")
        ),
        easyClose = FALSE,
        footer = tagList(
          actionButton("abort", "Abbrechen", class = "bnt-danger")
        )
      )
    )
  })
  
  observeEvent(input$static_msg, {
    msg_typ("statisch")
    removeModal()
    showModal(
      modalDialog(
        title = paste0("Statische Mitteilung erstellen"),
        tagList(
          shiny::textAreaInput("msg", "Mitteilung", width = "400px", height = "400px"),
          shiny::numericInput("msg_time_s", "Mitteilungs Anzeigezeit [s]", value = 1)
        ),
        easyClose = FALSE,
        footer = tagList(
          actionButton("add_static_exe", "Erstellen"),
          actionButton("abort", "Abbrechen", class = "bnt-danger")
        )
      )
    )
  })
  
  observeEvent(input$add_static_exe, {
    sql <- "
    INSERT INTO msg 
      (msg, typ, msg_time_s)
    VALUES 
      (?, ?, ?)
  "
    tryCatch({
      dbExecute(pool, sql, params = list(
        input$msg, msg_typ(), input$msg_time_s
      ))
      
      showNotification("Teilnehmer hinzugefügt", type = "message")
    }, error = function(e) {
      showNotification(paste("Teilnehmer konnte nicht hinzugefügt werden:", e$message), type = "error")
    })
    
    removeModal()
    
  })
  
  observeEvent(input$timed_msg, {
    msg_typ("zeitlich")
    removeModal()
    
    # Set default time to current time + 5 minutes for convenience
    default_time <- strptime(
      format(Sys.time() + minutes(5), "%H:%M"), 
      "%H:%M"
    )
    
    showModal(
      modalDialog(
        title = paste0("Zeitliche Mitteilung erstellen"),
        tagList(
          div(style = "margin-bottom: 15px;",
              shiny::textAreaInput("msg", "Mitteilung", 
                                   placeholder = "Geben Sie hier Ihre Mitteilung ein...",
                                   width = "100%", 
                                   height = "200px")
          ),
          div(style = "display: flex; gap: 20px; margin-bottom: 15px;",
              div(style = "flex: 1;",
                  shiny::dateInput("date", "Datum", 
                                   value = Sys.Date(), 
                                   format = "dd.mm.yyyy", 
                                   weekstart = 1, 
                                   language = "de")
              ),
              div(style = "flex: 1;",
                  shinyTime::timeInput("time", "Zeit bis", 
                                       seconds = FALSE, 
                                       value = default_time,
                                       minute.steps = 5)
              )
          ),
          div(style = "margin-bottom: 15px;",
              shiny::numericInput("msg_time_s", "Anzeigedauer [Sekunden]", 
                                  value = 5, 
                                  min = 1, 
                                  max = 60,
                                  step = 1,
                                  width = "200px")
          )
        ),
        easyClose = FALSE,
        footer = tagList(
          actionButton("add_timed_exe", "Mitteilung erstellen", 
                       class = "btn-success"),
          modalButton("Abbrechen")
        ),
        size = "l"
      )
    )
  })

  observeEvent(input$add_timed_exe, {
    print("here")
    p <- regex("\\d\\d:\\d\\d:\\d\\d")
    
    # Extract time from input
    time_str <- str_extract(input$time, p)
    if (is.na(time_str)) {
      showNotification("Ungültiges Zeitformat", type = "error")
      return()
    }
    
    # Create datetime in user's local timezone (Europe/Zurich/CET)
    datetime_local <- lubridate::as_datetime(
      paste(input$date, time_str), 
      tz = "Europe/Zurich"  # Explicitly set user's timezone
    )
    
    # Convert to UTC for storage
    datetime_utc <- lubridate::with_tz(datetime_local, "UTC")
    
    cat("\n=== Creating Timed Message ===\n")
    cat("User entered:", format(datetime_local, "%Y-%m-%d %H:%M:%S"), "CET\n")
    cat("Converting to UTC:", format(datetime_utc, "%Y-%m-%d %H:%M:%S %Z"), "\n")
    cat("Current time (UTC):", format(lubridate::with_tz(Sys.time(), "UTC"), "%Y-%m-%d %H:%M:%S %Z"), "\n")
    cat("Current time (CET):", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"), "\n")
    cat("Message will expire at:", format(datetime_local, "%H:%M:%S"), "CET\n")
    cat("(which is", format(datetime_utc, "%H:%M:%S"), "UTC in database)\n")
    
    sql <- "
    INSERT INTO msg 
      (msg, typ, display_till, msg_time_s)
    VALUES 
      (?, ?, ?, ?)
  "
    tryCatch({
      dbExecute(pool, sql, params = list(
        input$msg, msg_typ(), datetime_utc, input$msg_time_s
      ))
      
      showNotification("Zeitliche Mitteilung hinzugefügt", type = "message")
    }, error = function(e) {
      showNotification(paste("Mitteilung konnte nicht hinzugefügt werden:", e$message), type = "error")
    })
    
    removeModal()
  })
  
  observeEvent(input$n_time_msg, {
    msg_typ("n mal")
    removeModal()
    showModal(
      modalDialog(
        title = paste0("Mitteilungen mehrfach anzeigen erstellen"),
        tagList(
          shiny::textAreaInput("msg", "Mitteilung", width = "400px", height = "400px"),
          shiny::numericInput("n_times", "Wie oft soll die Meldung angeigt werden?", value = 1),
          shiny::numericInput("msg_time_s", "Mitteilungs Anzeigezeit [s]", value = 1)
        ),
        easyClose = FALSE,
        footer = tagList(
          actionButton("add_n_time_msg_exe", "Erstellen"),
          actionButton("abort", "Abbrechen", class = "bnt-danger")
        )
      )
    )
  })
  
  
  observeEvent(input$add_n_time_msg_exe, {
    sql <- "
    INSERT INTO msg 
      (msg, typ, display_n_times, msg_time_s)
    VALUES 
      (?, ?, ?, ?)
  "
    tryCatch({
      dbExecute(pool, sql, params = list(
        input$msg, msg_typ(), input$n_times, input$msg_time_s
      ))
      showNotification("Mitteilung hinzugefügt", type = "message")
    }, error = function(e) {
      showNotification(paste("Teilnehmer konnte nicht hinzugefügt werden:", e$message), type = "error")
    })
    removeModal()
  })
  
  ### button edit message ####
  observeEvent(input$edit_msg, {
    sel <- input$msg_tbl_rows_selected
    req(sel) # early stop if nothing has been selected
    
    ID <- last_rendered_msg()|>
      slice(sel)|>
      select(ID)|>
      pull()
    ID  
    
    df_edit <- tbl(pool, "msg")|>
      filter(id == ID)|>
      collect()
    df_edit
    
    if(df_edit$typ == "statisch"){
      showModal(
        modalDialog(
          title = paste0("Statische Mitteilung editieren"),
          tagList(
            shiny::textAreaInput("msg", "Mitteilung", value = df_edit$msg, width = "400px", height = "400px"),
            shiny::numericInput("msg_time_s", "Mitteilungs Anzeigezeit [s]", value = df_edit$msg_time_s)
          ),
          easyClose = FALSE,
          footer = tagList(
            actionButton("edit_static_exe", "Update"),
            actionButton("abort", "Abbrechen", class = "bnt-danger")
          )
        )
      )
    } else if (df_edit$typ == "n mal"){
      showModal(
        modalDialog(
          title = paste0("Mitteilungen mehrfach anzeigen editieren"),
          tagList(
            shiny::textAreaInput("msg", "Mitteilung", value = df_edit$msg, width = "400px", height = "400px"),
            shiny::numericInput("n_times", "Wie oft soll die Meldung angeigt werden?", value = df_edit$display_n_times),
            shiny::numericInput("msg_time_s", "Mitteilungs Anzeigezeit [s]", value = df_edit$msg_time_s)
          ),
          easyClose = FALSE,
          footer = tagList(
            actionButton("edit_n_time_msg_exe", "Update"),
            actionButton("abort", "Abbrechen", class = "bnt-danger")
          )
        )
      )
    } else if ((df_edit$typ == "zeitlich")){
      # Convert from UTC (database) to local time (Europe/Zurich) for display
      if (!is.na(df_edit$display_till)) {
        # Parse the UTC time from database
        display_time_utc <- as.POSIXct(df_edit$display_till, tz = "UTC")
        # Convert to local time (Europe/Zurich)
        display_time_local <- lubridate::with_tz(display_time_utc, "Europe/Zurich")
      } else {
        # Default to current time + 5 minutes if no time is set
        display_time_local <- Sys.time() + minutes(5)
      }
      
      # Extract date and time components
      display_date <- as.Date(display_time_local)
      display_hour <- as.numeric(format(display_time_local, "%H"))
      display_minute <- as.numeric(format(display_time_local, "%M"))
      
      # Create time value for shinyTime
      time_value <- strptime(
        paste(display_hour, display_minute, sep = ":"), 
        "%H:%M"
      )
      
      showModal(
        modalDialog(
          title = paste0("Zeitliche Mitteilung editieren (ID: ", ID, ")"),
          tagList(
            div(style = "margin-bottom: 15px;",
                shiny::textAreaInput("msg", "Mitteilung", 
                                     value = df_edit$msg, 
                                     width = "100%", 
                                     height = "200px",
                                     placeholder = "Geben Sie hier Ihre Mitteilung ein...")
            ),
            div(style = "display: flex; gap: 20px; margin-bottom: 15px;",
                div(style = "flex: 1;",
                    shiny::dateInput("date", "Datum bis", 
                                     value = display_date, 
                                     format = "dd.mm.yyyy", 
                                     weekstart = 1, 
                                     language = "de")
                ),
                div(style = "flex: 1;",
                    shinyTime::timeInput("time", "Zeit bis", 
                                         seconds = FALSE, 
                                         value = time_value,
                                         minute.steps = 1)
                )
            ),
            div(style = "margin-bottom: 15px;",
                shiny::numericInput("msg_time_s", "Anzeigedauer [Sekunden]", 
                                    value = df_edit$msg_time_s, 
                                    min = 1, 
                                    max = 60,
                                    step = 1,
                                    width = "200px")
            )
          ),
          easyClose = FALSE,
          footer = tagList(
            actionButton("edit_timed_exe", "Änderungen speichern", 
                         class = "btn-primary"),
            modalButton("Abbrechen")
          ),
          size = "l"
        )
      )
    }
  })
  
  # Helper function to show timezone conversion
  show_timezone_conversion <- function(utc_time, message_id = NULL) {
    if (!is.null(message_id)) {
      cat(paste0("\n=== Message ID ", message_id, " ===\n"))
    }
    
    if (!is.na(utc_time)) {
      utc_time <- as.POSIXct(utc_time, tz = "UTC")
      local_time <- lubridate::with_tz(utc_time, "Europe/Zurich")
      
      cat("Database (UTC):", format(utc_time, "%Y-%m-%d %H:%M:%S %Z"), "\n")
      cat("User sees (CET):", format(local_time, "%Y-%m-%d %H:%M:%S %Z"), "\n")
      cat("Time difference:", format(local_time - utc_time), "\n")
    } else {
      cat("No time set for this message\n")
    }
  }
  
  observeEvent(input$edit_static_exe, {
    sel <- input$msg_tbl_rows_selected
    req(sel) # early stop if nothing has been selected
    
    ID <- last_rendered_msg()|>
      slice(sel)|>
      select(ID)|>
      pull()
    
    df_data <- tbl(pool, "msg")|>
      filter(id == ID)|>
      collect()
    df_data
    
    sql <- "UPDATE msg SET msg = ?, msg_time_s = ? WHERE id  = ?;"
    
    tryCatch({
      dbExecute(pool, sql, params = list(
        input$msg, input$msg_time_s, ID
      ))
      showNotification("Statische Mitteilung editiert", type = "message")
    }, error = function(e) {
      print("jump to error")
      showNotification(paste("Statische Mitteilung konnte nicht editiert werden:", e$message), type = "error")
    })
    removeModal()
  })
  
  observeEvent(input$edit_n_time_msg_exe, {
    sel <- input$msg_tbl_rows_selected
    req(sel) # early stop if nothing has been selected
    
    ID <- last_rendered_msg()|>
      slice(sel)|>
      select(ID)|>
      pull()
    
    df_data <- tbl(pool, "msg")|>
      filter(id == ID)|>
      collect()
    df_data
    
    sql <- "UPDATE msg SET msg = ?, display_n_times = ?, msg_time_s = ? WHERE id  = ?;"
    
    tryCatch({
      dbExecute(pool, sql, params = list(
        input$msg, input$msg_time_s, input$n_times, ID
      ))
      showNotification("n mall Mitteilung editiert", type = "message")
    }, error = function(e) {
      print("jump to error")
      showNotification(paste("n mal Mitteilung konnte nicht editiert werden:", e$message), type = "error")
    })
    removeModal()
  })
  
  observeEvent(input$edit_timed_exe, {
    sel <- input$msg_tbl_rows_selected
    req(sel) # early stop if nothing has been selected
    
    ID <- last_rendered_msg()|>
      slice(sel)|>
      select(ID)|>
      pull()
    
    df_data <- tbl(pool, "msg")|>
      filter(id == ID)|>
      collect()
    df_data
    
    p <- regex("\\d\\d:\\d\\d:\\d\\d")
    
    # Extract time from input
    time_str <- str_extract(input$time, p)
    if (is.na(time_str)) {
      showNotification("Ungültiges Zeitformat", type = "error")
      return()
    }
    
    # Create datetime in user's local timezone (Europe/Zurich/CET)
    datetime_local <- lubridate::as_datetime(
      paste(input$date, time_str), 
      tz = "Europe/Zurich"  # Explicitly set user's timezone
    )
    
    # Convert to UTC for storage
    datetime_utc <- lubridate::with_tz(datetime_local, "UTC")
    
    cat("\n=== Editing Timed Message ===\n")
    cat("Message ID:", ID, "\n")
    cat("User entered:", format(datetime_local, "%Y-%m-%d %H:%M:%S"), "CET\n")
    cat("Converting to UTC:", format(datetime_utc, "%Y-%m-%d %H:%M:%S %Z"), "\n")
    cat("Current time (UTC):", format(lubridate::with_tz(Sys.time(), "UTC"), "%Y-%m-%d %H:%M:%S %Z"), "\n")
    cat("Current time (CET):", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"), "\n")
    cat("Message will expire at:", format(datetime_local, "%H:%M:%S"), "CET\n")
    cat("(which is", format(datetime_utc, "%H:%M:%S"), "UTC in database)\n")
    
    sql <- "UPDATE msg SET msg = ?, display_till = ?, msg_time_s = ? WHERE id = ?;"
    
    tryCatch({
      dbExecute(pool, sql, params = list(
        input$msg, datetime_utc, input$msg_time_s, ID
      ))
      showNotification("Zeitliche Mitteilung aktualisiert", type = "message")
    }, error = function(e) {
      print("jump to error")
      showNotification(paste("Zeitliche Mitteilung konnte nicht aktualisiert werden:", e$message), type = "error")
    })
    removeModal()
  })
  
  
  ### button Delete message ####
  observeEvent(input$del_msg, {
    print("here")
    sel <- input$msg_tbl_rows_selected
    req(sel) # early stop if nothing has been selected
    
    ID <- last_rendered_msg()|>
      slice(sel)|>
      select(ID)|>
      pull()
    
    tryCatch({
      pool::poolWithTransaction(pool, function(conn) {
        DBI::dbExecute(conn, "DELETE FROM msg WHERE id = ?", params = ID)
      })
      
      showNotification(
        paste0("Mitteilung ", sel," wurde gelöscht."),
        type = "message"
      )
    }, error = function(e) {
      showNotification(paste("Löschen fehlgeschlagen:", e$message), type = "error")
    })
  })
  
  ### button publish message ####
  observeEvent(input$publish_msg, {
    sel <- input$msg_tbl_rows_selected
    req(sel) # early stop if nothing has been selected
    
    ID <- last_rendered_msg()|>
      slice(sel)|>
      select(ID)|>
      pull()
    
    sql <- "UPDATE msg SET publish_msg = ? WHERE id  = ?;"
    
    tryCatch({
      dbExecute(pool, sql, params = list(
        TRUE, ID
      ))
      showNotification("Mitteilung publiziert", type = "message")
    }, error = function(e) {
      print("jump to error")
      showNotification(paste("Mitteilung konnte nicht publiziert werden:", e$message), type = "error")
    })
  })
  
  ### button stop publishing message ####
  observeEvent(input$unpublish_msg, {
    sel <- input$msg_tbl_rows_selected
    req(sel) # early stop if nothing has been selected
    
    ID <- last_rendered_msg()|>
      slice(sel)|>
      select(ID)|>
      pull()
    
    sql <- "
    UPDATE msg SET publish_msg = ? WHERE id  = ?;
    "
    tryCatch({
      dbExecute(pool, sql, params = list(
        FALSE, ID
      ))
      showNotification("Mitteilung publiziert", type = "message")
    }, error = function(e) {
      showNotification(paste("Mitteilung konnte nicht publiziert werden:", e$message), type = "error")
    })
  })
  
  ### Reactive: Message System to render ####
  msg_tbl <- reactivePoll(3000, session,
                          checkFunc = function() {
                            row_count <- dbGetQuery(pool, "SELECT COUNT(*) as row_count FROM msg")$row_count
                            max_update <- dbGetQuery(pool, "SELECT MAX(last_updated) as max_update FROM msg")$max_update
                            paste0("nrow = ", row_count, ", max_update = ", max_update)
                          },
                          valueFunc = function() {
                            print("Reactive: messages")
                            
                            # Get raw data from database
                            df_msg <- tbl(pool, "msg") |> 
                              collect()
                            
                            # Process the data
                            df_msg <- df_msg |>
                              mutate(
                                publish_msg = if_else(publish_msg == 1, TRUE, FALSE),
                                # display_till is stored as UTC in database
                                # Convert to local time for display
                                display_till_utc = as.POSIXct(display_till, tz = "UTC"),
                                display_till_local = with_tz(display_till_utc, tz = "Europe/Zurich"),
                                display_n_times = as.integer(display_n_times),
                                msg_time_s = as.numeric(msg_time_s),
                                last_displayed = as.POSIXct(last_displayed),
                                display_count = as.integer(display_count)
                              )
                            
                            return(df_msg)
                          }
  )
  
  ### Debug observer to check message data ####
  observe({
    # Get published messages
    df_data <- msg_tbl() |> filter(publish_msg == 1)
    
    if (nrow(df_data) > 0) {
      cat("Debug - Message data before building queue:\n")
      print(df_data |> select(id, typ, msg_time_s, publish_msg))
      
      # Check for NA or invalid msg_time_s
      invalid_times <- df_data |> 
        filter(is.na(msg_time_s) | msg_time_s <= 0)
      
      if (nrow(invalid_times) > 0) {
        cat("Warning - Messages with invalid display times:\n")
        print(invalid_times)
      }
    }
  })
  
  ### Observer to update message queue when messages change ####
  observe({
    # Get published messages
    df_data <- msg_tbl() |> filter(publish_msg == 1)
    
    if (nrow(df_data) > 0) {
      # Build new queue
      new_queue <- build_message_queue(df_data)
      
      # Clean expired messages
      new_queue <- clean_expired_messages(new_queue)
      
      # Update queue
      message_queue(new_queue)
    } else {
      message_queue(data.frame())
    }
  })
  
  ### Observer to manage message display timer ####
  ### Observer to manage message display timer ####
  observe({
    # Get current queue and index
    queue <- message_queue()
    idx <- queue_index()
    
    # If queue is empty, clear current message
    if (nrow(queue) == 0) {
      current_message(NULL)
      message_start_time(NULL)
      return()
    }
    
    # Check if current message exists and has valid duration
    if (!is.null(current_message())) {
      # Check if display_duration is valid
      if (is.na(current_message()$display_duration) || 
          current_message()$display_duration <= 0) {
        # Skip this message and move to next
        cat("Warning - Invalid display duration for message:", 
            current_message()$id, "\n")
        
        # Update n-time counter for previous message if needed
        if (!is.null(current_message()) && current_message()$type == "n_time") {
          update_n_time_counter(current_message()$id)
        }
        
        # Force move to next message
        current_message(NULL)
        message_start_time(NULL)
      }
    }
    
    # If no current message or timer expired, show next message
    if (is.null(current_message()) || 
        (as.numeric(difftime(Sys.time(), message_start_time(), units = "secs")) >= 
         current_message()$display_duration)) {
      
      # Update n-time counter for previous message if needed
      if (!is.null(current_message()) && current_message()$type == "n_time") {
        cat("Debug - Updating counter for n-time message ID:", current_message()$id, "\n")
        update_n_time_counter(current_message()$id)
        
        # Also update database counter
        tryCatch({
          current_count <- n_time_counter()[[as.character(current_message()$id)]]
          sql <- "UPDATE msg SET display_count = ? WHERE id = ?"
          dbExecute(pool, sql, params = list(current_count, current_message()$id))
          cat("Debug - Updated database counter for message", current_message()$id, "to", current_count, "\n")
        }, error = function(e) {
          cat("Debug - Failed to update database counter:", e$message, "\n")
        })
      }
      
      # Get next message from queue
      if (idx > nrow(queue)) {
        idx <- 1  # Loop back to start
      }
      
      next_msg <- queue[idx, ]
      
      # Check if display_duration is valid
      if (is.na(next_msg$display_duration) || next_msg$display_duration <= 0) {
        cat("Warning - Skipping message with invalid duration:", next_msg$id, "\n")
        # Skip this message and try next one
        queue_index(idx + 1)
        return()
      }
      
      # For timed messages, check if expired
      if (next_msg$type == "timed" && !is.na(next_msg$expires_at)) {
        if (next_msg$expires_at < Sys.time()) {
          cat("Debug - Skipping expired timed message:", next_msg$id, "\n")
          # Skip this message and try next one
          queue_index(idx + 1)
          return()
        }
      }
      
      current_message(next_msg)
      message_start_time(Sys.time())
      
      cat("Debug - Now showing message ID:", next_msg$id, 
          "Type:", next_msg$type,
          "Duration:", next_msg$display_duration, "s\n")
      
      # Update index for next message
      next_idx <- idx + 1
      if (next_idx > nrow(queue)) {
        # Rebuild queue to refresh available messages
        df_data <- msg_tbl() |> filter(publish_msg == 1)
        if (nrow(df_data) > 0) {
          new_queue <- build_message_queue(df_data)
          new_queue <- clean_expired_messages(new_queue)
          message_queue(new_queue)
          next_idx <- 1
          cat("Debug - Rebuilt queue, new size:", nrow(new_queue), "\n")
        }
      }
      queue_index(next_idx)
    }
    
    # Schedule check every second
    invalidateLater(1000, session)
  })
  
  ### Render Message with Queue System ####
  ### Render Message with Queue System (JavaScript version) ####
  output$msg_ui <- renderUI({
    # Get current message
    current_msg <- current_message()
    
    if (!is.null(current_msg)) {
      # Generate unique ID for this message
      msg_id <- paste0("msg_", current_msg$queue_id)
      
      tagList(
        # JavaScript to handle the countdown
        tags$script(HTML(sprintf("
        $(document).ready(function() {
          var duration = %d * 1000; // Convert to milliseconds
          var startTime = Date.now();
          var endTime = startTime + duration;
          var progressBar = $('#%s .progress-fill');
          
          function updateProgress() {
            var now = Date.now();
            var elapsed = now - startTime;
            var remaining = duration - elapsed;
            var percentage = (remaining / duration) * 100;
            
            // Update progress bar
            progressBar.css('width', percentage + '%%');
            
            // Change color based on time
            if (percentage > 50) {
              progressBar.css('background-color', '#4CAF50');
            } else if (percentage > 25) {
              progressBar.css('background-color', '#FF9800');
            } else {
              progressBar.css('background-color', '#F44336');
            }
            
            // Continue updating if time remains
            if (remaining > 0) {
              requestAnimationFrame(updateProgress);
            }
          }
          
          // Start the animation
          requestAnimationFrame(updateProgress);
        });
      ", current_msg$display_duration, msg_id))),
        
        div(
          id = msg_id,
          style = paste0(
            "padding: 20px;",
            "background-color: rgba(0, 0, 0, 0.7);",
            "color: white;",
            "font-size: 24px;",
            "text-align: center;",
            "border-radius: 10px;",
            "margin: 10px 0;",
            "min-height: 100px;",
            "display: flex;",
            "flex-direction: column;",
            "justify-content: center;",
            "align-items: center;"
          ),
          # Message text
          div(
            current_msg$msg,
            style = "margin-bottom: 10px;"
          ),
          # Progress bar container
          div(
            style = "width: 100%; background-color: #555; border-radius: 5px; height: 10px; margin-top: 10px; overflow: hidden;",
            # Progress bar fill
            div(
              class = "progress-fill",
              style = paste0(
                "width: 100%;",
                "height: 100%;",
                "background-color: #4CAF50;",
                "transition: width 0.1s linear, background-color 0.5s ease;"
              )
            )
          ),
          # Message info
          div(
            style = "font-size: 12px; margin-top: 10px; color: #ccc;",
            paste(
              "Typ:", current_msg$type, "|",
              "Dauer:", current_msg$display_duration, "s |",
              if (current_msg$type == "n_time") {
                paste("Anzeige", current_msg$display_count, "von", current_msg$max_displays)
              } else ""
            )
          )
        )
      )
    } else {
      # No message to display
      div(
        style = paste0(
          "padding: 20px;",
          "background-color: rgba(0, 0, 0, 0.3);",
          "color: #ccc;",
          "font-size: 18px;",
          "text-align: center;",
          "border-radius: 10px;",
          "margin: 10px 0;",
          "min-height: 100px;",
          "display: flex;",
          "justify-content: center;",
          "align-items: center;"
        ),
        "Keine Mitteilungen verfügbar"
      )
    }
  })
  

  ### Next message button ####
  observeEvent(input$next_message, {
    # Force display of next message
    if (!is.null(current_message()) && current_message()$type == "n_time") {
      update_n_time_counter(current_message()$id)
    }
    
    queue <- message_queue()
    idx <- queue_index()
    
    if (nrow(queue) > 0) {
      if (idx > nrow(queue)) idx <- 1
      next_msg <- queue[idx, ]
      current_message(next_msg)
      message_start_time(Sys.time())
      queue_index(idx + 1)
    } else {
      current_message(NULL)
    }
  })
  
  ### Reset counters button ####
  observeEvent(input$reset_counters, {
    showModal(
      modalDialog(
        title = "Zähler zurücksetzen",
        "Möchten Sie wirklich alle Anzeige-Zähler zurücksetzen?",
        easyClose = FALSE,
        footer = tagList(
          modalButton("Abbrechen"),
          actionButton("confirm_reset_counters", "Zurücksetzen", class = "btn-danger")
        )
      )
    )
  })
  
  observeEvent(input$confirm_reset_counters, {
    # Reset n_time_counter
    n_time_counter(list())
    
    # Reset database counters if you're tracking there
    tryCatch({
      dbExecute(pool, "UPDATE msg SET display_count = 0, last_displayed = NULL")
      showNotification("Alle Zähler wurden zurückgesetzt", type = "message")
    }, error = function(e) {
      showNotification("Fehler beim Zurücksetzen der Zähler", type = "error")
    })
    
    removeModal()
    
    # Rebuild queue
    df_data <- msg_tbl() |> filter(publish_msg == 1)
    if (nrow(df_data) > 0) {
      new_queue <- build_message_queue(df_data)
      message_queue(new_queue)
      queue_index(1)
    }
  })
  
  ### Render message table ####
  output$msg_tbl <- renderDT({
    showNotification(paste("update message table"), type = "message")
    df_data <- msg_tbl()
    df_data <- df_data|>
      mutate(typ = factor(typ),
             publish_msg = if_else(publish_msg, "öffentlich", "gesperrt")|>
               factor()
      )|>
      rename(ID = id,
             Mitteilung = msg,
             `Mitteilung publizieren` = publish_msg,
             `Mitteilungstyp` = typ,
             `Publizieren bis` = display_till,
             `Anzahl Publikationen` =  display_n_times,
             `Anzeigezeit [s]` = msg_time_s
      )
    
    last_rendered_msg(df_data)
    
    datatable(
      df_data, 
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
            "  Shiny.setInputValue('msg_tbl_signal', new Date().getTime());",
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
  
  ## Testing ####
  
  ### Render: Table events for testing ####
  output$events_tbl_test <- renderDT({
    showNotification(paste("New Events found"), type = "message")
    df_test <- events_data()|>
      mutate(Startnummer = factor(Startnummer),
             run = factor(run),
             device_name = factor(device_name),
             race_status = factor(race_status))|>
      rename(Lauf = run, 
             Zeitstempel = timestamp_ms,
             `Geräte ID` = device_id, 
             `Gerätename` = device_name, 
             Rennstatus = race_status)|>
      select(id,Startnummer, Lauf, Rennstatus, Gerätename, Zeitstempel, `Geräte ID` )
    
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
            "  Shiny.setInputValue('events_tbl_signal', new Date().getTime());",
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
  
  ### start race ####
  observeEvent(input$test_start_race, {
    df_temp <- participants_smart_poll()
    
    # Calculate modal size based on number of columns
    num_cols <- ncol(df_temp)
    modal_width <- ifelse(num_cols <= 3, "s", ifelse(num_cols <= 5, "m", "l"))
    modal_height <- ifelse(nrow(df_temp) <= 5, "auto", "600px")
    
    showModal(
      modalDialog(
        title = paste0("Zeitmessung starten"),
        tagList(
          renderText("Bitte Startnummer selektieren!"),
          shiny::hr(),
          div(style = paste0("max-height: ", modal_height, "; overflow-y: auto;"),
              dataTableOutput("modal_participants_tbl"))
        ),
        easyClose = FALSE,
        footer = tagList(
          actionButton("start_race_participant", "Zeitmessung starten!", class = "bnt-danger"),
          actionButton("abort", "Abbrechen")
        )
      )
    )
    
  })
  
  observeEvent(input$start_race_participant, {
    print("race start")
    # select start number
    c_selected <- input$modal_participants_tbl_rows_selected
    df_temp <- participants_smart_poll()
    row <- df_temp[c_selected,]
    
    removeModal()
    
    # test if start number is allowed to start
    df_temp <- events_data()
    df_temp <- df_temp|>
      filter((Startnummer == row$Startnummer) & run == max(run) & (race_status %in% c("started","finished")))
    df_temp
    if(nrow(df_temp) == 1) {
      paste0("racer with Startnumber ", row$Startnummer, " is still on track and has not been disqualified")|>
        writeLines()
      req(NULL) # early exit
    }
    
    # Create and execute SQL query
    ts0 <- as.POSIXct(Sys.time())
    sn <- row$Startnummer
    run_no <- dbGetQuery(pool, "SELECT next_run FROM participant WHERE Startnummer = ?", params = list(sn))$next_run
    dbExecute(pool, "INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name, last_updated)
                     VALUES (?, ?, ?, 'started', 'chip001', 'StartGate', NOW(3))",
              params = list(sn, run_no, format(ts0, "%Y-%m-%d %H:%M:%OS3")))
    dbExecute(pool, "UPDATE participant SET last_run = ?, last_updated = NOW(3) WHERE Startnummer = ?", 
              params = list(run_no, sn))
  })
  
  ### finish race ####
  
  observeEvent(input$test_finish_race, {
    df_temp <- participants_smart_poll()
    
    # Calculate modal size based on number of columns
    num_cols <- ncol(df_temp)
    modal_width <- ifelse(num_cols <= 3, "s", ifelse(num_cols <= 5, "m", "l"))
    modal_height <- ifelse(nrow(df_temp) <= 5, "auto", "600px")
    
    showModal(
      modalDialog(
        title = paste0("Zeitmessung stoppen"),
        tagList(
          renderText("Bitte Startnummer selektieren!"),
          shiny::hr(),
          div(style = paste0("max-height: ", modal_height, "; overflow-y: auto;"),
              dataTableOutput("modal_participants_tbl"))
        ),
        easyClose = FALSE,
        footer = tagList(
          actionButton("finish_race_participant", "Zeitmessung stoppen!", class = "bnt-danger"),
          actionButton("abort", "Abbrechen")
        )
      )
    )
    
  })
  
  observeEvent(input$finish_race_participant, {
    print("race start")
    # select start number
    c_selected <- input$modal_participants_tbl_rows_selected
    df_temp <- participants_smart_poll()
    row <- df_temp[c_selected,]
    
    removeModal()
    
    # Create and execute SQL query
    ts0 <- as.POSIXct(Sys.time())
    sn <- row$Startnummer
    run_no <- dbGetQuery(pool, "SELECT next_run FROM participant WHERE Startnummer = ?", params = list(sn))$next_run
    dbExecute(pool, "INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name, last_updated)
                     VALUES (?, ?, ?, 'finished', 'chip002', 'FinishGate', NOW(3))",
              params = list(sn, run_no, format(ts0, "%Y-%m-%d %H:%M:%OS3")))
    dbExecute(pool, "UPDATE participant SET next_run = ?, last_updated = NOW(3) WHERE Startnummer = ?", 
              params = list(run_no + 1, sn))
  })
  
  ### Render Rennablauf: Table participant ####
  output$modal_participants_tbl <- renderDT({
    
    if(is.null(participants_smart_poll())) {
      print("not yet initialized")
      df_temp <- tibble(Teilnehmer = "Bitte Teilnehmerliste aktualisieren")
      
      datatable(
        df_temp,
        rownames = FALSE
      )
      
    } else {
      df_temp <- participants_smart_poll()|>
        rename(
          RFID = rfid_uid_le,
          Startreihenfolge = race_order,
          `Letzter Lauf` = last_run,
          `Nächster Lauf` = next_run)
      
      df_temp <-  df_temp|>
        mutate(Startnummer = factor(Startnummer),
               Startreihenfolge = factor(Startreihenfolge),
               `Letzter Lauf` = factor(`Letzter Lauf`),
               `Nächster Lauf` = factor(`Nächster Lauf`))|>
        select(Startnummer, RFID, Vorname, Name, Nickname)
      
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
              "  Shiny.setInputValue('modal_participants_tbl_signal', new Date().getTime());",
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
    } 
  })
  
  ### Demo sequence: start → interim → finish — uses Startnummer ####
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
  
  ## Delete all picologs ####
  observeEvent(input$delete_picologs, {
    # Show a confirmation modal for safety
    showModal(modalDialog(
      title = "Delete All Logs",
      size = "m",
      shiny::tagList(
        p(strong("Warning: This action cannot be undone!")),
        p("This will delete ALL entries from the picolog table."),
        p(paste("Current log count:", 
                dbGetQuery(pool, "SELECT COUNT(*) as count FROM Picolog")$count))
      ),
      footer = tagList(
        modalButton("Cancel"),
        actionButton("confirm_delete_picologs", "Delete All Logs", class = "btn-danger")
      ),
      easyClose = TRUE,
    ))
  })
  
  ## Confirm deletion ####
  observeEvent(input$confirm_delete_picologs, {
    tryCatch({
      # Delete all logs
      rows_deleted <- dbExecute(pool, "DELETE FROM Picolog")
      
      removeModal()
      
      # Show success message
      showNotification(
        sprintf("Successfully deleted all logs (%d rows removed).", rows_deleted),
        type = "message",
        duration = 5
      )
      
    }, error = function(e) {
      showNotification(paste("Failed to delete logs:", e$message), type = "error")
    })
  })
}


# Run the shiny app ####
shiny::runApp(
  host = DB_hoste_name,
  shiny::shinyApp(ui = ui, server = server),
  port = 5000,
  launch.browser = TRUE
)

