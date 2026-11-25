verify_curated_areal_source <- function(config, root = ".") {
  directory <- file.path(root, "methodology", "areal")
  vendored <- file.path(directory, "source")
  pin <- c(paste0("repository=", config$areal_repository),
           paste0("commit=", config$areal_commit))
  if (!identical(readLines(file.path(directory, "UPSTREAM.txt"), warn = FALSE), pin)) {
    stop("Recorded areal pin does not match project configuration.")
  }
  if (file.exists(file.path(vendored, ".git")) || dir.exists(file.path(vendored, ".git"))) {
    commit <- system2("git", c("-C", shQuote(vendored), "rev-parse", "HEAD"), stdout = TRUE)
    if (!identical(commit, config$areal_commit)) stop("Vendored areal commit does not match the pin.")
    dirty <- system2("git", c("-C", shQuote(vendored), "status", "--porcelain",
                              "--untracked-files=no"), stdout = TRUE)
    if (length(dirty)) stop("Vendored areal checkout has tracked modifications.")
  }
  hashes <- read.csv(file.path(directory, "SOURCE_HASHES.csv"), stringsAsFactors = FALSE)
  source_dir <- file.path(vendored, "R")
  actual_files <- list.files(source_dir, pattern = "^(aw_.*|ar_validate)\\.R$")
  if (!setequal(hashes$path, paste0("R/", actual_files)) || anyDuplicated(hashes$path)) {
    stop("Pinned areal source hash manifest does not match its workflow files.")
  }
  for (i in seq_len(nrow(hashes))) {
    path <- file.path(vendored, hashes$path[i])
    bytes <- readBin(path, "raw", n = file.info(path)$size)
    if (length(bytes)) {
      bytes <- bytes[!(bytes == as.raw(13) & c(bytes[-1], as.raw(0)) == as.raw(10))]
    }
    if (!identical(digest::digest(bytes, algo = "sha256", serialize = FALSE), hashes$sha256[i])) {
      stop(paste("Pinned areal source hash mismatch:", hashes$path[i]))
    }
  }
  upstream <- new.env(parent = asNamespace("areal"))
  for (file in file.path(source_dir, actual_files)) sys.source(file, envir = upstream)
  for (name in ls(upstream)) {
    expected <- get(name, envir = upstream)
    if (is.function(expected)) {
      actual <- get0(name, envir = asNamespace("areal"), inherits = FALSE)
      if (!is.function(actual) || !identical(formals(expected), formals(actual)) ||
          !identical(body(expected), body(actual))) {
        stop(paste("Installed areal differs from pinned source:", name))
      }
    }
  }
}

curated_spec <- function() {
  spec <- jsonlite::fromJSON("data/census_socioeconomic/acs/curated_indicators.json")
  if (anyDuplicated(spec$estimates) || anyDuplicated(spec$rates) ||
      !all(spec$intensive %in% spec$estimates) ||
      !all(spec$currency %in% spec$estimates)) {
    stop("Invalid curated indicator configuration.")
  }
  spec
}

curated_fields <- function() {
  curated_spec()$estimates
}

curated_rates <- function() {
  rates <- rate_definitions()
  selected <- curated_spec()$rates
  rates <- rates[vapply(rates, function(x) x$id %in% selected, logical(1))]
  if (!setequal(vapply(rates, function(x) x$id, character(1)), selected)) {
    stop("Curated rate configuration does not match the defined rates.")
  }
  rates
}

