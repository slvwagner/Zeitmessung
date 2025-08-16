library(httr)
library(rvest)
library(tidyverse)
library(purrr)

# Git ####
git_commit <- function(message, repo = ".") {
  git_add(repo = repo)
  capture_messages_warnings(gert::git_commit(message = message, repo = repo))
}

git_push <- function(repo = ".") {
  capture_messages_warnings(gert::git_push(repo = repo))
}

git_pull <- function(repo = ".") {
  capture_messages_warnings(gert::git_pull(repo = repo))
}

git_log <- function(repo = ".") {
  log <- gert::git_log(repo = repo, max = 5)
  paste(sapply(log$message, function(msg) paste0("- ", msg)), collapse = "\n")
}

### capture message and warnings ####
capture_messages_warnings <- function(expr) {
  messages <- character()
  warnings <- character()
  
  result <- withCallingHandlers(
    tryCatch(
      expr,
      warning = function(w) {
        # suppress default warning printing
        invokeRestart("muffleWarning")
      }
    ),
    message = function(m) {
      messages <<- c(messages, conditionMessage(m))
      invokeRestart("muffleMessage")
    },
    warning = function(w) {
      warnings <<- c(warnings, conditionMessage(w))
    }
  )
  
  list(result = result, messages = messages, warnings = warnings)
}

# find semester from date ####
get_semester <- function(date) {
  # Ensure input is of Date class
  date <- as.Date(date)
  
  # Extract month
  month <- as.integer(format(date, "%m"))
  
  # Semester 1 = Jan–Jun, Semester 2 = Jul–Dec
  semester <- ifelse(month <= 6, 1, 2)
  
  return(semester)
}

# spez. Round for Swiss currency "CHF" ####
round5Rappen <- function(x) {
  round(x * 20) / 20
}

r_get_colnames <- function(x){
  paste0("\"",names(x),"\"", collapse = ",")|>
    writeLines()
}

# variable is present in global environment ####
r_is.defined <- function(sym) {
  sym <- deparse(substitute(sym))
  env <- parent.frame()
  exists(sym, env)
}

# library is loaded in global environment ####
r_is.library_loaded <- function(package_name) {
  is_loaded <- FALSE
  tryCatch({
    is_loaded <- requireNamespace(package_name, quietly = TRUE)
  }, error = function(e) {
    is_loaded <- FALSE
  })
  return(is_loaded)
}

# clean console ####
if (commandArgs()[1]=='RStudio'){
  print.cleanup <- function(cleanupObject) cat("\f")     
} else if(substr(commandArgs()[1], nchar(commandArgs()[1]), nchar(commandArgs()[1])) == "R"){        
  print.cleanup <- function(cleanupObject) cat(c("\033[2J","\033[H"))
} else {
  print(paste0("not support: ",commandArgs()[1]))
}                                                                         
clc <- 0                                        ##  variable from class numeric
class(clc) <- 'cleanup'                         ##  class cleanup
#print(clc)                                      ##  when you load this source,
# cls  it cleans all console

