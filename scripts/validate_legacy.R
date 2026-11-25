.libPaths(c(normalizePath(".runtime/R-library"), .libPaths()))
suppressPackageStartupMessages(library(sf))
suppressPackageStartupMessages(library(dplyr))
suppressPackageStartupMessages(library(areal))
source("scripts/areal_helpers.R")
config <- jsonlite::fromJSON("project.json")
rate_specs <- rate_definitions()
report <- c("PERSISTENT AREAL DATASET VALIDATION",
            paste("UTC:", format(Sys.time(), tz = "UTC", usetz = TRUE)))
all_meta <- read.csv("outputs/source_dictionary.csv", stringsAsFactors = FALSE)
original_catalog <- read.csv("data/census_socioeconomic/acs/apportionment_catalog.csv", stringsAsFactors = FALSE)
dataset_catalog <- read.csv("outputs/dataset_catalog.csv", stringsAsFactors = FALSE)
all_meta <- all_meta[all_meta$year %in% config$years, , drop = FALSE]
dataset_catalog <- dataset_catalog[dataset_catalog$year %in% config$years, , drop = FALSE]
stopifnot(setequal(paste(all_meta$year, all_meta$variable),
                   paste(original_catalog$year, original_catalog$variable)),
          nrow(dataset_catalog) == length(config$years) * 2L,
          !anyDuplicated(paste(dataset_catalog$year, dataset_catalog$key_level)),
          all(file.exists(dataset_catalog$dataset_path)))
