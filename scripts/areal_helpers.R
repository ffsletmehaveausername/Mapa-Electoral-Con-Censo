save_csv <- function(data, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  write.csv(data, path, row.names = FALSE, na = "", fileEncoding = "UTF-8")
}
save_json <- function(data, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  jsonlite::write_json(data, path, auto_unbox = TRUE, na = "null", digits = NA,
                       dataframe = "rows")
}
key_format <- function(values, width = 0L) {
  numbers <- suppressWarnings(as.integer(as.character(values)))
  if (anyNA(numbers)) stop("Missing or nonnumeric electoral key.", call. = FALSE)
  if (width > 0L) sprintf(paste0("%0", width, "d"), numbers) else as.character(numbers)
}
read_elections <- function(year, level) {
  path <- sprintf("data/electoral/normalized/%s_%s.csv",
                  if (level == "prekey") "precincts" else "units", year)
  data <- read.csv(path, colClasses = "character", check.names = FALSE, na.strings = "")
  data$PREKEY <- key_format(data$PREKEY, 3)
  if (level == "unikey") data$UNIKEY <- key_format(data$UNIKEY)
  data$TARGET_ID <- if (level == "prekey") data$PREKEY else {
    paste(data$PREKEY, data$UNIKEY, sep = "-")
  }
  stopifnot(!anyDuplicated(data$TARGET_ID))
  for (field in setdiff(names(data), c("TARGET_ID", "PREKEY", "UNIKEY"))) {
    converted <- suppressWarnings(as.numeric(data[[field]]))
    if (any(!is.na(data[[field]]) & is.na(converted))) {
      stop(paste("Unexpected election value in", field), call. = FALSE)
    }
    data[[field]] <- converted
  }
  data
}

read_targets <- function(config, year, level) {
  spec <- if (level == "prekey") {
    config$precinct_boundaries[config$precinct_boundaries$year == year, ]
  } else config$unit_boundaries[config$unit_boundaries$year == year, ]
  data <- sf::st_transform(sf::st_read(spec$path, quiet = TRUE), config$analysis_crs)
  if (any(!sf::st_is_valid(data)) || any(sf::st_is_empty(data))) {
    stop(paste(year, level, "contains invalid or empty geometry."), call. = FALSE)
  }
  data$PREKEY <- key_format(data[[spec$prekey_field]], 3)
  if (level == "prekey") {
    data$municipio <- trimws(as.character(data[[spec$municipio_field]]))
    data$TARGET_ID <- data$PREKEY
    data <- data[, c("TARGET_ID", "PREKEY", "municipio")]
    stopifnot(!anyDuplicated(data$TARGET_ID))
  } else {
    data$UNIKEY <- key_format(data[[spec$unikey_field]])
    data$TARGET_ID <- paste(data$PREKEY, data$UNIKEY, sep = "-")
    data <- data[, c("TARGET_ID", "PREKEY", "UNIKEY")]
    data <- data %>%
      dplyr::group_by(TARGET_ID, PREKEY, UNIKEY) %>%
      dplyr::summarize(.groups = "drop")
    pre_spec <- config$precinct_boundaries[config$precinct_boundaries$year == year, ]
    precincts <- sf::st_read(pre_spec$path, quiet = TRUE)
    names <- setNames(trimws(as.character(precincts[[pre_spec$municipio_field]])),
                      key_format(precincts[[pre_spec$prekey_field]], 3))
    data$municipio <- unname(names[data$PREKEY])
    if (anyNA(data$municipio)) stop("Unit has no corresponding precinct municipality.")
  }
  stopifnot(!anyDuplicated(data$TARGET_ID), all(sf::st_is_valid(data)),
            all(as.numeric(sf::st_area(data)) > 0))
  attr(data, "boundary_path") <- spec$path
  data
}

classify_estimate <- function(description, label) {
  text <- paste(description, label)
  if (grepl("median|mean|average|per capita|gini|quartile|quintile.*(limit|share)|shares? of.*income",
            text, ignore.case = TRUE)) return("intensive")
  if (grepl("aggregate number", text, ignore.case = TRUE)) return("count")
  if (grepl("aggregate", text, ignore.case = TRUE)) return("amount")
  "count"
}

