library(RMySQL)
library(DBI)
library(tidyverse)

source("source/functions.R")

# Database functions to work with mysql

# connection to Database ####
DB_connect <- function(DB_host, DB_name, DB_user, DB_PW, con = NULL, max_attempts = 3) {
  attempt <- 1
  
  while(attempt <= max_attempts) {
    # Check if connection exists and is valid
    if (!is.null(con) && dbIsValid(con)) {
      return(con)
    }
    
    tryCatch({
      con <- dbConnect(
        MySQL(),
        host = DB_host,
        user = DB_user,
        password = DB_PW,
        dbname = DB_name,
        port = 3306
      )
      return(con)
    }, error = function(e) {
      message(sprintf("Connection attempt %d failed: %s", attempt, e$message))
      if(attempt == max_attempts) {
        stop("Failed to connect after ", max_attempts, " attempts")
      }
      Sys.sleep(2^attempt) # Exponential backoff
      attempt <<- attempt + 1
    })
  }
}

# Update all tables in DB with ID as Primary Key
DB_update_all <- function(l_data, con) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_get_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  
  if (!is.list(l_data) || is.null(names(l_data))) {
    stop("l_data must be a named list where names correspond to table names.")
  }
  
  lapply(names(l_data), function(table_name) {
    DB_copy_table(l_data[[table_name]], con, table_name)
  })
}

# get all data defined by a template ####
DB_get_Data <- function(l_template, con, download = TRUE) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_get_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  if(download){
    temp <- names(l_template)|>
      lapply(function(x){
        tbl(con, x)|>
          collect()
      })
  } else {
    temp <- names(l_template)|>
      lapply(function(x){
        tbl(con, x)
      })
  }
  names(temp) <- names(l_template)
  return(temp)
}

# get data from given table ####
DB_get_table <- function(table_name, con, download = TRUE){
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  if(download){
    tbl(con, table_name)|>
      collect()
  } else {
    tbl(con, table_name)
  }
}

# Copy a data frame to SQL DB (slow because it is done for each row => DB batch restrictions) ####
DB_copy_table <- function(df_data, con, table_name, delete_existing = TRUE) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  
  library(DBI)
  library(hms)
  
  if (!dbIsValid(con)) {
    stop("Invalid database connection.")
  }
  
  # Get first column name (intended as primary key)
  primary_key_col <- names(df_data)[1]
  
  # Ensure primary key column has unique values
  if (anyDuplicated(df_data[[primary_key_col]]) > 0) {
    stop(sprintf("Primary key column '%s' contains duplicate values.", primary_key_col))
  }
  
  # Determine SQL types for each column
  sql_types <- sapply(df_data, function(x) {
    if (is.integer(x)) {
      return("INT")
    } else if (is.numeric(x)) {
      return("DOUBLE")
    } else if (is.logical(x)) {
      return("BOOLEAN")
    } else if (inherits(x, "Date")) {
      return("DATE")
    } else if (inherits(x, "hms")) {
      return("TIME")
    } else if (inherits(x, "POSIXct") || inherits(x, "POSIXlt")) {
      return("DATETIME")
    } else if (is.character(x) || is.factor(x)) {
      return("TEXT")
    } else {
      stop(sprintf("Unsupported data type for column: %s", class(x)))
    }
  })
  
  table_exists <- dbExistsTable(con, table_name)
  
  if (table_exists && delete_existing) {
    message(sprintf("Table '%s' exists. Dropping it...", table_name))
    dbExecute(con, sprintf("DROP TABLE `%s`;", table_name))
    table_exists <- FALSE
  }
  
  if (!table_exists) {
    # Construct CREATE TABLE query with primary key on first column
    column_defs <- paste0("`", names(sql_types), "` ", sql_types)
    column_defs[1] <- paste(column_defs[1], "PRIMARY KEY")
    
    create_query <- sprintf(
      "CREATE TABLE `%s` (%s);",
      table_name,
      paste(column_defs, collapse = ", ")
    )
    
    dbExecute(con, create_query)
    message(sprintf("Table '%s' created successfully.", table_name))
  }
  
  # Prepare column names for insert
  col_names <- paste0("`", colnames(df_data), "`", collapse = ", ")
  
  # Initialize counter for successful inserts
  success_count <- 0
  
  # Process values row by row
  for (i in 1:nrow(df_data)) {
    if (all(is.na(df_data[i, ]))) next
    
    values <- sapply(df_data[i, ], function(x) {
      if (is.na(x)) {
        return("NULL")
      } else if (is.logical(x)) {
        return(as.character(as.integer(x)))
      } else if (is.numeric(x)) {
        return(as.character(x))
      } else if (inherits(x, "Date")) {
        return(sprintf("'%s'", as.character(x)))
      } else if (inherits(x, "hms")) {
        return(sprintf("'%s'", as.character(x)))
      } else if (inherits(x, "POSIXct") || inherits(x, "POSIXlt")) {
        return(sprintf("'%s'", format(x, "%Y-%m-%d %H:%M:%S")))
      } else if (is.character(x) || is.factor(x)) {
        return(sprintf("'%s'", gsub("'", "''", as.character(x))))
      } else {
        stop(sprintf("Unsupported data type for value: %s", class(x)))
      }
    })
    
    query <- sprintf(
      "INSERT INTO `%s` (%s) VALUES (%s);",
      table_name,
      col_names,
      paste(values, collapse = ", ")
    )
    
    query <- gsub("'NULL'", "NULL", query)
    
    tryCatch({
      dbExecute(con, query)
      success_count <- success_count + 1
    }, error = function(e) {
      warning(sprintf("Failed to insert row %d: %s", i, e$message))
    })
  }
  
  message(sprintf("Successfully inserted %d of %d rows into '%s'.",
                  success_count, nrow(df_data), table_name))
  
  # Return the number of successful inserts
  invisible(success_count)
}