# Inhaltsverzeichnis für Markdown ####
r_toc_for_Rmd <- function(
    c_Rmd,
    toc_heading_string = "Table of Contents" ,
    create_nb = TRUE, create_top_link = TRUE , nb_front = TRUE, set_first_heading_level = FALSE,
    pagebreak_level = "non"
)
{
  create_df <- function(c_Rmd) {
    p <- "^```"
    df_data <- data.frame(
      index = 1:length(c_Rmd),
      c_Rmd,
      code_sections = lapply(c_Rmd, function(x)
        stringr::str_detect(x, p)) |> unlist(),
      is.heading = stringr::str_detect(c_Rmd, "^[#]+\\s")
    )
    
    # search and exclude code sections
    c_start_ii <- 0
    for (ii in 1:nrow(df_data)) {
      if (df_data$code_sections[ii] &  (c_start_ii != 0)) {
        df_data$code_sections[c_start_ii:ii] <-
          rep(TRUE, length(c_start_ii:ii))
        c_start_ii <- 0
      } else if (df_data$code_sections[ii]) {
        c_start_ii <- ii
      }
    }
    
    # remove heading in code section
    df_data$is.heading <- ifelse(df_data$code_sections, FALSE, df_data$is.heading)
    
    # Store headings
    df_data$`#` <- stringr::str_detect(df_data$c_Rmd, "^#\\s") |> ifelse(1, 0)
    df_data$`##` <-  stringr::str_detect(df_data$c_Rmd, "^##\\s") |> ifelse(1, 0)
    df_data$`###` <- stringr::str_detect(df_data$c_Rmd, "^###\\s") |> ifelse(1, 0)
    df_data$`####` <- stringr::str_detect(df_data$c_Rmd, "^####\\s") |> ifelse(1, 0)
    df_data$`#####` <- stringr::str_detect(df_data$c_Rmd, "^#####\\s") |> ifelse(1, 0)
    df_data$`######` <- stringr::str_detect(df_data$c_Rmd, "^######\\s") |> ifelse(1, 0)
    return(df_data)
  }
  # create data frame to work with
  df_data <- create_df(c_Rmd)
  
  # Headings
  m <- df_data[df_data$is.heading, 5:ncol(df_data)]
  
  # Analyze heading structure
  heading_struct <- m|>
    apply(2, function(x) {
      sum(x)>0
    })
  
  # highest order heading column index
  for (ii in 1:ncol(m)) {
    if(heading_struct[ii]) {
      highest_order_jj <- ii
      break
    }
  }
  
  # highest order heading row index
  for (ii in 1:nrow(m)) {
    if(m[ii,highest_order_jj]) {
      highest_order_ii <- ii
      break
    }
  }
  
  # find first heading
  for (ii in 1:6) {
    if(m[1,ii]>0){
      first_heading_column <- ii
    }
  }
  
  # correct heading structure
  c_names <- c("#","##","###","####","#####","######")
  
  if(highest_order_jj != first_heading_column){
    # correct structure
    temp <- m[1:(highest_order_ii-1),first_heading_column:6]
    temp
    temp <- switch (first_heading_column ,
                    temp = temp,
                    temp = cbind(temp,p1 = rep(0,nrow(temp))),
                    temp = cbind(temp,p1 = rep(0,nrow(temp)),p2 = rep(0,nrow(temp))),
                    temp = cbind(temp,p1 = rep(0,nrow(temp)),p2 = rep(0,nrow(temp)),p3 = rep(0,nrow(temp))),
                    temp = cbind(temp,p1 = rep(0,nrow(temp)),p2 = rep(0,nrow(temp)),p3 = rep(0,nrow(temp)),p4 = rep(0,nrow(temp))),
                    temp = cbind(temp,p1 = rep(0,nrow(temp)),p2 = rep(0,nrow(temp)),p3 = rep(0,nrow(temp)),p4 = rep(0,nrow(temp)),p5 = rep(0,nrow(temp))),
                    temp = cbind(temp,p1 = rep(0,nrow(temp)),p2 = rep(0,nrow(temp)),p3 = rep(0,nrow(temp)),p4 = rep(0,nrow(temp)),p5 = rep(0,nrow(temp)),p6 = rep(0,nrow(temp))),
    )
    temp
    
    temp1 <- m[highest_order_ii:nrow(m),highest_order_jj:6]
    temp1 <- switch (highest_order_jj,
                     temp1 = temp1,
                     temp1 = cbind(temp1,p1 = rep(0,nrow(temp1))),
                     temp1 = cbind(temp1,p1 = rep(0,nrow(temp1)),p2 = rep(0,nrow(temp1))),
                     temp1 = cbind(temp1,p1 = rep(0,nrow(temp1)),p2 = rep(0,nrow(temp1)),p3 = rep(0,nrow(temp1))),
                     temp1 = cbind(temp1,p1 = rep(0,nrow(temp1)),p2 = rep(0,nrow(temp1)),p3 = rep(0,nrow(temp1)),p4 = rep(0,nrow(temp1))),
                     temp1 = cbind(temp1,p1 = rep(0,nrow(temp1)),p2 = rep(0,nrow(temp1)),p3 = rep(0,nrow(temp1)),p4 = rep(0,nrow(temp1)),p5 = rep(0,nrow(temp1))),
                     temp1 = cbind(temp1,p1 = rep(0,nrow(temp1)),p2 = rep(0,nrow(temp1)),p3 = rep(0,nrow(temp1)),p4 = rep(0,nrow(temp1)),p5 = rep(0,nrow(temp1)),p6 = rep(0,nrow(temp1))),
    )
    names(temp1) <- c_names[highest_order_jj:6]
    names(temp) <- c_names[highest_order_jj:6]
    m_ <- rbind(temp,temp1)
  }else{
    if(highest_order_jj>0){ # remove not populated columns
      m_ <- switch (highest_order_jj,
                    m_ = m,
                    m_ = cbind(m[,2:6],p1 = rep(0,nrow(m))),
                    m_ = cbind(m[,3:6],p1 = rep(0,nrow(m)),p2 = rep(0,nrow(m))),
                    m_ = cbind(m[,4:6],p1 = rep(0,nrow(m)),p2 = rep(0,nrow(m)),p3 = rep(0,nrow(m))),
                    m_ = cbind(m[,5:6],p1 = rep(0,nrow(m)),p2 = rep(0,nrow(m)),p3 = rep(0,nrow(m)),p4 = rep(0,nrow(m))),
                    m_ = cbind(m[,6:6],p1 = rep(0,nrow(m)),p2 = rep(0,nrow(m)),p3 = rep(0,nrow(m)),p4 = rep(0,nrow(m)),p5 = rep(0,nrow(m)))
      )
    }else{
      m_ <- m
    }
  }
  m_
  
  # create structure number system
  # Heading structure counts
  heading_cnt <- rep(0, 6)
  heading_cnt_ <- rep(0, 6)
  last_heading_edited <- 0
  
  # Structure string
  c_add <- c("* ",
             "    + ",
             "        + ",
             "            + ",
             "                + ",
             "                    + ")
  
  c_add_structure <- 1:nrow(m_)
  column_cnt <- 0
  m__ <- m_
  c_Heading_level <- 1:nrow(m_)
  for (ii in 1:nrow(m_)) {
    for (jj in 1:6)
      if (m_[ii, jj] > 0) {
        heading_cnt[jj] <- heading_cnt[jj] + 1
        if (last_heading_edited > jj) {
          # if heading order changes to higher order clear heading_cnt accordingly
          heading_cnt[(jj + 1):length(heading_cnt)] <- 0
          
        }
        last_heading_edited <- jj
        break
      }
    m__[ii, 1:6] <- heading_cnt
    heading_cnt_ <- heading_cnt
    if(set_first_heading_level){
      c_Heading_level[ii] <- c_names[jj]
    }else{
      c_Heading_level[ii] <- c_names[jj + (highest_order_jj-1)]
    }
    c_add_structure[ii] <- c_add[jj]
    
  }
  
  # create structure number
  c_nb <- m__ |>
    apply(1, function(x) {
      temp <- x[x > 0]
      paste0(temp, collapse = ".")
    })
  
  # create link link to table of contents
  c_top_link <-  paste0("\n[", toc_heading_string, "](#", toc_heading_string, ")\n")
  c_top_link
  
  c_Heading <- c_Rmd[df_data$is.heading]|>stringr::str_remove_all("#")|>stringr::str_trim()
  c_Heading
  
  # create anchor
  if (create_nb) {
    if (nb_front) { # number system in front of heading
      c_anchor <- paste0(
        c_Heading_level," " , c_nb, " ", c_Heading ,
        "<a name=\"",
        "A_", # add some characters to ensure html links will work
        c_nb, "_", c_Heading ,
        "\"></a>",
        if(create_top_link) c_top_link
      )
      c_toc <- paste0("[", c_nb,  " ", c_Heading,"](#A_", c_nb,"_", c_Heading, ")")
    } else {  # heading flowed by number system
      c_anchor <- paste0(
        c_Heading_level, " " , c_Heading, " ", c_nb,
        "<a name=\"",
        "A_", # add some characters to ensure html links will work
        c_Heading, " ", c_nb,
        "\"></a>",
        if(create_top_link) c_top_link
      )
      c_toc <- paste0("[", c_Heading, " ",c_nb,"](#A_", c_Heading," ",c_nb,")")
    }
  } else { # No numbering system / Do not Include number system
    c_anchor <- paste0(
      c_Heading_level, " ", c_Heading,
      "<a name=\"",
      "A_", # add some characters to ensure html links will work
      c_Heading,
      "\"></a>",
      if(create_top_link) c_top_link
    )
    c_toc <- paste0("[", c_Heading, "](#A_", c_Heading, ")")
  }
  
  # format toc according to found heading structure
  c_toc <- paste0(c_add_structure, c_toc)
  
  # Enhance headings
  df_data_ <- dplyr::left_join(df_data[, 1:4],
                               data.frame(index = rownames(m__) |> as.integer(),
                                          c_anchor),
                               by = "index")
  
  df_data_$c_Rmd_ <-  ifelse(!is.na(df_data_$c_anchor), df_data_$c_anchor, c_Rmd)
  
  # create TOC
  highest_order_jj <- ifelse(set_first_heading_level, 1, highest_order_jj)
  c_toc_link <- switch(highest_order_jj,
                       paste0(c_names[1]," ",toc_heading_string),
                       paste0(c_names[2]," ",toc_heading_string),
                       paste0(c_names[3]," ",toc_heading_string),
                       paste0(c_names[4]," ",toc_heading_string),
                       paste0(c_names[5]," ",toc_heading_string),
                       paste0(c_names[6]," ",toc_heading_string)
  )
  
  c_toc_link <- ifelse(create_top_link,
                       paste0(c_toc_link, "<a name=\"", toc_heading_string, "\"></a>"),
                       c_toc_link)
  
  # find position to insert table of contents
  check <- stringr::str_detect(c_Rmd, "---")
  c_start <- 1
  cnt <- 0
  
  for (ii in 1:length(c_Rmd)) {
    if (check[ii]) {
      c_start <- ii
      cnt <- cnt + 1
      if(cnt == 2) break
    }
  }
  
  # Insert table of contents
  c_Rmd <- c(df_data_$c_Rmd_ [1:(c_start)],
             c_toc_link,
             c_toc,
             "\n",
             df_data_$c_Rmd_[(c_start+1):nrow(df_data)]
  )
  
  # Insert page breaks
  #create data frame to work with
  df_data <- create_df(c_Rmd)
  
  # Headings
  m <- df_data[df_data$is.heading, 5:ncol(df_data)]
  
  # Analyze heading structure
  heading_struct <- m|>
    apply(2, function(x) {
      sum(x)>0
    })
  
  # highest order heading column index
  for (ii in 1:ncol(m)) {
    if(heading_struct[ii]) {
      highest_order_jj <- ii
      break
    }
  }
  
  if(highest_order_jj > 1 & pagebreak_level != "non") pagebreak_level <- (pagebreak_level|>as.integer() +  highest_order_jj - 1)|>as.character()
  
  m_pb <- switch (
    pagebreak_level,
    "non" = FALSE,
    "1" = m[, 1:1]|>matrix(dimnames =list(row.names(m),"#"))|>as.data.frame(),
    "2" = m[, 1:2],
    "3" = m[, 1:3],
    "4" = m[, 1:4],
    "5" = m[, 1:5],
    "6" = m[, 1:6],
  )
  
  # add html page break tag
  if(is.data.frame(m_pb)) {
    for (ii in 2:nrow(m_pb)) {
      for (jj in 1:ncol(m_pb)) {
        if (m_pb[ii, jj] > 0) {
          index <- row.names(m_pb)[ii] |> as.integer()
          c_Rmd[index] <-
            paste0("\n",
                   "\\newpage",
                   # "<div style=\"page-break-after: always\"></div>",
                   "\n",
                   c_Rmd[index])
        }
      }
    }
  }
  
  return(c_Rmd)
}

