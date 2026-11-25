options(digits = 17)
.libPaths(c(normalizePath(".runtime/R-library"), .libPaths()))
suppressPackageStartupMessages(library(sf))
suppressPackageStartupMessages(library(dplyr))
suppressPackageStartupMessages(library(areal))
source("scripts/areal_helpers.R")
source("scripts/curated_areal_helpers.R")

config <- jsonlite::fromJSON("project.json")
verify_curated_areal_source(config)

fields <- curated_fields()
intensive <- curated_spec()$intensive
extensive <- setdiff(fields, intensive)
rates <- curated_rates()
years <- c(2012L, 2016L, 2020L, 2024L)
dictionary <- read.csv("outputs/source_dictionary.csv", check.names = FALSE,
                       stringsAsFactors = FALSE)
metadata <- dictionary[dictionary$year %in% years & dictionary$variable %in% fields, ]
if (nrow(metadata) != length(fields) * length(years) ||
    anyDuplicated(metadata[, c("year", "variable")])) {
  stop("The source dictionary does not contain exactly one entry per selected field and year.")
}

output <- file.path("outputs", paste0("curated_areal_", format(Sys.time(), "%Y%m%d_%H%M%S"),
                                      "_", Sys.getpid()))
if (!dir.create(output, recursive = FALSE)) stop("Could not create a new output directory.")
writeLines("INCOMPLETE: the build has not finished.", file.path(output, "STATUS.txt"))
if (!file.copy("methodology/CURATED_AREAL.txt", file.path(output, "README.txt"))) {
  stop("Could not copy curated analysis documentation.")
}
curated_write_csv(metadata, file.path(output, "source_dictionary.csv"))
writeLines(c(paste("areal commit:", config$areal_commit),
             paste("areal version:", packageVersion("areal")),
             paste("R version:", getRversion()),
             trimws(capture.output(sf::sf_extSoftVersion()), which = "right")),
           file.path(output, "runtime.txt"))

read_selected <- function(year) {
  path <- sprintf("data/census_socioeconomic/acs/curated/census_%s.csv", year)
  headers <- names(read.csv(path, nrows = 0, check.names = FALSE, fileEncoding = "UTF-8-BOM"))
  selected <- unique(c("GEOID", fields, sub("E$", "M", fields),
                       paste0(fields, "A"), paste0(sub("E$", "M", fields), "A")))
  if (!all(c("GEOID", fields, sub("E$", "M", fields)) %in% headers)) {
    stop(paste("Missing selected estimate or MOE column in", path))
  }
  classes <- ifelse(headers %in% selected, "character", "NULL")
  data <- read.csv(path, colClasses = classes, check.names = FALSE,
                   na.strings = c("", "."), fileEncoding = "UTF-8-BOM")
  if (anyNA(data$GEOID) || anyDuplicated(data$GEOID)) stop("Invalid Census GEOIDs.")
  for (field in c(fields, sub("E$", "M", fields))) {
    raw <- data[[field]]
    values <- suppressWarnings(as.numeric(raw))
    if (any(!is.na(raw) & (is.na(values) | !is.finite(values)))) {
      stop(paste("Non-numeric Census value:", year, field))
    }
    values[values %in% c(-1, -222222222, -333333333, -555555555,
                         -666666666, -888888888, -999999999)] <- NA_real_
    if (any(values < 0, na.rm = TRUE)) stop(paste("Unexpected negative value:", year, field))
    data[[field]] <- values
  }
  for (field in fields) {
    ea <- paste0(field, "A")
    ma <- paste0(sub("E$", "M", field), "A")
    if (!ea %in% names(data)) data[[ea]] <- NA_character_
    if (!ma %in% names(data)) data[[ma]] <- NA_character_
    quality <- curated_quality(data[[field]], data[[sub("E$", "M", field)]],
                               data[[ea]], data[[ma]])
    data[[paste0(field, "_cv")]] <- quality$cv
    data[[paste0(field, "_quality")]] <- quality$quality
  }
  for (definition in rates) {
    data[[definition$id]] <- calculate_rate(data, definition)
    data[[paste0(definition$id, "_quality")]] <- "unassessable_derived"
  }
  data
}

