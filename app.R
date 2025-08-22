# app.R — Shiny app for zeitmessung_V2 (event-log model) — UPDATED FOR Startnummer

# --- Packages ---
library(shiny)
library(DBI)
library(pool)
library(RMariaDB)
library(dplyr)
library(DT)

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
               column(8,
                      h3("Participants"),
                      DTOutput("participants_tbl"),
                      br(),
                      actionButton("add_participant", "Add participant", class = "btn-success"),
                      actionButton("open_edit_modal", "Edit selected participant", class = "btn-primary")
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
                                      # Add error handling here
                                      tryCatch({
                                        data <- dbReadTable(pool, "participant")
                                        if (!is.null(data) && nrow(data) > 0) {
                                          data |> arrange(Startnummer)
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
  
  ## --- Add participant via modal -----------------------------------------
  # open modal when button clicked (requires a row selection)
  observeEvent(input$add_participant, {
    # Calculate next Startnummer with better error handling
    df <- participants_data()
    
    # Debug: check what's in the data
    print(str(df))
    print(head(df))
    
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
        numericInput("edit_Gewicht", "Gewicht (kg)", value = 0, min = 0, step = 1),
        numericInput("edit_race_order", "Race order", value = -1)
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
    # Get form values
    nm  <- trimws(input$edit_Name %||% "")
    vn  <- trimws(input$edit_Vorname %||% "")
    nn  <- trimws(input$edit_Nickname %||% "")
    ph  <- trimws(input$edit_Phone %||% "")
    em  <- trimws(input$edit_Email %||% "")
    ka  <- trimws(input$edit_Kategorie %||% "")
    gw  <- if (!length(input$edit_Gewicht) || is.na(input$edit_Gewicht)) NA else as.numeric(input$edit_Gewicht)
    ro  <- if (!length(input$edit_race_order) || is.na(input$edit_race_order)) NA else as.integer(input$edit_race_order)
    
    # Correct SQL statement with proper column names
    sql <- "
  INSERT INTO participant 
    (race_order, Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, Gewicht, next_run, last_updated)
  VALUES 
    (?, ?, ?, ?, ?, ?, ?, ?, 1, NOW(3))
  "
    
    tryCatch({
      dbExecute(pool, sql, params = list(ro, nm, vn, nn, ph, em, ka, gw))
      
      # Force reactive reloads
      participant_update_counter(participant_update_counter() + 1)
      
      removeModal()
      showNotification("Participant added successfully", type = "message")
    }, error = function(e) {
      showNotification(paste("Error adding participant:", e$message), type = "error")
    })
  })
  
  ## --- Edit participant via modal -----------------------------------------
  # Safe null/NA helper
  `%||%` <- function(a, b) if (!is.null(a) && !is.na(a) && nzchar(as.character(a))) a else b
  
  # Open edit modal
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
        textInput("edit_Kategorie", "Kategorie", value = row$Kategorie),
        numericInput("edit_Gewicht", "Gewicht (kg)", value = ifelse(is.na(row$Gewicht), 0, row$Gewicht), min = 0, step = 1),
        numericInput("edit_race_order", "Race order", value = ifelse(is.na(row$race_order), -1, row$race_order))
      ),
      footer = tagList(
        modalButton("Cancel"),
        actionButton("save_edit_participant", "Save", class = "btn-primary")
      ),
      easyClose = FALSE,
    ))
  })
  
  # Save changes for editing participant
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
      options = 
        list(
          pageLength = 10, order = list(list(0, 'asc')),
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
            "  Shiny.setInputValue('participants_tbl', new Date().getTime());",
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
      selection = "single"
    )
  })
  
  output$events_tbl <- renderDT({
    datatable(
      events_data(), 
      options = 
        list(
          pageLength = 10,
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
  
  output$summary_tbl <- renderDT({
    df <- summary_data()
    if (!is.null(input$participant_filter) && nzchar(input$participant_filter)) {
      df <- df |> filter(Startnummer == as.integer(input$participant_filter))
    }
    datatable(
      df, 
      options = 
        list(
          pageLength = 10,
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