# Variel ist definiert? ####
r_is.defined <- function(sym) {
  sym <- deparse(substitute(sym))
  env <- parent.frame()
  exists(sym, env)
}

# signif but return sting 
r_signif <- function (x, significant_digits = 3){
  format(x, format = "g", digits = significant_digits)
}

# inspect links ####
inspect_link <- function(df_mapping, ID){
  link <- df_mapping|>
    filter(`Event ID` == ID)|>
    pull()
  if(length(link) > 0) return(link)
  else return(NULL)
}

# inspect link id`s ####
inspect_link_ids <- function(df_mapping) {
  result <- vector("list", nrow(df_mapping))  # Initialize an empty list
  names(result) <- df_mapping$`Event ID`  # Set names to ID_Programm
  
  for (ii in seq_len(nrow(df_mapping))) {
    link_ids <- df_mapping[ii,"Event ID"]|>pull()  # Store all consecutive Link IDs for this row
    link <- df_mapping[ii,"Link to Event ID"]|>pull()
    run <- TRUE
    if (!is.na(link)){
      link_ids <- c(link_ids, link)
      while (run) {
        link <- inspect_link(df_mapping, link)
        if (!is.na(link)){
          link_ids <- c(link_ids, link)
        }else {
          run <- FALSE
        }
      }
    }
    result[[ii]] <- link_ids
  }
  return(result)
}

# Collect all values that appear in chains (excluding the first element of each list) ####
nullify_used_entries <- function(lst) {
 
  used_values <- unlist(lapply(lst, function(x) x[-1])) 
  
  # Convert list names to numeric for comparison
  used_names <- as.numeric(names(lst))
  
  # Nullify entries whose names are used in another chain
  lst[used_names %in% used_values] <- NULL
  
  return(lst)
}

# Eintritte aus Advanced Tickets files #####
convert_data_Film_txt <- function(fileName, con) {
  paste0("convert_data_Film_txt, filename: ", fileName)|>
    writeLines()

  library(rebus)
  Programm <- DB_get_table("Programm",con)
  Programm <- convert_to_template_types(Programm, l_template$Programm)

  l_Eintritt <- fileName|>
    lapply(function(fileName){
      
      # find ID_Program from file name
      ID <- str_match(fileName, "ID"%R%optional(SPC)%R%capture(one_or_more(DGT)))[2]|>
        as.integer()
      
      # read in data
      c_raw <- DB_get_file(con, fileName, "Eintritt files")$`file content`|>
        str_split("\n")|>
        unlist()
      
      l_temp <- list()
      
      # Extract suisa from file
      p <- or(START%R%DGT%R%DGT%R%DGT%R%DGT%R%DOT%R%DGT%R%DGT%R%DGT,
              START%R%WRD%R%WRD%R%WRD%R%DGT%R%DOT%R%DGT%R%DGT%R%DGT) #suisa
      index <- c_raw|>
        str_detect(p)
      
      c_temp <- c_raw[index]|>
        str_split("\t")|>
        unlist()
      
      # Error handling: Suisa from Eintritt vs Suisa from Programm
      df_temp <- Programm|>
        filter(`Event ID` == ID)
      df_temp$Suisanummer
      
      if(length(df_temp$Suisanummer) == 0){
        stop("\nEs gibt keinen Programmeintrag für: .../Kinoklub/", fileName,
             "\nBitte im Program einen Eintrag erstellen für Programm ID", ID,"\n" )
      }
      
      if(c_temp[1] != df_temp$Suisanummer) {
        warning("\nIn der Datei: .../Kinoklub/", fileName,
                "\nwurde die Suisanummer ",c_temp[1]," gefunden.",
                "\nIm Program wurde aber die Suisanummer ",df_temp$Suisanummer, " für Programm ID: ", ID," / ",df_temp$Filmtitel," definiert\n" )
      }
      
      
      # Save Suisa
      ii <- 1
      l_temp[[ii]] <- c_temp[1]
      names(l_temp)[ii] <- "Suisa"
      ii <- ii+1
      
      # Extract Filmtitel
      l_temp[[ii]] <- c_temp[2]
      names(l_temp)[ii] <- "Filmtitel"
      ii <- ii+1
      
      # Extract Datum
      p <- or("\t"%R%DGT%R%DGT%R%DOT%R%DGT%R%DGT%R%DOT%R%DGT%R%DGT%R%DGT%R%DGT, # format 01.01.2025
              "\t"%R%DGT%R%DGT%R%"/"%R%DGT%R%DGT%R%"/"%R%DGT%R%DGT%R%DGT%R%DGT  # format 01/01/2025
      )
      index <- c_raw|>
        str_detect(p)
      index
      
      c_temp <- c_raw[index]|>
        str_split("\t")|>
        unlist()
      c_temp
      
      if(dmy(c_temp[2]) != df_temp$Datum) {
        stop("\nIn der Datei: .../Kinoklub/", fileName,
             "\nwurde das Datum ",format(c_temp[2], "%d.%m.%Y")," gefunden.",
             "\nIm Programm wurde aber das Datum ",format(df_temp$Datum, "%d.%m.%Y")," für Programm ID: ", ID," / ",df_temp$Filmtitel," definiert\n" )
      }
      
      l_temp[[ii]] <- c_temp[2]
      names(l_temp)[ii] <- "Datum"
      ii <- ii+1
      
      # Extract suisa-Vorabzug
      p <- DGT%R%DOT%R%one_or_more(DGT)%R%"%"%R%SPC%R%"SUISA" #Datum
      index <- c_raw|>
        str_detect(p)
      index
      
      c_temp <- c_raw[index]|>
        str_split("%"%R%SPC)|>
        unlist()
      c_temp
      
      l_temp[[ii]] <- c_temp[1]|>as.numeric()
      names(l_temp)[ii] <- "SUISA-Vorabzug"
      ii <- ii+1
      
      # Extract Tabelle
      p <- "Platzkategorie" #Tabellenanfang
      p1 <- "Brutto" # Tabellenende
      
      index <- c_raw|>
        str_detect(p)
      index1 <- c_raw|>
        str_detect(p1)
      
      for (jj in 1:length(c_raw)) {
        if(index[jj]== TRUE) {
          index <- jj
          break
        }
      }
      for (jj in 1:length(c_raw)) {
        if(index1[jj]== TRUE) {
          index1 <- jj-2
          break
        }
      }
      df_data <- c_raw[(index+1):index1]|>
        str_split("\t")|>
        bind_cols()|>
        as.matrix()|>
        t()|>
        as.data.frame()|>
        suppressMessages()
      
      names(df_data) <- c_raw[index]|>
        str_split("\t")|>
        unlist()
      
      df_data <- df_data|>
        mutate(Preis = as.numeric(Preis),
               Tax = as.numeric(Tax),
               Anzahl = as.numeric(Anzahl),
               Umsatz= Preis*Anzahl
        )|>
        tibble()
      
      l_temp[[ii]] <- df_data|>
        tibble()
      names(l_temp)[ii] <- "Abrechnung"
      
      l_temp[[ii]] |>
        mutate(Suisanummer = l_temp[[1]],
               Filmtitel = l_temp[[2]],
               Datum = dmy(l_temp[[3]]),
               `SUISA-Vorabzug [%]` = l_temp[[4]],
               fileName = fileName
        )
    })
  names(l_Eintritt) <- fileName
  
  # create data frame
  df_Eintritt <- l_Eintritt |>
    bind_rows() |>
    mutate(Verkaufspreis = Preis ,
           Zahlend = if_else(Verkaufspreis == 0, F, T)) |>
    select(
      Datum,
      Suisanummer,
      Filmtitel,
      Platzkategorie,
      Zahlend,
      Verkaufspreis,
      Anzahl,
      Umsatz,
      `SUISA-Vorabzug [%]`
    )
  df_Eintritt
  
  # join `Event ID`
  df_Eintritt <- df_Eintritt |>
    left_join(
      Programm |>
        filter(`Verleiher Angefragt?` != "Wird nicht gespielt") |>
        select(`Event ID`, Datum, Suisanummer),
      by = join_by(Datum, Suisanummer)
    ) |>
    rename(`Umsatz [CHF]` = Umsatz)
  
  df_Eintritt <- df_Eintritt |>
    select(
      `Event ID`,
      `Datum`,
      `Suisanummer`,
      `Filmtitel`,
      `Platzkategorie`,
      `Zahlend`,
      `Verkaufspreis`,
      `Anzahl`,
      `Umsatz [CHF]`,
      `SUISA-Vorabzug [%]`
    )
  
  if (sum(is.na(df_Eintritt$`Event ID`)) > 0) {
    df_temp <- df_Eintritt |>
      filter(is.na(`Event ID`)) |>
      distinct(Datum, Suisanummer, .keep_all = TRUE)
    stop(
      "\nFür den Film ",
      df_temp$Filmtitel,
      " mit Suisanummer ",
      df_temp$Suisanummer,
      " am ",
      paste0(format(df_temp$Datum, "%d.%m.%Y"), collapse = ", "),
      " existiert kein Programmeintrag\nBitte das Programm korrigieren!\n"
    )
  }
  
  return(df_Eintritt)
}

