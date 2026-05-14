from unittest import TestSuite

from .all_tests.test_coding_serializer import *
from .all_tests.test_assignment import *
from .all_tests.test_course_lifecycle import *
from .all_tests.test_enrollment import *


def load_tests(loader, tests, pattern):
    if pattern is None:
        return tests
    # App-level discovery loads modular test files directly; avoid duplicate runs there.
    return TestSuite()