curated_quality <- function(estimate, moe, estimate_annotation, moe_annotation) {
  stopifnot(length(estimate) == length(moe),
            length(estimate) == length(estimate_annotation),
            length(estimate) == length(moe_annotation))
  annotated <- (!is.na(estimate_annotation) & nzchar(estimate_annotation)) |
    (!is.na(moe_annotation) & nzchar(moe_annotation))
  status <- rep("unassessable", length(estimate))
  status[is.na(estimate)] <- "estimate_missing"
  status[!is.na(estimate) & annotated] <- "annotated_review"
  assessable <- !is.na(estimate) & estimate > 0 & !is.na(moe) & moe >= 0 & !annotated
  cv <- rep(NA_real_, length(estimate))
  cv[assessable] <- moe[assessable] / (1.645 * estimate[assessable])
  status[assessable & cv < 0.15] <- "lower_uncertainty"
  status[assessable & cv >= 0.15 & cv < 0.30] <- "moderate_uncertainty"
  status[assessable & cv >= 0.30] <- "high_uncertainty"
  data.frame(cv = cv, quality = status)
}

curated_values <- function(weighted, source_data, target, fields) {
  result <- data.frame(TARGET_ID = target$TARGET_ID)
  source_index <- match(weighted$GEOID, source_data$GEOID)
  if (anyNA(source_index)) stop("Overlay contains an unknown source GEOID.")
  for (field in fields) {
    weighted$value <- source_data[[field]][source_index]
    calculated <- areal::aw_calculate(weighted, value = "value", areaWeight = "weight")
    aggregated <- areal::aw_aggregate(calculated, target = target[, "TARGET_ID"],
                                      tid = "TARGET_ID", interVar = "value")
    result[[field]] <- aggregated$value[match(result$TARGET_ID, aggregated$TARGET_ID)]
  }
  result
}

same_areal_values <- function(x, y) {
  identical(is.na(x), is.na(y)) && identical(is.nan(x), is.nan(y)) &&
    isTRUE(all.equal(x[!is.na(x)], y[!is.na(y)], tolerance = 1e-10,
                     check.attributes = FALSE))
}

curated_write_csv <- function(data, path) {
  for (field in names(data)) {
    if (is.numeric(data[[field]]) && any(is.nan(data[[field]]))) {
      values <- data[[field]]
      data[[field]] <- ifelse(is.nan(values), "NaN",
                              ifelse(is.na(values), NA_character_, as.character(values)))
    }
  }
  write.csv(data, path, row.names = FALSE, na = "", fileEncoding = "UTF-8")
}

curated_border_correction <- function(output) {
  fields <- curated_fields()
  rates <- curated_rates()
  checks <- list()
  for (year in c(2012L, 2016L, 2020L, 2024L)) {
    year_dir <- file.path(output, as.character(year))
    path <- file.path(year_dir, "block_groups.csv")
    headers <- names(read.csv(path, nrows = 0, check.names = FALSE))
    classes <- ifelse(headers == "GEOID", "character",
                      ifelse(headers %in% fields, "numeric", "NULL"))
    data <- read.csv(path, colClasses = classes, check.names = FALSE, na.strings = "")
    source <- readRDS(file.path(year_dir, "source_geometry.rds"))
    for (level in c("prekey", "unikey")) {
      dir <- file.path(year_dir, level)
      paths <- file.path(dir, c("sum_positive_area.csv", "total_positive_area.csv",
                                "intensive_positive_area.csv", "coverage.csv"))
      if (any(file.exists(paths))) stop(paste("Correction outputs already exist in", dir))
      target <- readRDS(file.path(dir, "target_geometry.rds"))
      intersection <- readRDS(file.path(dir, "intersection.rds"))
      positive <- intersection[as.numeric(intersection$overlap_m2) > 0, ]
      if (!nrow(positive)) stop("No positive-area intersections.")
      covered <- tapply(as.numeric(positive$overlap_m2), positive$GEOID, sum)
      areas <- as.numeric(sf::st_area(source))
      overlap <- unname(covered[match(source$GEOID, names(covered))])
      overlap[is.na(overlap)] <- 0
      coverage <- data.frame(GEOID = source$GEOID, source_area_m2 = areas,
                             overlap_area_m2 = overlap, overlap_fraction = overlap / areas)
      curated_write_csv(coverage, file.path(dir, "coverage.csv"))
      for (mode in c("sum", "total", "intensive")) {
        cat(year, level, mode, "positive-area correction\n")
        selected <- if (mode == "intensive") curated_spec()$intensive else {
          setdiff(fields, curated_spec()$intensive)
        }
        weighted <- native_weight(positive, source, mode)
        corrected <- curated_values(weighted, data, target, selected)
        exact <- readRDS(file.path(dir, paste0(mode, ".rds")))
        for (field in selected) {
          old <- exact[[field]][match(corrected$TARGET_ID, exact$TARGET_ID)]
          new <- corrected[[field]]
          both <- !is.na(old) & !is.na(new)
          if (any(!is.finite(old[both])) || any(!is.finite(new[both])) ||
              !isTRUE(all.equal(old[both], new[both], tolerance = 1e-10)) ||
              any(!is.na(old) & is.na(new))) {
            stop(paste("Unexpected correction effect:", year, level, mode, field))
          }
          checks[[length(checks) + 1L]] <- data.frame(
            year = year, level = level, mode = mode, variable = field,
            exact_missing = sum(is.na(old)), corrected_missing = sum(is.na(new)),
            resolved = sum(is.na(old) & !is.na(new)),
            zero_area_intersections_removed = nrow(intersection) - nrow(positive)
          )
        }
        if (mode != "intensive") {
          for (definition in rates) corrected[[definition$id]] <- calculate_rate(corrected, definition)
        }
        corrected <- dplyr::left_join(sf::st_drop_geometry(target), corrected, by = "TARGET_ID")
        curated_write_csv(corrected, file.path(dir, paste0(mode, "_positive_area.csv")))
        saveRDS(corrected, file.path(dir, paste0(mode, "_positive_area.rds")))
      }
    }
  }
  curated_write_csv(dplyr::bind_rows(checks), file.path(output, "border_correction_checks.csv"))
}

