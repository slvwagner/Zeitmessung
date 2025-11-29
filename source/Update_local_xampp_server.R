# Script to update htdocs in local xampp server


# The path to local xampp server must be defined in envirnonment variable
c_xampp_path <- Sys.getenv("xampp_server")

# get php 
c_files <- list.files(path = "source/XAMPP",pattern = "php")
c_files

c_filePath <- list.files(pattern = "php", recursive = TRUE)
c_filePath

for (ii in 1:length(c_files)) {
  c_raw <- readLines(c_filePath[ii])
  writeLines(c_raw, paste0(c_xampp_path,"/",c_files[ii]))
}

message("The following files:\n", paste0(".../",c_filePath, collapse = "\n"),"\nhave been written to ", c_xampp_path)

# get html
c_files <- list.files(path = "source/XAMPP",pattern = "html")
c_files

c_filePath <- list.files(pattern = "html", recursive = TRUE)
c_filePath

ii <- 2
for (ii in 1:length(c_files)) {
  c_raw <- readLines(c_filePath[ii])
  c_raw
  writeLines(c_raw, paste0(c_xampp_path,"/",c_files[ii]))
}

message("The following files:\n", paste0(".../",c_filePath, collapse = "\n"),"\nhave been written to ", c_xampp_path)