# Extrakt Kioskverkauf und Überschuss / Manko #####
convert_kiosk_txt <- function(fileName, con, l_template) {
  if (!dbIsValid(con)) {
    stop("Invalid database connection.")
  }
  if(length(fileName) ==  0) stop("No file names found in function convert_kiosk_txt")
  
  paste0("convert_data_kiosk_txt, filename: ", fileName)|>
    writeLines()
  
  # library(rebus)
  # p <- capture(one_or_more(DGT))%R%DOT%R%"txt"
  
  p <- "([\\d]+)\\.txt"
  
  IDs <- str_match(fileName, p)[,2]|>
    as.integer()
  
  Programm <- tbl(con,"Programm")|>
    filter(`Event ID` %in% IDs)|>
    collect()|>
    convert_to_template_types(l_template$Programm)
  
  `Einkauf Kiosk` <- DB_get_table("Einkauf Kiosk", con)|>
    convert_to_template_types(l_template$`Einkauf Kiosk`)
  
  Spezialpreise <- DB_get_table("Spezialpreis", con)|>
    select(Spezialpreisname)|>
    pull()
  
  # library(rebus)
  # p <- DOT%R%DOT%R%DOT
  # as.character(p)
  p <- "\\.\\.\\."
  Spezialpreise <- Spezialpreise[!str_detect(Spezialpreise, p)]
  
  l_temp <- fileName|>
    lapply(function(fileName){
      c_raw <- DB_get_file(con, fileName, "Kiosk files")$`file content`|>
        str_split("\n")|>
        unlist()
      # p <- "ID"%R%optional(SPC)%R%capture(one_or_more(DGT))
      p <- "ID[\\s]?([\\d]+)"
      as.character(p)
      # find ID_Program from file name
      ID <- str_match(fileName, p)[2]|>
        as.integer()
      
      # find ID_Program
      df_temp <- Programm|>
        filter(`Event ID` == ID)
      
      # Detect Verkaufarikel in string
      # Arikel erfasst im Kassensystem (Advanced tickets). 
      # Achtung muss in der Tabelle `Einkauf Kiosk` `Artickelname-Kassensystem` erfasst sein ansonsten wird der Artikel nicht erkannt
      
      p1 <- rebus::or1(paste0(`Einkauf Kiosk`$`Artikelname-Kassensystem`))
      
      # detect Spez Preise
      # library(rebus)
      # p <- "Spez"%R%SPC
      # as.character(p)
      
      # Detect Spezialartikel
      # Achtung muss in der Tabelle Spezialpreis erfasst sein sonst wird es nicht erkannt
      p2 <- rebus::or1(Spezialpreise)
      
      
      # create list to store data
      l_extracted <- list()
      ii <- 1L
      
      # get all lines with Verkauf
      l_extracted[[ii]] <-
        list(Verkaufsartikel =
               tibble(Verkaufartikel_string = c(c_raw[str_detect(c_raw, p1)], ## Arikel erfasst im Kassensystem (Advanced tickets). Achtung muss in der Tabelle `Einkauf Kiosk` `Artickelname-Kassensystem` erfasst sein ansonsten wird der Artikel nicht erkannt
                                                c_raw[str_detect(c_raw, p2)]  ## Spez Arikel
               )
               )
        )
      
      # Extract Überschuss Manko der Kasse
      # Detect Überschuss Manko
      # p3 <- optional("-") %R% one_or_more(DGT) %R% optional(DOT)%R% one_or_more(DGT)
      p3 <- "[-]?[\\d]+[\\.]?[\\d]+" 
      
      ii <- ii + 1L
      l_extracted[[ii]] <-
        list(
          `Überschuss / Manko` =
            tibble(`Überschuss / Manko` =
                     c_raw[str_detect(c_raw, "Manko")]|>
                     str_extract(p3)|>
                     as.numeric()
            )|>
            mutate(`Überschuss / Manko` = if_else(is.na(`Überschuss / Manko`), 0, `Überschuss / Manko`))
        )
      
      if(nrow(l_extracted[[ii]]$`Überschuss / Manko`) != 1) stop("Überschuss / Manko konnte nicht in der Datei:", fileName, " gefunden werden")
      
      
      # `Event ID`
      ii <- ii + 1L
      l_extracted[[ii]] <-
        list(`Event ID` =  ID)
      
      # extract Verkauf
      m_Kiosk <-
        l_extracted[[1]][["Verkaufsartikel"]]$Verkaufartikel_string |>
        str_split(pattern = "\t", simplify = T)
      m_Kiosk
      
      # Wie viele Spalten
      c_lenght <- ncol(m_Kiosk)
      c_lenght
      
      # extract according to nrow(m_Kiosk), not all files have the same number of columns
      if(c_lenght == 7){ # mit Korrekturbuchungen
        if(nrow(m_Kiosk) == 1){
          # print(m_Kiosk)
          x <- m_Kiosk[c(2,4:5,7)]|>
            as.numeric()
          x <- matrix(x, ncol = 4)|>
            suppressWarnings()
          colnames(x) <- c("Einzelpreis", "Anzahl", "Korrektur", "Betrag")
          
          x <- x|>
            as_tibble()|>
            mutate(Anzahl = if_else(!is.na(Korrektur),  Anzahl + Korrektur, Anzahl))|>
            select(-Korrektur)
          
          m_Kiosk <- bind_cols(Verkaufsartikel = m_Kiosk[,1], x)
          
        } else {
          # print(m_Kiosk)
          x <- m_Kiosk[,c(2,4:5,7)]
          x <- apply(x, 2, as.numeric)
          colnames(x) <- c("Einzelpreis", "Anzahl", "Korrektur", "Betrag")
          
          x <- x|>
            as_tibble()|>
            mutate(Anzahl = if_else(!is.na(Korrektur),Anzahl+Korrektur,Anzahl))|>
            select(-Korrektur)
          
          m_Kiosk <- bind_cols(Verkaufsartikel = m_Kiosk[,1], x)
        }
      }else if(c_lenght == 5){ # keine Korrekturbuchungen
        m_Kiosk <- m_Kiosk[,c(1:3,5)]
        x <- m_Kiosk[,2:ncol(m_Kiosk)]|>
          apply(2, as.numeric)
        colnames(x) <- c("Einzelpreis", "Anzahl", "Betrag")
        
        m_Kiosk <-
          bind_cols(Verkaufsartikel = m_Kiosk[,1], x)
      }else if(c_lenght == 0){ # Keine Kioskverkäufe
        m_Kiosk <- tibble(Verkaufsartikel = "Keine Kioskverkäufe",
                          Einzelpreis = 0,
                          Anzahl = 0,
                          Betrag = 0
        )
      } else if (c_lenght == 9){
        temp <- l_extracted[[1]]$Verkaufsartikel$Verkaufartikel_string|>
          str_split("\t")|>
          lapply(matrix, ncol = 9)|>
          lapply(as.data.frame)|>
          bind_rows()
        
        m_Kiosk <- tibble(
          Verkaufsartikel = temp$V1,
          Einzelpreis = as.numeric(temp$V2),
          Anzahl = as.numeric(temp$V4),
          Betrag = as.numeric(temp$V5)
        )
        
      } else {
        stop(paste0("\nDie Datei: input/advance tickets/Kiosk ",names(m_Kiosk),".txt",
                    "\nhat hat ein anderes Format und ist noch nicht implementiert.\nBitte wenden dich an die Entwicklung"))
      }
      
      m_Kiosk
      
      # Data returned by function
      df_Kiosk <- m_Kiosk|>
        mutate(Datum = df_temp$Datum,
               `Einzelpreis` = if_else(is.na(Einzelpreis), Betrag / Anzahl, Einzelpreis),
               `Betrag` = if_else(Anzahl == 0, 0, Betrag),
               `Überschuss / Manko [CHF]` = l_extracted[[2]]$`Überschuss / Manko`$`Überschuss / Manko`
        )|>
        rename(`Einzelpreis [CHF]`= Einzelpreis,
               `Betrag [CHF]` = Betrag
        )
      return(df_Kiosk)
    })
  # p <- capture(one_or_more(DGT))%R%DOT%R%"txt"
  p <- "([\\d]+)\\.txt"
  names(l_temp) <- str_match(fileName, p)[,2]
  
  # Kiosk data 
  df_Kiosk <- bind_rows(l_temp, .id = "Event ID")|>
    mutate(`Event ID` = as.integer(`Event ID`))|>
    rename("Artikel-Kassensystem" = Verkaufsartikel)|>
    select(`Event ID`, Datum,`Artikel-Kassensystem`, `Einzelpreis [CHF]`, Anzahl, `Betrag [CHF]`, `Überschuss / Manko [CHF]`)|>
    arrange(`Event ID`)
  df_Kiosk
  
  return(df_Kiosk)
}

