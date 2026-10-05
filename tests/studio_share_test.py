"""Protected board sharing: adoption, lifecycle, retries and credential isolation."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/mobile"))
import studio_share as share


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        executable = self.folder / "cloudflared.exe"
        executable.touch()
        self.config = dict(enabled=True, executable=str(executable), port=45320, allowed_mail=["friend@example.com"])
        self.process = Mock(pid=12)
        self.process.exe.return_value = str(executable)
        self.process.cmdline.return_value = share.command(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_adopts_only_the_exact_protected_board_tunnel(self):
        self.assertTrue(share.matches(self.process, self.config))
        for args in (
            share.command(self.config)[:-2],
            [*share.command(self.config)[:-1], "stranger@example.com"],
            [*share.command(self.config), "--token", "unrelated"],
        ):
            self.process.cmdline.return_value = args
            self.assertFalse(share.matches(self.process, self.config))
        self.process.cmdline.return_value = share.command(self.config)
        self.process.exe.return_value = str(self.folder / "other.exe")
        self.assertFalse(share.matches(self.process, self.config))

    def test_existing_tunnel_is_adopted_without_changing_its_url(self):
        supervisor = share.Supervisor(self.folder)
        with patch.object(share, "find", return_value=self.process), patch.object(share, "spawn") as spawn, \
                patch.object(share, "tunnel_url", return_value="https://same.trycloudflare.com"):
            first = supervisor.tick(self.config, True, 100)
            second = supervisor.tick(self.config, True, 105)
        spawn.assert_not_called()
        self.assertEqual(first["url"], second["url"])
        self.assertEqual(second["pid"], 12)

    def test_board_outage_stops_tunnel_after_grace_and_restarts_when_available(self):
        supervisor = share.Supervisor(self.folder)
        with patch.object(share, "find", return_value=self.process), patch.object(share, "spawn", return_value=self.process) as spawn, \
                patch.object(share, "stop") as stop, patch.object(share, "tunnel_url", return_value="https://test.trycloudflare.com"):
            supervisor.tick(self.config, True, 100)
            supervisor.tick(self.config, False, 105)
            stop.assert_not_called()
            supervisor.tick(self.config, False, 136)
            stop.assert_called_once_with(self.process, self.config)
            with patch.object(share, "find", return_value=None):
                supervisor.tick(self.config, True, 140)
            spawn.assert_called_once()

    def test_invalid_allowlist_never_starts_an_unprotected_tunnel(self):
        for emails in ([], ["*@example.com"], ["friend@example.com,stranger@example.com"]):
            with self.subTest(emails=emails), patch.object(share, "spawn") as spawn:
                with self.assertRaises(ValueError):
                    share.Supervisor(self.folder).tick({**self.config, "allowed_mail": emails}, True, 100)
                spawn.assert_not_called()

    def test_dead_tunnel_restarts_with_backoff(self):
        supervisor = share.Supervisor(self.folder)
        with patch.object(share, "find", return_value=None), patch.object(share, "matches", return_value=False), \
                patch.object(share, "tunnel_url", return_value=None), patch.object(share, "spawn", return_value=self.process) as spawn:
            supervisor.tick(self.config, True, 100)
            supervisor.tick(self.config, True, 105)
            self.assertEqual(spawn.call_count, 1)
            supervisor.tick(self.config, True, 130)
            self.assertEqual(spawn.call_count, 2)

    def test_cloudflared_does_not_inherit_coordinator_credentials_or_tunnel_overrides(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "private", "TUNNEL_TOKEN": "private", "DISCORD_TOKEN": "private"}), \
                patch.object(share.subprocess, "Popen", return_value=Mock(pid=12)) as popen, \
                patch.object(share.psutil, "Process", return_value=self.process):
            share.spawn(self.config, self.folder)
        env = popen.call_args.kwargs["env"]
        self.assertTrue(set(env).isdisjoint({"GITHUB_TOKEN", "TUNNEL_TOKEN", "DISCORD_TOKEN"}))
        self.assertIn("--allowed-mail", popen.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
