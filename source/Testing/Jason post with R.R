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

### Function to update a value in race_management ####
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