# Spezialpreise abgleichen ####
Spezialpreisekiosk <- function(df_Kiosk, con, l_template) {
  if (!dbIsValid(con)) {
    stop("Invalid database connection.")
  }
  # Spez Verkaufsartikel / Spezialpreise einlesen ####
  df_Spezialpreisekiosk <- DB_get_table("Spezialpreisekiosk", con )|>
    convert_to_template_types(l_template$Spezialpreisekiosk)|>
    mutate(`Event ID` = as.character(`Event ID`)|>as.integer(),
           Spezialpreis = as.character(Spezialpreis)
    )|>
    arrange(`Event ID`, Spezialpreis)
  df_Spezialpreisekiosk
  
  c_Event_IDs <- df_Kiosk|>
    distinct(`Event ID`)|>
    pull()
  c_Event_IDs
  
  # Spezialpreisekiosk abgleichen
  l_temp <- lapply(c_Event_IDs, function(ii){
    # Spezialpreisekiosk per `Event ID`
    df_temp1 <- df_Spezialpreisekiosk|>
      filter(`Event ID` == ii)
    
    # Kiosk daten per `Event ID`
    df_temp2 <- df_Kiosk|>
      filter(`Event ID` == ii)
    
    if((nrow(df_temp2) == 0) & (nrow(df_temp1) > 0)) {
      stop("Es sind Spezialpreise in der Tabelle `Spezialpreisekiosk` 
           definiert aber es wurden keine Spezialpreise in der extrahierten Datei gefunden!", 
           paste0(df_temp2$`Artikel-Kassensystem`, collapse = TRUE),
      )
    }
    
    # sind spezialpreise vorhanden?
    if((sum(str_detect(df_temp2$`Artikel-Kassensystem`, rebus::or("Spez", "spez"))) > 0) & (nrow(df_temp1) > 0)){
      df_temp <- left_join(
        df_temp2,
        df_temp1, 
        by = c("Event ID" = "Event ID", "Artikel-Kassensystem" = "Spezialpreis")
      )|>
        rename(ID_Spezialpreisekiosk = ID)
      df_temp
    } else {
      df_temp <- df_temp2|>
        mutate(ID_Spezialpreisekiosk = NA,
               Artikelname = NA)
    }
    return(df_temp)
  })
  
  df_extracted <- l_temp|>
    bind_rows()|>
    select(`Event ID`,Datum, `Artikel-Kassensystem`, ID_Spezialpreisekiosk, Artikelname, `Einzelpreis [CHF]`, 
           Anzahl, `Betrag [CHF]`, `Überschuss / Manko [CHF]`)
  
  df_extracted <- bind_cols(
    ID = 1:nrow(df_extracted),
    df_extracted
  )
  
  return(df_extracted)
}

# find the negative number nreaest to zero ####
nearest_negative_to_zero <- function(x) {
  neg_values <- x[x < 0]  # Filter negative values
  if (length(neg_values) == 0) {
    return(NA)  # Or handle as you prefer if no negative values are present
  }
  max(neg_values)  # The closest to zero from the negative side
}

# Einkaufspreise abgleichen ####
Einkaufspreise <- function(df_extracted, con, l_template) {
  if (!dbIsValid(con)) {
    stop("Invalid database connection.")
  }
  # Einkauf Kiosk
  Einkauf_Kiosk <- DB_get_table("Einkauf Kiosk", con )|>
    convert_to_template_types(l_template$`Einkauf Kiosk` )|>
    arrange(ID)
  Einkauf_Kiosk
  
  # look up Einkaufspreise per date (Gültig ab Datum?) ####
  ii <- 1
  l_temp2 <- list()
  
  for (ii in 1:length(df_extracted$ID)) {
    row_kiosk <- df_extracted|>
      filter(`ID` == ii)
    row_kiosk
    
    row_einkaufspreise <- Einkauf_Kiosk|>
      filter(`Artikelname-Kassensystem` == row_kiosk$`Artikel-Kassensystem`)
    row_einkaufspreise
    
    if(nrow(row_kiosk)  )
      
      if(nrow(row_einkaufspreise) == 0) { # Keine Artikel gefunden (Spezialpreis)
        df_temp <- tibble(
          ID_Kioskartikel = NA,
          Artikel = NA,
          `Artikelname-Kassensystem` = NA,
          `Verkaufspreis [CHF]` = NA,
          Menge = NA,
          `Einkaufspreis [CHF]` = NA,
          Lieferant = NA,
          `Gültig ab Datum` = NA,
          `Event ID` = NA,
          Artikelname = NA,
          Datum = NA
        )
      } else { # Artikelabgleich
        df_temp <-
          left_join(
            row_einkaufspreise ,
            row_kiosk|>
              select(`Event ID`,`Artikel-Kassensystem`, Artikelname, Datum),
            by = c(`Artikelname-Kassensystem` = "Artikel-Kassensystem")
          )|>
          rename(ID_Kioskartikel = ID)
        df_temp
        
        df_temp <- df_temp|>
          mutate(`time deviation` = (`Gültig ab Datum` - Datum))
        df_temp

        c_select <- nearest_negative_to_zero(df_temp$`time deviation`)
        
        df_temp <- df_temp|>
          filter(`time deviation` == c_select) # only keep the smallest `time deviation`
        df_temp
        
        # delete time deviation
        df_temp <- df_temp|>
          mutate(`time deviation` = NULL)
        df_temp
      }
    l_temp2[[ii]] <- df_temp  
  }

  
  df_temp <- bind_rows(l_temp2, .id = "ID")|>
    mutate(ID = as.integer(ID))
  
  df_temp <- df_temp|>
    select(ID, ID_Kioskartikel, Artikel, `Artikelname-Kassensystem`, `Verkaufspreis [CHF]`, `Einkaufspreis [CHF]`, Menge, Lieferant, `Gültig ab Datum`)
  df_temp
  
  # join 
  df_joined <- df_extracted|>
    left_join(df_temp, by = join_by(ID))|>
    mutate(Artikelname = if_else(is.na(ID_Kioskartikel),Artikelname, Artikel)
    )
  df_joined
  
  df_joined|>
    filter(!is.na(ID_Spezialpreisekiosk))

  df_joined <- df_joined|>
    mutate(`Gewinn [CHF]` = (`Einzelpreis [CHF]` - `Einkaufspreis [CHF]`) *  Anzahl)
    
  df_joined <- df_joined|>
    select("ID", "Event ID", "ID_Spezialpreisekiosk", "ID_Kioskartikel", 
           "Artikel-Kassensystem", "Artikelname", "Einzelpreis [CHF]", "Anzahl", "Betrag [CHF]", `Gewinn [CHF]`,
           "Überschuss / Manko [CHF]",
           "Verkaufspreis [CHF]", "Einkaufspreis [CHF]", "Menge", "Lieferant", "Gültig ab Datum")
  
  return(df_joined)
}