# Function to add a row to any table ####
DB_add_row <- function(con, table_name, new_row) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_add_row(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  if (!dbExistsTable(con, table_name)) {
    stop("Table '", table_name, "' does not exist in the database.")
  }
  
  # Ensure new_row is a named list or data frame
  if (!is.list(new_row) || is.null(names(new_row))) {
    stop("new_row must be a named list or data frame.")
  }
  
  # Get the table's column names and types
  # library(rebus)
  # p <- SPC
  # as.character(p)
  p <- "\\s"
  
  # handle column names correctly
  table_info <- DB_describe_table(con, table_name)
  
  col_names <- table_info$Field
  col_types <- table_info$Type
  
  # # Debug: Print column names and new_row names
  # message("Column names in table: ", paste(col_names, collapse = ", "))
  # message("Column names in new_row: ", paste(names(new_row), collapse = ", "))
  
  # Validate the new row data
  if (!all(names(new_row) %in% col_names)) {
    stop("New row contains invalid column names.")
  }
  
  # Ensure all column names are non-empty
  if (any(names(new_row) == "")) {
    stop("One or more column names in new_row are empty.")
  }
  
  # Ensure the new row has all required columns (non-NULL columns without defaults)
  required_cols <- table_info |>
    filter(Null == "NO" & is.na(Default)) |>
    pull(Field)
  missing_cols <- setdiff(required_cols, names(new_row))
  if (length(missing_cols) > 0) {
    stop("Missing required columns: ", paste(missing_cols, collapse = ", "))
  }
  
  # Replace TRUE and FALSE with 1 or 0 for SQL
  for (ii in 1:length(new_row)) {
    if(names(new_row)[ii] == "Zahlend"){
      if(!is.na(new_row[[ii]])) {
        if(new_row[[ii]] == "TRUE") new_row[[ii]] <- 1L 
        else new_row[[ii]] <- 0L
      }
    }
  }
  
  # Preplace single quotes "'" with "´" 
  new_row <- new_row|>
    lapply(function(x){
      str_replace_all(x, "'", "´")
    })
  
  # Debug: Print new_row values
  # message("Values in new_row: ", paste(new_row, collapse = ", "))
  
  library(hms)
  
  is_time <- function(x) {
    # Apply tryCatch on each element to handle errors
    check_each <- sapply(x, function(val) {
      tryCatch({
        !is.na(as_hms(val))  # Returns TRUE if valid, FALSE if NA
      }, error = function(e) FALSE)  # Catch errors and return FALSE
    })
    
    check_each  # Ensure all elements are valid times
  }
  
  # Prepare the SQL query
  sql_cols <- paste(paste0("`", names(new_row), "`"), collapse = ", ")
  sql_vals <- 
    paste(
      sapply(new_row, function(x) {
        if (is.na(x)) "NULL" # Replace NA values with NULL for SQL
        else paste0("'", x, "'")
        }), 
      collapse = ", ")
  sql_vals
  
  sql_query <- paste0(
    "INSERT INTO ", "`",table_name, "`"," (", sql_cols, ") VALUES (", sql_vals, ")"
    )
  
  # Execute the query
  dbExecute(con, sql_query)
  
  message("Row ",new_row[[1]][1] ," added successfully to table '","`", table_name,"`", "'.")
}

