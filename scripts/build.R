.libPaths(c(normalizePath(".runtime/R-library"), .libPaths()))
suppressPackageStartupMessages(library(sf))
suppressPackageStartupMessages(library(dplyr))
suppressPackageStartupMessages(library(areal))
source("scripts/areal_helpers.R")
options(digits = 15)
config <- jsonlite::fromJSON("project.json")
stopifnot(identical(
  system2("git", c("-C", "methodology/areal/source", "rev-parse", "HEAD"), stdout = TRUE),
  config$areal_commit
))
dir.create("outputs", showWarnings = FALSE)
dir.create("map/site/data", recursive = TRUE, showWarnings = FALSE)
report <- c("AREAL PREKEY UNIKEY BUILD", paste("areal version:", packageVersion("areal")),
            paste("Pinned source:", config$areal_commit),
            "Native aw_intersect -> aw_total -> aw_weight -> aw_calculate -> aw_aggregate.",
            "All interpolation uses original geometry in EPSG:32161.",
            "Extensive SUM and TOTAL exported independently; intensive uses target-overlap-area weights.",
            "Missing values propagate on every positive-area overlap; no NA-to-zero replacement.")
record <- function(text) {
  report <<- c(report, text)
  cat(text, "\n")
  writeLines(report, "outputs/VALIDATION_REPORT.txt")
}
dictionary <- read.csv("data/census_socioeconomic/acs/apportionment_catalog.csv",
                       stringsAsFactors = FALSE, check.names = FALSE)
