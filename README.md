# tromi-osm

The OpenStreetMap data behind [Tromi](https://play.google.com/store/apps/details?id=ca.romi.tromi),
a Québec gas-price app: fuel stations and visitor centres, published as a static file so the
app doesn't have to query the public Overpass API from every install.

This is OSM data only. Tromi matches it to stations and parks on the device, and no price or
other data is joined into it. The plan lives in the app's private repo.

## The file

```
https://fonceur.github.io/tromi-osm/data/qc/amenities.json
```

`.github/workflows/build.yml` runs `build.py` once a week. It downloads the
[Geofabrik extract](https://download.geofabrik.de/north-america/canada/quebec.html) for each
region in `regions.json`, keeps what the app's two Overpass queries returned, and commits the
result. Pages serves it with an ETag, so a client can revalidate with `If-None-Match`.

```json
{"schema":1,
"region":"qc",
"osm_timestamp":"2026-10-29T21:21:26Z",
"license":"ODbL-1.0",
"attribution":"© OpenStreetMap contributors",
"fuel":[
{"type":"node","id":123,"lat":45.5,"lon":-73.6,"tags":{"name":"Esso","shop":"convenience"}}
],
"visitor_centres":[
{"type":"way","id":456,"lat":46.3,"lon":-74.6,"tags":{"name":"Accueil"}}
]}
```

`schema/amenities.schema.json` pins the format, and a file that doesn't match it isn't
published.

- `osm_timestamp` is how recent the OSM data is, from the extract's header.
- `id` is unique only within a `type`: a node and a way can share one.
- `lat` and `lon` are the object's position for a node, and the centre of its bounding box for
  a way or relation, as Overpass's `out center` gives.
- `fuel` is `amenity=fuel` on nodes, ways and relations. It keeps only `name`, `car_wash`,
  `shop`, `atm`, `food`, `cuisine` and `compressed_air`.
- `visitor_centres` is nodes and ways with `tourism=visitor_centre`, `information=office`, or
  `tourism=information` with `information=visitor_centre`. It keeps only `name`.

**A failed run means the extract looks wrong:** under a region's floors in `regions.json`, or a
file the schema refuses. It publishes nothing, so Pages keeps serving the last good file.

These rules mirror the app's `buildFuelNodes` and `buildVisitorCentres`. If those change, change
them here too.

```bash
pip install -r requirements.txt
python -m unittest discover -s tests
python build.py                     # downloads each extract, about 1.2 GB for Québec
python build.py --pbf-dir <dir>     # reuses extracts already in <dir>, named as Geofabrik names them
```

## Adding a region

Add an entry to `regions.json`. It publishes to `data/<code>/amenities.json` on the next run.

Geofabrik's province outlines overlap slightly, so an object near a border can appear in two
regions' files. De-duplicate by `type` and `id` when combining them.

## Licence

The data in `data/` is © OpenStreetMap contributors and is available under the
[Open Database License](https://opendatacommons.org/licenses/odbl/1-0/); the full text is in
[`data/LICENSE`](data/LICENSE). See [openstreetmap.org/copyright](https://www.openstreetmap.org/copyright).