# Helper function to get column types from table ####
DB_describe_table <- function(con, table_name){
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  dbGetQuery(con, paste0("DESCRIBE ","`", table_name ,"`"))
}

# number of rows for a database table ####
DB_nrow <- function(con, my_table){
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  
  data <- dbGetQuery(con, paste0("SELECT COUNT(*) AS n FROM ","`",my_table,"`"))
  return(data$n)
}

# Function to edit a row in table ####
DB_edit_row_in_table <- function(con, table_name, primary_key_col, primary_key_value, updated_values, c_class) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  
  if (!DBI::dbExistsTable(con, table_name)) {
    stop("Table '", table_name, "' does not exist in the database.")
  }
  
  # Get the table's column names and types
  table_info <- DB_describe_table(con, table_name)
  col_names <- table_info$Field
  
  # Validate the primary key column
  if (!primary_key_col %in% col_names) {
    stop("Primary key column '", primary_key_col, "' does not exist in the table.")
  }
  
  # Validate the updated values
  if (!all(names(updated_values) %in% col_names)) {
    stop("Updated values contain invalid column names.")
  }
  
  # Replace NA values with NULL explicitly for factors 
  updated_values <- as.list(updated_values)
  for (ii in 1:length(c_class)) {
    if(c_class[ii] == "factor"){
      if(!is.na(updated_values[[ii]])){
        if(updated_values[[ii]] == "NA"){
          updated_values[[ii]] <- NA
        }
      }
    }
  }
  
  # Replace NA values with NULL explicitly
  updated_values <- lapply(updated_values, function(x) {
    if (is.atomic(x) && length(x) == 1 && is.na(x)) {
      NULL
    } else {
      x
    }
  })

  # Prepare the SET clause for the SQL query
  set_clause <- paste(
    vapply(names(updated_values), function(col) {
      value <- updated_values[[col]]
      
      if (is.null(value)) {
        paste0("`", col, "` = NULL")
      } else if (is.character(value)) {
        paste0("`", col, "` = '", gsub("'", "''", value), "'")
      } else if (inherits(value, "POSIXt") || inherits(value, "Date")) {
        paste0("`", col, "` = '", format(value, "%Y-%m-%d %H:%M:%S"), "'")
      } else if (inherits(value, "difftime")) {
        paste0("`", col, "` = '", format(as.POSIXct(value, origin = "1970-01-01"), "%H:%M:%S"), "'")
      } else if(is.logical(value)){
        paste0("`", col, "` = ","'", ifelse(value == TRUE, 1L, 0L), "'")
      } else {
        paste0("`", col, "` = ","'", value, "'")
      }
    }, character(1)),
    collapse = ", "
  )
  
  # Prepare the WHERE clause
  where_clause <- paste0(
    "`", primary_key_col, "` = ",
    if (is.character(primary_key_value)) paste0("'", gsub("'", "''", primary_key_value), "'") else primary_key_value
  )
  
  # Construct and execute the SQL query
  sql_query <- paste0(
    "UPDATE `", table_name, "` SET ", set_clause, " WHERE ", where_clause
  )
  
  DBI::dbExecute(con, sql_query)
  
  message("Row with ", primary_key_col, " = ", primary_key_value,
          " updated successfully in table '", table_name, "'.")
}