rate_definitions <- function() {
  rate <- function(id, label, topic, numerator, denominator, universe) {
    list(id = id, label = label, topic = topic, numerator = numerator,
         denominator = denominator, universe = universe)
  }
  definitions <- list(
    rate("owner_occupied_pct", "Owner-occupied housing (%)", "housing",
         "B25003_002E", "B25003_001E", "Occupied housing units"),
    rate("vacant_housing_pct", "Vacant housing units (%)", "housing",
         "B25002_003E", "B25002_001E", "Housing units"),
    rate("income_100k_plus_pct", "Households with income $100,000+ (%)", "income_and_earnings_related",
         sprintf("B19001_%03dE", 14:17), "B19001_001E", "Households"),
    rate("below_poverty_pct", "Income below poverty level (%)", "poverty",
         c("C17002_002E", "C17002_003E"), "C17002_001E",
         "Population for whom poverty status is determined")
  )
  education <- list(less_than_high_school = 2:16, high_school_or_ged = 17:18,
                    some_college_or_associates = 19:21, bachelors = 22,
                    graduate_or_professional = 23:25, bachelors_or_higher = 22:25)
  for (id in names(education)) {
    definitions[[length(definitions) + 1]] <- rate(
      paste0(id, "_pct"), paste(gsub("_", " ", id), "(%, age 25+)"), "education",
      sprintf("B15003_%03dE", education[[id]]), "B15003_001E", "Population 25 years and over"
    )
  }
  for (spec in list(
    c("B19055", "social_security", "Households with Social Security income (%)"),
    c("B19056", "ssi", "Households with SSI income (%)"),
    c("B19057", "public_assistance", "Households with public assistance income (%)"),
    c("B19058", "assistance_or_snap", "Households with assistance OR food stamps (%)"),
    c("B22010", "snap", "Households receiving food stamps/SNAP (%)")
  )) {
    definitions[[length(definitions) + 1]] <- rate(
      paste0(spec[2], "_pct"), spec[3], "government_aid_and_retirement",
      paste0(spec[1], "_002E"), paste0(spec[1], "_001E"), "Households"
    )
  }
  definitions
}

calculate_rate <- function(data, definition) {
  needed <- c(definition$numerator, definition$denominator)
  if (!all(needed %in% names(data))) return(rep(NA_real_, nrow(data)))
  numerator <- rowSums(as.matrix(data[, definition$numerator, drop = FALSE]))
  denominator <- data[[definition$denominator]]
  ifelse(!is.na(denominator) & denominator > 0, 100 * numerator / denominator, NA_real_)
}

assert_nonoverlap <- function(target, tolerance_m2 = 1) {
  neighbors <- sf::st_intersects(target)
  overlaps <- list()
  for (i in seq_len(nrow(target))) for (j in neighbors[[i]][neighbors[[i]] > i]) {
    area <- sum(as.numeric(sf::st_area(suppressWarnings(sf::st_intersection(
      sf::st_geometry(target[i, ]), sf::st_geometry(target[j, ])
    )))))
    if (area > tolerance_m2) {
      stop(sprintf("Targets %s and %s overlap by %.3f m2.",
                   target$TARGET_ID[i], target$TARGET_ID[j], area), call. = FALSE)
    }
    if (area > 0) overlaps[[length(overlaps) + 1]] <- data.frame(
      left = target$TARGET_ID[i], right = target$TARGET_ID[j], overlap_m2 = area
    )
  }
  dplyr::bind_rows(overlaps)
}

native_weight <- function(intersection, source, mode) {
  weighted <- if (mode == "intensive") {
    areal::aw_total(intersection, source = source, id = "TARGET_ID",
                    areaVar = "overlap_m2", totalVar = "denominator_m2",
                    type = "intensive", weight = "sum")
  } else areal::aw_total(
    intersection, source = source, id = "GEOID", areaVar = "overlap_m2",
    totalVar = "denominator_m2", type = "extensive", weight = mode
  )
  areal::aw_weight(weighted, areaVar = "overlap_m2", totalVar = "denominator_m2",
                   areaWeight = "weight")
}

native_values <- function(weighted, source_data, target, fields) {
  target_ids <- target[, "TARGET_ID"]
  output <- data.frame(TARGET_ID = target$TARGET_ID)
  source_index <- match(weighted$GEOID, source_data$GEOID)
  numeric_weight <- as.numeric(weighted$weight)
  target_index <- match(weighted$TARGET_ID, target$TARGET_ID)
  for (i in seq_along(fields)) {
    field <- fields[i]
    weighted$value <- source_data[[field]][source_index]
    calculated <- areal::aw_calculate(weighted, value = "value", areaWeight = "weight")
    result <- areal::aw_aggregate(calculated, target = target_ids, tid = "TARGET_ID",
                                  interVar = "value")
    values <- result$value[match(output$TARGET_ID, result$TARGET_ID)]
    # Independent arithmetic checks every field, including strict NA propagation.
    expected <- rowsum(source_data[[field]][source_index] * numeric_weight,
                       target_index, reorder = FALSE, na.rm = FALSE)
    manual <- rep(NA_real_, nrow(target))
    manual[as.integer(rownames(expected))] <- as.numeric(expected)
    stopifnot(isTRUE(all.equal(values, manual, tolerance = 1e-10, check.attributes = FALSE)))
    output[[field]] <- values
    if (i %% 250 == 0) cat(sprintf("  native calculate/aggregate: %s/%s fields\n", i, length(fields)))
  }
  output
}
