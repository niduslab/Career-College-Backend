from django.test import TestCase

from courses.serializers import CodingExerciseCreateUpdateSerializer


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
