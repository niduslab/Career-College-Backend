"""Tests for AuthoredModel stamping (created_by / last_edited_by) and the
nested author fields exposed by the content read serializers.

Covers: section/lecture/quiz/assignment/coding creation stamps the actor,
edits preserve created_by while updating last_edited_by, and the read
serializers surface the nested {id, full_name, email} author block.
"""
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, User
from courses.models import (
    Assignment,
    CodingExercise,
    CourseSection,
    Lecture,
    NidusCourse,
    Quiz,
    SectionContent,
)


class AuthorshipTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = cls._make_instructor('alice@example.com', 'Alice Instructor')
        cls.other_instructor = cls._make_instructor('bob@example.com', 'Bob Instructor')
        cls.learner = User.objects.create_user(
            email='dave@example.com', password='pw12345!',
            full_name='Dave Learner', user_type='learner', is_email_verified=True,
        )
        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor, title='Test Course', description='A test course.',
        )
        # Both instructors are on the roster so both pass the ownership guard.
        cls.course.instructors.add(cls.instructor, cls.other_instructor)
        cls.section = CourseSection.objects.create(
            course=cls.course, title='Section 1', position=1,
            created_by=cls.instructor, last_edited_by=cls.instructor,
        )

    @staticmethod
    def _make_instructor(email, full_name):
        user = User.objects.create_user(
            email=email, password='pw12345!', full_name=full_name,
            user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=user).update(is_verified=True)
        return user

    def auth(self, user):
        self.client.force_authenticate(user=user)


class SectionAuthorshipTests(AuthorshipTestBase):
    def test_create_stamps_both_author_fields(self):
        self.auth(self.instructor)
        url = reverse('courses:section-create', kwargs={'course_id': self.course.id})
        response = self.client.post(url, {'title': 'New Section', 'position': 2}, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        section = CourseSection.objects.get(pk=response.data['data']['id'])
        self.assertEqual(section.created_by_id, self.instructor.id)
        self.assertEqual(section.last_edited_by_id, self.instructor.id)

    def test_update_preserves_created_by_and_updates_editor(self):
        # Section was created by `instructor`; a different instructor edits it.
        self.auth(self.other_instructor)
        url = reverse('courses:section-detail', kwargs={'section_id': self.section.id})
        response = self.client.patch(url, {'title': 'Renamed'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.section.refresh_from_db()
        self.assertEqual(self.section.created_by_id, self.instructor.id)        # unchanged
        self.assertEqual(self.section.last_edited_by_id, self.other_instructor.id)  # updated

    def test_read_serializer_exposes_nested_author(self):
        self.auth(self.instructor)
        url = reverse('courses:section-list', kwargs={'course_id': self.course.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = response.data['data'][0]
        self.assertEqual(
            set(row['created_by'].keys()), {'id', 'full_name', 'email'}
        )
        self.assertEqual(row['created_by']['id'], self.instructor.id)
        self.assertEqual(row['created_by']['email'], self.instructor.email)


class ContentCreationAuthorshipTests(AuthorshipTestBase):
    """Lecture / quiz / assignment / coding created via the unified
    section-contents endpoint stamp the creator on both the content row and
    its SectionContent slot."""

    def setUp(self):
        self.auth(self.instructor)
        self.contents_url = reverse(
            'courses:section-content-list-create', kwargs={'section_id': self.section.id}
        )

    def _assert_authored(self, instance):
        self.assertEqual(instance.created_by_id, self.instructor.id)
        self.assertEqual(instance.last_edited_by_id, self.instructor.id)

    def _slot_for(self, item_type, object_id):
        return SectionContent.objects.get(item_type=item_type, object_id=object_id)

    def test_lecture_create_stamps_author_and_slot(self):
        response = self.client.post(
            self.contents_url,
            {'item_type': 'lecture', 'title': 'Intro',
             'lecture_type': 'article', 'article_content': 'Hello world.'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        lecture = Lecture.objects.get(pk=response.data['data']['content']['id'])
        self._assert_authored(lecture)
        slot = self._slot_for(SectionContent.ItemType.LECTURE, lecture.id)
        self.assertEqual(slot.created_by_id, self.instructor.id)
        self.assertEqual(slot.last_edited_by_id, self.instructor.id)

    def test_quiz_create_stamps_author(self):
        response = self.client.post(
            self.contents_url,
            {'item_type': 'quiz', 'title': 'Quiz 1', 'description': 'Check.'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        quiz = Quiz.objects.get(pk=response.data['data']['content']['id'])
        self._assert_authored(quiz)

    def test_assignment_create_stamps_author(self):
        response = self.client.post(
            self.contents_url,
            {'item_type': 'assignment', 'title': 'Essay',
             'instructions': 'Write.', 'total_score': 100, 'passing_score': 50},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        assignment = Assignment.objects.get(pk=response.data['data']['content']['id'])
        self._assert_authored(assignment)

    def test_coding_create_stamps_author(self):
        response = self.client.post(
            self.contents_url,
            {'item_type': 'coding', 'title': 'Two Sum',
             'problem_statement': 'Return indices.',
             'default_language': 'python', 'supported_languages': ['python']},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        exercise = CodingExercise.objects.get(pk=response.data['data']['content']['id'])
        self._assert_authored(exercise)


class ContentEditAuthorshipTests(AuthorshipTestBase):
    def test_lecture_patch_preserves_created_by_updates_editor(self):
        lecture = Lecture.objects.create(
            section=self.section, title='Orig', lecture_type='article',
            article_content='x', created_by=self.instructor, last_edited_by=self.instructor,
        )
        self.auth(self.other_instructor)
        url = reverse('courses:lecture-detail', kwargs={'lecture_id': lecture.id})
        response = self.client.patch(url, {'title': 'Edited'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        lecture.refresh_from_db()
        self.assertEqual(lecture.created_by_id, self.instructor.id)
        self.assertEqual(lecture.last_edited_by_id, self.other_instructor.id)

    def test_assignment_service_update_stamps_editor(self):
        assignment = Assignment.objects.create(
            section=self.section, title='Orig', total_score=10, passing_score=0,
            created_by=self.instructor, last_edited_by=self.instructor,
        )
        self.auth(self.other_instructor)
        url = reverse('courses:assignment-detail', kwargs={'assignment_id': assignment.id})
        response = self.client.patch(url, {'title': 'Edited'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assignment.refresh_from_db()
        self.assertEqual(assignment.created_by_id, self.instructor.id)
        self.assertEqual(assignment.last_edited_by_id, self.other_instructor.id)
