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
    def test_create_requires_supported_languages(self):
        serializer = CodingExerciseCreateUpdateSerializer(
            data={
                'title': 'Two Sum',
                'description': 'Find two numbers that add up to target.',
                'problem_statement': 'Return indices of the two numbers.',
                'difficulty': 'easy',
                'default_language': 'python',
                'time_limit_ms': 2000,
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('supported_languages', serializer.errors)

    def test_create_uses_default_language_when_omitted_for_membership_check(self):
        serializer = CodingExerciseCreateUpdateSerializer(
            data={
                'title': 'Reverse String',
                'description': 'Reverse a string.',
                'problem_statement': 'Given a string s, return reversed s.',
                'difficulty': 'easy',
                'supported_languages': ['javascript'],
                'time_limit_ms': 2000,
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('default_language', serializer.errors)


# ---------------------------------------------------------------------------
# Assignment test base — shared fixtures
# ---------------------------------------------------------------------------
