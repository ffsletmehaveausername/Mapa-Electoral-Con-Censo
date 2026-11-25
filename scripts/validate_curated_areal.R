.libPaths(c(normalizePath(".runtime/R-library"), .libPaths()))
suppressPackageStartupMessages(library(sf))
suppressPackageStartupMessages(library(dplyr))
suppressPackageStartupMessages(library(areal))
source("scripts/areal_helpers.R")
source("scripts/curated_areal_helpers.R")

args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 1L) stop("Supply at most one curated output directory.")
if (length(args)) {
  output <- args[1]
} else {
  pointer <- jsonlite::fromJSON("outputs/curated_latest.json")
  if (length(pointer$run) != 1L || basename(pointer$run) != pointer$run ||
      pointer$run %in% c("", ".", "..")) stop("Invalid curated output pointer.")
  output <- file.path("outputs", pointer$run)
}
status <- readLines(file.path(output, "STATUS.txt"), warn = FALSE)
if (length(status) != 1L || !startsWith(status, "COMPLETE:")) stop("Incomplete curated build.")
checks <- read.csv(file.path(output, "direct_areal_checks.csv"), stringsAsFactors = FALSE)
if (nrow(checks) != 24L || any(!checks$direct_areal_match) ||
    anyDuplicated(checks[, c("year", "level", "mode")])) {
  stop("Direct areal checks are incomplete.")
}
fields <- curated_fields()
intensive <- curated_spec()$intensive
for (year in c(2012L, 2016L, 2020L, 2024L)) {
  year_dir <- file.path(output, year)
  data <- read.csv(file.path(year_dir, "block_groups.csv"), colClasses = c(GEOID = "character"),
                   check.names = FALSE, na.strings = "")
  source <- readRDS(file.path(year_dir, "source_geometry.rds"))
  if (anyDuplicated(data$GEOID) || !identical(data$GEOID, source$GEOID)) {
    stop(paste("Source GEOIDs do not match:", year))
  }
  for (field in fields) {
    quality <- curated_quality(data[[field]], data[[sub("E$", "M", field)]],
                               data[[paste0(field, "A")]],
                               data[[paste0(sub("E$", "M", field), "A")]])
    if (!identical(quality$quality, data[[paste0(field, "_quality")]]) ||
        !same_areal_values(quality$cv, data[[paste0(field, "_cv")]])) {
      stop(paste("Source quality mismatch:", year, field))
    }
  }
  for (level in c("prekey", "unikey")) {
    dir <- file.path(year_dir, level)
    target <- readRDS(file.path(dir, "target_geometry.rds"))
    intersection <- readRDS(file.path(dir, "intersection.rds"))
    stored_weights <- readRDS(file.path(dir, "weights.rds"))
    for (mode in c("sum", "total", "intensive")) {
      weighted <- native_weight(intersection, source, mode)
      stored <- stored_weights[[mode]]
      if (!identical(stored$GEOID, weighted$GEOID) ||
          !identical(stored$TARGET_ID, weighted$TARGET_ID) ||
          !same_areal_values(as.numeric(stored$weight), as.numeric(weighted$weight))) {
        stop(paste("Stored weight mismatch:", year, level, mode))
      }
      selected <- if (mode == "intensive") intensive else setdiff(fields, intensive)
      recomputed <- curated_values(weighted, data, target, selected)
      result <- readRDS(file.path(dir, paste0(mode, ".rds")))
      if (!identical(recomputed$TARGET_ID, result$TARGET_ID)) stop("Target IDs changed.")
      for (field in selected) {
        if (!same_areal_values(recomputed[[field]], result[[field]])) {
          stop(paste("Stored target estimate mismatch:", year, level, mode, field))
        }
      }
      if (mode != "intensive") for (rate in curated_rates()) {
        if (!same_areal_values(calculate_rate(recomputed, rate), result[[rate$id]])) {
          stop(paste("Stored target rate mismatch:", year, level, mode, rate$id))
        }
      }
      csv <- read.csv(file.path(dir, paste0(mode, ".csv")), colClasses = "character",
                      check.names = FALSE, na.strings = "")
      if (!identical(csv$TARGET_ID, result$TARGET_ID)) stop("Target CSV IDs changed.")
      for (field in c(selected, if (mode != "intensive") vapply(
        curated_rates(), function(rate) rate$id, character(1)
      ))) {
        values <- suppressWarnings(as.numeric(csv[[field]]))
        if (any(!is.na(csv[[field]]) & is.na(values) & !is.nan(values)) ||
            !same_areal_values(values, result[[field]])) {
          stop(paste("Target CSV mismatch:", year, level, mode, field))
        }
      }
    }
    cat("PASS", year, level, ": source quality, native weights and every target value\n")
  }
}
cat("PERSISTENT CURATED AREAL VALIDATION PASSED\n")
