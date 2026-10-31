import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "workers", "route_a"))
import attempt_from_bundle  # noqa: E402

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".tmp", "attempt-tests"))


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def workdir(name):
    d = os.path.join(BASE, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


class AttemptFromBundleTest(unittest.TestCase):
    def test_mapping_and_evidence(self):
        d = workdir("mapping")
        try:
            job = os.path.join(d, "job")
            work = os.path.join(d, "work")
            os.makedirs(os.path.join(work, "ev"), exist_ok=True)
            open(os.path.join(work, "ev", "audit.json"), "w").write("{}")
            open(os.path.join(work, "ev", "formal.json"), "w").write("{}")
            bundle = {
                "route": "A",
                "identity": {"candidate_sha256": "c4a1592157a2858fcf2dd52623b6ae8c280355ce735a1c224530845569bcaa82"},
                "gates": {
                    "V0": {"status": "pass", "details": {}, "evidence": [], "missing": []},
                    "V1": {"status": "pass", "details": {}, "evidence": [{"relative_path": "ev/audit.json"}], "missing": []},
                    "V2": {"status": "not_run", "details": {}, "evidence": [], "missing": ["x"]},
                    "V3": {"status": "pass", "details": {}, "evidence": [{"relative_path": "ev/formal.json"}], "missing": []},
                    "V4": {"status": "inconclusive", "details": {}, "evidence": [], "missing": []},
                    "V5": {"status": "not_run", "details": {}, "evidence": [], "missing": []},
                },
            }
            bp = os.path.join(d, "bundle.json")
            write(bp, bundle)
            out = os.path.join(d, "attempt.json")
            attempt_from_bundle.main(["--bundle", bp, "--job-dir", job, "--work-dir", work, "--out", out])
            got = json.load(open(out, encoding="utf-8"))
            self.assertEqual(got["results"], {
                "V0": "passed", "V1": "passed", "V2": "not_run",
                "V3": "passed", "V4": "unknown", "V5": "not_run",
            })
            self.assertIn("V1", got["evidence_files"])
            self.assertIn("V3", got["evidence_files"])
            self.assertNotIn("outcome", got)
            self.assertNotIn("decision", got)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_passed_without_evidence_fails_closed(self):
        d = workdir("fail-closed")
        try:
            bundle = {
                "route": "A",
                "identity": {"candidate_sha256": "a" * 64},
                "gates": {g: {"status": "not_run", "evidence": [], "details": {}, "missing": []}
                          for g in ("V0", "V1", "V2", "V3", "V4", "V5")},
            }
            bundle["gates"]["V1"]["status"] = "pass"
            bp = os.path.join(d, "bundle.json")
            write(bp, bundle)
            with self.assertRaises(SystemExit):
                attempt_from_bundle.main(["--bundle", bp, "--job-dir", d, "--work-dir", d,
                                          "--out", os.path.join(d, "a.json")])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
