# app.R — Shiny app for zeitmessung_V2 (event-log model)
# ------------------------------------------------------
# Features
# - Manage participants
# - Insert timing events (started / interim / finished / disqualify)
# - Auto-updates participant.last_run and next_run when appropriate
# - Live views of raw events and summarized runs (duration)
# - Uses DBI + pool + RMariaDB with parameterized queries

# --- Packages ---
library(shiny)
library(DBI)
library(pool)
library(RMariaDB)
library(dplyr)
library(DT)

# --- DB Pool (configure via environment variables) ---
# Set these in your environment (e.g., ~/.Renviron) or before launching the app:
Sys.setenv(
  ZEIT_DB_HOST = "localhost",
  ZEIT_DB_NAME = "zeitmessung_V2",
  ZEIT_DB_USER = "race",
  ZEIT_DB_PASS = "49rb61",
  ZEIT_DB_PORT = "3306"
)

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

pool <- dbPool(
  drv = RMariaDB::MariaDB(),
  host = Sys.getenv("ZEIT_DB_HOST", "localhost"),
  dbname = Sys.getenv("ZEIT_DB_NAME", "zeitmessung_V2"),
  user = Sys.getenv("ZEIT_DB_USER", "root"),
  password = Sys.getenv("ZEIT_DB_PASS", ""),
  port = as.integer(Sys.getenv("ZEIT_DB_PORT", "3306")),
  bigint = "integer"
)

onStop(function() {
  poolClose(pool)
})

# --- Helpers ---
now_ms <- function() {
  # Format with millisecond precision for DATETIME(3)
  format(Sys.time(), "%Y-%m-%d %H:%M:%OS3")
}

# Create/refresh a summary view that pairs started/finished per run
ensure_summary_view <- function(pool) {
  sql <- "CREATE OR REPLACE VIEW race_summary AS
  SELECT 
    p.id AS participant_id,
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
    (SELECT participant_id, run, MIN(timestamp_ms) AS start_time
     FROM race WHERE race_status = 'started'
     GROUP BY participant_id, run) s
  LEFT JOIN
    (SELECT participant_id, run, MAX(timestamp_ms) AS finish_time
     FROM race WHERE race_status = 'finished'
     GROUP BY participant_id, run) f
  ON s.participant_id = f.participant_id AND s.run = f.run
  LEFT JOIN participant p ON p.id = s.participant_id;"
  dbExecute(pool, sql)
}

# Initialize view
try(ensure_summary_view(pool), silent = TRUE)

# --- UI ---
ui <- fluidPage(
  shiny::tags$head(
    shiny::tags$link(rel = "stylesheet", type = "text/css", 
                     href = paste0("custom_styles/dark.css?v=", as.integer(Sys.time()))
    )
  ),
  titlePanel("Zeitmessung V2 — Event Log"),
  tabsetPanel(
    tabPanel("Participants",
             fluidRow(
               column(4,
                      h3("Add participant"),
                      textInput("p_name", "Name", value = ""),
                      textInput("p_vorname", "Vorname", value = ""),
                      numericInput("p_race_order", "Race order", value = NA, min = 1),
                      textInput("p_phone", "Phone", value = ""),
                      textInput("p_email", "E-mail", value = ""),
                      textInput("p_kategorie", "Kategorie", value = ""),
                      numericInput("p_gewicht", "Gewicht (kg)", value = NA, min = 0, step = 0.1),
                      actionButton("add_participant", "Add participant", class = "btn btn-primary")
               ),
               column(8,
                      h3("Participants"),
                      DTOutput("participants_tbl")
               )
             )
    ),
    tabPanel("Events",
             fluidRow(
               column(4,
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
                      hr(),
                      h4("Quick demo for selected participant"),
                      actionButton("demo_sequence", "Insert demo: start → interim → finish", class = "btn btn-secondary")
               ),
               column(8,
                      h3("Events (race)"),
                      DTOutput("events_tbl")
               )
             )
    ),
    tabPanel("Summary",
             fluidRow(
               column(4,
                      h3("Filters"),
                      uiOutput("participant_filter_ui")
               ),
               column(8,
                      h3("Run summary (view)"),
                      DTOutput("summary_tbl")
               )
             )
    )
  )
)