numeric_equal <- function(left, right) {
  isTRUE(all.equal(as.numeric(left), as.numeric(right), tolerance = 1e-10,
                   check.attributes = FALSE))
}
for (year in config$years) for (level in c("prekey", "unikey")) {
  dir <- file.path("outputs", year, level)
  built <- readRDS(file.path(dir, "build_data.rds"))
  data <- read.csv(file.path(dir, "full_dataset.csv"), colClasses = "character",
                   check.names = FALSE, na.strings = "")
  catalog_row <- dataset_catalog[
    dataset_catalog$year == year & dataset_catalog$key_level == level, ]
  stopifnot(nrow(catalog_row) == 1L,
            catalog_row$rows == nrow(data),
            catalog_row$polygon_rows == sum(built$keys$has_polygon),
            catalog_row$election_rows == sum(built$keys$has_election),
            catalog_row$no_polygon_rows == sum(!built$keys$has_polygon),
            catalog_row$no_election_rows == sum(!built$keys$has_election))
  stopifnot(identical(data$TARGET_ID, built$full$TARGET_ID), !anyDuplicated(data$TARGET_ID))
  target <- read_targets(config, year, level)
  elections <- read_elections(year, level)
  stopifnot(setequal(data$TARGET_ID, union(target$TARGET_ID, elections$TARGET_ID)))
  for (field in names(built$full)) {
    if (is.numeric(built$full[[field]])) {
      stopifnot(numeric_equal(data[[field]], built$full[[field]]))
    } else stopifnot(identical(as.character(data[[field]]), as.character(built$full[[field]])))
  }
  for (field in setdiff(names(elections), c("TARGET_ID", "PREKEY", "UNIKEY"))) {
    actual <- built$full[[field]][match(elections$TARGET_ID, built$full$TARGET_ID)]
    stopifnot(numeric_equal(actual, elections[[field]]))
  }
  source_data <- built$source_data
  source_geometry <- st_transform(st_read(sprintf(
    "data/geodata/tiger_block_groups/tl_%s_72_bg/tl_%s_72_bg.shp", year, year
  ), quiet = TRUE)[, "GEOID"], config$analysis_crs)
  stopifnot(identical(source_geometry$GEOID, source_data$GEOID))
  complete_source <- st_sf(source_data, geometry = st_geometry(source_geometry))
  stopifnot(ar_validate(complete_source, target, setdiff(names(source_data), "GEOID")))
  save_csv(ar_validate(complete_source, target, setdiff(names(source_data), "GEOID"),
                        verbose = TRUE), file.path(dir, "ar_validate_all_variables.csv"))
  intersection <- readRDS(file.path(dir, "intersection.rds"))
  source_index <- match(intersection$GEOID, source_data$GEOID)
  target_index <- match(intersection$TARGET_ID, target$TARGET_ID)
  stopifnot(!anyNA(source_index), !anyNA(target_index))
  for (mode in c("sum", "total", "intensive")) {
    weights <- read.csv(file.path(dir, paste0("weights_", mode, ".csv")),
                        colClasses = c(TARGET_ID = "character", GEOID = "character"))
    stopifnot(identical(weights$TARGET_ID, intersection$TARGET_ID),
              identical(weights$GEOID, intersection$GEOID))
    native <- st_drop_geometry(native_weight(intersection, source_geometry, mode))
    stopifnot(numeric_equal(weights$weight, native$weight))
    result <- built$results[[mode]]
    fields <- setdiff(names(result), "TARGET_ID")
    for (field in fields) {
      values <- source_data[[field]][source_index] * weights$weight
      sums <- rowsum(values, target_index, reorder = FALSE, na.rm = FALSE)
      expected <- rep(NA_real_, nrow(target))
      expected[as.integer(rownames(sums))] <- as.numeric(sums)
      stopifnot(numeric_equal(result[[field]], expected))
      output_field <- paste0(field, "_", mode)
      joined_expected <- expected[match(data$TARGET_ID, target$TARGET_ID)]
      stopifnot(numeric_equal(data[[output_field]], joined_expected))
    }
  }
  missing <- !built$keys$has_polygon
  ses_fields <- grep("_(sum|total|intensive)$", names(data), value = TRUE)
  stopifnot(all(is.na(as.matrix(data[missing, ses_fields, drop = FALSE]))))
  for (kind in c("count", "amount")) for (mode in c("sum", "total")) {
    path <- file.path(dir, paste0(if (kind == "count") "counts_" else "amounts_", mode, ".csv"))
    separate <- read.csv(path, colClasses = "character", check.names = FALSE, na.strings = "")
    stopifnot(identical(separate$TARGET_ID, data$TARGET_ID))
    fields <- built$dictionary$variable[built$dictionary$kind == kind]
    stopifnot(setequal(setdiff(names(separate), names(built$keys)), fields))
    for (field in fields) {
      stopifnot(numeric_equal(separate[[field]], data[[paste0(field, "_", mode)]]))
    }
  }
  intensive <- read.csv(file.path(dir, "intensive_estimates.csv"), colClasses = "character",
                         check.names = FALSE, na.strings = "")
  stopifnot(identical(intensive$TARGET_ID, data$TARGET_ID))
  for (field in setdiff(names(intensive), names(built$keys))) {
    stopifnot(numeric_equal(intensive[[field]], data[[paste0(field, "_intensive")]]))
  }
  for (mode in c("sum", "total")) for (definition in rate_specs) {
    expected <- calculate_rate(built$results[[mode]], definition)
    actual <- data[[paste0(definition$id, "_from_counts_", mode)]]
    expected <- expected[match(data$TARGET_ID, target$TARGET_ID)]
    stopifnot(numeric_equal(actual, expected),
              all(is.na(expected) | (expected >= -1e-8 & expected <= 100 + 1e-8)))
  }
  map_dir <- file.path("map", "site", "data", year, level)
  map_geometry <- st_read(file.path(map_dir, "geometry.geojson"), quiet = TRUE)
  stopifnot(setequal(map_geometry$TARGET_ID, target$TARGET_ID))
  catalog <- jsonlite::fromJSON(file.path(map_dir, "catalog.json"), simplifyVector = FALSE)
  metric_ids <- vapply(catalog$metrics, function(x) x$id, character(1))
  stopifnot(!anyDuplicated(metric_ids),
            all(built$dictionary$variable %in% metric_ids),
            catalog$missing_polygon_rows == sum(missing))
  payload_cache <- new.env(parent = emptyenv())
  for (metric in catalog$metrics) for (mode in names(metric$fields)) {
    payload_file <- paste0(metric$table, "_", mode, ".json")
    if (!exists(payload_file, envir = payload_cache, inherits = FALSE)) {
      assign(payload_file, jsonlite::fromJSON(file.path(map_dir, payload_file),
        simplifyVector = FALSE), envir = payload_cache)
    }
    payload <- get(payload_file, envir = payload_cache, inherits = FALSE)
    ids <- unlist(payload$ids, use.names = FALSE)
    field <- metric$fields[[mode]]
    values <- vapply(payload$values[[field]], function(x) {
      if (is.null(x)) NA_real_ else as.numeric(x)
    }, numeric(1))
    stopifnot(length(values) == length(ids),
              numeric_equal(values, built$full[[field]][match(ids, built$full$TARGET_ID)]))
  }
  report <- c(report, sprintf(
    "PASS %s %s: %s full rows; %s polygons; %s no-polygon rows; every native-weight SES value, CSV and map payload checked.",
    year, level, nrow(data), nrow(target), sum(missing)
  ))
  cat(tail(report, 1), "\n")
}
manifest <- read.csv("outputs/input_manifest.csv")
stopifnot(all(file.exists(manifest$path)),
  all(vapply(seq_len(nrow(manifest)), function(i) {
    digest::digest(file = manifest$path[i], algo = "sha256") == manifest$sha256[i]
  }, logical(1))))
report <- c(report, "PASS: every copied input hash matches build manifest.",
            "PASS: election values unchanged; missing geography remains missing; separate method datasets consistent.",
            "PERSISTENT VALIDATION PASSED.")
writeLines(report, "outputs/PERSISTENT_VALIDATION.txt")
cat("PERSISTENT VALIDATION PASSED.\n")
