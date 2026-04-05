# update_version.R
# Script to update all project version tags in relevant files

# List of files and the line patterns to update
files_to_update <- list(
      list(file = "source/pico2 W/micropython/project/native_modules/zeitmessung.cmake", pattern = '^set\\(ZEITMESSUNG_BANNER ".*"\\)$', replacement = function(ver) sprintf('set(ZEITMESSUNG_BANNER "Firmware for Zeitmessung v%s on Raspberry Pi Pico 2 W (built ${ZEITMESSUNG_BUILD_TIMESTAMP})")', ver)),
    # R app and version updater
    list(file = "app.R", pattern = '^PROJECT_VERSION <- ".*"$', replacement = function(ver) sprintf('PROJECT_VERSION <- "%s"', ver)),
    list(file = "update_version.R", pattern = '^PROJECT_VERSION <- ".*"$', replacement = function(ver) sprintf('PROJECT_VERSION <- "%s"', ver)),
  # Python and CMake
  list(file = "source/pico2 W/micropython/project/start_gate.py", pattern = '^PROJECT_VERSION = ".*"$', replacement = function(ver) sprintf('PROJECT_VERSION = "%s"', ver)),
  list(file = "source/pico2 W/micropython/project/DMX_controller.py", pattern = '^PROJECT_VERSION = ".*"$', replacement = function(ver) sprintf('PROJECT_VERSION = "%s"', ver)),
  list(file = "source/pico2 W/micropython/project/finish_gate.py", pattern = '^PROJECT_VERSION = ".*"$', replacement = function(ver) sprintf('PROJECT_VERSION = "%s"', ver)),
  list(file = "source/pico2 W/micropython/project/native_modules/zeitmessung.cmake", pattern = '^set\\(ZEITMESSUNG_PROJECT_VERSION ".*"\\)$', replacement = function(ver) sprintf('set(ZEITMESSUNG_PROJECT_VERSION "%s")', ver)),
  # SQL
  list(file = "source/SQL/registration.sql", pattern = '^-- \\[Zeitmessung\\] Project Version: .+$', replacement = function(ver) sprintf('-- [Zeitmessung] Project Version: %s', ver)),
  list(file = "source/SQL/SQL_Database_creation.sql", pattern = '^-- \\[Zeitmessung\\] Project Version: .+$', replacement = function(ver) sprintf('-- [Zeitmessung] Project Version: %s', ver)),
  # xampp PHP
  list(file = "source/Server_admin/xampp/ontrack_sse.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/edit_run.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/ontrack_once.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/update_racemanagement.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/log.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/edit.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/status.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/insert_race.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/read.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/race_control.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/get_participant.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/participant_lookup_by_RFID.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/race_classement.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/device_params.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
  list(file = "source/Server_admin/xampp/race_classement_projector.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver)),
# Prompt for new version
  list(file = "source/Server_admin/xampp/open_runs.php", pattern = "^\\$PROJECT_VERSION = '[^']*';$", replacement = function(ver) sprintf("$PROJECT_VERSION = '%s';", ver))
)
# Prompt for new version
# Prompt for new version
new_version <- "0.1.2"
cat(sprintf("[DEBUG] User entered version: %s\n", new_version))

for (item in files_to_update) {
  cat(sprintf("Processing: %s\n", item[["file"]]))
  flush.console()
  cat(sprintf("[DEBUG] Processing: %s\n", item[["file"]]))
  file_path <- item[["file"]]
  pattern <- item[["pattern"]]
  replacement_fun <- item[["replacement"]]
  if (!file.exists(file_path)) {
    cat(sprintf("File not found: %s\n", file_path))
    next
  }
  lines <- readLines(file_path, warn = FALSE)
  cat(sprintf("[DEBUG] Read %d lines from %s\n", length(lines), file_path))
  changed <- FALSE
  for (i in seq_along(lines)) {
    if (grepl(pattern, lines[[i]])) {
      lines[[i]] <- replacement_fun(new_version)
      changed <- TRUE
    }
  }
  # Ensure file ends with a newline to avoid 'incomplete final line' warnings
  if (!grepl("", lines[length(lines)])) {
    lines[length(lines)] <- paste0(lines[length(lines)], "\n")
  }
  if (changed) {
    writeLines(lines, file_path)
    cat(sprintf("Updated: %s\n", file_path))
  }
}

cat("\nDone. All version tags updated to:", new_version, "\n")
