.libPaths(c(normalizePath(".runtime/R-library"), .libPaths()))
suppressPackageStartupMessages(library(sf))
suppressPackageStartupMessages(library(dplyr))
suppressPackageStartupMessages(library(areal))
source("scripts/areal_helpers.R")
rectangle <- function(left, bottom, right, top) st_polygon(list(matrix(
  c(left, bottom, right, bottom, right, top, left, top, left, bottom),
  ncol = 2, byrow = TRUE
)))
source <- st_sf(GEOID = c("a", "b"), count = c(100, 200), rate = c(10, 30),
                 geometry = st_sfc(rectangle(0, 0, 10, 10), rectangle(10, 0, 20, 10), crs = 32161))
target <- st_sf(TARGET_ID = c("1", "2", "3"), geometry = st_sfc(
  rectangle(0, 0, 5, 10), rectangle(5, 0, 15, 10), rectangle(30, 0, 40, 10), crs = 32161
))
intersection <- aw_intersect(target, source = source[, "GEOID"], areaVar = "overlap_m2")
intersection <- intersection[as.numeric(intersection$overlap_m2) > 0, ]
expected <- list(sum = c(50, 250, NA), total = c(50, 150, NA), intensive = c(10, 20, NA))
for (mode in names(expected)) {
  field <- if (mode == "intensive") "rate" else "count"
  weighted <- native_weight(intersection, source, mode)
  result <- native_values(weighted, st_drop_geometry(source), target, field)
  stopifnot(isTRUE(all.equal(result[[field]], expected[[mode]])))
}
source$count[2] <- NA_real_
result <- native_values(native_weight(intersection, source, "sum"),
                         st_drop_geometry(source), target, "count")
stopifnot(result$count[1] == 50, is.na(result$count[2]), is.na(result$count[3]))
source$count <- c(0, 0)
result <- native_values(native_weight(intersection, source, "sum"),
                         st_drop_geometry(source), target, "count")
stopifnot(all(result$count[1:2] == 0), is.na(result$count[3]))
stopifnot(classify_estimate("Per capita income", "Estimate!!Total") == "intensive",
          classify_estimate("Aggregate household income", "Estimate!!Total") == "amount",
          classify_estimate("Gross rent as a percentage of income", "Estimate!!Total") == "count",
          classify_estimate("Median home value", "Estimate!!Total") == "intensive",
          classify_estimate("Lower Value Quartile (Dollars)", "Estimate!!Total") == "intensive",
          classify_estimate("Aggregate Number of Rooms", "Estimate!!Total") == "count",
          classify_estimate("Aggregate Number of Vehicles Available by Tenure", "Estimate!!Total") == "count")
rate <- list(numerator = c("n1", "n2"), denominator = "d")
values <- calculate_rate(data.frame(n1 = c(10, NA, 0), n2 = c(10, 10, 0), d = c(100, 100, 0)), rate)
stopifnot(values[1] == 20, is.na(values[2]), is.na(values[3]))
st_crs(target) <- 4326
stopifnot(!ar_validate(source, target, "count"))
cat("PASS: native SUM/TOTAL/intensive, missingness, true zero, no overlap, classification, derived rates and CRS rejection.\n")
