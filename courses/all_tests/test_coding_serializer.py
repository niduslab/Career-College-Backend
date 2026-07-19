from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, User
from courses.models import (
    Assignment,
    AssignmentQuestion,
    CourseSection,
    Lecture,
    NidusCourse,
    SectionContent,
)
from courses.serializers import (
    AssignmentQuestionSerializer,
    AssignmentSerializer,
    CodingExerciseCreateUpdateSerializer,
)


# ---------------------------------------------------------------------------
# Existing CodingExercise serializer tests (kept)
# ---------------------------------------------------------------------------

class CodingExerciseCreateUpdateSerializerTests(TestCase):
    def test_create_accepts_flattened_single_language_payload(self):
        serializer = CodingExerciseCreateUpdateSerializer(
            data={
                'title': 'Two Sum',
                'description': 'Return indices of the two numbers adding up to target.',
                'language': 'python',
                'starter_code': 'def two_sum(nums, target):\n    pass\n',
                'solution_code': 'def two_sum(nums, target): ...',
                'evaluation_script': 'import unittest\nfrom exercise import two_sum\n',
                'time_limit_ms': 2000,
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_create_rejects_unknown_language(self):
        serializer = CodingExerciseCreateUpdateSerializer(
            data={
                'title': 'Reverse String',
                'description': 'Reverse a string.',
                'language': 'rust',
                'time_limit_ms': 2000,
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('language', serializer.errors)

    def test_create_rejects_short_title(self):
        serializer = CodingExerciseCreateUpdateSerializer(
            data={'title': 'ab', 'language': 'python'}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('title', serializer.errors)


# ---------------------------------------------------------------------------
# Assignment test base — shared fixtures
# ---------------------------------------------------------------------------