# Function to delete a row from any table ####
DB_delete_row <- function(con, table_name, primary_key_col, primary_key_value) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_add_row(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  
  if(length(primary_key_value)> 1) 
    stop("DB_delete_row() function can only handle single primary key values. If you need to delete more than one use lapply.")

  if (!dbExistsTable(con, table_name)) {
    stop("Table '", table_name, "' does not exist in the database.")
  }
  
  # Get the table's column names
  table_info <- DB_describe_table(con, table_name)
  col_names <- table_info$Field
  
  # Validate the primary key column
  if (!primary_key_col %in% col_names) {
    stop(paste("Primary key column '", primary_key_col, "' does not exist in the table."))
  }

  # Prepare the WHERE clause for the SQL query
  where_clause <- paste0("`", primary_key_col, "` = ", if (is.character(primary_key_value)) paste0("'", primary_key_value, "'") else primary_key_value)
  
  # Construct the SQL query
  sql_query <- paste0(
    "DELETE FROM `", table_name, "` WHERE ", where_clause
  )
  
  # Print the SQL query for debugging
  # message("Executing SQL query: ", sql_query)
  
  # Execute the query
  test <- dbExecute(con, sql_query)
  
  if(test){
    warning(paste0("Row with ", primary_key_col, " = ", paste0(primary_key_value, collapse = ",\n"), " deleted successfully from table '", table_name, "'."))
  }
  else stop("Row with ", primary_key_col, " = ", primary_key_value, " have not been deleted from table '", table_name, "'.")
}

# Function to update a single cell in a table ####
DB_update_cell <- function(con, table_name, primary_key_col, primary_key_value, target_col, new_value) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  if (!dbExistsTable(con, table_name)) {
    stop("Table '", table_name, "' does not exist in the database.")
  }
  
  # Get the table's column names
  table_info <- DB_describe_table(con, table_name)
  col_names <- table_info$Field
  
  # Validate column names
  if (!primary_key_col %in% col_names) {
    stop(paste("Primary key column '", primary_key_col, "' does not exist in the table."))
  }
  if (!target_col %in% col_names) {
    stop(paste("Target column '", target_col, "' does not exist in the table."))
  }
  
  # Format the new value for SQL
  formatted_value <- if (is.null(new_value) || is.na(new_value)) {
    "NULL"
  } else if (is.character(new_value)) {
    paste0("'", gsub("'", "''", new_value), "'")  # Escape single quotes in strings
  } else if (inherits(new_value, "POSIXt") || inherits(new_value, "Date")) {
    paste0("'", format(new_value, "%Y-%m-%d"), "'")
  } else {
    new_value
  }
  
  # Prepare the WHERE clause for the SQL query
  where_clause <- paste0("`", primary_key_col, "` = ", 
                         if (is.character(primary_key_value)) paste0("'", primary_key_value, "'") else primary_key_value)
  
  # Construct the SQL query
  sql_query <- paste0(
    "UPDATE `", table_name, "` SET `", target_col, "` = ", formatted_value, 
    " WHERE ", where_clause
  )
  
  # Execute the query
  dbExecute(con, sql_query)
  
  message("Cell in table '", table_name, "' updated successfully: ", target_col, " = ", new_value, 
          " (Row where ", primary_key_col, " = ", primary_key_value, ").")
}

# Back up all tables from database ####
DB_backup_DB <- function(con) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  
  # List all tables in the connected database
  tables <- dbListTables(con)
  
  # Backup all data 
  l_data <- tables|>
    lapply(DB_get_table, con)
  
  names(l_data) <- tables
  message("Downloaded the following tables from the Database:\n", paste(tables, collapse = "\n"))

  return(l_data)
}