# Do complete conversion of Advanced tickest Kiosk files ####
Convert_Kiosk_files <- function(last_uploaded_file, DB_con, l_template){
  
  c_messages <- rep("",3)
  
  # convert file 
  results <- Run_capture_error_warnings(
    convert_kiosk_txt, last_uploaded_file, DB_con, l_template
  )
  c_messages[1] <- results$messages
  
  # get Spezialpreise
  results <- Run_capture_error_warnings(
    Spezialpreisekiosk, results$result, DB_con, l_template
  )
  c_messages[2] <- results$messages
  
  # get Einkaufspeise
  results <- Run_capture_error_warnings(
    Einkaufspreise, results$result, DB_con, l_template
  )
  c_messages[3] <- results$messages
  return(results)
}

# search procinem by a given Suisanummber
# Example usage
# suisa_number <- "1020.295"  # Example SUISA number
# results <- search_procinema_by_suisa(suisa_number)
# print(results)
search_procinema_by_suisa <- function(suisa_number) {
  # Create the form POST request
  response <- POST(
    "https://www.procinema.ch/de/statistics/filmdb/",
    body = list(
      sta_fdb_movid = suisa_number,
      sta_fdb_search = "Suchen",  # The search button value
      process = "Filter"          # The submit action
    ),
    encode = "form"
  )
  
  # Check if successful
  if(status_code(response) != 200) {
    message("Request failed with status: ", status_code(response))
    return(tibble())
  }
  
  # Parse the HTML content
  html_content <- content(response, as = "text") %>% 
    read_html()
  
  # Check if results were found
  results_header <- html_content %>% 
    html_node("h3") %>% 
    html_text(trim = TRUE)
  
  if(is.na(results_header) || !str_detect(results_header, "Suchresultate")) {
    message("No results found for SUISA number: ", suisa_number)
    return(tibble())
  }
  
  # Extract film information
  film_nodes <- html_content %>% html_nodes(".listline")
  
  if(length(film_nodes) == 0) {
    message("No film nodes found in the results")
    return(tibble())
  }
  
  # Process each film
  results <- map_df(film_nodes, function(node) {
    tibble(
      Filmtitel = node %>% html_node(".fl a") %>% html_text(trim = TRUE),
      link = node %>% html_node(".fl a") %>% html_attr("href") %>% 
        paste0("https://www.procinema.ch", .),
      Verleiher = node %>% html_node(".fc") %>% html_text(trim = TRUE) %>% 
        str_replace_all("\\s+", " ") %>% str_trim(),
      Suisanummer = node %>% html_node(".fdbsuisa") %>% html_text(trim = TRUE),
      release_date = node %>% html_node(".fdbrelease") %>% html_text(trim = TRUE),
      admissions = node %>% html_node(".fdbadm") %>% html_text(trim = TRUE) %>% 
        str_remove_all("'") %>% as.integer()
    )
  })
  
  return(results)
}

# get detailed information for a film ####
# Example usage
# result <- film_details("https://www.procinema.ch/de/statistics/filmdb/1020295.html")
# print(result$synopsis)
# print(result)
film_details <- function(url) {
  if(is_empty(url)) {
    return(NULL)
  }
  suppressPackageStartupMessages({
    require(rvest)
    require(dplyr)
    require(stringr)
    require(purrr)
    require(tibble)
    require(httr)
  })
  
  # Helper functions (keep your existing ones)
  extract_faditem <- function(page, label) {
    items <- page %>% html_nodes(".faditem")
    for (item in items) {
      lbl <- item %>% html_node(".faditemlbl") %>% html_text(trim = TRUE)
      if (!is.na(lbl) && str_detect(lbl, label)) {
        return(item %>% html_node(".faditemcont") %>% html_text(trim = TRUE))
      }
    }
    return(NA_character_)
  }
  
  clean_number <- function(x) {
    if (is.na(x) || x == "") return(NA_integer_)
    as.integer(str_remove_all(x, "[^0-9]"))
  }
  
  clean_date <- function(x) {
    if (is.na(x) || x == "") return(NA_character_)
    x
  }
  
  # Fetch the page
  page <- tryCatch({
    resp <- GET(url, timeout(2))
    if (http_error(resp)) stop("HTTP error")
    read_html(resp)
  }, error = function(e) {
    message(str_glue("Error loading {url}: {e$message}"))
    return(NULL)
  })
  
  if (is.null(page)) return(tibble())
  
  # Extract extras block data
  extras_block <- page %>% html_node("aside.extras")
  
  extras_data <- list()
  if (!is.null(extras_block)) {
    items <- extras_block %>% html_nodes("li") %>% html_text(trim = TRUE)
    
    # Process each item
    for (item in items) {
      if (str_detect(item, ":")) {
        parts <- str_split(item, ":", n = 2)
        key <- str_trim(parts[[1]][1])
        value <- str_trim(parts[[1]][2])
        extras_data[[key]] <- value
      } else if (!item %in% c("", "ISAN:")) {
        # Handle special cases like ISAN number
        if (str_detect(item, "0000-0000")) {
          extras_data[["ISAN"]] <- item
        } else if (str_detect(item, "Link:")) {
          link_text <- str_remove(item, "Link:")
          extras_data[[str_trim(link_text)]] <- 
            extras_block %>% 
            html_node(str_glue("li:contains('{item}') a")) %>% 
            html_attr("href")
        }
      }
    }
  }
  
  # Create the main tibble (keep your existing fields)
  result <- tibble(
    # Basic info
    title = page %>% html_node("h1") %>% html_text(trim = TRUE) %||% NA_character_,
    link = url,
    
    # Titles
    original_title = extract_faditem(page, "Original"),
    german_title = extract_faditem(page, "Deutsch"),
    french_title = extract_faditem(page, "Französisch"),
    italian_title = extract_faditem(page, "Italienisch"),
    
    # Release dates
    release_ch = clean_date(extract_faditem(page, "Schweiz")),
    release_dch = clean_date(extract_faditem(page, "Deutschschweiz")),
    release_fch = clean_date(extract_faditem(page, "Suisse romande")),
    release_ich = clean_date(extract_faditem(page, "Tessin")),
    
    # Admissions
    admissions_ch = clean_number(extract_faditem(page, "Schweiz$")),
    admissions_dch = clean_number(extract_faditem(page, "Deutschschweiz$")),
    admissions_fch = clean_number(extract_faditem(page, "Suisse romande$")),
    admissions_ich = clean_number(extract_faditem(page, "Tessin$")),
    
    # Age ratings
    age_approved = clean_number(extract_faditem(page, "Zugelassen ab")),
    age_recommended = clean_number(extract_faditem(page, "Empfohlen ab")),
    age_ti = clean_number(extract_faditem(page, "Kanton TI")),
    
    # Synopsis
    synopsis = page %>% 
      html_node("h3:contains('INHALT') + p") %>% 
      html_text(trim = TRUE) %||% NA_character_,
    
    # Images
    images = page %>% 
      html_nodes(".scenimg") %>% 
      html_attr("src") %>% 
      {if (length(.) > 0) str_c("https://www.procinema.ch", .) else NA_character_} %>% 
      str_c(collapse = ", "),
    
    # Crew
    director = extract_faditem(page, "Regie") %>% str_replace_all("<br>", ", "),
    producer = extract_faditem(page, "Produzent"),
    writer = extract_faditem(page, "Drehbuch") %>% str_replace_all("<br>", ", "),
    music = extract_faditem(page, "Musik"),
    actors = extract_faditem(page, "Schauspieler"),
    
    # Extras data as a list column
    Produktionsland = extras_data$Produktionsland,
    Genre = extras_data$Genre
    
    
  )
  
  return(result)
}

