# Script to create Executable to startup the application
get_os <- function() {
  sysname <- Sys.info()[["sysname"]]
  switch(sysname,
         "Windows" = "Windows",
         "Darwin"  = "macOS",
         "Linux"   = "Linux",
         sysname)  # fallback if unknown
}


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

create_linux_shortcut <- function(name, exec_path, shortcut_path, icon_path = NULL) {
  shortcut_content <- c(
    "[Desktop Entry]",
    "Type=Application",
    paste0("Name=", name),
    paste0("Exec=", exec_path),
    paste0("Icon=", icon_path),
    "Terminal=false"
  )
  
  writeLines(shortcut_content, shortcut_path)
  Sys.chmod(shortcut_path, mode = "0755")  # mode to make executable
}

create_mac_command <- function(r_script_path, command_path, icon_path = NULL) {
  # find RStudio pandoc
  rstudio_pandoc <- rmarkdown::find_pandoc()$dir
  
  # --- Build the .command file ---
  app_dir <- dirname(normalizePath(r_script_path))
  r_file <- basename(normalizePath(r_script_path))
  
  cmd <- paste(
    "#!/bin/bash",
    sprintf('export RSTUDIO_PANDOC="%s"', rstudio_pandoc),
    sprintf('cd "%s"', app_dir),
    sprintf('Rscript "%s"', r_file),
    'read -n 1 -s -r -p "Press any key to close..."',
    sep = "\n"
  )
  
  writeLines(cmd, command_path)
  Sys.chmod(command_path, mode = "0755")
  
  # If icon is provided, set it using AppleScript
  if (!is.null(icon_path)) {
    system(sprintf(
      'osascript -e \'tell application "Finder" to set icon of file POSIX file "%s" to icon of file POSIX file "%s"\'',
      normalizePath(command_path),
      normalizePath(icon_path)
    ))
  }
  
  message("Created command file: ", command_path)
  if (!is.null(icon_path)) {
    message("→ Custom icon applied: ", icon_path)
  }
}

if(get_os() == "Windows"){
  writeLines("Running on Windows")
  r_path <- function(x) {
    x <- chartr("\\", "/", x)
    return(x)
  }
  
  r_win_path <- function(x){
    x <- chartr("/","\\", x)
    return(x)
  }
  
  r_exe <- Sys.which("Rscript")|>
    normalizePath()
  r_exe
  
  r_wd <- getwd()|>
    normalizePath()
  
  if((nchar(r_wd) == 0) | (r_wd != r_win_path(getwd()))) {
    # Define variable name and value
    
    var_value <- getwd()|>
      r_win_path()
    
    # Build the command
    cmd <- sprintf('setx %s "%s"', var_name, var_value)
    
    # Execute (use shell() on Windows for better behavior)
    shell(cmd)
    
    message("Systemvarible `Kinoklub_wd` wurde erstellt.")
  }
  
  r_file <- paste0(r_wd, "/app.R")|>
    r_win_path()
  r_file
  
  c_raw <- readLines("source/OS_support/template")
  c_raw
  
  c_raw[4] <- paste0("set \"RSTUDIO_PANDOC=", rmarkdown::find_pandoc()[[2]]|>normalizePath(),"\"")
  c_raw[5] <- paste0("\"",r_exe,"\""," ","\"", r_file, "\"")
  c_raw
  
  # Write bat file
  writeLines(c_raw, "source/OS_support/Zeitmessung_app.bat")
  
  # create shortcut
  create_windows_shortcut(
    target_bat = paste0(getwd(),"/source/OS_support/Zeitmessung_app.bat"),
    shortcut_path = paste0(getwd(),"/source/OS_support/Zeitmessung"),
    icon_path = paste0(getwd(),"/source/OS_support/wagnius.ico"),
    working_dir = getwd(),
    description = "Kinoklub Input Tabellen"
  )
  message("Die Datei: ",getwd(),"/source/OS_support/Kinoklub input.lnk wurde erstellt.")
  
} else if (get_os() == "Linux"){
  writeLines("running on Linux")
  
  
  # --- Get working directory from .Renviron ---
  kinoklub_wd <- Sys.getenv("Kinoklub_wd")
  if(kinoklub_wd == "") stop("Kinoklub_wd not set in .Renviron")
  
  # --- Define shell script paths ---
  start_gui_sh <- file.path(kinoklub_wd, "startGui.sh")
  start_edit_sh <- file.path(kinoklub_wd, "startEdit.sh")
  
  # --- Generate startGui.sh ---
  gui_script <- file.path(kinoklub_wd, "Start_GUI.R")
  gui_sh <- paste(
    "#!/bin/bash",
    sprintf('cd "%s"', kinoklub_wd),
    sprintf('Rscript "%s"', gui_script),
    sep = "\n"
  )
  writeLines(gui_sh, start_gui_sh)
  Sys.chmod(start_gui_sh, mode = "0755")  # Make executable
  
  # --- Generate startEdit.sh ---
  edit_script <- file.path(kinoklub_wd, "Start_Input_data_edit.R")
  edit_sh <- paste(
    "#!/bin/bash",
    sprintf('cd "%s"', kinoklub_wd),
    sprintf('Rscript "%s"', edit_script),
    sep = "\n"
  )
  writeLines(edit_sh, start_edit_sh)
  Sys.chmod(start_edit_sh, mode = "0755")  # Make executable
  
  # --- Create .desktop shortcuts on Desktop ---
  desktop_dir <- "~/Desktop"
  create_linux_shortcut <- function(name, exec_path, shortcut_path, icon_path = NULL) {
    shortcut_content <- c(
      "[Desktop Entry]",
      "Type=Application",
      paste0("Name=", name),
      paste0("Exec=", exec_path),
      paste0("Icon=", ifelse(is.null(icon_path), "", icon_path)),
      "Terminal=true",
      "Categories=Utility;"
    )
    writeLines(shortcut_content, shortcut_path)
    Sys.chmod(shortcut_path, mode = "0755")  # Make executable
  }
  
  create_linux_shortcut(
    name = "Kinoklub GUI",
    exec_path = start_gui_sh,
    shortcut_path = file.path(desktop_dir, "Zeitmessung.desktop"),
    icon_path = file.path(kinoklub_wd, "source/OS_support/wagnius.png")
  )
  
  create_linux_shortcut(
    name = "Kinoklub Edit",
    exec_path = start_edit_sh,
    shortcut_path = file.path(desktop_dir, "Kinoklub Edit.desktop"),
    icon_path = file.path(kinoklub_wd, "source/OS_support/wagnius.png")
  )
  
  message("Linux shortcuts and shell scripts created successfully!")
  
  writeLines("startup icons created on Linux")
  
} else if (get_os() == "macOS"){
  writeLines("running on macOS")
  
  create_mac_command(
    r_script_path = "~/Zeitmessung/source/app.R",
    command_path = "~/Desktop/app.command",
    icon_path = "~/Kinoklub/source/OS_support/wagnius.png"
    
  )

  message("Die Applikationen wurden auf dem Desktop erstellt.")
}
  





