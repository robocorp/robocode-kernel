import unittest

from robotkernel import display, exceptions, executors, install, kernel
from robotkernel import nbreader


class TestMisc(unittest.TestCase):
    def test_importing_of_components_from_modules(self):
        'To prevent syntax errors in modules.'
        assert display.DisplayKernel is not None
        assert exceptions.BrokenOpenConnection is not None
        assert executors.normalize_argument is not None
        assert install.kernel_json is not None
        assert kernel.RobotKernel is not None
        assert nbreader.robot is not None
