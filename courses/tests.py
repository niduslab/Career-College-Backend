from unittest import TestSuite

from .all_tests.test_coding_serializer import *
from .all_tests.test_assignment import *
from .all_tests.test_course_lifecycle import *


def load_tests(loader, tests, pattern):
    # Modular test files are discovered directly; this shim exists for imports only.
    return TestSuite()