curated_target_key_audit <- function(output) {
  for (year in c(2012L, 2016L, 2020L, 2024L)) {
    for (level in c("prekey", "unikey")) {
      dir <- file.path(output, as.character(year), level)
      path <- file.path(dir, "target_key_audit.csv")
      if (file.exists(path)) stop(paste("Target key audit already exists:", path))
      snapshot <- file.path("outputs", year, level, "full_dataset.csv")
      headers <- names(read.csv(snapshot, nrows = 0, check.names = FALSE))
      needed <- c("TARGET_ID", "PREKEY", "has_polygon", "has_election")
      if (level == "unikey") needed <- c(needed, "UNIKEY")
      if (!all(needed %in% headers)) stop(paste("Missing election snapshot keys:", snapshot))
      classes <- ifelse(headers %in% needed, "character", "NULL")
      keys <- read.csv(snapshot, colClasses = classes, check.names = FALSE, na.strings = "")
      target <- readRDS(file.path(dir, "target_geometry.rds"))
      if (anyDuplicated(keys$TARGET_ID) || !all(target$TARGET_ID %in% keys$TARGET_ID) ||
          any((keys$has_polygon == "TRUE") != (keys$TARGET_ID %in% target$TARGET_ID))) {
        stop(paste("Election snapshot and target geometries disagree:", year, level))
      }
      names(keys)[names(keys) == "has_election"] <- "has_election_in_existing_snapshot"
      curated_write_csv(keys, path)
    }
  }
}

