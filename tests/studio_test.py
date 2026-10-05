"""Native manager behavior and YYEngine mobile delivery without external accounts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "studio"), str(ROOT / "tools/mobile")]
import fe_board
import manager_core
import manager_discord
import manager_store
import manager_workers
import studio_config
import yy_mobile as mobile


class NativeManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"FE_BOARD_DIR": self.temp.name})
        self.env.start()
        self.registry = patch.object(fe_board, "registry", return_value={"remote": "origin", "branch": "main", "agents": {}})
        self.registry.start()
        self.board = fe_board.Board()
        self.config = dict(repo=self.temp.name, owner_id="11", owner_ids=["11", "22"], guild_id="33", channel_id="44",
                           codex=sys.executable, claude=sys.executable, created=0, usage_report=False, nightly_crew="off")

    def tearDown(self):
        self.board.close()
        self.registry.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_discord_allowlist_still_enforces_guild_channel_and_known_threads(self):
        for owner in ("11", "22"):
            self.assertTrue(manager_discord.authorized(self.config, owner, "33", "44"))
            self.assertTrue(manager_discord.authorized(self.config, owner, "33", "55", "44", ["55"]))
        self.assertFalse(manager_discord.authorized(self.config, "99", "33", "44"))
        self.assertFalse(manager_discord.authorized(self.config, "11", "99", "44"))
        self.assertFalse(manager_discord.authorized(self.config, "11", "33", "55", "44", []))

    def test_owner_goal_is_a_native_planning_conversation(self):
        manager = manager_core.Manager(self.board, self.config)
        manager_store.receive(self.board, "discord:1", "reply", "Improve touch scoring")
        manager.input_events()
        goal = self.board.q1("SELECT * FROM pm_goals")
        self.assertEqual(goal["body"], "Improve touch scoring")
        self.assertFalse(self.board.q("SELECT * FROM pm_tasks"))
        self.assertIn("planner", manager.prompt("planner", goal=goal["id"]))

    def test_approval_deduplicates_tasks_and_acceptance_waits_for_main(self):
        with self.board.tx():
            goal = self.board.con.execute("INSERT INTO pm_goals(source,body,status,created) VALUES(?,?,?,?)",
                                          ("test", "goal", "planned", 0)).lastrowid
        tasks = [dict(title="Improve touch", body="Update the touch test", reasoning="Why: taps align", depends=[])]
        first = manager_store.plan_tasks(self.board, goal, tasks, approved=True, key="same-plan")
        second = manager_store.plan_tasks(self.board, goal, tasks, approved=True, key="same-plan")
        self.assertIsNone(second)
        self.assertEqual(len(self.board.q("SELECT * FROM pm_tasks")), 1)
        with patch.object(self.board, "land_pushed", return_value=False), self.assertRaises(ValueError):
            manager_store.accept(self.board, first[0])

    def test_workers_use_native_manager_with_filtered_environment(self):
        source = {"PATH": "tools", "GITHUB_TOKEN": "secret", "DISCORD_TOKEN": "secret", "ASC_KEY_CONTENT": "secret",
                  "OPENAI_API_KEY": "secret", "FE_BOARD_DIR": self.temp.name, "LOCALAPPDATA": self.temp.name,
                  "FE_MANAGER_RUN": "borrowed", "CODEX_HOME": "login"}
        with patch.dict(os.environ, source, clear=True):
            env = manager_workers.clean_env({"id": "fresh", "provider": "codex"})
        self.assertEqual(env["FE_MANAGER_RUN"], "fresh")
        self.assertEqual(env["CODEX_HOME"], "login")
        for key in ("GITHUB_TOKEN", "DISCORD_TOKEN", "ASC_KEY_CONTENT", "OPENAI_API_KEY"):
            self.assertNotIn(key, env)

    def test_build_requests_are_explicit_and_idempotent(self):
        self.assertTrue(mobile.build_message(self.board, "make a new TestFlight build", "event"))
        self.assertTrue(mobile.build_message(self.board, "make a new TestFlight build", "event"))
        self.assertFalse(mobile.build_message(self.board, "Can you explain the TestFlight build?", "other"))
        self.assertEqual(len(self.board.q("SELECT * FROM pm_settings WHERE key LIKE 'mobile.delivery:%'")), 1)

    def test_board_build_control_queues_native_delivery(self):
        fe_board.post(self.board, "/api/mobile-build", {})
        self.assertEqual(len(self.board.q("SELECT * FROM pm_settings WHERE key LIKE 'mobile.delivery:%'")), 1)
        display = mobile.view(self.board)
        self.assertEqual(display["deliveries"][0]["state"], "queued")
        self.assertNotIn("request", display["deliveries"][0])

    def dispatch_fixture(self, error=None):
        mobile.queue_build(self.board, "build", sha="a" * 40)
        api = Mock()
        api.runs.return_value = []
        api.api.side_effect = error
        mobile.tick(self.board, self.config, api, now=1000)
        mobile.tick(self.board, self.config, api, now=1100)
        return api, json.loads(manager_store.setting(self.board, mobile.delivery_key("build")))

    def test_dispatch_is_not_repeated_while_github_exposes_the_run(self):
        api, item = self.dispatch_fixture()
        self.assertEqual(api.api.call_count, 1)
        self.assertEqual(item["state"], "dispatching")

    def test_lost_dispatch_response_recovers_without_duplicate_upload(self):
        api, item = self.dispatch_fixture(TimeoutError("unknown network outcome"))
        self.assertEqual(api.api.call_count, 1)
        api.runs.return_value = [dict(id=7, head_sha="a" * 40, display_title="iOS [tapdemo] " + item["request"], html_url="https://example.test/run/7")]
        api.state.return_value = "ready"
        mobile.tick(self.board, self.config, api, now=1200)
        item = json.loads(manager_store.setting(self.board, mobile.delivery_key("build")))
        self.assertEqual(item["state"], "ready")
        self.assertEqual(item["run"], 7)
        self.assertEqual(api.api.call_count, 1)

    def test_workflow_at_other_commit_cannot_satisfy_delivery(self):
        api, item = self.dispatch_fixture()
        api.runs.return_value = [dict(id=8, head_sha="b" * 40, display_title=item["request"], html_url="https://example.test/run/8")]
        mobile.tick(self.board, self.config, api, now=1200)
        api.state.assert_not_called()

    def test_network_error_keeps_identity_and_does_not_create_work(self):
        api, item = self.dispatch_fixture()
        api.runs.side_effect = TimeoutError("temporarily offline")
        mobile.tick(self.board, self.config, api, now=1200)
        self.assertEqual(json.loads(manager_store.setting(self.board, mobile.delivery_key("build")))["request"], item["request"])
        self.assertFalse(self.board.q("SELECT * FROM pm_tasks"))


class MobileSafetyTests(unittest.TestCase):
    def test_codex_extended_paths_are_normalized_before_checkout_matching(self):
        import fe_codex
        self.assertEqual(fe_codex.plain_path("\\\\?\\C:\\game\\studio"), "C:/game/studio")
        self.assertEqual(fe_codex.plain_path("\\\\?\\\\tmp\\game"), "/tmp/game")

    def test_native_presence_refresh_survives_a_board_restart(self):
        live = fe_board.Live()
        live.data = {"A2": {"doing": "last reading"}}
        live.busy = True
        with patch.object(fe_board, "live_agents", side_effect=OSError("registry unavailable")):
            live.refresh()
        self.assertEqual(live.data["A2"]["doing"], "last reading")
        self.assertFalse(live.busy)
        self.assertGreater(live.at, 0)

    def test_native_index_and_line_counts_cover_mobile_cpp(self):
        import fe_index
        import fe_loc
        self.assertEqual(fe_index.LANG[".cpp"], "slang")
        symbols, _, _ = fe_index.parse("namespace yy { class Model { public: void step() { return; } }; }", "slang")
        self.assertIn("yy::Model", [s[2] for s in symbols])
        self.assertIn("yy::Model::step", [s[2] for s in symbols])
        self.assertEqual(fe_loc.area("engine/src/runtime.cpp"), "engine")
        self.assertEqual(fe_loc.area("games/tapdemo/model.cpp"), "games")

    def test_scope_rejects_renames_secrets_and_other_games(self):
        mobile.assert_scope(["engine/a.cpp", "games/tapdemo/model.cpp", "tests/a_test.py"], "games/tapdemo")
        for path in ("scripts/build.ps1", "games/puzzle/main.cpp", "engine/../scripts/build.ps1", "tests/.env", "engine/key.p8", "studio/manager.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                mobile.assert_scope([path], "games/tapdemo")

    def test_native_sync_refuses_provider_publication(self):
        with patch.dict(os.environ, {"FE_MANAGER_RUN": "worker"}):
            import fe_sync
            self.assertEqual(fe_sync.main(["push"]), 2)

    def test_mobile_adapter_is_the_native_worker_landing_hook(self):
        result = dict(status="complete")
        with patch.object(mobile, "land", return_value={"sentinel": True}) as land:
            self.assertEqual(manager_workers.land({"role": "worker"}, result), {"sentinel": True})
        land.assert_called_once()

    def test_only_verified_internal_testing_counts_as_ready(self):
        client = mobile.GitHub.__new__(mobile.GitHub)
        run = dict(id=8, status="completed")
        job = dict(name="TestFlight (tapdemo)", status="completed", conclusion="success", steps=[])
        client.api = Mock(return_value={"jobs": [job]})
        self.assertEqual(client.state(run, "tapdemo"), "failed")
        job["steps"] = [dict(name="Verify TestFlight readiness", conclusion="success")]
        self.assertEqual(client.state(run, "tapdemo"), "ready")
        job["conclusion"] = "failure"
        self.assertEqual(client.state(run, "tapdemo"), "failed")
        client.api.return_value = {"jobs": [dict(name="TestFlight (puzzle)", conclusion="success", steps=job["steps"])]}
        self.assertEqual(client.state(run, "tapdemo"), "failed")

    def test_candidate_publication_rebases_and_runs_trusted_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            origin, candidate = Path(folder) / "origin.git", Path(folder) / "candidate"
            def execute(*args, cwd=None):
                return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
            execute("init", "--bare", str(origin))
            execute("clone", str(origin), str(candidate))
            execute("config", "user.name", "Test", cwd=candidate)
            execute("config", "user.email", "test@example.test", cwd=candidate)
            execute("checkout", "-b", "main", cwd=candidate)
            (candidate / "engine").mkdir()
            (candidate / "engine/model.cpp").write_text("before")
            execute("add", ".", cwd=candidate)
            execute("commit", "-m", "base", cwd=candidate)
            execute("push", "origin", "main", cwd=candidate)
            (candidate / "engine/model.cpp").write_text("after")
            execute("commit", "-am", "change", cwd=candidate)
            head = execute("rev-parse", "HEAD", cwd=candidate)
            result = dict(status="complete", head=head, summary="Changed model", checks=[dict(name=c, outcome="not_applicable", command="", detail="Not relevant") for c in manager_workers.CHECKS])
            concurrent = Path(folder) / "concurrent"
            execute("clone", "-b", "main", str(origin), str(concurrent))
            execute("config", "user.name", "Other", cwd=concurrent)
            execute("config", "user.email", "other@example.test", cwd=concurrent)
            (concurrent / "engine/other.cpp").write_text("concurrent change")
            execute("add", ".", cwd=concurrent)
            execute("commit", "-m", "concurrent main", cwd=concurrent)
            execute("push", "origin", "main", cwd=concurrent)
            with patch.object(studio_config, "data_dir", return_value=Path(folder)), patch.object(mobile, "validate", return_value="trusted build passed") as validate:
                landed = mobile.land(dict(role="worker", cwd=str(candidate), id="test"), result)
            self.assertEqual(landed["status"], "complete", landed.get("summary"))
            validate.assert_called_once()
            self.assertEqual(execute("rev-parse", "main", cwd=origin), landed["head"])
            self.assertNotEqual(landed["head"], head)
            self.assertEqual((candidate / "engine/other.cpp").read_text(), "concurrent change")
            self.assertEqual(next(c for c in landed["checks"] if c["name"] == "tests")["outcome"], "passed")


if __name__ == "__main__":
    unittest.main()