existing_metadata <- if (file.exists("outputs/source_dictionary.csv")) {
  previous <- read.csv("outputs/source_dictionary.csv", stringsAsFactors = FALSE,
                       check.names = FALSE)
  previous[!previous$year %in% config$years, , drop = FALSE]
} else dictionary[FALSE, , drop = FALSE]
existing_catalog <- if (file.exists("outputs/dataset_catalog.csv")) {
  previous <- read.csv("outputs/dataset_catalog.csv", stringsAsFactors = FALSE,
                       check.names = FALSE)
  previous[!previous$year %in% config$years, , drop = FALSE]
} else data.frame()
all_metadata <- list()
all_checks <- list()
dataset_catalog <- list()
rates <- rate_definitions()
for (year in config$years) {
  record(paste("YEAR", year))
  meta <- dictionary[dictionary$year == year, ]
  meta$kind <- mapply(classify_estimate, meta$description, meta$label, USE.NAMES = FALSE)
  stopifnot(!anyDuplicated(meta$variable))
  source <- st_transform(st_read(sprintf(
    "data/geodata/tiger_block_groups/tl_%s_72_bg/tl_%s_72_bg.shp", year, year
  ), quiet = TRUE)[, "GEOID"], config$analysis_crs)
  stopifnot(all(st_is_valid(source)), !anyDuplicated(source$GEOID))
  source_data <- data.frame(GEOID = source$GEOID)
  for (table in unique(meta$table)) {
    path <- file.path("data", "census_socioeconomic", "acs", year, "tables", paste0(table, ".csv"))
    data <- read.csv(path, colClasses = "character", check.names = FALSE, na.strings = "")
    stopifnot(setequal(source_data$GEOID, data$GEOID), !anyDuplicated(data$GEOID))
    fields <- meta$variable[meta$table == table]
    index <- match(source_data$GEOID, data$GEOID)
    for (field in fields) {
      values <- suppressWarnings(as.numeric(data[[field]][index]))
      stopifnot(!any(!is.na(data[[field]][index]) & is.na(values)))
      source_data[[field]] <- values
    }
  }
  source_rates <- lapply(rates, function(definition) calculate_rate(source_data, definition))
  names(source_rates) <- vapply(rates, function(x) x$id, character(1))
  for (field in names(source_rates)) source_data[[field]] <- source_rates[[field]]
  source$population_check <- source_data$B01003_001E
  kinds <- setNames(meta$kind, meta$variable)
  extensive_fields <- meta$variable[meta$kind %in% c("count", "amount")]
  intensive_fields <- c(meta$variable[meta$kind == "intensive"], names(source_rates))
  save_csv(meta, sprintf("outputs/%s/source_dictionary.csv", year))
  for (level in c("prekey", "unikey")) {
    record(paste(year, level, "building original-geometry overlay"))
    target <- read_targets(config, year, level)
    boundary <- attr(target, "boundary_path")
    dir <- file.path("outputs", year, level)
    dir.create(dir, recursive = TRUE, showWarnings = FALSE)
    slivers <- assert_nonoverlap(target)
    if (nrow(slivers) > 0L) {
      save_csv(slivers, file.path(dir, "sub_m2_overlap_audit.csv"))
    }
    stopifnot(ar_validate(source, target, "population_check"))
    save_csv(ar_validate(source, target, "population_check", verbose = TRUE),
             file.path(dir, "ar_validate.csv"))
    intersection <- aw_intersect(target[, "TARGET_ID"], source = source[, "GEOID"],
                                 areaVar = "overlap_m2")
    intersection <- intersection[as.numeric(intersection$overlap_m2) > 0, ]
    stopifnot(nrow(intersection) > 0)
    saveRDS(intersection, file.path(dir, "intersection.rds"))
    source_areas <- as.numeric(st_area(source))
    overlap_by_source <- tapply(as.numeric(intersection$overlap_m2), intersection$GEOID, sum)
    coverage <- unname(overlap_by_source[match(source$GEOID, names(overlap_by_source))])
    coverage[is.na(coverage)] <- 0
    coverage <- data.frame(
      GEOID = source$GEOID, source_area_m2 = source_areas,
      covered_area_m2 = coverage, coverage_fraction = coverage / source_areas,
      source_population = source_data$B01003_001E
    )
    save_csv(coverage, file.path(dir, "source_coverage.csv"))
    elections <- read_elections(year, level)
    keys <- full_join(
      st_drop_geometry(target),
      elections[, intersect(c("TARGET_ID", "PREKEY", "UNIKEY"), names(elections)), drop = FALSE],
      by = intersect(c("TARGET_ID", "PREKEY", "UNIKEY"), names(elections))
    )
    keys$has_polygon <- keys$TARGET_ID %in% target$TARGET_ID
    keys$has_election <- keys$TARGET_ID %in% elections$TARGET_ID
    keys$geometry_status <- ifelse(keys$has_polygon, "polygon_available", "no_polygon_SES_missing")
    keys$election_year <- year
    keys$acs_period_start <- year - 4L
    keys$acs_period_end <- year
    keys$boundary_plan <- basename(boundary)
    keys$target_area_m2 <- as.numeric(st_area(target))[match(keys$TARGET_ID, target$TARGET_ID)]
    target_overlap <- tapply(as.numeric(intersection$overlap_m2), intersection$TARGET_ID, sum)
    keys$source_overlap_fraction <- unname(target_overlap[keys$TARGET_ID]) / keys$target_area_m2
    keys$has_positive_overlap <- keys$TARGET_ID %in% intersection$TARGET_ID
    keys$geometry_status[keys$has_polygon & !keys$has_positive_overlap] <- "polygon_without_source_overlap"
    joined <- left_join(keys, elections[, setdiff(names(elections), c("PREKEY", "UNIKEY")), drop = FALSE],
                         by = "TARGET_ID")
    full <- joined
    mode_results <- list()
    for (mode in c("sum", "total", "intensive")) {
      record(sprintf("%s %s %s: native package interpolation", year, level, mode))
      weighted <- native_weight(intersection, source, mode)
      stopifnot(all(is.finite(weighted$weight)), all(weighted$weight > 0))
      save_csv(st_drop_geometry(weighted), file.path(dir, paste0("weights_", mode, ".csv")))
      if (mode == "sum") {
        sums <- tapply(weighted$weight, weighted$GEOID, sum)
      } else if (mode == "intensive") {
        sums <- tapply(weighted$weight, weighted$TARGET_ID, sum)
      } else sums <- NULL
      if (!is.null(sums)) stopifnot(max(abs(sums - 1)) < 1e-10)
      fields <- if (mode == "intensive") intensive_fields else extensive_fields
      result <- native_values(weighted, source_data, target, fields)
      mode_results[[mode]] <- result
      # Omnibus reference on one real variable verifies the reused manual overlay.
      check_field <- if (mode == "intensive") "B19013_001E" else "B01003_001E"
      check_source <- source[, "GEOID"]
      check_source[[check_field]] <- source_data[[check_field]]
      reference <- if (mode == "intensive") {
        aw_interpolate(target[, "TARGET_ID"], tid = "TARGET_ID", source = check_source,
                        sid = "GEOID", weight = "sum", intensive = check_field, output = "tibble")
      } else aw_interpolate(
        target[, "TARGET_ID"], tid = "TARGET_ID", source = check_source,
        sid = "GEOID", weight = mode, extensive = check_field, output = "tibble"
      )
      stopifnot(isTRUE(all.equal(result[[check_field]],
        reference[[check_field]][match(result$TARGET_ID, reference$TARGET_ID)], tolerance = 1e-9)))
      if (mode != "intensive") {
        for (field in fields) {
          present <- !is.na(source_data[[field]])
          observed_target <- sum(result[[field]], na.rm = TRUE)
          if (all(present)) {
            expected <- if (mode == "sum") {
              sum(source_data[[field]][coverage$covered_area_m2 > 0])
            } else sum(source_data[[field]] * coverage$coverage_fraction)
            stopifnot(abs(observed_target - expected) < max(1, abs(expected)) * 1e-8)
          } else expected <- NA_real_
          all_checks[[length(all_checks) + 1]] <- data.frame(
            year = year, level = level, mode = mode, variable = field,
            source_missing = sum(!present), target_missing = sum(is.na(result[[field]])),
            source_total_observed = sum(source_data[[field]], na.rm = TRUE),
            target_total_observed = observed_target,
            expected_complete_source_total = expected
          )
        }
        for (kind in c("count", "amount")) {
          selected <- meta$variable[meta$kind == kind]
          save_csv(left_join(keys, result[, c("TARGET_ID", selected), drop = FALSE], by = "TARGET_ID"),
                   file.path(dir, paste0(if (kind == "count") "counts_" else "amounts_", mode, ".csv")))
        }
        ratios <- data.frame(TARGET_ID = result$TARGET_ID)
        for (definition in rates) ratios[[definition$id]] <- calculate_rate(result, definition)
        save_csv(left_join(keys, ratios, by = "TARGET_ID"),
                 file.path(dir, paste0("ratios_from_counts_", mode, ".csv")))
        names(ratios)[-1] <- paste0(names(ratios)[-1], "_from_counts_", mode)
        full <- left_join(full, ratios, by = "TARGET_ID")
      } else save_csv(left_join(keys, result, by = "TARGET_ID"), file.path(dir, "intensive_estimates.csv"))
      names(result)[-1] <- paste0(names(result)[-1], "_", mode)
      full <- left_join(full, result, by = "TARGET_ID")
    }
    save_csv(full, file.path(dir, "full_dataset.csv"))
    dataset_catalog[[paste(year, level, sep = "_")]] <- data.frame(
      year = year,
      acs_period_start = year - 4L,
      acs_period_end = year,
      key_level = level,
      geography_level = if (level == "prekey") "precinct" else "voting_unit",
      dataset_path = file.path("outputs", year, level, "full_dataset.csv"),
      rows = nrow(keys),
      polygon_rows = sum(keys$has_polygon),
      election_rows = sum(keys$has_election),
      no_polygon_rows = sum(!keys$has_polygon),
      no_election_rows = sum(!keys$has_election),
      stringsAsFactors = FALSE
    )
    saveRDS(list(keys = keys, full = full, results = mode_results, source_data = source_data,
                 dictionary = meta, boundary = boundary), file.path(dir, "build_data.rds"))
    record(sprintf(
      "PASS %s %s: %s polygons; %s election keys; %s missing polygons; %s positive intersections; native/manual agreement.",
      year, level, nrow(target), nrow(elections), sum(!keys$has_polygon), nrow(intersection)
    ))
  }
  all_metadata[[as.character(year)]] <- meta
}
save_csv(bind_rows(existing_catalog, bind_rows(dataset_catalog)), "outputs/dataset_catalog.csv")
save_csv(bind_rows(existing_metadata, bind_rows(all_metadata)), "outputs/source_dictionary.csv")
save_csv(bind_rows(all_checks), "outputs/conservation_missingness.csv")
save_csv(bind_rows(lapply(rates, function(x) data.frame(
  id = x$id, label = x$label, topic = x$topic, universe = x$universe,
  numerator = paste(x$numerator, collapse = ";"), denominator = x$denominator,
  native_intensive = "target-overlap-area weighted source percentage",
  derived_ratio = "100 * allocated numerator / allocated denominator; separate estimand"
))), "outputs/rate_dictionary.csv")
source("export_map.R")
inputs <- sort(list.files("data", recursive = TRUE, full.names = TRUE))
save_csv(data.frame(path = inputs, bytes = file.info(inputs)$size,
  sha256 = vapply(inputs, function(path) digest::digest(file = path, algo = "sha256"), character(1))),
  "outputs/input_manifest.csv")
writeLines(capture.output(sessionInfo()), "outputs/R_SESSION.txt")
record("BUILD PASSED. Both geographies and all requested years; extensive and intensive datasets remain separate.")
