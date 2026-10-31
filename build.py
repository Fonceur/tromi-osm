#!/usr/bin/env python3
"""Publish what Tromi used to ask Overpass for, as one static file per region.

Each run downloads a region's Geofabrik extract, keeps what the app's two Overpass
queries returned, and writes `data/<region>/amenities.json`. A file under its
region's floors fails the run, which leaves the last good one in place.

Positions are bounding-box centres, as Overpass's `out center` gives, taken from a
relation's members rather than an assembled area: a mistagged multipolygon can't be
assembled, and Overpass still gave it a centre.

Keep the tag rules in step with the app's `buildFuelNodes` and `buildVisitorCentres`.
"""
import argparse
import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

import jsonschema
import osmium
from osmium.filter import IdFilter, KeyFilter, TagFilter

ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "schema" / "amenities.schema.json").read_text(encoding="utf-8"))
GEOFABRIK = "https://download.geofabrik.de"
USER_AGENT = "tromi-osm/1.0 (https://github.com/Fonceur/tromi-osm)"
EXTENSION = "osm.pbf"

SCHEMA_VERSION = 1
LICENSE = "ODbL-1.0"
ATTRIBUTION = "© OpenStreetMap contributors"

# Only what the app reads. The file stays pure OSM: matching to stations and parks,
# and anything joined with other data, happens on the device.
FUEL_TAGS = ("name", "car_wash", "shop", "atm", "food", "cuisine", "compressed_air")
VISITOR_CENTRE_TAGS = ("name",)

TYPE_ORDER = {"node": 0, "way": 1, "relation": 2}


class BelowFloor(Exception):
    """Too few objects for this region: publish nothing and fail so someone is told."""


def is_visitor_centre(tags):
    """As the app's query: `information=visitor_centre` needs `tourism=information`."""
    tourism, information = tags.get("tourism"), tags.get("information")
    return (tourism == "visitor_centre" or information == "office"
            or (tourism == "information" and information == "visitor_centre"))


def centre(points):
    """Bounding-box centre of [(lat, lon)], at OSM's 7 decimals."""
    lats, lons = zip(*points)
    return round((min(lats) + max(lats)) / 2, 7), round((min(lons) + max(lons)) / 2, 7)


def element(osm_type, osm_id, points, tags, keep):
    lat, lon = centre(points)
    return {"type": osm_type, "id": osm_id, "lat": lat, "lon": lon,
            "tags": {key: tags[key] for key in keep if key in tags}}


def node_points(node):
    return [(node.location.lat, node.location.lon)]


def way_points(way):
    return [(n.lat, n.lon) for n in way.nodes if n.location.valid()]


def nodes_and_ways(pbf):
    return osmium.FileProcessor(pbf, osmium.osm.NODE | osmium.osm.WAY).with_locations()


def read_nodes_and_ways(pbf):
    fuel, visitor_centres = [], []
    for obj in nodes_and_ways(pbf).with_filter(KeyFilter("amenity", "tourism", "information")):
        tags = dict(obj.tags)
        osm_type, points = ("node", node_points(obj)) if obj.is_node() else ("way", way_points(obj))
        if not points:
            continue
        if tags.get("amenity") == "fuel":
            fuel.append(element(osm_type, obj.id, points, tags, FUEL_TAGS))
        if is_visitor_centre(tags):
            visitor_centres.append(element(osm_type, obj.id, points, tags, VISITOR_CENTRE_TAGS))
    return fuel, visitor_centres


def read_fuel_relations(pbf):
    """Fuel relations only: the app's visitor-centre query covered nodes and ways."""
    relations = []
    for relation in (osmium.FileProcessor(pbf, osmium.osm.RELATION)
                     .with_filter(TagFilter(("amenity", "fuel")))):
        members = {(m.type, m.ref) for m in relation.members}
        relations.append((relation.id, dict(relation.tags), members))
    if not relations:
        return []

    # A node and a way can share an id, so match on the type as well as the filter's id.
    wanted = set().union(*(members for _, _, members in relations))
    points = {}
    for obj in nodes_and_ways(pbf).with_filter(IdFilter({ref for _, ref in wanted})):
        if obj.is_node() and ("n", obj.id) in wanted:
            points[("n", obj.id)] = node_points(obj)
        elif obj.is_way() and ("w", obj.id) in wanted:
            points[("w", obj.id)] = way_points(obj)

    located = []
    for osm_id, tags, members in relations:
        member_points = [p for member in members for p in points.get(member, [])]
        if member_points:
            located.append(element("relation", osm_id, member_points, tags, FUEL_TAGS))
    return located


def read_elements(pbf):
    """(fuel, visitor_centres) from an OSM file, each sorted so a run is reproducible."""
    fuel, visitor_centres = read_nodes_and_ways(pbf)
    fuel += read_fuel_relations(pbf)
    key = lambda e: (TYPE_ORDER[e["type"]], e["id"])  # noqa: E731
    return sorted(fuel, key=key), sorted(visitor_centres, key=key)


def build_region(region, pbf):
    fuel, visitor_centres = read_elements(pbf)
    counts = {"fuel": len(fuel), "visitor_centres": len(visitor_centres)}
    for name, floor in region["floors"].items():
        if counts[name] < floor:
            raise BelowFloor(f"{region['code']}: only {counts[name]} {name} (floor {floor})")
    payload = {
        "schema": SCHEMA_VERSION,
        "region": region["code"],
        "osm_timestamp": osmium.FileProcessor(pbf).header.get("osmosis_replication_timestamp", ""),
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "fuel": fuel,
        "visitor_centres": visitor_centres,
    }
    jsonschema.validate(payload, SCHEMA)
    return payload


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render(payload):
    """One element per line, so a week's commit shows what changed in OSM."""
    fields = [f"{dumps(key)}:{dumps(value)}" for key, value in payload.items()
              if not isinstance(value, list)]
    fields += [dumps(key) + ":[\n" + ",\n".join(dumps(e) for e in value) + "\n]"
               for key, value in payload.items() if isinstance(value, list)]
    return "{" + ",\n".join(fields) + "}\n"


def extract_name(region):
    return f"{Path(region['extract']).name}.{EXTENSION}"


def download(region, target):
    url = f"{GEOFABRIK}/{region['extract']}-latest.osm.pbf"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)


def build_all(regions, pbf_dir, fetch):
    """{code: file text}. One failure raises, so a bad run publishes no region."""
    built = {}
    for region in regions:
        pbf = pbf_dir / extract_name(region)
        if fetch:
            download(region, pbf)
        built[region["code"]] = render(build_region(region, str(pbf)))
        if fetch:
            pbf.unlink()
    return built


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pbf-dir", type=Path,
                        help="read extracts already in this directory instead of downloading")
    args = parser.parse_args(argv)

    regions = json.loads((ROOT / "regions.json").read_text(encoding="utf-8"))
    try:
        if args.pbf_dir:
            built = build_all(regions, args.pbf_dir, fetch=False)
        else:
            with tempfile.TemporaryDirectory() as scratch:
                built = build_all(regions, Path(scratch), fetch=True)
    except BelowFloor as e:
        print(f"::error::Nothing published: {e}")
        return 1

    for code, text in built.items():
        path = ROOT / "data" / code / "amenities.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"{path.relative_to(ROOT)}: {len(text.encode()):,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
