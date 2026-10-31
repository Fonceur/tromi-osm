import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402

FIXTURE = str(Path(__file__).resolve().parent / "fixture.osm")
QC = {"code": "qc", "extract": "north-america/canada/quebec",
      "floors": {"fuel": 1, "visitor_centres": 1}}


def by_id(elements):
    return {(e["type"], e["id"]): e for e in elements}


class ReadElements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fuel, visitor_centres = build.read_elements(FIXTURE)
        cls.fuel = by_id(fuel)
        cls.visitor_centres = by_id(visitor_centres)

    def test_a_fuel_node_keeps_only_the_tags_the_app_reads(self):
        self.assertEqual(self.fuel[("node", 1)], {
            "type": "node", "id": 1, "lat": 45.5, "lon": -73.6,
            "tags": {"name": "Station Un", "car_wash": "yes", "shop": "convenience", "atm": "no",
                     "food": "yes", "cuisine": "sandwich", "compressed_air": "yes"},
        })

    def test_a_way_gets_its_bounding_box_centre_not_the_mean_of_its_nodes(self):
        way = self.fuel[("way", 1)]
        self.assertEqual((way["lat"], way["lon"]), (45.05, -73.15))

    def test_a_node_and_a_way_can_share_an_id(self):
        self.assertIn(("node", 1), self.fuel)
        self.assertIn(("way", 1), self.fuel)

    def test_a_multipolygon_spans_its_untagged_member_ways(self):
        relation = self.fuel[("relation", 1)]
        self.assertEqual((relation["lat"], relation["lon"]), (46.1, -72.2))
        self.assertEqual(relation["tags"], {"name": "Station Trois"})

    def test_a_relation_of_nodes_still_gets_a_centre(self):
        relation = self.fuel[("relation", 2)]
        self.assertEqual((relation["lat"], relation["lon"]), (47.1, -71.1))

    def test_a_node_sharing_a_member_ways_id_stays_out_of_the_box(self):
        relation = self.fuel[("relation", 3)]
        self.assertEqual((relation["lat"], relation["lon"]), (48.1, -70.1))

    def test_a_relation_with_no_located_member_is_left_out(self):
        self.assertNotIn(("relation", 4), self.fuel)

    def test_untagged_ways_and_member_nodes_are_not_elements(self):
        self.assertEqual(set(self.fuel), {
            ("node", 1), ("way", 1), ("relation", 1), ("relation", 2), ("relation", 3),
        })

    def test_every_visitor_centre_form_is_kept_with_only_its_name(self):
        self.assertEqual(set(self.visitor_centres), {
            ("node", 101), ("node", 102), ("node", 103), ("way", 104),
        })
        self.assertEqual(self.visitor_centres[("node", 101)]["tags"],
                         {"name": "Centre de découverte"})
        self.assertEqual(self.visitor_centres[("node", 103)]["tags"], {})

    def test_a_visitor_centre_way_gets_a_centre(self):
        way = self.visitor_centres[("way", 104)]
        self.assertEqual((way["lat"], way["lon"]), (49.4, -69.4))


class IsVisitorCentre(unittest.TestCase):
    def test_information_visitor_centre_needs_tourism_information(self):
        self.assertTrue(build.is_visitor_centre(
            {"tourism": "information", "information": "visitor_centre"}))
        self.assertFalse(build.is_visitor_centre({"information": "visitor_centre"}))

    def test_other_information_points_are_not_visitor_centres(self):
        self.assertFalse(build.is_visitor_centre(
            {"tourism": "information", "information": "guidepost"}))


class Centre(unittest.TestCase):
    def test_keeps_osm_precision(self):
        self.assertEqual(build.centre([(45.12345671, -73.1), (45.12345673, -73.1)]),
                         (45.1234567, -73.1))

    def test_a_single_point_is_its_own_centre(self):
        self.assertEqual(build.centre([(45.5, -73.6)]), (45.5, -73.6))


class BuildRegion(unittest.TestCase):
    def test_elements_come_out_sorted_and_the_order_is_stable(self):
        first = build.build_region(QC, FIXTURE)
        self.assertEqual([(e["type"], e["id"]) for e in first["fuel"]], [
            ("node", 1), ("way", 1), ("relation", 1), ("relation", 2), ("relation", 3),
        ])
        self.assertEqual(build.render(first), build.render(build.build_region(QC, FIXTURE)))

    def test_the_header_carries_attribution_and_the_region(self):
        payload = build.build_region(QC, FIXTURE)
        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["region"], "qc")
        self.assertEqual(payload["license"], "ODbL-1.0")
        self.assertEqual(payload["attribution"], "© OpenStreetMap contributors")

    def test_exactly_at_a_floor_passes(self):
        region = dict(QC, floors={"fuel": 5, "visitor_centres": 4})
        self.assertEqual(len(build.build_region(region, FIXTURE)["fuel"]), 5)

    def test_below_either_floor_is_rejected(self):
        for floors in ({"fuel": 6, "visitor_centres": 1}, {"fuel": 1, "visitor_centres": 5}):
            with self.assertRaises(build.BelowFloor):
                build.build_region(dict(QC, floors=floors), FIXTURE)

    def test_a_payload_the_schema_refuses_is_not_published(self):
        with mock.patch.object(build, "FUEL_TAGS", ("name", "brand")):
            with self.assertRaises(build.jsonschema.ValidationError):
                build.build_region(QC, FIXTURE)


class Render(unittest.TestCase):
    payload = {"schema": 1, "region": "qc", "fuel": [{"id": 1, "tags": {"name": "Été"}}, {"id": 2}],
               "visitor_centres": []}

    def test_parses_back_to_the_same_payload(self):
        self.assertEqual(json.loads(build.render(self.payload)), self.payload)

    def test_one_element_per_line_so_a_commit_shows_what_changed(self):
        lines = build.render(self.payload).splitlines()
        self.assertIn('{"id":1,"tags":{"name":"Été"}},', lines)
        self.assertIn('{"id":2}', lines)


class Main(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = mock.patch.object(build, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pbf_dir = self.root / "pbf"
        self.pbf_dir.mkdir()
        (self.pbf_dir / "quebec.osm").write_bytes(Path(FIXTURE).read_bytes())

    def regions(self, *regions):
        (self.root / "regions.json").write_text(json.dumps(regions), encoding="utf-8")

    def run_main(self):
        with mock.patch.object(build, "EXTENSION", "osm"):
            return build.main(["--pbf-dir", str(self.pbf_dir)])

    def test_writes_under_the_region_code(self):
        self.regions(QC)
        self.assertEqual(self.run_main(), 0)
        written = json.loads((self.root / "data" / "qc" / "amenities.json").read_text("utf-8"))
        self.assertEqual(len(written["fuel"]), 5)

    def test_below_a_floor_fails_and_leaves_the_last_good_file(self):
        good = self.root / "data" / "qc" / "amenities.json"
        good.parent.mkdir(parents=True)
        good.write_text("last good", encoding="utf-8")
        self.regions(dict(QC, floors={"fuel": 999, "visitor_centres": 1}))
        self.assertEqual(self.run_main(), 1)
        self.assertEqual(good.read_text(encoding="utf-8"), "last good")

    def test_one_failed_region_publishes_no_region(self):
        self.regions(QC, dict(QC, code="on", floors={"fuel": 999, "visitor_centres": 1}))
        self.assertEqual(self.run_main(), 1)
        self.assertFalse((self.root / "data").exists())


if __name__ == "__main__":
    unittest.main()