# Convert data frame from SQL DB to R data types ####
convert_to_template_types <- function(df_sql, df_template) {
  # Align columns (keep only those present in both data frames)
  common_cols <- intersect(colnames(df_sql), colnames(df_template))
  df_sql <- df_sql |> select(all_of(common_cols))
  df_template <- df_template |> select(all_of(common_cols))
  
  # Convert data types
  for (col in common_cols) {
    col_type <- class(df_template[[col]])
    
    if (any(col_type == "Date")) {
      df_sql[[col]] <- as.Date(df_sql[[col]])
    } else if (any(col_type == "hms")) {
      df_sql[[col]] <- hms::as_hms(df_sql[[col]])
    } else if (any(col_type %in% c("POSIXct", "POSIXlt"))) {
      df_sql[[col]] <- as.POSIXct(df_sql[[col]])
    } else if (any(col_type == "double")) {
      df_sql[[col]] <- as.numeric(df_sql[[col]])
    } else if (any(col_type == "integer")) {
      df_sql[[col]] <- as.integer(df_sql[[col]])
    } else if (any(col_type == "numeric")) {
      df_sql[[col]] <- as.numeric(df_sql[[col]])
    } else if (any(col_type == "character")) {
      df_sql[[col]] <- as.character(df_sql[[col]])
    } else if (any(col_type == "factor")) {
      df_sql[[col]] <- as.factor(df_sql[[col]])
    } else if(any(col_type == "logical")){
      df_sql[[col]] <- as.logical(df_sql[[col]])
    } else {
      warning(sprintf("Unsupported data type for column '%s': %s", col, paste(col_type, collapse = ", ")))
    }
  }
  
  return(df_sql)
}

# convert data from DB to R with correct conversion template ####
convert_DB_to_R <- function(data,template) {
  # Convert data types for each table
  data_converted <- names(data) |>
    map(~ {
      table_name <- .x
      df_sql <- data[[table_name]]
      df_template <- template[[table_name]]
      
      # Convert data types
      convert_to_template_types(df_sql, df_template)
    })
  # Assign names to the converted list
  names(data_converted) <- names(data)
  return(data_converted)
}

# Add one or more rows to a database table ####
DB_add_rows <- function(new_rows, table_name, con, batch_size = 1) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  
  if (!DBI::dbExistsTable(con, table_name)) {
    stop("Table '", table_name, "' does not exist in the database.")
  }
  
  # Convert single row to data frame if needed
  if (!is.data.frame(new_rows) && is.list(new_rows) && !is.null(names(new_rows))) {
    new_rows <- as.data.frame(new_rows, stringsAsFactors = FALSE)
  }
  
  if (!is.data.frame(new_rows)) {
    stop("new_rows must be a data frame or named list.")
  }
  
  # Get table information
  table_info <- DB_describe_table(con, table_name)
  col_names <- table_info$Field
  
  # Check if all columns exist in the table
  invalid_cols <- setdiff(names(new_rows), col_names)
  if (length(invalid_cols) > 0) {
    stop("The following columns don't exist in the table: ", 
         paste(invalid_cols, collapse = ", "))
  }
  
  # Check required columns (non-NULL columns without defaults)
  required_cols <- table_info |> 
    filter(Null == "NO" & is.na(Default)) |> 
    pull(Field)
  
  missing_cols <- setdiff(required_cols, names(new_rows))
  if (length(missing_cols) > 0) {
    stop("Missing required columns: ", paste(missing_cols, collapse = ", "))
  }
  
  # Replace single quotes with backticks to prevent SQL injection
  new_rows <- new_rows |>
    mutate(
      across(
        where(is.character), ~ stringr::str_replace_all(.x, "'", "´"))
    )
  
  # Split into batches for more efficient insertion
  total_rows <- nrow(new_rows)
  batches <- split(new_rows, (seq_len(total_rows) - 1) %/% batch_size)
  
  success_count <- 0
  
  for (batch in batches) {
    # Prepare the SQL query for batch insert
    sql_cols <- paste0("`", names(batch), "`", collapse = ", ")
    
    # Prepare values for each row
    value_rows <- apply(batch, 1, function(row) {
      values <- sapply(row, function(val) {
        if (is.na(val) || is.null(val)) {
          "NULL"
        } else if ((val == "TRUE") | (val == "FALSE")) {
          paste0("'", ifelse(val == "TRUE", 1, 0), "'")
        } else {
          paste0("'", as.character(val), "'")
        }
      })
      paste0("(", paste(values, collapse = ", "), ")")
    })
    
    # Combine all value rows
    values_sql <- paste(value_rows, collapse = ", ")
    
    # Build and execute the query
    sql_query <- sprintf(
      "INSERT INTO `%s` (%s) VALUES %s",
      table_name, sql_cols, values_sql
    )
    
    tryCatch({
      DBI::dbExecute(con, sql_query)
      success_count <- success_count + nrow(batch)
    }, error = function(e) {
      warning("Failed to insert batch: ", e$message)
    })
  }
  
  message(sprintf("Successfully inserted %d of %d rows into '%s'",
                  success_count, total_rows, table_name))
  
  invisible(success_count)
}