curated_target_source_quality <- function(output) {
  fields <- curated_fields()
  categories <- c("lower_uncertainty", "moderate_uncertainty", "high_uncertainty",
                  "unassessable", "annotated_review", "estimate_missing")
  for (year in c(2012L, 2016L, 2020L, 2024L)) {
    year_dir <- file.path(output, as.character(year))
    path <- file.path(year_dir, "block_groups.csv")
    headers <- names(read.csv(path, nrows = 0, check.names = FALSE))
    selected <- c("GEOID", paste0(fields, "_quality"))
    if (!all(selected %in% headers)) stop(paste("Missing source quality columns:", path))
    data <- read.csv(path, colClasses = ifelse(headers %in% selected, "character", "NULL"),
                     check.names = FALSE)
    for (level in c("prekey", "unikey")) {
      dir <- file.path(year_dir, level)
      path <- file.path(dir, "target_source_quality.csv")
      if (file.exists(path)) stop(paste("Target source quality already exists:", path))
      target <- readRDS(file.path(dir, "target_geometry.rds"))
      intersection <- readRDS(file.path(dir, "intersection.rds"))
      positive <- intersection[as.numeric(intersection$overlap_m2) > 0, ]
      links <- unique(sf::st_drop_geometry(positive)[, c("TARGET_ID", "GEOID")])
      target_index <- match(links$TARGET_ID, target$TARGET_ID)
      source_index <- match(links$GEOID, data$GEOID)
      if (anyNA(target_index) || anyNA(source_index)) stop("Unknown ID in source quality overlay.")
      output_rows <- lapply(fields, function(field) {
        quality <- data[[paste0(field, "_quality")]][source_index]
        if (anyNA(quality) || any(!quality %in% categories)) stop("Unknown source quality flag.")
        result <- data.frame(TARGET_ID = target$TARGET_ID, variable = field,
                             overlapping_source_groups = tabulate(target_index, nrow(target)))
        for (category in categories) {
          result[[paste0(category, "_sources")]] <- tabulate(
            target_index[quality == category], nrow(target)
          )
        }
        result
      })
      curated_write_csv(dplyr::bind_rows(output_rows), path)
    }
  }
}

curated_population_summary <- function(output) {
  path <- file.path(output, "population_coverage_summary.csv")
  if (file.exists(path)) stop(paste("Population summary already exists:", path))
  summaries <- list()
  for (year in c(2012L, 2016L, 2020L, 2024L)) {
    year_dir <- file.path(output, as.character(year))
    headers <- names(read.csv(file.path(year_dir, "block_groups.csv"), nrows = 0,
                              check.names = FALSE))
    classes <- ifelse(headers == "GEOID", "character",
                      ifelse(headers == "B01003_001E", "numeric", "NULL"))
    source <- read.csv(file.path(year_dir, "block_groups.csv"), colClasses = classes,
                       check.names = FALSE, na.strings = "")
    for (level in c("prekey", "unikey")) {
      dir <- file.path(year_dir, level)
      coverage <- read.csv(file.path(dir, "coverage.csv"), colClasses = c(GEOID = "character"))
      coverage <- coverage[match(source$GEOID, coverage$GEOID), ]
      if (anyNA(coverage$GEOID)) stop("Missing source population coverage.")
      targets <- lapply(c("sum", "total"), function(mode) {
        readRDS(file.path(dir, paste0(mode, ".rds")))$B01003_001E
      })
      source_missing <- sum(is.na(source$B01003_001E))
      sum_missing <- sum(is.na(targets[[1]]))
      covered_population <- sum(source$B01003_001E[coverage$overlap_fraction > 0], na.rm = TRUE)
      sum_population <- sum(targets[[1]], na.rm = TRUE)
      if (!source_missing && !sum_missing &&
          !isTRUE(all.equal(covered_population, sum_population, tolerance = 1e-10))) {
        stop(paste("SUM population conservation failed:", year, level))
      }
      summaries[[length(summaries) + 1L]] <- data.frame(
        year = year, level = level, source_population_missing = source_missing,
        source_population = sum(source$B01003_001E, na.rm = TRUE),
        source_groups_no_overlap = sum(coverage$overlap_fraction == 0),
        population_no_overlap = sum(source$B01003_001E[coverage$overlap_fraction == 0], na.rm = TRUE),
        inhabited_groups_under_half_area_covered = sum(
          coverage$overlap_fraction < 0.5 & source$B01003_001E > 0, na.rm = TRUE
        ),
        sum_population = sum_population, sum_targets_missing = sum_missing,
        total_population = sum(targets[[2]], na.rm = TRUE),
        total_targets_missing = sum(is.na(targets[[2]]))
      )
    }
  }
  curated_write_csv(dplyr::bind_rows(summaries), path)
}
