library(httr)
library(jsonlite)

# Rennstop und Start ####
php_url_racemanagement <- paste0("http://", Sys.getenv("ZEIT_DB_HOST"), "/zeitmessung/xampp/update_racemanagement.php")
api_key <- Sys.getenv("API_KEY")  # if required

data <- list(name = "Rennstatus", value = 1)

response <- POST(
  url = php_url_racemanagement,
  body = toJSON(data, auto_unbox = TRUE),
  content_type("application/json")
)

# Check response
status_code(response)
content(response, "text")

status_code(response) != 200
content(response, "parsed")

# Function to update a value in race_management ####
update_race_value <- function(name, value, url, api_key = NULL) {
  
  # Prepare the request body
  body <- list(
    name = name,
    value = value
  )
  
  # Add API key if needed
  if (!is.null(api_key)) {
    body$api_key <- api_key
  }
  
  # Make the POST request
  response <- POST(
    url = url,
    body = body,
    encode = "json"
  )
  
  # Check for HTTP errors
  if (status_code(response) != 200) {
    stop("Request failed with status: ", status_code(response))
  }
  
  # Parse the response
  result <- content(response, "parsed")
  
  # Check for API-level errors
  if (result$status == "error") {
    stop("API error: ", result$data$message)
  }
  
  # Return the successful result
  return(result$data)
}
## race run execute ####
observeEvent(input$race_restart_exe,{
  # Update "Rennstatus"
  result <- update_race_value(
    name = "Rennstatus", 
    value = "1",  # 1 to restart the race
    url = php_url_racemanagement
  )
  removeModal()
})

# Helpers ####
now_ms <- function() {
  format(Sys.time(), "%Y-%m-%d %H:%M:%OS3")
}

# Participant lookup by RFID ####
library(tidyverse)
library(httr)

# Function to lookup participant by RFID
lookup_participant <- function(rfid) {
  php_url <- paste0("http://", Sys.getenv("ZEIT_DB_HOST"), 
                    "/zeitmessung/xampp/participant_lookup_by_RFID.php")
  
  # URL encode the RFID (colons need to be encoded)
  encoded_rfid <- URLencode(rfid, reserved = TRUE)
  
  # Make the GET request
  response <- GET(
    url = paste0(php_url, "?rfid=", encoded_rfid),
    timeout(10)
  )
  
  # Check response
  cat("Status code:", status_code(response), "\n")
  cat("Content type:", headers(response)$`content-type`, "\n\n")
  
  # Parse response
  if (status_code(response) == 200) {
    result <- content(response, "parsed")
    
    if (result$status == "ok") {
      return(result$data)
    } else {
      warning(paste("API error:", result$data$message))
      return(NULL)
    }
  } else {
    warning(paste("HTTP error:", status_code(response)))
    
    # Try to get error details
    error_content <- content(response, "text")
    cat("Error response:", error_content, "\n")
    return(NULL)
  }
}

# Test with the RFID from your database
test_rfids <- c(
  "5A:01:CD:B2",  # Should find participant #5 (Jablonsk Schabi)
  "5A:91:A7:AF",  # Should find participant #1 (Sager Lian)
  "AA:BB:CC:DD"   # Should return "RFID not assigned"
)

for (rfid in test_rfids) {
  cat("\n=== Testing RFID:", rfid, "===\n")
  result <- lookup_participant(rfid)
  
  if (!is.null(result)) {
    if (!is.null(result$participant)) {
      cat("Found participant:\n")
      cat("  Startnummer:", result$participant$Startnummer, "\n")
      cat("  Name:", result$participant$Name, result$participant$Vorname, "\n")
      cat("  On track:", result$on_track, "\n")
      cat("  Allowed to lock:", result$allowed_to_lock, "\n")
      cat("  Reason:", result$reason, "\n")
    } else {
      cat("RFID not assigned to any participant\n")
    }
  }
}