# Function to create a table for storing files if it doesn't exist ####
DB_create_files_table <- function(con, table_name) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  
  if (!dbExistsTable(con, table_name)) {
    create_query <- paste0(
      "CREATE TABLE `", table_name, "` (
        `ID` INT AUTO_INCREMENT PRIMARY KEY,
        `filename` VARCHAR(255) NOT NULL,
        `Event ID` INT, 
        `file content` LONGTEXT NOT NULL,
        `upload time` DATETIME DEFAULT CURRENT_TIMESTAMP,
        `file size` INT,
        `file type` VARCHAR(100)
      )"  # Removed the semicolon and properly closed the parenthesis
    )
    
    dbExecute(con, create_query)
    message("Table '", table_name, "' created successfully.")
  } else {
    message("Table '", table_name, "' already exists.")
  }
}

# Function to upload a text file to the database with overwrite option ####
DB_upload_file <- function(con, file_path, filename , table_name, overwrite = FALSE) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  # Validate inputs
  if (!file.exists(file_path)) {
    stop("File does not exist: ", file_path)
  }
  
  # Check if table exists, create if not
  if (!dbExistsTable(con, table_name)) {
    DB_create_files_table(con, table_name)
  }
  
  # Get filename and check if it exists
  existing_files <- tbl(con, table_name) |> 
    filter(filename == !!filename) |>
    collect()
  
  if (nrow(existing_files) > 0) {
    
    # Handle existing files based on overwrite parameter
    if (overwrite) {
      message("File '", filename, "' exists. Overwriting...")
      
      # Delete existing file record
      DB_delete_row(con, table_name, names(existing_files)[1], existing_files[1])

      # uses the existing ID
      c_ID <- existing_files$ID
      
    } else {
      warning("File '", filename, "' already exists in table '", 
              table_name, "'. Set overwrite = TRUE to replace it.")
      c_ID <- DB_get_max_pk(con, table_name)
    }
  } else {
    # Get next ID
    max_id <- tbl(con, table_name) |>
      summarise(max_id = max(ID, na.rm = TRUE)) |>
      collect()
    
    c_ID <- if (is.na(max_id$max_id)) 1L else max_id$max_id + 1L
  }
  # create `Event ID`
  p <- "([\\d]+)\\.txt"
  `Event ID` <- str_match(filename, p)[,2]|>as.integer()
  
  # Read file content
  file_content <- paste(readLines(file_path, warn = FALSE), collapse = "\n")|>
    suppressWarnings()
  
  # Prepare file metadata
  file_info <- file.info(file_path)
  file_size <- file_info$size
  file_type <- tools::file_ext(filename)
  
  # library(rebus)
  # p <- DOT%R%one_or_more(DGT)%R%END
  p <- "\\.[\\d]+$"
  
  # Prepare the data frame for upload
  file_data <- tibble(
    ID = c_ID,
    filename = filename,
    `Event ID` = `Event ID`,
    `file content` = file_content,
    `upload time` = Sys.time()|>as.character()|>str_remove(p) ,
    `file size` = file_size,
    `file type` = file_type
  )
  
  # Use your existing function to add the file to the database
  test <- Run_capture_error_warnings(
    DB_add_rows,file_data, table_name, con
    )
  message(test$message)
  return(file_content)
}