read_2012_units <- function() {
  path <- "data/geodata/unit_boundaries/Uni_CEE_2012/Uni_CEE_2012.shp"
  target <- st_transform(st_read(path, quiet = TRUE), config$analysis_crs)
  invalid <- which(!st_is_valid(target))
  original_area <- as.numeric(st_area(target[invalid, ]))
  repaired <- st_make_valid(target[invalid, ])
  repaired_area <- as.numeric(st_area(repaired))
  if (any(original_area <= 0) || any(abs(repaired_area - original_area) / original_area > 1e-8)) {
    stop("2012 geometry repair materially changed an original area.")
  }
  repair_audit <- data.frame(PREKEY = key_format(target$Precinto2[invalid], 3),
                             UNIKEY = key_format(target$Unidad[invalid]),
                             original_area_m2 = original_area, repaired_area_m2 = repaired_area)
  if (length(invalid)) target[invalid, ] <- repaired
  cat("2012 unit geometries repaired:", length(invalid), "\n")
  target$PREKEY <- key_format(target$Precinto2, 3)
  target$UNIKEY <- key_format(target$Unidad)
  target$TARGET_ID <- paste(target$PREKEY, target$UNIKEY, sep = "-")
  target <- target[, c("TARGET_ID", "PREKEY", "UNIKEY")] %>%
    group_by(TARGET_ID, PREKEY, UNIKEY) %>% summarize(.groups = "drop")
  stopifnot(!anyDuplicated(target$TARGET_ID), all(st_is_valid(target)),
            !any(st_is_empty(target)), all(as.numeric(st_area(target)) > 0))
  attr(target, "boundary_path") <- path
  attr(target, "repair_audit") <- repair_audit
  target
}

checks <- list()
for (year in years) {
  cat("YEAR", year, "\n")
  year_dir <- file.path(output, as.character(year))
  dir.create(year_dir)
  data <- read_selected(year)
  curated_write_csv(data, file.path(year_dir, "block_groups.csv"))
  source <- st_transform(st_read(sprintf(
    "data/geodata/tiger_block_groups/tl_%s_72_bg/tl_%s_72_bg.shp", year, year
  ), quiet = TRUE)[, "GEOID"], config$analysis_crs)
  stopifnot(all(st_is_valid(source)), !anyDuplicated(source$GEOID),
            setequal(source$GEOID, data$GEOID))
  source <- source[match(data$GEOID, source$GEOID), ]
  stopifnot(identical(source$GEOID, data$GEOID))
  saveRDS(source, file.path(year_dir, "source_geometry.rds"))
  for (level in c("prekey", "unikey")) {
    cat(year, level, "overlay\n")
    target <- if (year == 2012L && level == "unikey") read_2012_units() else {
      read_targets(config, if (year == 2012L) 2016L else year, level)
    }
    dir <- file.path(year_dir, level)
    dir.create(dir)
    writeLines(attr(target, "boundary_path"), file.path(dir, "boundary_path.txt"))
    if (!is.null(attr(target, "repair_audit"))) {
      curated_write_csv(attr(target, "repair_audit"), file.path(dir, "geometry_repair_audit.csv"))
    }
    saveRDS(target, file.path(dir, "target_geometry.rds"))
    intersection <- aw_intersect(target[, "TARGET_ID"], source = source,
                                 areaVar = "overlap_m2")
    if (!nrow(intersection)) stop("No source-target intersections.")
    saveRDS(intersection, file.path(dir, "intersection.rds"))
    weight_tables <- list()
    for (mode in c("sum", "total", "intensive")) {
      cat(year, level, mode, "\n")
      weighted <- native_weight(intersection, source, mode)
      weight_tables[[mode]] <- st_drop_geometry(weighted)
      mode_fields <- if (mode == "intensive") intensive else extensive
      result <- curated_values(weighted, data, target, mode_fields)
      check_field <- if (mode == "intensive") "B19013_001E" else "B01003_001E"
      reference_source <- source
      reference_source[[check_field]] <- data[[check_field]]
      reference <- if (mode == "intensive") {
        aw_interpolate(target[, "TARGET_ID"], tid = "TARGET_ID", source = reference_source,
                       sid = "GEOID", weight = "sum", intensive = check_field, output = "tibble")
      } else {
        aw_interpolate(target[, "TARGET_ID"], tid = "TARGET_ID", source = reference_source,
                       sid = "GEOID", weight = mode, extensive = check_field, output = "tibble")
      }
      if (!same_areal_values(result[[check_field]],
                             reference[[check_field]][match(result$TARGET_ID, reference$TARGET_ID)])) {
        stop(paste("Mismatch with direct areal::aw_interpolate:", year, level, mode))
      }
      checks[[length(checks) + 1L]] <- data.frame(
        year = year, level = level, mode = mode, variable = check_field,
        targets = nrow(result), missing = sum(is.na(result[[check_field]])),
        nan = sum(is.nan(result[[check_field]])), direct_areal_match = TRUE
      )
      if (mode != "intensive") {
        for (definition in rates) result[[definition$id]] <- calculate_rate(result, definition)
      }
      result <- left_join(st_drop_geometry(target), result, by = "TARGET_ID")
      curated_write_csv(result, file.path(dir, paste0(mode, ".csv")))
      saveRDS(result, file.path(dir, paste0(mode, ".rds")))
    }
    saveRDS(weight_tables, file.path(dir, "weights.rds"))
  }
}
curated_write_csv(bind_rows(checks), file.path(output, "direct_areal_checks.csv"))
curated_border_correction(output)
curated_target_key_audit(output)
curated_target_source_quality(output)
curated_population_summary(output)
writeLines("COMPLETE: all direct areal checks passed.", file.path(output, "STATUS.txt"))
cat("COMPLETE:", normalizePath(output), "\n")
