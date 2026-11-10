from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
import importlib.util,unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('portable_launch',ROOT/'scripts/jd-batch-portable-launch.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)

class PortableLaunchTests(unittest.TestCase):
    def test_closed_port_does_not_wait_for_http_timeout(self):
        with patch.object(launcher,'port_in_use',return_value=False),patch.object(launcher.OPENER,'open') as request:
            self.assertFalse(launcher.health(4184));request.assert_not_called()

    def test_default_browser_failure_still_prints_working_url(self):
        for failure in (False,OSError('No browser association')):
            output=StringIO()
            browser=patch.object(launcher.webbrowser,'open',side_effect=failure) if isinstance(failure,Exception) else patch.object(launcher.webbrowser,'open',return_value=failure)
            with patch.object(launcher.sys,'argv',['launch.py']),patch.object(launcher,'health',return_value=True),browser,redirect_stdout(output):launcher.main()
            self.assertIn('工具已启动：http://127.0.0.1:4184/',output.getvalue())
            self.assertIn('可手动打开',output.getvalue())

if __name__=='__main__':unittest.main()
