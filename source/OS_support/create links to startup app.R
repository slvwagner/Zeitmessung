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
}

# Main execution ####
current_os <- get_os()

## Windows ####
if (current_os == "Windows") {
  writeLines("Running on Windows")
  
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
    
    # Also try to place it in Startup folder for auto-start
    startup_folder <- fs::path(Sys.getenv("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    if (fs::dir_exists(startup_folder)) {
      startup_target <- fs::path(startup_folder, "Zeitmessung.lnk")
      tryCatch({
        fs::file_copy(shortcut_in_app, startup_target, overwrite = TRUE)
        message("Startup shortcut created: ", startup_target)
      }, error = function(e) {
        message("Note: Could not create startup shortcut. Error: ", e$message)
      })
    }
  }
  
  message("\nNote: The application will start with minimized terminal window.")
  message("To change this behavior, modify the 'window_style' parameter in the script.")
  
} else if (current_os == "Linux") { ## Linux ####
  writeLines("Running on Linux")
  
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
  create_mac_command(
    r_script_path = r_script_path,
    command_path = file.path(desktop_dir, "Zeitmessung"),
    app_name = "Zeitmessung",
    icon_path = icon_path
  )
  
  message("macOS application created on Desktop.")
  message("Note: On macOS, you might need to right-click and select 'Open' the first time")
  message("due to Gatekeeper security settings.")
  
  # Additional macOS tip
  message("\nOptional: To create a proper .app bundle, consider using Platypus:")
  message("https://github.com/sveinbjornt/Platypus")
  
} else {
  message("Unsupported operating system: ", current_os)
}