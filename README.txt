AREAL PREKEY / UNIKEY
====================

PROJECT LAYOUT
--------------
data/electoral/source/          Original election workbooks and returns
data/electoral/normalized/      Precinct and voting-unit input snapshots
data/census_socioeconomic/acs/  ACS source tables, metadata, and catalogs
data/geodata/                   CEE boundaries, TIGER block groups, and layers
methodology/areal/              JOSS paper and pinned areal R package source
scripts/                        Build, export, and validation scripts
outputs/                        Apportioned analysis datasets and reports
map/site/                       Offline map application and its local data
tests/                          Focused Python and R tests

The local .runtime/ R package library and archive/legacy-map/ are retained
outside the published repository. Census credentials are stored outside this
project.

RUNNING THE MAP
---------------
Double-click map/start_map.bat to serve the offline map at
http://127.0.0.1:8768/. Python 3.9 or newer is required. The map and Leaflet
assets are local; no network basemap is used.

The Census Data tab displays direct ACS estimates on matching TIGER/Line
block-group boundaries for 2012, 2016, 2020, and 2024. The Combined tab layers
those direct block-group estimates over the electoral map. The map does not
display apportioned ACS estimates on electoral geographies; those remain in
outputs/ for analysis. The map's HTML, CSS, and behavior are unchanged by the
project reorganization.

DATA AND RESULTS
----------------
Original election returns and their data dictionary are in
data/electoral/source/Electionspuertorico.com/. The normalized 2016-2024
precinct and unit inputs are in data/electoral/normalized/. Colegio-level
returns are retained for analysis; join within a year using PREKEY, UNIKEY,
and COLKEY. The supplemental 2024 colegio plebiscite file adds details for
the same keys; do not append it as additional returns.

ACS source data are in data/census_socioeconomic/acs/. TIGER block groups,
CEE precinct and unit boundaries, and the vivienda_publica layer are in
data/geodata/. The Census availability inventory is in the ACS folder.

The eight complete apportionment datasets are outputs/{2012,2016,2020,2024}/
{prekey,unikey}/full_dataset.csv. PREKEY is precinct geography; PREKEY +
UNIKEY identifies voting units. Election returns are joined by key and never
spatially apportioned.

METHOD AND VALIDATION
---------------------
The pinned upstream `areal` repository is the authority for interpolation
mechanics: https://github.com/chris-prener/areal. Its repository, commit,
source copy, and workflow are recorded in project.json and
methodology/areal/UPSTREAM.txt. The JOSS paper is supporting context; the
pinned package source and vignette define the operational method.

The 2016-2024 R build calls the pinned package's native workflow. The 2012
Python backfill parses the ACS Summary File and implements the same
aw_intersect -> aw_total -> aw_weight -> aw_calculate -> aw_aggregate
operations; it is not a separate interpolation method. Both extensive SUM
and TOTAL estimates are kept because the package leaves that choice to the
analyst's overlap assumptions. Intensive outputs are area-weighted estimates,
not population-weighted reconstructions or exact target medians. Read
methodology/METHODOLOGY.txt before interpreting results.

The 2012 Python backfill uses the same formulas and has focused synthetic
tests. The 2016-2024 R build follows the pinned package methods and has
independent arithmetic checks in the code, but a full rebuild and native R
tests have not been completed. An earlier attempt reported an application-
control block; the latest focused test failed loading the bundled rlang.dll
("The specified procedure could not be found"). The root cause is unresolved.
Representative outputs were independently checked; that does not replace a
successful full rebuild.

BUILD AND TEST COMMANDS
-----------------------
Run commands from the project root:

  python -m unittest discover -s tests -v
  # Install only when rebuilding the 2012 backfill:
  python -m pip install -r scripts/requirements-2012.txt
  python scripts/build_2012.py
  python scripts/build_2012.py --refresh-metadata
  python scripts/build_census_map.py
  & 'C:\Program Files\R\R-4.6.2\bin\Rscript.exe' scripts\build.R
  & 'C:\Program Files\R\R-4.6.2\bin\Rscript.exe' scripts\export_map.R
  & 'C:\Program Files\R\R-4.6.2\bin\Rscript.exe' scripts\validate.R
  & 'C:\Program Files\R\R-4.6.2\bin\Rscript.exe' tests\test_method.R

Rscript is not currently on PATH in this environment, so the commands above
use its installed full path. The 2016-2024 R scripts use the local
.runtime/R-library. Resolve the current package DLL load failure before
rebuilding.