# create empty line with correct data type ####
create_empty_line <- function(df_data) {
  df_data|>
  slice(0) |>
    add_row() |>
    mutate(across(everything(), ~ {
      if (is.character(.)) {
        ""
      } else if (is.integer(.)) {
        0L
      } else if (is.numeric(.)) {
        0
      } else if (is.factor(.)) {
        factor(NA, levels = levels(.))
      } else if (inherits(., "Date")) {
        as.Date(NA)
      } else {
        NA
      }
    }))
}

# Initialize fast dictionary environment ####
# The reason for using dictionaries in the first place is performance.
# Although it is correct that you can use named vectors and lists for the task,
# the issue is that they are becoming quite slow and memory hungry with more data.
# Yet what many people don't know is that R has indeed an inbuilt dictionary data structure
# environments with the option hash = TRUE
dict_init <- function(length)
{
  new.env(hash = TRUE, parent = emptyenv(), size = length)
}

# Assigne key and value to fast dictionary ####
# The reason for using dictionaries in the first place is performance.
# Although it is correct that you can use named vectors and lists for the task,
# the issue is that they are becoming quite slow and memory hungry with more data.
# Yet what many people don't know is that R has indeed an inbuilt dictionary data structure
# environments with the option hash = TRUE
dict_assign_key_values <- Vectorize(assign, vectorize.args = c("x", "value"))

# Get values from dictionary ####
# The reason for using dictionaries in the first place is performance.
# Although it is correct that you can use named vectors and lists for the task,
# the issue is that they are becoming quite slow and memory hungry with more data.
# Yet what many people don't know is that R has indeed an inbuilt dictionary data structure
# environments with the option hash = TRUE
dict_get_values <- Vectorize(get, vectorize.args = "x")

# Check if key is in dictionary ####
# The reason for using dictionaries in the first place is performance.
# Although it is correct that you can use named vectors and lists for the task,
# the issue is that they are becoming quite slow and memory hungry with more data.
# Yet what many people don't know is that R has indeed an inbuilt dictionary data structure
# environments with the option hash = TRUE
dict_exists_key <- Vectorize(exists, vectorize.args = "x")

# Create a fast dictionary from data frame ####
# The reason for using dictionaries in the first place is performance.
# Although it is correct that you can use named vectors and lists for the task,
# the issue is that they are becoming quite slow and memory hungry with more data.
# Yet what many people don't know is that R has indeed an inbuilt dictionary data structure
# environments with the option hash = TRUE
dict_from_data.frame <- function(df){
  df <- as.data.frame(df)
  if(!is.character(df[1,1])){
    c("dict_from_data.frame:\nkey is not character in column 1 of the dataframe argument")|>
      r_colourise("Red")|>
      writeLines()
    return(NULL)
  }else
  {
    # initialize hash
    hash = new.env(hash = TRUE, parent = emptyenv(), size = nrow(df))
    # assign values to keys
    dict_assign_key_values(df[,1], df[,2], hash)
    return(hash)
  }
}

# Update key/value pairs of fast dictionary ####
# The reason for using dictionaries in the first place is performance.
# Although it is correct that you can use named vectors and lists for the task,
# the issue is that they are becoming quite slow and memory hungry with more data.
# Yet what many people don't know is that R has indeed an inbuilt dictionary data structure
# environments with the option hash = TRUE
dict_update <- function(df, dict){
  df <- as.data.frame(df)
  if(nrow(df) == 1){
    dict[[df[1,1]]] <- df[1,2]
  }else{
    for (ii in 1:nrow(df)) {
      dict[[df[ii,1]]] <- df[ii,2]
    }
  }
  return(dict)
}

# Run function and capture message, warning and errors ####
# Example: 
# Function: DB_copy_table
# Arguments: new_rows, DB_con(), "df_Eintritt"
# Run_capture_error_warnings(DB_copy_table, new_rows, DB_con(), "df_Eintritt")
# Returns list with results and all captured messages, warnings and errors
Run_capture_error_warnings <- function(fun, ...) {
  # Initialize variables
  result <- NULL
  c_message <- ""
  args <- list(...)
  
  # Run function 
  tryCatch({
    captured_output <- capture.output({
      withCallingHandlers(
        {
          result <- fun(...)
        },
        warning = function(w) {
          c_message <<- paste0(c_message, "Warning: ", conditionMessage(w), "\n")
          invokeRestart("muffleWarning")
        },
        message = function(m) {
          c_message <<- paste0(c_message, "Message: ", conditionMessage(m), "\n")
          invokeRestart("muffleMessage")
        }
      )
    }, type = "message")
  }, error = function(e) {
    c_message <<- paste0(
      c_message,
      "\nError in function call:\n",
      deparse(substitute(fun)),
      "\nArguments:\n",
      paste(names(args), "=", sapply(args, function(x) if(length(x) > 1) paste0("c(", paste(x, collapse = ","), ")") else x), collapse = "\n"),
      "\nError message: ", conditionMessage(e), "\n"
    )
  })
  
  # Return list with results and captured message
  list(
    result = result,
    messages = c_message
  )
}


### get date type for each column from a data frame ####
get_data_type <- function(df){
  1:ncol(df)|>
    lapply(function(x){
      c_temp <- df|>
        select(all_of(x))|>
        pull()
      class(c_temp)[1] # only use the first class
    })|>
    unlist()
}

# Escape regex literals ####
escape_regex <- function(pattern) {
  gsub("([][{}()+*^$|\\\\?.])", "\\\\\\1", pattern)
}

