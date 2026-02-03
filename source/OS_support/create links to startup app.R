# Script to create Executable to startup the application
get_os <- function() {
  sysname <- Sys.info()[["sysname"]]
  switch(sysname,
         "Windows" = "Windows",
         "Darwin"  = "macOS",
         "Linux"   = "Linux",
         sysname)  # fallback if unknown
}

# Windows shortcut creation - updated with hidden terminal option
create_windows_shortcut <- function(
    target_bat,
    shortcut_path,
    icon_path = NULL,
    working_dir = NULL,
    description = NULL,
    window_style = "normal"  # "normal", "minimized", "maximized", "hidden"
) {
  # Ensure all paths are normalized
  target_bat <- normalizePath(target_bat, winslash = "\\", mustWork = TRUE)
  shortcut_path <- normalizePath(shortcut_path, winslash = "\\", mustWork = FALSE)
  
  if (!grepl("\\.lnk$", shortcut_path, ignore.case = TRUE)) {
    shortcut_path <- paste0(shortcut_path, ".lnk")
  }
  
  # Map window style to VBS values
  window_styles <- list(
    "normal" = 1,
    "minimized" = 7,
    "maximized" = 3,
    "hidden" = 0
  )
  window_style_value <- window_styles[[window_style]] %||% 1
  
  icon_line <- if (!is.null(icon_path)) {
    icon_path <- normalizePath(icon_path, winslash = "\\", mustWork = TRUE)
    paste0("shortcut.IconLocation = \"", icon_path, "\"")
  } else {
    ""
  }
  
  working_dir_line <- if (!is.null(working_dir)) {
    working_dir <- normalizePath(working_dir, winslash = "\\", mustWork = TRUE)
    paste0("shortcut.WorkingDirectory = \"", working_dir, "\"")
  } else {
    ""
  }
  
  description_line <- if (!is.null(description)) {
    paste0("shortcut.Description = \"", description, "\"")
  } else {
    ""
  }
  
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
}

# Function to create a VBS wrapper that hides the terminal
create_vbs_wrapper <- function(bat_file, vbs_file = NULL) {
  bat_file <- normalizePath(bat_file, winslash = "\\", mustWork = TRUE)
  
  if (is.null(vbs_file)) {
    vbs_file <- sub("\\.bat$", "_hidden.vbs", bat_file)
  }
  
  vbs_content <- paste(
    "Set WshShell = CreateObject(\"WScript.Shell\")",
    paste0("WshShell.Run chr(34) & \"", bat_file, "\" & Chr(34), 0"),
    "Set WshShell = Nothing",
    sep = "\n"
  )
  
  writeLines(vbs_content, vbs_file)
  return(vbs_file)
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
  
  writeLines(batch_content, output_file)
  return(output_file)
}

# Linux shortcut creation (unchanged)
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

# macOS command file creation (unchanged)
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

# Helper function for NULL coalescing
`%||%` <- function(a, b) if (!is.null(a)) a else b

# Main execution
current_os <- get_os()

if (current_os == "Windows") {
  writeLines("Running on Windows")
  
  r_win_path <- function(x) {
    chartr("/", "\\", x)
  }
  
  r_exe <- normalizePath(Sys.which("Rscript"))
  r_wd <- normalizePath(getwd())
  
  # Set environment variable if needed
  var_name <- "Zeitmessung_wd"
  current_var <- Sys.getenv(var_name)
  if (current_var == "" || normalizePath(current_var) != r_wd) {
    cmd <- sprintf('setx %s "%s"', var_name, r_win_path(r_wd))
    shell(cmd)
    message("System variable `Zeitmessung_wd` was created/updated.")
  }
  
  r_file <- file.path(r_wd, "app.R") |> r_win_path()
  
  # Get pandoc path
  pandoc_path <- tryCatch({
    normalizePath(rmarkdown::find_pandoc()[[2]])
  }, error = function(e) {
    ""
  })
  
  # Create bat file with hidden/minimized terminal
  bat_file <- file.path(r_wd, "source", "OS_support", "Zeitmessung_app.bat")
  
  # OPTION 1: Create a batch file that minimizes itself (recommended)
  create_minimized_batch(
    r_exe = r_exe,
    r_file = r_file,
    pandoc_path = pandoc_path,
    output_file = bat_file
  )
  
  message("Created minimized batch file: ", bat_file |> r_win_path())
  
  # OPTION 2: Create VBS wrapper that hides terminal completely
  # Uncomment below if you prefer completely hidden terminal
  # vbs_wrapper <- create_vbs_wrapper(bat_file)
  # target_for_shortcut <- vbs_wrapper  # Use VBS instead of BAT
  
  # For now, use the batch file
  target_for_shortcut <- bat_file
  
  # Create shortcut with minimized window
  create_windows_shortcut(
    target_bat = target_for_shortcut,
    shortcut_path = file.path(r_wd, "source", "OS_support", "Zeitmessung") |> r_win_path(),
    icon_path = file.path(r_wd, "source", "OS_support", "wagnius.ico") |> r_win_path(),
    working_dir = r_wd,
    description = "Zeitmessung Application",
    window_style = "minimized"  # Options: "normal", "minimized", "maximized", "hidden"
  )
  
  message("Shortcut created: ", file.path(r_wd, "source", "OS_support", "Zeitmessung.lnk") |> r_win_path())
  message("\nNote: The application will start with minimized terminal window.")
  message("To change this behavior, modify the 'window_style' parameter in the script.")
  
} else if (current_os == "Linux") {
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
  
} else if (current_os == "macOS") {
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