# Function to retrieve all files from the database ####
DB_get_file <- function(con, filename, table_name ) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  # Query the database for the file
  query <- paste0(
    "SELECT * FROM `", table_name, "` ",
    "WHERE `filename` = '", gsub("'", "''", filename), "'"
  )
  
  result <- dbGetQuery(con, query)
  
  if (nrow(result) == 0) {
    stop("File not found in database: ", filename)
  }
  
  # Return as a list with filename and content
  as_tibble(result)
}

# Function to download a file from the database to disk ####
DB_download_file <- function(con, filename, output_path, table_name ) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  file_data <- DB_get_file(con, filename, table_name)
  
  # find end of line "\n"
  x <- file_data$`file content`
  last_two <- substr(x, nchar(x) - 1, nchar(x))
  
  # Write the content to file
  if(str_detect(last_two, "\n")){ # end of line found, no need to append
    writeLines(file_data$`file content`, paste0(output_path,filename)) 
  } else { # end of line not found so append 
    writeLines(paste0(file_data$`file content`,"\n"), paste0(output_path,filename))
  }
  
  message("File '", filename, "' downloaded to '", paste0(output_path,filename), "'.")
  return(NULL)
}

# Check if a table exists on a database ####
DB_table_exists <- function(con, table_name, schema = NULL) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  if (is.null(schema)) {
    tables <- DBI::dbListTables(con)
    tolower(table_name) %in% tolower(tables)
  } else {
    # For databases that support schemas
    query <- DBI::sqlInterpolate(
      con,
      "SELECT COUNT(*) FROM information_schema.tables 
       WHERE table_schema = ?schema AND table_name = ?table",
      schema = schema,
      table = table_name
    )
    DBI::dbGetQuery(con, query)[1, 1] > 0
  }
}

# Get the maximum primary key value from a table ####
DB_get_max_pk <- function(con, table_name, primary_key_col = NULL) {
  # Add connection validation at start
  if(!dbIsValid(con)) {
    warning("Connection lost in DB_edit_row_in_table(), attempting to reconnect...")
    con <- DB_connect(DB_host, DB_name, DB_user, DB_pw)
  }
  # Validate inputs
  if (!DBI::dbIsValid(con)) {
    stop("Invalid database connection.")
  }
  
  if (!DBI::dbExistsTable(con, table_name)) {
    stop("Table '", table_name, "' does not exist in the database.")
  }
  
  # If primary key column not provided, try to determine it
  if (is.null(primary_key_col)) {
    table_info <- DB_describe_table(con, table_name)
    pk_cols <- table_info[table_info$Key == "PRI", "Field"]
    
    if (length(pk_cols) == 0) {
      stop("No primary key column found in table '", table_name, "'.")
    }
    
    if (length(pk_cols) > 1) {
      warning("Multiple primary keys found in table '", table_name, 
              "'. Using the first one: ", pk_cols[1])
    }
    
    primary_key_col <- pk_cols[1]
  }
  
  # Construct and execute the query
  query <- sprintf("SELECT MAX(`%s`) AS max_pk FROM `%s`", 
                   primary_key_col, table_name)
  
  result <- DBI::dbGetQuery(con, query)
  
  # Handle case where table is empty
  if (is.na(result$max_pk)) {
    warning("Table '", table_name, "' appears to be empty. Returning NA.")
    return(NA)
  }
  
  return(result$max_pk)
}
