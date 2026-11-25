import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "conductor.config.example.json"
BASE = "a" * 40
HEAD = "b" * 40
OTHER = "c" * 40
CHECKPOINT = "2026-11-23T12:00:00Z"
FIRST_SEEN = "2026-11-23T11:55:00Z"
PENDING = {"pr": 12, "head": OTHER, "author": "author-a", "first_seen": FIRST_SEEN}


class ScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".rstack-test-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.home = self.project / "home"
        self.home.mkdir()
        self.merge_log = self.project / "merge-calls"
        self.intake_state = self.project / ".rstack" / "intake-state.json"
        self.config_file = self.project / "conductor.config.json"
        self.config = json.loads(EXAMPLE.read_text())
        self.config["merge_queue"]["repository"] = "owner/repo"
        self.config["merge_queue"]["merge_when_green"].update(
            deploy_workflow="deploy.yml", log_file="queue.log", poll_seconds=1,
        )
        self.config["merge_queue"]["push_gate"].update(
            pr_description_command=["true"],
            secret_scan_command=["true"],
            test_command=["true"],
            test_paths=["spec/*"],
            test_families=[{"name": "regression", "paths": ["spec/*"], "min_files": 1}],
        )
        self.write_config()
        self.env = {
            **os.environ,
            "PATH": f"{ROOT / 'tests' / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.home),
            "CLAUDE_SKILLS_DIR": str(self.project / "custom skills"),
            "TMPDIR": str(self.project),
            "TEST_BASE": BASE,
            "TEST_HEAD": HEAD,
            "TEST_OTHER": OTHER,
            "TEST_MERGE_LOG": str(self.merge_log),
        }

    def write_config(self):
        self.config_file.write_text(json.dumps(self.config))

    def run_script(self, status, script, *arguments, scenario=""):
        result = subprocess.run(
            [str(ROOT / script), *map(str, arguments)],
            cwd=self.project,
            env={**self.env, "TEST_SCENARIO": scenario},
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        return result

    def merge(self, status, scenario="", message=""):
        (self.project / "ruling").write_text("Reviewed head\n")
        result = self.run_script(
            status, "scripts/merge-when-green.sh", "17", HEAD, "ruling", scenario=scenario,
        )
        self.assertIn(message, result.stdout)
        if status:
            self.assertFalse(self.merge_log.exists())

    def test_merge_refuses_moved_head(self):
        self.merge(4, "moved-head", "PR head changed")

    def test_merge_refuses_red_workflow(self):
        self.merge(3, "red-workflow", "RED workflow")

    def test_merge_refuses_failed_deploy(self):
        self.merge(3, "failed-deploy", "RED main deploy")

    def test_merge_holds_during_freeze(self):
        self.config["merge_queue"]["merge_when_green"]["freeze_windows"] = [
            {"days": [1], "start_utc": "11:00", "end_utc": "13:00"},
        ]
        self.write_config()
        self.merge(75, message="holding: freeze is open")

    def test_merge_when_all_green(self):
        self.merge(0, message=f"merged {OTHER}")
        self.assertEqual(self.merge_log.read_text(), (
            f"api --method PUT repos/owner/repo/pulls/17/merge -f merge_method=rebase -f sha={HEAD}\n"
        ))

    def push_gate(self, status, scenario="", message=""):
        (self.project / "body").write_text("Describe the change\n")
        (self.project / "spec").mkdir()
        (self.project / "spec" / "test_part.py").write_text("# A tracked test\n")
        result = self.run_script(
            status, "scripts/push-gate.sh", ".", "body", "main", scenario=scenario,
        )
        self.assertIn(message, result.stdout + result.stderr)

    def test_push_gate_refuses_empty_commit(self):
        self.push_gate(1, "empty-commit", f"empty commit {HEAD}")

    def test_push_gate_refuses_missing_family_floor(self):
        self.config["merge_queue"]["push_gate"]["test_families"][0]["min_files"] = 2
        self.write_config()
        self.push_gate(1, message="family regression selected 1 files, needs 2")

    def test_push_gate_passes_with_family_floor(self):
        self.push_gate(0, message=f"GATE PASS: {BASE}..{HEAD}")

    def intake(self, status=0, *arguments, scenario=""):
        return self.run_script(status, "scripts/intake.sh", *arguments, scenario=scenario)

    def write_intake_state(self, since=CHECKPOINT, pending=(), acked=()):
        self.intake_state.parent.mkdir(exist_ok=True)
        state = {"since": since, "pending": list(pending), "acked": list(acked)}
        self.intake_state.write_text(json.dumps(state))
        return state

    def read_intake_state(self):
        return json.loads(self.intake_state.read_text())

    def write_verdict(self, head):
        verdicts = self.project / ".rstack" / "verdicts"
        verdicts.mkdir(parents=True, exist_ok=True)
        (verdicts / f"{head}.md").write_text("VERDICT: PASS\n")

    def test_intake_groups_authors_and_flags_missing_verdicts(self):
        self.config["intake"] = {"state_file": "queue/checkpoint.json", "verdicts_dir": "verdicts"}
        self.write_config()
        state = self.project / "queue" / "checkpoint.json"
        state.parent.mkdir()
        state.write_text(json.dumps({"since": "2026-11-23T11:30:00Z"}))
        verdicts = self.project / "verdicts"
        verdicts.mkdir()
        (verdicts / f"{HEAD}.md").write_text("VERDICT: PASS\n")
        (verdicts / f"{BASE}.md").write_text("VERDICT: PASS\n")

        result = self.intake(0, self.project)
        self.assertEqual(result.stdout.splitlines(), [
            "author-a",
            f"  #12 head {OTHER} draft true commits 1. needs review",
            f"  #13 head {'d' * 40} draft false commits 3. needs review",
            "author-b",
            f"  #11 head {HEAD} draft false commits 4. verdict exists",
        ])
        self.assertEqual(json.loads(state.read_text()), {
            "since": CHECKPOINT,
            "pending": [
                {**PENDING, "first_seen": CHECKPOINT},
                {"pr": 13, "head": "d" * 40, "author": "author-a", "first_seen": CHECKPOINT},
            ],
            "acked": [],
        })

    def test_intake_uses_default_checkpoint_on_first_run(self):
        del self.config["intake"]
        self.write_config()
        result = self.intake()
        self.assertIn("#10", result.stdout)
        state = self.read_intake_state()
        self.assertEqual(state["since"], CHECKPOINT)
        self.assertCountEqual([entry["pr"] for entry in state["pending"]], [10, 11, 12, 13])
        self.assertEqual(state["acked"], [])

    def test_intake_repeats_pending_pr_after_interrupted_report(self):
        self.write_intake_state(since="2026-11-23T11:50:00Z")
        read_fd, write_fd = os.pipe()
        os.close(read_fd)
        with os.fdopen(write_fd, "wb") as output:
            result = subprocess.run(
                [str(ROOT / "scripts/intake.sh")], cwd=self.project,
                env={**self.env, "TEST_SCENARIO": ""}, stdout=output, stderr=subprocess.PIPE,
                timeout=15,
            )
        self.assertNotEqual(result.returncode, 0)
        state = self.read_intake_state()
        self.assertEqual(state["since"], CHECKPOINT)
        self.assertEqual([entry["pr"] for entry in state["pending"]], [13])
        self.assertIn(f"#13 head {'d' * 40} draft false commits 3. still pending", self.intake().stdout)
        self.assertEqual(self.read_intake_state(), state)

    def test_intake_skips_unchanged_pr_without_pending_review(self):
        state = self.write_intake_state()
        self.assertEqual(self.intake().stdout, f"No open PRs created or updated since {CHECKPOINT}.\n")
        self.assertEqual(self.read_intake_state(), state)

    def test_intake_verdict_resolves_pending_head(self):
        self.write_intake_state(pending=[PENDING])
        self.write_verdict(OTHER)
        self.assertNotIn("#12", self.intake().stdout)
        self.assertEqual(self.read_intake_state()["pending"], [])

    def test_intake_verdict_for_another_head_keeps_review_pending(self):
        self.write_intake_state(pending=[PENDING])
        self.write_verdict(BASE)
        self.assertIn(f"#12 head {OTHER} draft true commits 1. still pending", self.intake().stdout)
        self.assertEqual(self.read_intake_state()["pending"], [PENDING])

    def test_intake_moved_head_replaces_pending_review(self):
        self.write_intake_state(pending=[PENDING])
        result = self.intake(scenario="intake-moved")
        self.assertIn(f"#12 head {BASE} draft true commits 1. needs review", result.stdout)
        self.assertNotIn(OTHER, result.stdout)
        self.assertEqual(self.read_intake_state()["pending"], [
            {**PENDING, "head": BASE, "first_seen": CHECKPOINT},
        ])

    def test_intake_unchanged_head_keeps_first_seen(self):
        self.write_intake_state(pending=[PENDING])
        self.assertIn(f"#12 head {OTHER} draft true commits 1. still pending", self.intake().stdout)
        self.assertEqual(self.read_intake_state()["pending"], [PENDING])

    def test_intake_ack_records_reason_and_suppresses_same_head(self):
        since = "2026-11-23T11:40:00Z"
        other_pending = {**PENDING, "pr": 13, "head": "d" * 40}
        self.write_intake_state(since=since, pending=[PENDING, other_pending])
        reason = "Deferred for 'follow-up'"
        self.intake(0, "--ack", f"12:{OTHER}", reason, scenario="intake-error")
        acknowledged = {"pr": 12, "head": OTHER, "reason": reason, "at": CHECKPOINT}
        expected = {"since": since, "pending": [other_pending], "acked": [acknowledged]}
        self.assertEqual(self.read_intake_state(), expected)
        self.intake(0, "--ack", f"12:{OTHER}")
        self.assertEqual(self.read_intake_state(), expected)
        self.assertNotIn("#12", self.intake().stdout)
        self.assertEqual(self.read_intake_state(), {**expected, "since": CHECKPOINT})

    def test_intake_ack_for_old_head_leaves_new_head_pending(self):
        self.write_intake_state(since="2026-11-23T11:40:00Z", pending=[PENDING])
        self.intake(0, "--ack", f"12:{OTHER}")
        self.assertIn("#12", self.intake(scenario="intake-moved").stdout)
        state = self.read_intake_state()
        self.assertIn({**PENDING, "head": BASE, "first_seen": CHECKPOINT}, state["pending"])
        self.assertEqual(state["acked"], [{"pr": 12, "head": OTHER, "reason": "", "at": CHECKPOINT}])
        self.intake(1, "--ack", f"12:{HEAD}")
        self.assertEqual(self.read_intake_state(), state)

    def test_intake_closed_or_merged_pr_drops_pending_review(self):
        for scenario in ("intake-closed", "intake-merged"):
            with self.subTest(scenario=scenario):
                self.write_intake_state(pending=[PENDING])
                self.assertNotIn("#12", self.intake(scenario=scenario).stdout)
                self.assertEqual(self.read_intake_state()["pending"], [])

    def test_intake_open_pr_missing_from_scan_stays_pending(self):
        self.write_intake_state(pending=[PENDING])
        self.assertIn("#12 head", self.intake(scenario="intake-missed").stdout)
        self.assertEqual(self.read_intake_state()["pending"], [PENDING])

    def test_intake_keeps_state_on_failed_read(self):
        self.write_intake_state(since=FIRST_SEEN, pending=[PENDING])
        original = self.intake_state.read_bytes()
        for scenario, message in (("intake-error", "Cannot read open PRs."),
                                  ("intake-pending-error", "Cannot read pending PR #12.")):
            with self.subTest(scenario=scenario):
                result = self.intake(1, scenario=scenario)
                self.assertIn(message, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(self.intake_state.read_bytes(), original)

    def test_intake_advances_checkpoint_on_successful_read(self):
        state = self.write_intake_state(since=FIRST_SEEN, pending=[PENDING])
        self.intake()
        self.assertEqual(self.read_intake_state(), {**state, "since": CHECKPOINT})

    def test_validator_accepts_example(self):
        self.run_script(0, "scripts/validate-config.py", EXAMPLE)

    def test_validator_rejects_bad_cap_and_reports_each_problem(self):
        self.config["caps"]["trains_in_ci"] = 0
        self.config["caps"]["local_heavy_run_slots"] = True
        push = self.config["merge_queue"]["push_gate"]
        push["test_command"] = ["true", 1]
        del push["secret_scan_command"]
        self.config["merge_queue"]["merge_when_green"]["freeze_windows"] = [
            {"days": [1], "start_utc": "11:00", "end_utc": "11:00"},
            {"days": [8], "start_utc": "24:00", "end_utc": "01:00"},
        ]
        self.write_config()
        result = self.run_script(1, "scripts/validate-config.py")
        locations = {line.split(": ", 1)[0] for line in result.stderr.splitlines()}
        self.assertEqual(locations, {
            "$.caps.trains_in_ci",
            "$.caps.local_heavy_run_slots",
            "$.merge_queue.push_gate.test_command[1]",
            "$.merge_queue.push_gate.secret_scan_command",
            "$.merge_queue.merge_when_green.freeze_windows[0].end_utc",
            "$.merge_queue.merge_when_green.freeze_windows[1].days[0]",
            "$.merge_queue.merge_when_green.freeze_windows[1].start_utc",
        })

    def test_validator_accepts_overnight_freeze(self):
        self.config["merge_queue"]["merge_when_green"]["freeze_windows"] = [
            {"days": [7], "start_utc": "22:00", "end_utc": "03:00"},
        ]
        self.write_config()
        self.run_script(0, "scripts/validate-config.py")

    def test_install_is_repeatable_and_requires_force_for_conflicts(self):
        destination = Path(self.env["CLAUDE_SKILLS_DIR"]) / "rstack"
        agent = self.home / ".claude" / "agents" / "reviewer.md"
        result = self.run_script(0, "install.sh")
        self.assertIn("Validated", result.stdout)
        self.assertEqual(agent.read_text(), (ROOT / "agents" / agent.name).read_text())
        self.assertFalse((destination / ".git").exists())
        self.assertFalse((destination / "conductor.config.json").exists())
        result = self.run_script(0, "install.sh")
        self.assertIn("Skill already installed", result.stdout)

        agent.write_text("Local agent\n")
        result = self.run_script(1, "install.sh")
        self.assertIn(f"Skipped {agent}", result.stderr)
        self.assertEqual(agent.read_text(), "Local agent\n")

        skill = destination / "SKILL.md"
        skill.write_text("Local skill\n")
        result = self.run_script(1, "install.sh")
        self.assertIn(f"Skipped {destination}", result.stderr)
        self.assertEqual(skill.read_text(), "Local skill\n")
        self.run_script(0, "install.sh", "--force")
        self.assertEqual(skill.read_text(), (ROOT / skill.name).read_text())
        self.assertEqual(agent.read_text(), (ROOT / "agents" / agent.name).read_text())

    def test_install_validates_before_copying(self):
        self.config["caps"]["prs_per_train"] = 0
        self.write_config()
        result = self.run_script(1, "install.sh")
        self.assertIn("$.caps.prs_per_train", result.stderr)
        self.assertFalse(Path(self.env["CLAUDE_SKILLS_DIR"]).exists())
        self.assertFalse((self.home / ".claude" / "agents").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
