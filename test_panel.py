"""Combined test suite runner importing all modular panel test suites."""
import unittest

from test_core_contract import CoreFunctionalityContractTests
from test_warp import WarpTests
from test_awg import AmneziaWgTests
from test_panel_api import DnsCatalogTests, PanelApiTests

__all__ = [
    'CoreFunctionalityContractTests',
    'WarpTests',
    'AmneziaWgTests',
    'DnsCatalogTests',
    'PanelApiTests',
]

if __name__ == '__main__':
    unittest.main()
