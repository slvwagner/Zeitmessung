# Script to create Executable to startup the application

# Load fs package for better file operations
if (!require("fs", quietly = TRUE)) {
  install.packages("fs")
}
library(fs)

# Helper function for NULL coalescing
`%||%` <- function(a, b) if (!is.null(a)) a else b

# OS detection
get_os <- function() {
  sysname <- Sys.info()[["sysname"]]
  switch(sysname,
         "Windows" = "Windows",
         "Darwin"  = "macOS",
         "Linux"   = "Linux",
         sysname)  # fallback if unknown
}

# Function to check if app is already in Windows autostart
check_windows_autostart <- function(app_name = "Zeitmessung") {
  startup_folder <- fs::path(Sys.getenv("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
  shortcut_path <- fs::path(startup_folder, paste0(app_name, ".lnk"))
  return(fs::file_exists(shortcut_path))
}

# Function to check if app is already in Linux autostart
check_linux_autostart <- function(app_name = "Zeitmessung") {
  autostart_dir <- path.expand("~/.config/autostart")
  desktop_file <- fs::path(autostart_dir, paste0(app_name, ".desktop"))
  return(file.exists(desktop_file))
}

# Function to check if app is already in macOS autostart (Login Items)
check_macos_autostart <- function(app_name = "Zeitmessung") {
  if (Sys.info()["sysname"] != "Darwin") return(FALSE)
  
  # Use AppleScript to check login items
  applescript <- '
  tell application "System Events"
    try
      set loginItems to get the name of every login item
      return loginItems
    on error
      return ""
    end try
  end tell
  '
  
  temp_script <- tempfile(fileext = ".scpt")
  writeLines(applescript, temp_script)
  
  result <- tryCatch({
    system(paste("osascript", temp_script), intern = TRUE)
  }, error = function(e) "")
  
  unlink(temp_script)
  
  # Check if our app name appears in the login items
  return(grepl(app_name, paste(result, collapse = " "), ignore.case = TRUE))
}

# Function to ask about autostart on Windows
ask_windows_autostart <- function(is_already_autostart, app_name = "Zeitmessung") {
  if (!interactive()) {
    message("Running in non-interactive mode. Skipping autostart dialog.")
    return(FALSE)
  }
  
  if (is_already_autostart) {
    # App is already in autostart - ask if user wants to remove it
    message <- paste(app_name, "is already in autostart.",
                     "\n\nDo you want to remove it?",
                     "\n\nClick 'No' to keep it (default).",
                     "\nClick 'Yes' to remove it.")
    
    response <- utils::winDialog("yesno", message)
    
    if (is.null(response)) {
      # User closed the dialog - default to keeping it
      return(FALSE)
    }
    
    # Yes = remove, No = keep
    return(response == "YES")
    
  } else {
    # App is NOT in autostart - ask if user wants to add it
    message <- paste("Do you want to add", app_name, "to autostart?",
                     "\n\nThis will launch the app when you log in.",
                     "\n\nClick 'No' to skip (default).",
                     "\nClick 'Yes' to add to autostart.")
    
    response <- utils::winDialog("yesno", message)
    
    if (is.null(response)) {
      # User closed the dialog - default to not adding
      return(FALSE)
    }
    
    # Yes = add, No = don't add
    return(response == "YES")
  }
}

# Function to ask about autostart on Linux (using zenity for GUI dialog)
ask_linux_autostart <- function(is_already_autostart, app_name = "Zeitmessung") {
  if (!interactive()) {
    message("Running in non-interactive mode. Skipping autostart dialog.")
    return(FALSE)
  }
  
  # Check if zenity is available for GUI dialog
  zenity_available <- system("which zenity > /dev/null 2>&1", ignore.stderr = TRUE) == 0
  
  if (zenity_available) {
    if (is_already_autostart) {
      # Use zenity for GUI dialog
      cmd <- sprintf(
        'zenity --question --title="Autostart Management" --text="%s is already in autostart.\\n\\nDo you want to remove it?" --ok-label="Remove" --cancel-label="Keep"',
        app_name
      )
      
      response <- system(cmd, ignore.stderr = TRUE)
      # zenity returns 0 for OK (Remove), 1 for Cancel (Keep)
      return(response == 0)
      
    } else {
      # Use zenity for GUI dialog
      cmd <- sprintf(
        'zenity --question --title="Autostart Management" --text="Do you want to add %s to autostart?\\n\\nThis will launch the app when you log in." --ok-label="Add" --cancel-label="Skip"',
        app_name
      )
      
      response <- system(cmd, ignore.stderr = TRUE)
      # zenity returns 0 for OK (Add), 1 for Cancel (Skip)
      return(response == 0)
    }
  } else {
    # Fallback to console dialog
    message("\n" + paste(rep("=", 50), collapse = ""))
    if (is_already_autostart) {
      message(paste(app_name, "is already in autostart."))
      message("Remove it from autostart?")
      message("\nOptions:")
      message("  n) No, keep it in autostart (default)")
      message("  y) Yes, remove it")
      message("\nYour choice [n]: ")
    } else {
      message(paste("Add", app_name, "to autostart?"))
      message("This will launch the app when you log in.")
      message("\nOptions:")
      message("  n) No, skip (default)")
      message("  y) Yes, add to autostart")
      message("\nYour choice [n]: ")
    }
    
    response <- readline()
    return(tolower(trimws(response)) %in% c("y", "yes"))
  }
}

# Function to ask about autostart on macOS (using osascript for GUI dialog)
ask_macos_autostart <- function(is_already_autostart, app_name = "Zeitmessung") {
  if (!interactive()) {
    message("Running in non-interactive mode. Skipping autostart dialog.")
    return(FALSE)
  }
  
  if (is_already_autostart) {
    # Use AppleScript for GUI dialog
    applescript <- sprintf('
    display dialog "%s is already in Login Items.\\n\\nDo you want to remove it?" with title "Autostart Management" buttons {"Keep", "Remove"} default button "Keep"
    ', app_name)
    
    temp_script <- tempfile(fileext = ".scpt")
    writeLines(applescript, temp_script)
    
    result <- tryCatch({
      system(paste("osascript", temp_script), intern = TRUE)
    }, error = function(e) "")
    
    unlink(temp_script)
    
    # Check if user clicked "Remove"
    return(grepl("Remove", result))
    
  } else {
    # Use AppleScript for GUI dialog
    applescript <- sprintf('
    display dialog "Do you want to add %s to Login Items?\\n\\nThis will launch the app when you log in." with title "Autostart Management" buttons {"Skip", "Add"} default button "Skip"
    ', app_name)
    
    temp_script <- tempfile(fileext = ".scpt")
    writeLines(applescript, temp_script)
    
    result <- tryCatch({
      system(paste("osascript", temp_script), intern = TRUE)
    }, error = function(e) "")
    
    unlink(temp_script)
    
    # Check if user clicked "Add"
    return(grepl("Add", result))
  }
}

# Function to manage macOS login items
manage_macos_login_item <- function(action = "add", app_path, app_name = "Zeitmessung") {
  if (action == "add") {
    applescript <- sprintf('
    tell application "System Events"
      try
        make new login item at end of login items with properties {name:"%s", path:"%s", hidden:false}
        return "added"
      on error
        return "error"
      end try
    end tell
    ', app_name, app_path)
  } else if (action == "remove") {
    applescript <- sprintf('
    tell application "System Events"
      try
        delete login item "%s"
        return "removed"
      on error
        return "error"
      end try
    end tell
    ', app_name)
  }
  
  temp_script <- tempfile(fileext = ".scpt")
  writeLines(applescript, temp_script)
  
  result <- tryCatch({
    system(paste("osascript", temp_script), intern = TRUE)
  }, error = function(e) "error")
  
  unlink(temp_script)
  return(result)
}

# Function to get Windows Desktop path (handles OneDrive)
get_windows_desktop <- function() {
  # Try to get Desktop path from Windows shell
  desktop_path <- tryCatch({
    # Method 1: Use shell command to get Desktop
    cmd <- 'powershell -Command "[Environment]::GetFolderPath(\"Desktop\")"'
    path <- system(cmd, intern = TRUE, ignore.stderr = TRUE)
    if (length(path) > 0 && nzchar(path[1])) {
      path[1]
    } else {
      NULL
    }
  }, error = function(e) NULL)
  
  # Method 2: Try common Desktop locations
  if (is.null(desktop_path) || !fs::dir_exists(desktop_path)) {
    possible_paths <- c(
      # OneDrive Desktop
      fs::path(Sys.getenv("USERPROFILE"), "OneDrive", "Desktop"),
      # Regular Desktop
      fs::path(Sys.getenv("USERPROFILE"), "Desktop"),
      # Public Desktop
      fs::path(Sys.getenv("PUBLIC"), "Desktop"),
      # Documents as fallback
      fs::path(Sys.getenv("USERPROFILE"), "Documents")
    )
    
    for (path in possible_paths) {
      if (fs::dir_exists(path)) {
        desktop_path <- path
        break
      }
    }
  }
  
  # Method 3: Use Windows API via shell
  if (is.null(desktop_path) || !fs::dir_exists(desktop_path)) {
    desktop_path <- tryCatch({
      shell("echo %USERPROFILE%\\Desktop", intern = TRUE)
    }, error = function(e) NULL)
  }
  
  # Final fallback
  if (is.null(desktop_path) || !fs::dir_exists(desktop_path)) {
    desktop_path <- fs::path(Sys.getenv("USERPROFILE"))
  }
  
  return(fs::path_abs(desktop_path))
}

# Windows short cut creation
create_windows_shortcut <- function(
    target_bat,
    shortcut_path,
    icon_path = NULL,
    working_dir = NULL,
    description = NULL,
    window_style = "normal"
) {
  # Use fs for path normalization
  target_bat <- fs::path_abs(target_bat)
  
  # Ensure .lnk extension
  shortcut_path <- if (!fs::path_ext(shortcut_path) %in% c("lnk", "LNK")) {
    fs::path_ext_set(shortcut_path, "lnk")
  } else {
    fs::path(shortcut_path)
  }
  
  # Map window style to VBS values
  window_styles <- list(
    "normal" = 1,
    "minimized" = 7,
    "maximized" = 3,
    "hidden" = 0
  )
  window_style_value <- window_styles[[window_style]] %||% 1
  
  icon_line <- if (!is.null(icon_path) && fs::file_exists(icon_path)) {
    icon_path <- fs::path_abs(icon_path)
    paste0("shortcut.IconLocation = \"", icon_path, "\"")
  } else {
    ""
  }
  
  working_dir_line <- if (!is.null(working_dir)) {
    working_dir <- fs::path_abs(working_dir)
    paste0("shortcut.WorkingDirectory = \"", working_dir, "\"")
  } else {
    ""
  }
  
  description_line <- if (!is.null(description)) {
    paste0("shortcut.Description = \"", description, "\"")
  } else {
    ""
  }
  
  # Ensure shortcut directory exists
  fs::dir_create(fs::path_dir(shortcut_path))
  
  vbs_script <- paste(
    "Set WshShell = CreateObject(\"WScript.Shell\")",
    paste0("Set shortcut = WshShell.CreateShortcut(\"", shortcut_path, "\")"),
    paste0("shortcut.TargetPath = \"", target_bat, "\""),
    paste0("shortcut.WindowStyle = ", window_style_value),
    icon_line,
    working_dir_line,
    description_line,
    "shortcut.Save",
    sep = "\n"
  )
  
  # Write VBS script
  vbs_file <- tempfile(fileext = ".vbs")
  writeLines(vbs_script, vbs_file)
  
  # Run the VBS script
  system(paste("cscript //nologo", shQuote(vbs_file)))
  
  # Cleanup
  unlink(vbs_file)
  
  return(fs::file_exists(shortcut_path))
}

# Function to create a batch file that minimizes itself
create_minimized_batch <- function(r_exe, r_file, pandoc_path = NULL, output_file) {
  batch_content <- c(
    "@echo off",
    ":: Hide the command window",
    "if \"%1\" == \"hide\" goto hidden",
    "start \"\" /min \"%~f0\" hide",
    "exit /b",
    ":hidden",
    "",
    if (!is.null(pandoc_path) && pandoc_path != "") paste0('set "RSTUDIO_PANDOC=', pandoc_path, '"'),
    'setlocal',
    'cd /d "%~dp0..\\.."',
    paste0('"', r_exe, '" "', r_file, '"'),
    'pause'
  )
  
  # Remove empty lines
  batch_content <- batch_content[batch_content != ""]
  
  # Ensure output directory exists
  fs::dir_create(fs::path_dir(output_file))
  writeLines(batch_content, output_file)
  return(output_file)
}

# Linux shortcut creation
create_linux_shortcut <- function(name, exec_path, shortcut_path, icon_path = NULL) {
  # Ensure .desktop extension
  if (!grepl("\\.desktop$", shortcut_path)) {
    shortcut_path <- paste0(shortcut_path, ".desktop")
  }
  
  # Expand home directory
  exec_path <- path.expand(exec_path)
  if (!is.null(icon_path)) {
    icon_path <- path.expand(icon_path)
  }
  
  shortcut_content <- c(
    "[Desktop Entry]",
    "Version=1.0",
    "Type=Application",
    paste0("Name=", name),
    paste0("Exec=", shQuote(exec_path)),
    if (!is.null(icon_path)) paste0("Icon=", icon_path),
    "Terminal=false",
    "Categories=Utility;",
    "StartupNotify=true"
  )
  
  # Remove NULL lines
  shortcut_content <- shortcut_content[!is.na(shortcut_content) & shortcut_content != ""]
  
  writeLines(shortcut_content, shortcut_path)
  Sys.chmod(shortcut_path, mode = "0755")
}

# macOS command file creation
create_mac_command <- function(r_script_path, command_path, app_name = NULL, icon_path = NULL) {
  # Expand paths
  r_script_path <- path.expand(r_script_path)
  command_path <- path.expand(command_path)
  
  # Ensure .command extension
  if (!grepl("\\.command$", command_path)) {
    command_path <- paste0(command_path, ".command")
  }
  
  # Find Rscript path
  rscript_path <- Sys.which("Rscript")
  if (rscript_path == "") {
    rscript_path <- "/usr/local/bin/Rscript"
  }
  
  # Get working directory
  app_dir <- dirname(r_script_path)
  
  # Find pandoc from RStudio or system
  pandoc_dir <- tryCatch({
    rmarkdown::find_pandoc()$dir
  }, error = function(e) {
    # Try system pandoc
    system_path <- Sys.which("pandoc")
    if (system_path != "") {
      dirname(system_path)
    } else {
      ""
    }
  })
  
  # Build the .command file
  cmd <- paste(
    "#!/bin/bash",
    "# Set environment variables",
    if (pandoc_dir != "") sprintf('export RSTUDIO_PANDOC="%s"', pandoc_dir),
    "",
    "# Change to app directory",
    sprintf('cd "%s"', app_dir),
    "",
    "# Run the R script",
    sprintf('"%s" "%s"', rscript_path, basename(r_script_path)),
    "",
    "# Keep terminal open",
    'echo "Application finished. Closing in 5 seconds..."',
    'sleep 5',
    sep = "\n"
  )
  
  writeLines(cmd, command_path)
  Sys.chmod(command_path, mode = "0755")
  
  # If icon is provided, create .icns and set it
  if (!is.null(icon_path)) {
    icon_path <- path.expand(icon_path)
    
    # Create a simple .app bundle for better icon support
    if (!is.null(app_name)) {
      app_bundle <- file.path(dirname(command_path), paste0(app_name, ".app"))
      app_contents <- file.path(app_bundle, "Contents", "MacOS")
      
      dir.create(app_contents, recursive = TRUE, showWarnings = FALSE)
      
      # Create Info.plist
      info_plist <- file.path(app_bundle, "Contents", "Info.plist")
      plist_content <- c(
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
        '<plist version="1.0">',
        '<dict>',
        '  <key>CFBundleExecutable</key>',
        sprintf('  <string>%s</string>', basename(command_path)),
        '  <key>CFBundleIconFile</key>',
        '  <string>icon.icns</string>',
        '  <key>CFBundleIdentifier</key>',
        sprintf('  <string>com.zeitmessung.%s</string>', gsub("\\s+", "", app_name)),
        '  <key>CFBundleName</key>',
        sprintf('  <string>%s</string>', app_name),
        '  <key>CFBundleVersion</key>',
        '  <string>1.0</string>',
        '</dict>',
        '</plist>'
      )
      writeLines(plist_content, info_plist)
      
      # Copy command file to bundle
      file.copy(command_path, file.path(app_contents, basename(command_path)))
      
      # Copy icon if it exists
      if (file.exists(icon_path)) {
        icon_dest <- file.path(app_bundle, "Contents", "Resources", "icon.icns")
        dir.create(dirname(icon_dest), recursive = TRUE, showWarnings = FALSE)
        file.copy(icon_path, icon_dest, overwrite = TRUE)
      }
      
      message("Created macOS app bundle: ", app_bundle)
      command_path <- app_bundle
    }
  }
  
  message("Created command file: ", command_path)
  return(command_path)  # Return the path for autostart management
}

# Main execution ####
current_os <- get_os()

## Windows ####
if (current_os == "Windows") {
  writeLines("Running on Windows")
  
  # Check if app is already in autostart
  is_already_autostart <- check_windows_autostart()
  
  # Ask user about autostart
  change_autostart <- FALSE  # Default to not changing
  
  if (is_already_autostart) {
    message("App is already in autostart.")
    change_autostart <- ask_windows_autostart(TRUE, "Zeitmessung")
  } else {
    message("App is not in autostart.")
    change_autostart <- ask_windows_autostart(FALSE, "Zeitmessung")
  }
  
  # Use fs for all path operations
  r_wd <- fs::path_abs(getwd())
  r_exe <- fs::path_abs(Sys.which("Rscript"))
  
  # Set environment variable if needed
  var_name <- "Zeitmessung_wd"
  current_var <- Sys.getenv(var_name)
  if (current_var == "" || fs::path_abs(current_var) != r_wd) {
    # Convert to Windows path with backslashes for setx
    win_path <- gsub("/", "\\\\", r_wd)
    cmd <- sprintf('setx %s "%s"', var_name, win_path)
    shell(cmd, wait = TRUE)
    message("System variable `Zeitmessung_wd` was created/updated.")
  }
  
  r_file <- fs::path(r_wd, "app.R")
  
  # Get pandoc path
  pandoc_path <- tryCatch({
    fs::path_abs(rmarkdown::find_pandoc()[[2]])
  }, error = function(e) {
    ""
  })
  
  # Create bat file with hidden/minimized terminal
  bat_file <- fs::path(r_wd, "source", "OS_support", "Zeitmessung_app.bat")
  
  create_minimized_batch(
    r_exe = r_exe,
    r_file = r_file,
    pandoc_path = pandoc_path,
    output_file = bat_file
  )
  
  message("Created minimized batch file: ", bat_file)
  
  target_for_shortcut <- bat_file
  
  # Create shortcut in OS_support directory first
  shortcut_in_app <- fs::path(r_wd, "source", "OS_support", "Zeitmessung.lnk")
  
  success <- create_windows_shortcut(
    target_bat = target_for_shortcut,
    shortcut_path = shortcut_in_app,
    icon_path = fs::path(r_wd, "source", "OS_support", "wagnius.ico"),
    working_dir = r_wd,
    description = "Zeitmessung Application",
    window_style = "minimized"
  )
  
  if (success) {
    message("Shortcut created: ", shortcut_in_app)
  } else {
    warning("Failed to create shortcut: ", shortcut_in_app)
  }
  
  # Now copy to Desktop - using the new function
  if (success && fs::file_exists(shortcut_in_app)) {
    # Get Desktop path
    desktop_path <- get_windows_desktop()
    
    if (!is.null(desktop_path) && fs::dir_exists(desktop_path)) {
      c_target <- fs::path(desktop_path, "Zeitmessung.lnk")
      
      # Remove existing shortcut if it exists
      if (fs::file_exists(c_target)) {
        fs::file_delete(c_target)
      }
      
      # Copy the shortcut
      tryCatch({
        fs::file_copy(shortcut_in_app, c_target, overwrite = TRUE)
        message("Desktop shortcut created: ", c_target)
      }, error = function(e) {
        message("Note: Could not copy to desktop. Error: ", e$message)
        message("You can manually create a shortcut from: ", shortcut_in_app)
      })
    } else {
      message("Could not find Desktop folder.")
      message("Created application shortcut at: ", shortcut_in_app)
    }
    
    # Handle autostart based on user choice
    startup_folder <- fs::path(Sys.getenv("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    if (fs::dir_exists(startup_folder)) {
      startup_target <- fs::path(startup_folder, "Zeitmessung.lnk")
      
      if (change_autostart) {
        if (is_already_autostart) {
          # User said YES to removing from autostart
          if (fs::file_exists(startup_target)) {
            fs::file_delete(startup_target)
            message("✓ Removed from autostart: ", startup_target)
          }
        } else {
          # User said YES to adding to autostart
          tryCatch({
            fs::file_copy(shortcut_in_app, startup_target, overwrite = TRUE)
            message("✓ Added to autostart: ", startup_target)
          }, error = function(e) {
            message("Note: Could not create autostart shortcut. Error: ", e$message)
          })
        }
      } else {
        # User said NO to changing autostart
        if (is_already_autostart) {
          message("✓ Keeping app in autostart (as requested).")
        } else {
          message("✓ Not adding to autostart (as requested).")
        }
      }
    }
  }
  
  message("\nNote: The application will start with minimized terminal window.")
  message("To change this behavior, modify the 'window_style' parameter in the script.")
  
} else if (current_os == "Linux") { ## Linux ####
  writeLines("Running on Linux")
  
  # Check if app is already in autostart
  is_already_autostart <- check_linux_autostart()
  
  # Ask user about autostart
  change_autostart <- FALSE  # Default to not changing
  
  if (is_already_autostart) {
    message("App is already in autostart.")
    change_autostart <- ask_linux_autostart(TRUE, "Zeitmessung")
  } else {
    message("App is not in autostart.")
    change_autostart <- ask_linux_autostart(FALSE, "Zeitmessung")
  }
  
  # Get working directory
  working_dir <- normalizePath(getwd())
  
  # Define paths
  icon_path <- file.path(working_dir, "source", "OS_support", "wagnius.png")
  
  # Check if icon exists, use generic if not
  if (!file.exists(icon_path)) {
    icon_path <- NULL
    message("Icon not found at: ", file.path(working_dir, "source", "OS_support", "wagnius.png"))
  }
  
  # Create main app launcher script
  main_script <- file.path(working_dir, "launch_zeitmessung.sh")
  launch_script_content <- paste(
    "#!/bin/bash",
    "# Launch script for Zeitmessung",
    "",
    sprintf('cd "%s"', working_dir),
    'Rscript "app.R"',
    sep = "\n"
  )
  
  writeLines(launch_script_content, main_script)
  Sys.chmod(main_script, mode = "0755")
  
  # Create desktop shortcut
  desktop_dir <- path.expand("~/Desktop")
  if (!dir.exists(desktop_dir)) {
    desktop_dir <- path.expand("~")
    message("Desktop directory not found, using home directory instead")
  }
  
  create_linux_shortcut(
    name = "Zeitmessung",
    exec_path = main_script,
    shortcut_path = file.path(desktop_dir, "Zeitmessung"),
    icon_path = icon_path
  )
  
  # Handle autostart for Linux
  autostart_dir <- path.expand("~/.config/autostart")
  autostart_file <- file.path(autostart_dir, "Zeitmessung.desktop")
  
  if (change_autostart) {
    if (is_already_autostart) {
      # Remove from autostart
      if (file.exists(autostart_file)) {
        file.remove(autostart_file)
        message("✓ Removed from autostart: ", autostart_file)
      }
    } else {
      # Add to autostart
      if (!dir.exists(autostart_dir)) {
        dir.create(autostart_dir, recursive = TRUE)
      }
      
      # Create autostart desktop entry
      autostart_content <- c(
        "[Desktop Entry]",
        "Type=Application",
        "Name=Zeitmessung",
        paste0("Exec=", shQuote(main_script)),
        if (!is.null(icon_path)) paste0("Icon=", icon_path),
        "Terminal=false",
        "Categories=Utility;",
        "StartupNotify=true",
        "X-GNOME-Autostart-enabled=true"
      )
      
      writeLines(autostart_content[!is.na(autostart_content) & autostart_content != ""], autostart_file)
      message("✓ Added to autostart: ", autostart_file)
    }
  } else {
    if (is_already_autostart) {
      message("✓ Keeping app in autostart (as requested).")
    } else {
      message("✓ Not adding to autostart (as requested).")
    }
  }
  
  message("Linux shortcuts created successfully!")
  message("Main launcher: ", main_script)
  message("Desktop shortcut: ", file.path(desktop_dir, "Zeitmessung.desktop"))
  
  # Optional: Make the launcher script more robust
  if (file.exists(main_script)) {
    # Check if Rscript is in PATH
    if (system("which Rscript > /dev/null 2>&1") != 0) {
      warning("Rscript may not be in your PATH. Users might need to install R or add it to PATH.")
    }
  }
  
} else if (current_os == "macOS") { ## Mac ####
  writeLines("Running on macOS")
  
  # Check if app is already in autostart (Login Items)
  is_already_autostart <- check_macos_autostart()
  
  # Ask user about autostart
  change_autostart <- FALSE  # Default to not changing
  
  if (is_already_autostart) {
    message("App is already in Login Items.")
    change_autostart <- ask_macos_autostart(TRUE, "Zeitmessung")
  } else {
    message("App is not in Login Items.")
    change_autostart <- ask_macos_autostart(FALSE, "Zeitmessung")
  }
  
  # Get working directory
  working_dir <- normalizePath(getwd())
  
  # Define paths
  r_script_path <- file.path(working_dir, "app.R")
  icon_path <- file.path(working_dir, "source", "OS_support", "wagnius.png")
  
  # Check if R script exists
  if (!file.exists(r_script_path)) {
    stop("R script not found at: ", r_script_path)
  }
  
  # Check if icon exists
  if (!file.exists(icon_path)) {
    message("Icon not found at: ", icon_path)
    icon_path <- NULL
  }
  
  # Create command file on Desktop
  desktop_dir <- path.expand("~/Desktop")
  if (!dir.exists(desktop_dir)) {
    desktop_dir <- path.expand("~")
  }
  
  # Create main app command
  app_path <- create_mac_command(
    r_script_path = r_script_path,
    command_path = file.path(desktop_dir, "Zeitmessung"),
    app_name = "Zeitmessung",
    icon_path = icon_path
  )
  
  # Handle autostart on macOS (Login Items)
  if (change_autostart) {
    if (is_already_autostart) {
      # Remove from login items
      result <- manage_macos_login_item("remove", app_path)
      if (result == "removed") {
        message("✓ Removed from macOS Login Items.")
      } else {
        message("Could not remove from Login Items. You may need to remove it manually in System Settings.")
      }
    } else {
      # Add to login items
      result <- manage_macos_login_item("add", app_path)
      if (result == "added") {
        message("✓ Added to macOS Login Items.")
      } else {
        message("Could not add to Login Items. You may need to add it manually in System Settings.")
      }
    }
  } else {
    if (is_already_autostart) {
      message("✓ Keeping app in Login Items (as requested).")
    } else {
      message("✓ Not adding to Login Items (as requested).")
    }
  }
  
  message("\nmacOS application created on Desktop.")
  message("Note: On macOS, you might need to right-click and select 'Open' the first time")
  message("due to Gatekeeper security settings.")
  
  # Additional instructions for manual setup
  message("\nIf automatic Login Items setup failed:")
  message("1. Open System Settings")
  message("2. Go to General > Login Items")
  message("3. Click the '+' button and add the Zeitmessung app")
  
} else {
  message("Unsupported operating system: ", current_os)
}