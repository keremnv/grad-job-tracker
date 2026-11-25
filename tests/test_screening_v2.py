"""Unit and benchmark evaluation tests for candidate deterministic screener v2."""
import json
from pathlib import Path
import unittest

from jobwatch.filtering import RULES_VERSION, RULES_VERSION_V1, RULES_VERSION_V2, screen, screen_v1, screen_v2

ROOT = Path(__file__).resolve().parents[1]
EVAL_PATH = ROOT / "evaluation" / "expanded_corpus_eval.json"


def _evaluate(fn, cases):
    correct = 0
    fp_priority = 0
    fn_priority = 0
    fp_included = 0
    fn_included = 0
    mismatches = []
    for c in cases:
        inp = {
            "title": c["title"],
            "location": c.get("location", "London, United Kingdom"),
            "country": c.get("country", "GB"),
            "countries": c.get("countries", []),
            "department": c.get("department", ""),
            "team": c.get("team", ""),
            "employment_type": c.get("employment_type", ""),
            "description": c.get("description", ""),
        }
        got = fn(inp)["verdict"]
        exp = c["expected"]
        if got == exp:
            correct += 1
        else:
            mismatches.append((c["id"], c["category"], exp, got))
        if got == "priority" and exp != "priority":
            fp_priority += 1
        if exp == "priority" and got != "priority":
            fn_priority += 1
        if got != "excluded" and exp == "excluded":
            fp_included += 1
        if exp != "excluded" and got == "excluded":
            fn_included += 1
    return {
        "total": len(cases),
        "exact_accuracy": correct / len(cases),
        "correct": correct,
        "fp_priority": fp_priority,
        "fn_priority": fn_priority,
        "fp_included": fp_included,
        "fn_included": fn_included,
        "mismatches": mismatches,
    }


class ScreeningV2Tests(unittest.TestCase):
    def test_v1_rules_version_unchanged_and_v2_versioned(self):
        self.assertEqual(RULES_VERSION_V1, "screen-2026-10-23.1")
        self.assertEqual(RULES_VERSION, "screen-2026-11-25.1")
        self.assertEqual(RULES_VERSION_V2, RULES_VERSION)
        res = screen({"title": "Graduate Software Engineer 2027", "location": "London, UK", "description": "Python."})
        self.assertEqual(res["rules_version"], RULES_VERSION)
        self.assertIn("year.2027_explicit", res["codes"])

    def test_expanded_corpus_eval_v2_improves_over_v1(self):
        data = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
        cases = data["cases"]
        self.assertEqual(len(cases), 44)
        m1 = _evaluate(screen_v1, cases)
        m2 = _evaluate(screen, cases)
        # v2 must dramatically reduce both priority false negatives and non-engineering false positives
        self.assertGreater(m2["exact_accuracy"], m1["exact_accuracy"])
        self.assertEqual(m2["exact_accuracy"], 1.0, f"Unexpected v2 mismatches: {m2['mismatches']}")
        self.assertEqual(m2["fp_priority"], 0)
        self.assertEqual(m2["fn_priority"], 0)
        self.assertEqual(m2["fp_included"], 0)
        self.assertEqual(m2["fn_included"], 0)
        self.assertGreater(m1["fn_priority"], 10)
        self.assertGreater(m1["fp_included"], 3)

    def test_inspectable_atomic_codes_for_inclusive_phd_and_benign_residency(self):
        r = screen({
            "title": "Graduate AI Engineer (2027 start)",
            "location": "Cambridge, United Kingdom",
            "country": "GB",
            "description": "An excellent degree or PhD in CS. Must be continuously resident in the UK for a minimum of 5 years.",
        })
        self.assertEqual(r["verdict"], "priority")
        self.assertIn("year.2027_explicit", r["codes"])
        self.assertIn("level.benign_tenure_or_cap_only", r["codes"])
        self.assertIn("level.inclusive_degree_ladder", r["codes"])
        self.assertNotIn("level.experience_wording", r["codes"])
        self.assertNotIn("level.doctoral_wording", r["codes"])

    def test_generic_swe_entry_description_gets_explicit_code(self):
        r = screen_v2({
            "title": "Software Engineer",
            "location": "London, UK",
            "country": "GB",
            "description": "Entry-level position for recent graduates building Python APIs.",
        })
        self.assertEqual(r["verdict"], "review")
        self.assertIn("level.entry_description_only", r["codes"])
        self.assertIn("level.unknown", r["codes"])

    def test_software_evidenced_adjacent_code_for_grad_ds_and_cyber(self):
        r = screen({
            "title": "Graduate Data Scientist (2027)",
            "location": "London, United Kingdom",
            "country": "GB",
            "description": "Build production ML pipelines in Python.",
        })
        self.assertEqual(r["verdict"], "review")
        self.assertIn("role.adjacent_family", r["codes"])
        self.assertIn("role.software_evidenced_adjacent", r["codes"])

    def test_desirable_experience_records_info_code_without_demoting(self):
        r = screen_v2({
            "title": "Graduate Software Engineer",
            "location": "London, UK",
            "country": "GB",
            "description": "Commercial experience with Kubernetes is desirable but not required.",
        })
        self.assertEqual(r["verdict"], "priority")
        self.assertIn("level.desirable_experience_only", r["codes"])
        self.assertNotIn("level.experience_wording", r["codes"])


if __name__ == "__main__":
    unittest.main()