# Find datatable page ####
find_page <- function(table_rows_selected, table_search_columns, table_data, lastEdited_data_set_name, page_length_var){
  # Initialize default return values
  result <- list(
    ID_to_edit = NA_integer_,
    last_user_filter = NULL,
    last_selected_page = NA_integer_,
    last_selected_row = NA_integer_
  )
  
  # Early return if no row is selected
  if(is.null(table_rows_selected)) {
    return(result)
  }
  
  # map selected row to ID
  df_temp <- table_data
  ID_to_edit <- pull(df_temp[table_rows_selected,1])
  
  # get user filters
  column_filters = table_search_columns
  column_filters <- column_filters|>
    str_remove_all("\"")|>
    str_remove_all("\\[")|>
    str_remove_all("\\]")
  column_filters <- str_split(column_filters,",")
  
  # Update last user filter
  c_test <- lapply(column_filters, function(x){
    nchar(x) > 0
  })|>
    unlist()
  
  # get column data type
  c_class <- get_data_type(df_temp)
  
  ### extract data from column filters ####
  for (ii in 1:length(column_filters)) {
    col_filter <- column_filters[[ii]]
    if(nchar(col_filter[1]) > 0){
      if(c_class[ii] %in% c("Date", "hms")){
        c_date <- pull(df_temp[,ii])|>
          as.character()
        df_temp <- df_temp[str_detect(c_date, col_filter),]
        df_temp <- df_temp[!is.na(pull(df_temp[,ii])),]
      } 
      else if(c_class[ii] == "integer"){
        p1 <- "^[\\d]+"
        p2 <- "[\\d]+$"
        start <- str_extract(col_filter, p1)|>
          as.integer()
        end <- str_extract(col_filter, p2)|>
          as.integer()
        c_select <- start:end
        df_temp <- df_temp[pull(df_temp[,ii]) %in% c_select,]
      } else if (c_class[ii] == "factor"){
        if(length(col_filter) > 1){
          df_temp <- df_temp[pull(df_temp[,ii]) %in% col_filter,] 
        } else {
          c_select <- str_detect(pull(df_temp[,ii]), col_filter)
          c_select <- ifelse(is.na(c_select), FALSE, c_select)
          df_temp <- df_temp[c_select,]
        }
      }
      # character 
      else { 
        col_filter <- col_filter|>
          tolower()|>
          escape_regex()
        
        df_temp <- df_temp[str_detect(pull(df_temp[,ii])|>tolower(), col_filter),] 
        df_temp <- df_temp[!is.na(pull(df_temp[,ii])),]
      }
    }
  }
  
  # map ID to selected row
  df_temp <- df_temp |>
    mutate(index = row_number())
  row_filtered <- df_temp[df_temp[,1] == ID_to_edit,]$index
  
  if(!is_empty(row_filtered)){
    # Calculate page 
    c_page <- ceiling(row_filtered / as.integer(page_length_var))
    if(c_page == 0) c_page <- 1
    
    result$ID_to_edit <- ID_to_edit
    result$last_selected_page <- c_page
    result$last_selected_row <- table_rows_selected
    
    ### if column filters are present update column filters #####
    if(sum(!c_test) != length(column_filters)) {
      column_filters_temp <- table_search_columns|>
        lapply(function(x){
          if(nchar(x) > 0) {
            list(search = x)
          } 
          else {
            NULL
          }
        })
      result$last_user_filter <- column_filters_temp
    }
    
    writeLines(paste0("Selected row: ", table_rows_selected, 
                      ", ID: ", ID_to_edit,
                      ", table: `", lastEdited_data_set_name,
                      "`, Selected page: ", c_page,"\n"))
  }
  
  return(result)
}

# FTP file upload to reports server ####
ftp_upload <- function(file, ftp_server, ftp_user, password, path) {
  library(curl)
  
  if (!file.exists(file)) stop("file: ", file, " does not exist")
  
  # Open file connection
  file_conn <- file(file, "rb")
  on.exit(close(file_conn))  # Ensure file always closes
  
  # Build FTP URL
  encoded_filename <- utils::URLencode(basename(file), reserved = TRUE)
  ftp_url <- paste0(ftp_server, path, encoded_filename)
  
  # Create curl handle
  h <- new_handle(
    upload = TRUE,
    username = ftp_user,
    password = password,
    readfunction = function(n) readBin(file_conn, "raw", n)
  )
  
  # Try upload
  res <- tryCatch({
    curl_fetch_memory(ftp_url, handle = h)
  }, error = function(e) {
    stop("❌ Upload failed: ", e$message)
    return(NULL)
  })
  
  # Check result
  if (!is.null(res)) {
    if (res$status_code >= 200 && res$status_code < 300) {
      message("✅ Upload succeeded (HTTP ", res$status_code, ")")
    } else {
      message("⚠️ Upload failed with status: ", res$status_code)
      cat(rawToChar(res$content))
    }
  }
  
  # Return public web URL
  return(paste0("https://kinoklub.ch/kkTeam/reports/", encoded_filename))
}

# Download file from ftp ####
ftp_download_file <- function(remote_file, ftp_server, ftp_user, password, basepath, local_path = ".") {
  library(curl)
  
  # URL-encode remote file
  encoded_filename <- utils::URLencode(remote_file, reserved = TRUE)
  ftp_url <- paste0(ftp_server, basepath, encoded_filename)
  
  # Define local target path
  local_file <- file.path(local_path, basename(remote_file))
  
  # Create curl handle
  h <- new_handle(
    username = ftp_user,
    password = password
  )
  
  # Try download
  tryCatch({
    curl_download(ftp_url, destfile = local_file, handle = h)
    message("✅ Download succeeded: ", local_file)
    return(local_file)
  }, error = function(e) {
    stop("❌ Download failed: ", e$message)
    return(NULL)
  })
}

# list files on ftp server ####
ftp_list_files <- function(ftp_server, ftp_user, password, basepath, path = "") {
  library(curl)
  
  # Build full FTP path
  ftp_url <- paste0(ftp_server, basepath, path)
  
  # Create curl handle
  h <- new_handle(
    username = ftp_user,
    password = password,
    dirlistonly = TRUE
  )
  
  res <- tryCatch({
    curl_fetch_memory(ftp_url, handle = h)
  }, error = function(e) {
    stop("❌ Listing failed: ", e$message)
    return(NULL)
  })
  
  if (!is.null(res)) {
    files <- rawToChar(res$content)
    files <- strsplit(files, "\r?\n")[[1]]
    files <- files[!(files %in% c(".",".."))]
    return(files)
  } else {
    return(character(0))
  }
}

# Delete file from FTP  ####
ftp_delete_file <- function(remote_file, ftp_server, ftp_user, ftp_password, basepath, verify = TRUE) {
  # Normalize basepath (ensure leading and trailing slash)
  if (!grepl("^/", basepath)) basepath <- paste0("/", basepath)
  if (!grepl("/$", basepath)) basepath <- paste0(basepath, "/")
  
  # Ensure FTP server has trailing slash
  if (!grepl("/$", ftp_server)) {
    ftp_server <- paste0(ftp_server, "/")
  }
  
  # Check if file exists before deletion
  c_files <- ftp_list_files(ftp_server, ftp_user, ftp_password, basepath)
  if (!(remote_file %in% c_files)) {
    message("⚠️ File not found on FTP: ", remote_file)
    return(FALSE)
  } else {
    message("📂 File exists on FTP: ", remote_file)
  }
  
  # Escape filename for shell command (handles spaces and special characters)
  safe_file <- shQuote(remote_file)
  safe_basepath <- shQuote(basepath)
  
  # Compose curl command
  cmd <- sprintf(
    'curl -s -u "%s:%s" -Q "CWD %s" -Q "DELE %s" "%s"',
    ftp_user, ftp_password, basepath, remote_file, ftp_server
  )
  
  message("🔧 Running command:\n", cmd)
  res <- tryCatch({
    output <- system(cmd, intern = TRUE)
    attr(output, "status") <- attr(output, "status") %||% 0
    output
  }, error = function(e) {
    message("❌ Deletion failed: ", e$message)
    return(NULL)
  })
  
  # Check if system call was successful
  if (!is.null(attr(res, "status")) && attr(res, "status") != 0) {
    message("❌ Deletion command failed with status: ", attr(res, "status"))
    return(FALSE)
  } else {
    message("✅ File deletion *reported* successful by curl.")
  }
  
  # Wait briefly for server sync
  Sys.sleep(0.05)
  
  # Post-deletion verification
  if (verify) {
    c_files_after <- ftp_list_files(ftp_server, ftp_user, ftp_password, basepath)
    if (remote_file %in% c_files_after) {
      message("❌ File still present after deletion attempt.")
      return(FALSE)
    } else {
      message("✅ File no longer present. Deletion confirmed.")
      return(TRUE)
    }
  }
  
  return(TRUE)
}

# check if app runs on shiny depoly server ####
is_shiny_server <- function() {
  Sys.getenv("SHINY_PORT", unset = "") != ""
}

### Function to render a single RMarkdown file ####
render_single_file <- function(input, output, envir) {
  rmarkdown::render(
    input = input,        # input file name
    output_file = output, # output file name
    output_dir = "output",# where to put the output file (directory) 
    envir = envir, 
    quiet = TRUE  # Suppress output for cleaner logs
  )
}

