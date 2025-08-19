# app.R — Shiny app for zeitmessung_V2 (event-log model) — UPDATED FOR Startnummer

# --- Packages ---
library(shiny)
library(DBI)
library(pool)
library(RMariaDB)
library(dplyr)
library(DT)

# --- DB Pool (configure via environment variables) ---
Sys.setenv(
  ZEIT_DB_HOST = "localhost",
  ZEIT_DB_NAME = "zeitmessung_V2",
  ZEIT_DB_USER = "race",
  ZEIT_DB_PASS = "49rb61",
  ZEIT_DB_PORT = "3306"
)

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

# close database connection ####
onStop(function() {
  poolClose(pool)
})

# --- Helpers --- ####
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

# --- UI --- ####
ui <- function()fluidPage(
  shiny::tags$head(
    shiny::tags$link(rel = "stylesheet", type = "text/css", 
                     href = paste0("custom_styles/dark.css?v=", as.integer(Sys.time())))
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
                      DTOutput("participants_tbl"),
                      br(),
                      actionButton("open_edit_modal", "Edit selected participant", class = "btn btn-warning")
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

# --- Server --- ####
server <- function(input, output, session) {
  ## Track update counters ####
  participant_update_counter <- reactiveVal(0)
  race_update_counter <- reactiveVal(0)
  
  ## Functions to check last updates ####
  check_participants_update <- function() {
    max_update <- dbGetQuery(pool, "SELECT MAX(last_updated) as max_update FROM participant")$max_update
    if (is.na(max_update)) return(Sys.time())
    as.POSIXct(max_update)
  }
  check_race_update <- function() {
    max_update <- dbGetQuery(pool, "SELECT MAX(last_updated) as max_update FROM race")$max_update
    if (is.na(max_update)) return(Sys.time())
    as.POSIXct(max_update)
  }
  
  ## Track last known database timestamps ####
  last_db_participant_update <- reactiveVal(Sys.time())
  last_db_race_update <- reactiveVal(Sys.time())
  
  ## Reactive: participants data with smart polling ####
  participants_data <- reactivePoll(3000, session,
                                    checkFunc = function() {
                                      current_db_max <- check_participants_update()
                                      current_counter <- participant_update_counter()
                                      db_changed <- current_db_max > last_db_participant_update()
                                      local_changes <- current_counter > 0
                                      if (db_changed || local_changes) {
                                        if (db_changed) last_db_participant_update(current_db_max)
                                        if (local_changes) participant_update_counter(0)
                                        TRUE
                                      } else FALSE
                                    },
                                    valueFunc = function() {
                                      dbReadTable(pool, "participant") |> arrange(Startnummer)
                                    }
  )
  
  ## --- Edit participant via modal -----------------------------------------
  # open modal when button clicked (requires a row selection)
  observeEvent(input$open_edit_modal, {
    sel <- input$participants_tbl_rows_selected
    req(sel)
    
    df <- participants_data()
    row <- df[sel, , drop = FALSE]
    req(nrow(row) == 1)
    
    # Pre-fill fields
    showModal(modalDialog(
      title = paste0("Edit participant — Startnummer ", row$Startnummer),
      size = "m",
      footer = tagList(
        modalButton("Cancel"),
        actionButton("save_edit_participant", "Save", class = "btn btn-primary")
      ),
      easyClose = FALSE,
      
      # Read-only key
      strong("Startnummer: "), span(row$Startnummer), tags$hr(),
      
      # Editable fields (extend as needed)
      textInput("edit_Name", "Name", value = row$Name %||% ""),
      textInput("edit_Vorname", "Vorname", value = row$Vorname %||% ""),
      textInput("edit_Nickname", "Nickname", value = row$Nickname %||% ""),
      textInput("edit_Phone", "Phone", value = row$Phone %||% ""),
      textInput("edit_Email", "E-mail", value = row$`E.mail` %||% row$`E-mail` %||% ""),
      textInput("edit_Kategorie", "Kategorie", value = row$Kategorie %||% ""),
      numericInput("edit_Gewicht", "Gewicht (kg)", value = ifelse(is.na(row$Gewicht), NA, row$Gewicht), min = 0, step = 0.1),
      numericInput("edit_race_order", "Race order", value = ifelse(is.na(row$race_order), NA, row$race_order), min = 1)
    ))
  })
  
  # Safe null/NA helper
  `%||%` <- function(a, b) if (!is.null(a) && !is.na(a) && nzchar(as.character(a))) a else b
  
  # Save changes
  observeEvent(input$save_edit_participant, {
    # We need the selected row again to know which Startnummer to update
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
    gw  <- if (!length(input$edit_Gewicht) || is.na(input$edit_Gewicht)) NA else as.numeric(input$edit_Gewicht)
    ro  <- if (!length(input$edit_race_order) || is.na(input$edit_race_order)) NA else as.integer(input$edit_race_order)
    
    sql <- "
    UPDATE participant
       SET race_order   = ?,
           Name         = ?,
           Vorname      = ?,
           Nickname     = ?,
           Phone        = ?,
           `E-mail`     = ?,
           Kategorie    = ?,
           Gewicht      = ?,
           last_updated = NOW(3)
     WHERE Startnummer  = ?;
  "
    
    dbExecute(pool, sql, params = list(ro, nm, vn, nn, ph, em, ka, gw, sn))
    
    # Force reactive reloads
    participant_update_counter(participant_update_counter() + 1)
    
    removeModal()
    showNotification("Participant updated", type = "message")
  })
  
  
  
  ## Reactive: events data with smart polling ####
  events_data <- reactivePoll(3000, session,
                              checkFunc = function() {
                                current_db_max <- check_race_update()
                                current_counter <- race_update_counter()
                                db_changed <- current_db_max > last_db_race_update()
                                local_changes <- current_counter > 0
                                if (db_changed || local_changes) {
                                  if (db_changed) last_db_race_update(current_db_max)
                                  if (local_changes) race_update_counter(0)
                                  TRUE
                                } else FALSE
                              },
                              valueFunc = function() {
                                dbReadTable(pool, "race") |> arrange(desc(id))
                              }
  )
  
  ## Reactive: summary data (depends on both tables) ####
  summary_data <- reactivePoll(3000, session,
                               checkFunc = function() {
                                 current_part_db_max <- check_participants_update()
                                 current_race_db_max <- check_race_update()
                                 current_part_counter <- participant_update_counter()
                                 current_race_counter <- race_update_counter()
                                 part_db_changed <- current_part_db_max > last_db_participant_update()
                                 race_db_changed <- current_race_db_max > last_db_race_update()
                                 part_local_changes <- current_part_counter > 0
                                 race_local_changes <- current_race_counter > 0
                                 if (part_db_changed || race_db_changed || part_local_changes || race_local_changes) {
                                   if (part_db_changed) last_db_participant_update(current_part_db_max)
                                   if (race_db_changed) last_db_race_update(current_race_db_max)
                                   if (part_local_changes) participant_update_counter(0)
                                   if (race_local_changes) race_update_counter(0)
                                   TRUE
                                 } else FALSE
                               },
                               valueFunc = function() {
                                 ensure_summary_view(pool)
                                 dbReadTable(pool, "race_summary") |> arrange(Startnummer, run)
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
  
  ## Tables ####
  output$participants_tbl <- renderDT({
    datatable(
      participants_data(),
      options = list(pageLength = 10, order = list(list(0, 'asc'))),
      selection = "single"
    )
  })
  
  output$events_tbl <- renderDT({
    datatable(events_data(), options = list(pageLength = 10))
  })
  output$summary_tbl <- renderDT({
    df <- summary_data()
    if (!is.null(input$participant_filter) && nzchar(input$participant_filter)) {
      df <- df |> filter(Startnummer == as.integer(input$participant_filter))
    }
    datatable(df, options = list(pageLength = 10))
  })
  
  ## Insert participant ####
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
    
    participant_update_counter(participant_update_counter() + 1)
    showNotification("Participant added", type = "message")
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
    
    race_update_counter(race_update_counter() + 1)
    
    if (identical(input$race_status, "started")) {
      dbExecute(pool, "UPDATE participant SET last_run = ?, last_updated = NOW(3) WHERE Startnummer = ?", 
                params = list(as.integer(input$run_number), as.integer(input$participant_id)))
      participant_update_counter(participant_update_counter() + 1)
    }
    if (input$race_status %in% c("finished", "disqualify")) {
      dbExecute(pool, "UPDATE participant SET next_run = next_run + 1, last_updated = NOW(3) WHERE Startnummer = ?", 
                params = list(as.integer(input$participant_id)))
      participant_update_counter(participant_update_counter() + 1)
    }
    
    showNotification(sprintf("Event '%s' inserted", input$race_status), type = "message")
  })
  
  ## Demo sequence: start → interim → finish — uses Startnummer ####
  observeEvent(input$demo_sequence, {
    req(input$participant_id)
    ts0 <- as.POSIXct(Sys.time())
    ts1 <- ts0 + 45.345
    ts2 <- ts1 + 50.745
    
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
    
    race_update_counter(race_update_counter() + 1)
    participant_update_counter(participant_update_counter() + 1)
    showNotification("Demo events inserted (start → interim → finish)", type = "message")
  })
}

shinyApp(ui, server)
