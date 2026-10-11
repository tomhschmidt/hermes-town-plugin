import tempfile
import unittest
from pathlib import Path

import support


class HarnessSmokeTest(unittest.TestCase):
    def test_plugin_api_state_is_redirected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api = support.load_plugin_api(Path(tmp))
            self.assertEqual(api._state_path(), Path(tmp) / "feed-state.json")
            state = api._read_state_unlocked()
            self.assertEqual(state["version"], api.STATE_VERSION)


if __name__ == "__main__":
    unittest.main()
