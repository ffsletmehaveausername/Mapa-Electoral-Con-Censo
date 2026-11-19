map_dictionary <- list()
for (year in config$years) for (level in c("prekey", "unikey")) {
  output_dir <- file.path("outputs", year, level)
  map_dir <- file.path("map", "site", "data", year, level)
  dir.create(map_dir, recursive = TRUE, showWarnings = FALSE)
  built <- readRDS(file.path(output_dir, "build_data.rds"))
  target <- read_targets(config, year, level)
  map_geometry <- st_simplify(target, dTolerance = 10, preserveTopology = TRUE)
  map_geometry <- st_transform(map_geometry, 4326)
  st_write(map_geometry, file.path(map_dir, "geometry.geojson"),
           driver = "GeoJSON", delete_dsn = TRUE, quiet = TRUE)
  metrics <- list()
  catalog_entry <- function(id, label, topic, kind, table, fields, mode, description, universe) {
    list(id = id, label = label, topic = topic, kind = kind, table = table,
         fields = fields, mode = mode, description = description, universe = universe)
  }
  for (i in seq_len(nrow(built$dictionary))) {
    entry <- built$dictionary[i, ]
    variable <- entry$variable
    label <- paste(entry$table, sub("^Estimate!!", "", entry$label))
    mode <- if (entry$kind == "intensive") "intensive" else "extensive"
    fields <- if (mode == "intensive") {
      list(intensive = paste0(variable, "_intensive"))
    } else list(sum = paste0(variable, "_sum"), total = paste0(variable, "_total"))
    metrics[[length(metrics) + 1]] <- catalog_entry(
      variable, label, entry$topic, entry$kind, entry$table, fields, mode,
      entry$description, entry$universe
    )
  }
  for (definition in rates) {
    metrics[[length(metrics) + 1]] <- catalog_entry(
      paste0(definition$id, "_native"), paste(definition$label, "/ native intensive"),
      definition$topic, "source_percentage", "derived_rates",
      list(intensive = paste0(definition$id, "_intensive")), "intensive",
      "Area-weighted source percentages; not a ratio of allocated counts.", definition$universe
    )
    metrics[[length(metrics) + 1]] <- catalog_entry(
      paste0(definition$id, "_ratio"), paste(definition$label, "/ ratio of counts"),
      definition$topic, "derived_percentage", "derived_rates",
      list(sum = paste0(definition$id, "_from_counts_sum"),
           total = paste0(definition$id, "_from_counts_total")), "extensive",
      "Ratio of allocated counts; a different estimand from native intensive.", definition$universe
    )
  }
  vote_fields <- grep("__(GOB|COM|RPD|SND|RPA|SNA|ALC|PLE)_", names(built$full), value = TRUE)
  for (field in vote_fields) metrics[[length(metrics) + 1]] <- catalog_entry(
    field, field, "election_returns", "votes", "elections",
    list(direct = field), "direct",
    "Original election value joined by key, not spatially allocated.", "Election return field"
  )
  table_names <- unique(vapply(metrics, function(x) x$table, character(1)))
  for (table in table_names) {
    entries <- metrics[vapply(metrics, function(x) x$table == table, logical(1))]
    for (weight in c("sum", "total", "intensive", "direct")) {
      fields <- unique(unlist(lapply(entries, function(x) x$fields[[weight]]), use.names = FALSE))
      if (!length(fields)) next
      index <- match(target$TARGET_ID, built$full$TARGET_ID)
      values <- lapply(fields, function(field) unname(built$full[[field]][index]))
      names(values) <- fields
      save_json(list(ids = unname(target$TARGET_ID), values = values),
                file.path(map_dir, paste0(table, "_", weight, ".json")))
    }
  }
  for (i in seq_along(metrics)) {
    metric <- metrics[[i]]
    metric$coverage <- lapply(metric$fields, function(field) sum(!is.na(built$full[[field]])))
    metrics[[i]] <- metric
  }
  save_json(list(
    year = year, geography = level, polygons = nrow(target),
    election_rows = sum(built$keys$has_election),
    missing_polygon_rows = sum(!built$keys$has_polygon),
    boundary_plan = basename(built$boundary), metrics = metrics
  ), file.path(map_dir, "catalog.json"))
  map_dictionary[[paste(year, level)]] <- data.frame(
    year = year, geography = level,
    metric_count = length(metrics), polygons = nrow(target),
    missing_polygon_rows = sum(!built$keys$has_polygon)
  )
}
manifest_path <- "map/site/data/manifest.json"
existing_manifest <- if (file.exists(manifest_path)) {
  previous <- jsonlite::fromJSON(manifest_path)
  previous[!previous$year %in% config$years, , drop = FALSE]
} else data.frame()
save_json(bind_rows(existing_manifest, bind_rows(map_dictionary)), manifest_path)
file.copy("methodology/METHODOLOGY.txt", "map/site/methodology.txt", overwrite = TRUE)
