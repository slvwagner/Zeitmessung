# Script to update htdocs in local xampp server

c_xampp_path <- "D:/xampp/htdocs"

c_files <- list.files(path = "source/PHP_Xampp",pattern = "php")
c_files

c_filePath <- list.files(pattern = "php", recursive = TRUE)
c_filePath

for (ii in 1:length(c_files)) {
  c_raw <- readLines(c_filePath[ii])
  writeLines(c_raw, paste0(c_xampp_path,"/",c_files[ii]))
}

message("The following files:\n", paste0(".../",c_filePath, collapse = "\n"),"\nhave been written to ", c_xampp_path)
