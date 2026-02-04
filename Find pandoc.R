# Check if rmarkdown is installed, install if not
if (!requireNamespace("rmarkdown", quietly = TRUE)) {
  install.packages("rmarkdown")
}

# Get Pandoc path
library(rmarkdown)

# Method A: Find Pandoc executable
pandoc_path <- find_pandoc()$dir
print(paste("Pandoc directory:", pandoc_path))

# Method B: Get full path to pandoc executable
pandoc_exe <- rmarkdown::find_pandoc()$path
print(paste("Pandoc executable:", pandoc_exe))

# Method C: Get Pandoc version and path
pandoc_info <- rmarkdown::pandoc_version()
print(pandoc_info)