# --- Server ---
server <- function(input, output, session) {
  # Track last update timestamps
  last_participant_update <- reactiveVal(Sys.time())
  last_race_update <- reactiveVal(Sys.time())
  
  # Function to check if participants table has changed
  check_participants_update <- function() {
    max_update <- dbGetQuery(pool, "SELECT MAX(last_updated) as max_update FROM participant")$max_update
    if (is.na(max_update)) return(Sys.time())
    as.POSIXct(max_update)
  }
  
  # Function to check if race table has changed
  check_race_update <- function() {
    max_update <- dbGetQuery(pool, "SELECT MAX(last_updated) as max_update FROM race")$max_update
    if (is.na(max_update)) return(Sys.time())
    as.POSIXct(max_update)
  }
  
  # Reactive: participants data with smart polling
  participants_data <- reactivePoll(3000, session,
                                    checkFunc = function() {
                                      current_max <- check_participants_update()
                                      if (current_max > last_participant_update()) {
                                        last_participant_update(current_max)
                                        TRUE
                                      } else {
                                        FALSE
                                      }
                                    },
                                    valueFunc = function() {
                                      dbReadTable(pool, "participant") |> arrange(id)
                                    }
  )
  
  # Reactive: events data with smart polling
  events_data <- reactivePoll(3000, session,
                              checkFunc = function() {
                                current_max <- check_race_update()
                                if (current_max > last_race_update()) {
                                  last_race_update(current_max)
                                  TRUE
                                } else {
                                  FALSE
                                }
                              },
                              valueFunc = function() {
                                dbReadTable(pool, "race") |> arrange(desc(id))
                              }
  )
  
  # Reactive: summary data with smart polling (depends on both tables)
  summary_data <- reactivePoll(3000, session,
                               checkFunc = function() {
                                 # Check if either participants or race data has changed
                                 participants_max <- check_participants_update()
                                 race_max <- check_race_update()
                                 
                                 participants_changed <- participants_max > last_participant_update()
                                 race_changed <- race_max > last_race_update()
                                 
                                 if (participants_changed || race_changed) {
                                   if (participants_changed) last_participant_update(participants_max)
                                   if (race_changed) last_race_update(race_max)
                                   TRUE
                                 } else {
                                   FALSE
                                 }
                               },
                               valueFunc = function() {
                                 ensure_summary_view(pool)
                                 dbReadTable(pool, "race_summary") |> arrange(participant_id, run)
                               }
  )
  
  # Store the current participant selection to preserve it across updates
  current_participant <- reactiveVal(NULL)
  
  # Update current participant when user makes a selection
  observeEvent(input$participant_id, {
    current_participant(input$participant_id)
  })
  
  # UI pieces depending on DB content - preserve selection
  output$participant_select_ui <- renderUI({
    df <- participants_data()
    choices <- setNames(df$id, paste0(df$id, ": ", df$Name, " ", df$Vorname))
    
    # Preserve current selection if it exists and is valid, otherwise use first
    selected <- if (!is.null(current_participant()) && current_participant() %in% df$id) {
      current_participant()
    } else if (nrow(df) > 0) {
      df$id[1]
    } else {
      NULL
    }
    
    selectInput("participant_id", "Participant", choices = choices, selected = selected)
  })
  
  output$participant_filter_ui <- renderUI({
    df <- participants_data()
    selectInput("participant_filter", "Participant (optional)", choices = c("All" = "", setNames(df$id, paste0(df$id, ": ", df$Name, " ", df$Vorname))), selected = "")
  })
  
  # Populate run number based on participant.next_run when participant changes
  observeEvent(input$participant_id, {
    req(input$participant_id)
    run_no <- dbGetQuery(pool, "SELECT next_run FROM participant WHERE id = ?", params = list(input$participant_id))$next_run
    updateNumericInput(session, "run_number", value = ifelse(length(run_no), run_no, 1))
  }, ignoreInit = TRUE)
  
  # Tables
  output$participants_tbl <- renderDT({
    datatable(participants_data(), options = list(pageLength = 10, order = list(list(0, 'asc'))))
  })
  
  output$events_tbl <- renderDT({
    datatable(events_data(), options = list(pageLength = 10))
  })
  
  output$summary_tbl <- renderDT({
    df <- summary_data()
    if (!is.null(input$participant_filter) && nzchar(input$participant_filter)) {
      df <- df |> filter(participant_id == as.integer(input$participant_filter))
    }
    datatable(df, options = list(pageLength = 10))
  })
  
  # Insert participant
  observeEvent(input$add_participant, {
    nm <- trimws(input$p_name)
    vn <- trimws(input$p_vorname)
    ro <- ifelse(is.na(input$p_race_order), NA, as.integer(input$p_race_order))
    ph <- input$p_phone
    em <- input$p_email
    ka <- input$p_kategorie
    gw <- ifelse(is.na(input$p_gewicht), NA, as.numeric(input$p_gewicht))
    
    sql <- "INSERT INTO participant (race_order, last_run, next_run, Name, Vorname, Phone, `E-mail`, Kategorie, Gewicht, last_updated)
            VALUES (?, NULL, 1, ?, ?, ?, ?, ?, ?, NOW(3))"
    dbExecute(pool, sql, params = list(ro, nm, vn, ph, em, ka, gw))
    
    # Force update by setting the last update time to current time
    last_participant_update(Sys.time())
    
    showNotification("Participant added", type = "message")
  })
  
  # Insert event
  observeEvent(input$insert_event, {
    req(input$participant_id, input$run_number)
    
    ts <- if (isTRUE(input$use_now)) now_ms() else input$timestamp_free
    
    # Insert event row
    ins_sql <- "INSERT INTO race (participant_id, run, timestamp_ms, race_status, device_id, device_name, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, NOW(3))"
    dbExecute(pool, ins_sql, params = list(as.integer(input$participant_id), as.integer(input$run_number), ts, input$race_status, input$device_id, input$device_name))
    
    # Force update by setting the last update time to current time
    last_race_update(Sys.time())
    
    # If started: set last_run and update participant timestamp
    if (identical(input$race_status, "started")) {
      dbExecute(pool, "UPDATE participant SET last_run = ?, last_updated = NOW(3) WHERE id = ?", 
                params = list(as.integer(input$run_number), as.integer(input$participant_id)))
      # Also force participant update
      last_participant_update(Sys.time())
    }
    
    # If finished or disqualify: increment next_run and update participant timestamp
    if (input$race_status %in% c("finished", "disqualify")) {
      dbExecute(pool, "UPDATE participant SET next_run = next_run + 1, last_updated = NOW(3) WHERE id = ?", 
                params = list(as.integer(input$participant_id)))
      # Also force participant update
      last_participant_update(Sys.time())
    }
    
    showNotification(sprintf("Event '%s' inserted", input$race_status), type = "message")
  })
  
  # Demo sequence: start → interim → finish for selected participant
  observeEvent(input$demo_sequence, {
    req(input$participant_id)
    # Start at NOW(3)
    ts0 <- as.POSIXct(Sys.time())
    ts1 <- ts0 + 45.345
    ts2 <- ts1 + 50.745
    
    pid <- as.integer(input$participant_id)
    run_no <- dbGetQuery(pool, "SELECT next_run FROM participant WHERE id = ?", params = list(pid))$next_run
    
    # started
    dbExecute(pool, "INSERT INTO race (participant_id, run, timestamp_ms, race_status, device_id, device_name, last_updated) VALUES (?, ?, ?, 'started', 'chip001', 'StartGate', NOW(3))",
              params = list(pid, run_no, format(ts0, "%Y-%m-%d %H:%M:%OS3")))
    dbExecute(pool, "UPDATE participant SET last_run = ?, last_updated = NOW(3) WHERE id = ?", 
              params = list(run_no, pid))
    
    # interim 1
    dbExecute(pool, "INSERT INTO race (participant_id, run, timestamp_ms, race_status, device_id, device_name, last_updated) VALUES (?, ?, ?, 'interim 1', 'chip002', 'InterimGate 1', NOW(3))",
              params = list(pid, run_no, format(ts1, "%Y-%m-%d %H:%M:%OS3")))
    
    # finished
    dbExecute(pool, "INSERT INTO race (participant_id, run, timestamp_ms, race_status, device_id, device_name, last_updated) VALUES (?, ?, ?, 'finished', 'chip003', 'FinishGate', NOW(3))",
              params = list(pid, run_no, format(ts2, "%Y-%m-%d %H:%M:%OS3")))
    dbExecute(pool, "UPDATE participant SET next_run = next_run + 1, last_updated = NOW(3) WHERE id = ?", 
              params = list(pid))
    
    # Force updates for both tables
    last_race_update(Sys.time())
    last_participant_update(Sys.time())
    
    showNotification("Demo events inserted (start → interim → finish)", type = "message")
  })
}

shinyApp(ui, server)