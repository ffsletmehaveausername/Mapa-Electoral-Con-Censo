source("scripts/build_curated_areal.R")
status <- system2("python", c("scripts/build_census_map.py", "--analysis-output", shQuote(output)))
if (!identical(status, 0L)) {
  stop("The native areal build completed, but the curated map export failed.")
}
jsonlite::write_json(list(run = basename(output)), "outputs/curated_latest.json", auto_unbox = TRUE)
cat("Primary curated analysis and map export complete:", normalizePath(output), "\n")
