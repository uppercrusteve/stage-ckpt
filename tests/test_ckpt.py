import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ckpt  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ckpt-")
        self.out = os.path.join(self.root, "out")
        os.makedirs(self.out)
        self.led = ckpt.Ledger(self.root, "stage1")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def ev(self, name, body="x"):
        p = os.path.join(self.out, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def planned(self, *steps):
        self.led.plan(list(steps))


class TestPlan(Base):
    def test_plan_pending(self):
        self.planned("a", "b")
        data = self.led.load()
        self.assertEqual([s["name"] for s in data["steps"]], ["a", "b"])
        self.assertTrue(all(s["status"] == "pending" for s in data["steps"]))

    def test_duplicate_steps_collapse(self):
        self.planned("a", "a", "b")
        self.assertEqual(len(self.led.load()["steps"]), 2)

    def test_empty_plan_rejected(self):
        with self.assertRaises(SystemExit):
            self.led.plan([])

    def test_replan_needs_force(self):
        self.planned("a")
        with self.assertRaises(SystemExit):
            self.led.plan(["b"])
        self.led.plan(["b"], force=True)
        self.assertEqual([s["name"] for s in self.led.load()["steps"]], ["b"])

    def test_no_plan_yet(self):
        with self.assertRaises(SystemExit):
            self.led.next_step()


class TestProgress(Base):
    def test_next_follows_order(self):
        self.planned("a", "b")
        self.assertEqual(self.led.next_step()["name"], "a")
        self.led.done("a", [self.ev("a.txt")])
        self.assertEqual(self.led.next_step()["name"], "b")
        self.led.done("b", [self.ev("b.txt")])
        self.assertIsNone(self.led.next_step())

    def test_done_needs_evidence(self):
        self.planned("a")
        with self.assertRaises(SystemExit):
            self.led.done("a", [])

    def test_done_rejects_missing_file(self):
        self.planned("a")
        with self.assertRaises(SystemExit):
            self.led.done("a", [os.path.join(self.out, "nope")])

    def test_done_rejects_unknown_step(self):
        self.planned("a")
        with self.assertRaises(SystemExit):
            self.led.done("zzz", [self.ev("a.txt")])

    def test_no_tmp_left_behind(self):
        self.planned("a")
        self.led.done("a", [self.ev("a.txt")])
        self.assertEqual([f for f in os.listdir(self.led.dir) if f.endswith(".tmp")], [])


class TestEvidenceIntegrity(Base):
    def test_modified_evidence_reopens(self):
        self.planned("a", "b")
        p = self.ev("a.txt")
        self.led.done("a", [p])
        with open(p, "w", encoding="utf-8") as f:
            f.write("tampered")
        reopened = self.led.verify()
        self.assertEqual(reopened, ["a"])
        self.assertEqual(self.led.next_step()["name"], "a")
        self.assertIn("reason", self.led.load()["steps"][0])

    def test_deleted_evidence_reopens(self):
        self.planned("a")
        p = self.ev("a.txt")
        self.led.done("a", [p])
        os.remove(p)
        self.assertEqual(self.led.verify(), ["a"])

    def test_intact_evidence_stays_done(self):
        self.planned("a")
        self.led.done("a", [self.ev("a.txt")])
        self.assertEqual(self.led.verify(), [])
        self.assertIsNone(self.led.next_step())

    def test_redo_clears_reason(self):
        self.planned("a")
        p = self.ev("a.txt")
        self.led.done("a", [p])
        with open(p, "w", encoding="utf-8") as f:
            f.write("v2")
        self.led.verify()
        self.led.done("a", [p])
        self.assertNotIn("reason", self.led.load()["steps"][0])


class TestCorruption(Base):
    def test_recovers_from_bak(self):
        """坏账本回滚到上一次写盘前的版本：只丢最后一笔，不丢整份计划。"""
        self.planned("a", "b")
        self.led.done("a", [self.ev("a.txt")])
        with open(self.led.path, "w", encoding="utf-8") as f:
            f.write("{ broken json")
        data = self.led.load()
        self.assertIsNotNone(data)
        self.assertEqual([s["name"] for s in data["steps"]], ["a", "b"])
        self.assertEqual(data["steps"][0]["status"], "pending")
        self.assertEqual(self.led.next_step()["name"], "a")


class TestCli(Base):
    def args(self, *rest):
        return list(rest) + ["--root", self.root]

    def test_plan_next_done_cycle(self):
        self.assertEqual(ckpt.main(self.args("plan", "s", "--step", "x")), 0)
        self.assertEqual(ckpt.main(self.args("next", "s")), 0)
        p = self.ev("x.txt")
        self.assertEqual(ckpt.main(self.args("done", "s", "x", "--evidence", p)), 0)
        self.assertEqual(ckpt.main(self.args("next", "s")), 1)

    def test_verify_exit_one_on_stale(self):
        ckpt.main(self.args("plan", "s", "--step", "x"))
        p = self.ev("x.txt")
        ckpt.main(self.args("done", "s", "x", "--evidence", p))
        with open(p, "w", encoding="utf-8") as f:
            f.write("changed")
        self.assertEqual(ckpt.main(self.args("verify", "s")), 1)

    def test_status_json(self):
        ckpt.main(self.args("plan", "s", "--step", "x"))
        self.assertEqual(ckpt.main(self.args("status", "s", "--json")), 0)
        with open(os.path.join(self.root, "ckpt", "s.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["stage"], "s")

    def test_reset(self):
        ckpt.main(self.args("plan", "s", "--step", "x"))
        self.assertEqual(ckpt.main(self.args("reset", "s")), 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "ckpt", "s.json")))


if __name__ == "__main__":
    unittest.main()
