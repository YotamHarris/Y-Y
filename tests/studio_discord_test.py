"""Run the vendored upstream Discord regressions in YYEngine's service gate."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "studio"))
from test_discord_goals import DiscordGoalsTests  # noqa: F401
