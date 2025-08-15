# Script to update htdocs in local xampp server

c_xampp_path <- "D:/xampp/htdocs"

c_files <- list.files(pattern = "php")

lapply(c_files, function(x){
  c_raw <- readLines(x)|>
    suppressWarnings()
  writeLines(c_raw, paste0(c_xampp_path,"/",x))
})

message("The following files: ", paste(c_files, collapse = ", ")," have been written to ", c_xampp_path)
