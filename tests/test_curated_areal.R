.libPaths(c(normalizePath(".runtime/R-library"), .libPaths()))
suppressPackageStartupMessages(library(sf))
suppressPackageStartupMessages(library(dplyr))
suppressPackageStartupMessages(library(areal))
source("scripts/areal_helpers.R")
source("scripts/curated_areal_helpers.R")

config <- jsonlite::fromJSON("project.json")
verify_curated_areal_source(config)
checkout <- tempfile("areal-snapshot-")
dir.create(file.path(checkout, "methodology", "areal", "source", "R"), recursive = TRUE)
stopifnot(file.copy(c("methodology/areal/UPSTREAM.txt", "methodology/areal/SOURCE_HASHES.csv"),
                    file.path(checkout, "methodology", "areal")))
files <- list.files("methodology/areal/source/R", pattern = "^(aw_.*|ar_validate)\\.R$",
                    full.names = TRUE)
stopifnot(all(file.copy(files, file.path(checkout, "methodology", "areal", "source", "R"))))
verify_curated_areal_source(config, checkout)
cat("\n", file = file.path(checkout, "methodology", "areal", "source", "R", "aw_weight.R"),
    append = TRUE)
stopifnot(inherits(try(verify_curated_areal_source(config, checkout), silent = TRUE), "try-error"))

quality <- curated_quality(
  c(100, 100, 100, 0, NA, 100, 100),
  c(10, 30, 60, 10, 10, NA, 10),
  c(NA, NA, NA, NA, NA, NA, "2,500-"),
  rep(NA_character_, 7)
)
stopifnot(identical(quality$quality, c(
  "lower_uncertainty", "moderate_uncertainty", "high_uncertainty",
  "unassessable", "estimate_missing", "unassessable", "annotated_review"
)), is.na(quality$cv[4]), is.na(quality$cv[7]))

rectangle <- function(left, right) st_polygon(list(matrix(
  c(left, 0, right, 0, right, 10, left, 10, left, 0), ncol = 2, byrow = TRUE
)))
source <- st_sf(GEOID = c("a", "b"), count = c(100, NA_real_),
                rate = c(10, NA_real_),
                geometry = st_sfc(rectangle(0, 10), rectangle(10, 20), crs = 32161))
target <- st_sf(TARGET_ID = c("1", "2"), geometry = st_sfc(
  rectangle(0, 10), rectangle(30, 40), crs = 32161
))
intersection <- aw_intersect(target, source = source[, "GEOID"], areaVar = "overlap_m2")
stopifnot(any(as.numeric(intersection$overlap_m2) == 0))
for (mode in c("sum", "total", "intensive")) {
  field <- if (mode == "intensive") "rate" else "count"
  actual <- curated_values(native_weight(intersection, source, mode),
                           st_drop_geometry(source), target, field)
  reference <- if (mode == "intensive") {
    aw_interpolate(target, tid = "TARGET_ID", source = source, sid = "GEOID",
                   weight = "sum", intensive = field, output = "tibble")
  } else {
    aw_interpolate(target, tid = "TARGET_ID", source = source, sid = "GEOID",
                   weight = mode, extensive = field, output = "tibble")
  }
  stopifnot(same_areal_values(actual[[field]], reference[[field]]),
            is.na(actual[[field]][1]), is.na(actual[[field]][2]))
  positive <- intersection[as.numeric(intersection$overlap_m2) > 0, ]
  corrected <- curated_values(native_weight(positive, source, mode),
                              st_drop_geometry(source), target, field)
  stopifnot(corrected[[field]][1] == if (mode == "intensive") 10 else 100,
            is.na(corrected[[field]][2]))
}
stopifnot(!same_areal_values(NA_real_, NaN))
rates <- curated_rates()
lookup_rate <- function(id) rates[[match(id, vapply(rates, function(rate) rate$id, character(1)))]]
income <- lookup_rate("income_under_20k_pct")
earnings <- lookup_rate("earnings_under_10k_pct")
test_data <- as.data.frame(as.list(setNames(rep(0, length(curated_fields())), curated_fields())))
test_data$B19001_001E <- 10
test_data$B19001_002E <- 2
test_data$B19001_003E <- 1
test_data$B20001_001E <- 20
test_data$B20001_003E <- 2
test_data$B20001_024E <- 3
stopifnot(calculate_rate(test_data, income) == 30,
          calculate_rate(test_data, earnings) == 25)
for (i in 2:17) test_data[[sprintf("B19001_%03dE", i)]] <- 1
test_data$B19001_001E <- 16
for (i in c(3:22, 24:43)) test_data[[sprintf("B20001_%03dE", i)]] <- 1
test_data$B20001_001E <- 40
income_ids <- c("income_under_20k_pct", "income_20k_to_50k_pct",
                "income_50k_to_100k_pct", "income_100k_plus_pct")
earnings_ids <- c("earnings_under_10k_pct", "earnings_10k_to_25k_pct",
                  "earnings_25k_to_50k_pct", "earnings_50k_plus_pct")
stopifnot(isTRUE(all.equal(
  vapply(income_ids, function(id) calculate_rate(test_data, lookup_rate(id)), numeric(1)),
  setNames(100 * c(3, 6, 3, 4) / 16, income_ids)
)), isTRUE(all.equal(
  vapply(earnings_ids, function(id) calculate_rate(test_data, lookup_rate(id)), numeric(1)),
  setNames(100 * c(8, 12, 10, 10) / 40, earnings_ids)
)))
test_data$B20001_024E <- NA_real_
stopifnot(is.na(calculate_rate(test_data, earnings)))
test_data$B19001_001E <- 0
stopifnot(is.na(calculate_rate(test_data, income)))
cat("PASS: quality flags and exact upstream behavior for missing boundary contacts.\n")
