# Script to create Executable to startup the application
get_os <- function() {
  sysname <- Sys.info()[["sysname"]]
  switch(sysname,
         "Windows" = "Windows",
         "Darwin"  = "macOS",
         "Linux"   = "Linux",
         sysname)  # fallback if unknown
}

# Windows shortcut creation (unchanged)
create_windows_shortcut <- function(
    target_bat,
    shortcut_path,
    icon_path = NULL,
    working_dir = NULL,
    description = NULL
) {
  # Ensure all paths are normalized
  target_bat <- normalizePath(target_bat, winslash = "\\", mustWork = TRUE)
  shortcut_path <- normalizePath(shortcut_path, winslash = "\\", mustWork = FALSE)
  
  if (!grepl("\\.lnk$", shortcut_path, ignore.case = TRUE)) {
    shortcut_path <- paste0(shortcut_path, ".lnk")
  }
  
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

# Improved Linux shortcut creation
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

# Improved macOS command file creation
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
        sprintf('  <string>com.kinoklub.%s</string>', gsub("\\s+", "", app_name)),
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
  var_name <- "Kinoklub_wd"
  current_var <- Sys.getenv(var_name)
  if (current_var == "" || normalizePath(current_var) != r_wd) {
    cmd <- sprintf('setx %s "%s"', var_name, r_win_path(r_wd))
    shell(cmd)
    message("System variable `Kinoklub_wd` was created/updated.")
  }
  
  r_file <- file.path(r_wd, "app.R") |> r_win_path()
  
  # Read and modify batch template
  if (file.exists("source/OS_support/template")) {
    c_raw <- readLines("source/OS_support/template")
    pandoc_path <- tryCatch({
      normalizePath(rmarkdown::find_pandoc()[[2]])
    }, error = function(e) {
      ""
    })
    
    if (length(c_raw) >= 5) {
      if (pandoc_path != "") {
        c_raw[4] <- paste0('set "RSTUDIO_PANDOC=', pandoc_path, '"')
      }
      c_raw[5] <- paste0('"', r_exe, '" "', r_file, '"')
      
      # Write bat file
      bat_file <- file.path(r_wd, "source", "OS_support", "Zeitmessung_app.bat")
      writeLines(c_raw, bat_file)
      
      # Create shortcut
      create_windows_shortcut(
        target_bat = bat_file,
        shortcut_path = file.path(r_wd, "source", "OS_support", "Zeitmessung"),
        icon_path = file.path(r_wd, "source", "OS_support", "wagnius.ico"),
        working_dir = r_wd,
        description = "Kinoklub Zeitmessung App"
      )
      message("Shortcut created: ", file.path(r_wd, "source", "OS_support", "Zeitmessung.lnk"))
    }
  }
  
} else if (current_os == "Linux") {
  writeLines("Running on Linux")
  
  # Get working directory from environment or use current
  kinoklub_wd <- Sys.getenv("Kinoklub_wd")
  if (kinoklub_wd == "") {
    kinoklub_wd <- getwd()
    message("Kinoklub_wd not set in environment, using current directory: ", kinoklub_wd)
  }
  
  # Define paths
  icon_path <- file.path(kinoklub_wd, "source", "OS_support", "wagnius.png")
  
  # Check if icon exists, use generic if not
  if (!file.exists(icon_path)) {
    icon_path <- NULL
    message("Icon not found at: ", icon_path)
  }
  
  # Create main app launcher script
  main_script <- file.path(kinoklub_wd, "launch_app.sh")
  launch_script_content <- paste(
    "#!/bin/bash",
    "# Launch script for Kinoklub Zeitmessung",
    "",
    sprintf('cd "%s"', kinoklub_wd),
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
    name = "Kinoklub Zeitmessung",
    exec_path = main_script,
    shortcut_path = file.path(desktop_dir, "Zeitmessung"),
    icon_path = icon_path
  )
  
  # Optional: Create additional shortcuts for different scripts
  if (file.exists(file.path(kinoklub_wd, "Start_Input_data_edit.R"))) {
    edit_script <- file.path(kinoklub_wd, "launch_edit.sh")
    edit_content <- paste(
      "#!/bin/bash",
      sprintf('cd "%s"', kinoklub_wd),
      'Rscript "Start_Input_data_edit.R"',
      sep = "\n"
    )
    writeLines(edit_content, edit_script)
    Sys.chmod(edit_script, mode = "0755")
    
    create_linux_shortcut(
      name = "Kinoklub Edit",
      exec_path = edit_script,
      shortcut_path = file.path(desktop_dir, "Kinoklub_Edit"),
      icon_path = icon_path
    )
  }
  
  message("Linux shortcuts created successfully!")
  message("Main launcher: ", main_script)
  message("Desktop shortcut: ", file.path(desktop_dir, "Zeitmessung.desktop"))
  
} else if (current_os == "macOS") {
  writeLines("Running on macOS")
  
  # Get working directory
  kinoklub_wd <- getwd()
  
  # Define paths
  r_script_path <- file.path(kinoklub_wd, "app.R")
  icon_path <- file.path(kinoklub_wd, "source", "OS_support", "wagnius.png")
  
  # Check if R script exists
  if (!file.exists(r_script_path)) {
    stop("R script not found at: ", r_script_path)
  }
  
  # Create command file on Desktop
  desktop_dir <- path.expand("~/Desktop")
  if (!dir.exists(desktop_dir)) {
    desktop_dir <- path.expand("~")
  }
  
  # Create main app command
  create_mac_command(
    r_script_path = r_script_path,
    command_path = file.path(desktop_dir, "Kinoklub_Zeitmessung"),
    app_name = "Kinoklub Zeitmessung",
    icon_path = if (file.exists(icon_path)) icon_path else NULL
  )
  
  # Optional: Create edit command if edit script exists
  edit_script_path <- file.path(kinoklub_wd, "Start_Input_data_edit.R")
  if (file.exists(edit_script_path)) {
    create_mac_command(
      r_script_path = edit_script_path,
      command_path = file.path(desktop_dir, "Kinoklub_Edit"),
      app_name = "Kinoklub Edit",
      icon_path = if (file.exists(icon_path)) icon_path else NULL
    )
  }
  
  message("macOS applications created on Desktop.")
  message("Note: On macOS, you might need to right-click and select 'Open' the first time")
  message("due to Gatekeeper security settings.")
  
} else {
  message("Unsupported operating system: ", current_os)
}