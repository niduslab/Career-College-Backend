from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import CourseSection, LearnerNote, Lecture, NidusCourse


class _NoteFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='note_instructor@example.com',
            password='pw12345!',
            full_name='Note Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='note_learner@example.com',
            password='pw12345!',
            full_name='Note Learner',
            user_type='learner',
            is_email_verified=True,
        )
        cls.other_learner = User.objects.create_user(
            email='note_other@example.com',
            password='pw12345!',
            full_name='Note Other',
            user_type='learner',
            is_email_verified=True,
        )

        cls.course = cls._make_course('Notes Course', 'notes-course')
        cls.other_course = cls._make_course('Notes Course Two', 'notes-course-two')

        cls.section = CourseSection.objects.create(
            course=cls.course, title='Section 1', position=1,
        )
        cls.lecture = Lecture.objects.create(
            section=cls.section,
            title='Closures in depth',
            lecture_type=Lecture.LectureType.VIDEO,
        )

    @classmethod
    def _make_course(cls, title, slug):
        course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title=title,
            slug=slug,
            description='A course used by note tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        course.instructors.add(cls.instructor)
        return course

    @property
    def list_url(self):
        return reverse('courses:learner-note-list-create')

    def detail_url(self, pk):
        return reverse('courses:learner-note-detail', kwargs={'pk': pk})


class LearnerNoteCrudTests(_NoteFixtureMixin, APITestCase):
    def setUp(self):
        self.client.force_authenticate(user=self.learner)

    def test_create_with_body_only(self):
        response = self.client.post(self.list_url, {'body': 'A standalone thought.'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data['data']
        self.assertIsNone(data['course'])
        self.assertIsNone(data['lecture'])
        self.assertEqual(data['color'], 'default')

    def test_create_with_lecture_derives_the_course(self):
        response = self.client.post(
            self.list_url,
            {'body': 'Anchored note.', 'lecture_id': self.lecture.pk},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['course']['slug'], self.course.slug)
        self.assertEqual(response.data['data']['lecture']['id'], self.lecture.pk)

    def test_create_with_mismatched_course_and_lecture_returns_400(self):
        response = self.client.post(
            self.list_url,
            {
                'body': 'Mismatched.',
                'lecture_id': self.lecture.pk,
                'course_slug': self.other_course.slug,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_with_unknown_lecture_returns_404(self):
        response = self.client.post(
            self.list_url, {'body': 'Nope.', 'lecture_id': 999999}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_timestamp_without_lecture_is_rejected_by_the_serializer(self):
        response = self.client.post(
            self.list_url, {'body': 'At 90s.', 'timestamp_seconds': 90}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('timestamp_seconds', response.data['errors'])

    def test_timestamp_without_lecture_is_rejected_by_the_db_constraint(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LearnerNote.objects.create(
                    user=self.learner, body='At 90s.', timestamp_seconds=90,
                )

    def test_empty_body_is_rejected(self):
        response = self.client.post(self.list_url, {'body': ''}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_too_many_tags_is_rejected(self):
        response = self.client.post(
            self.list_url,
            {'body': 'Tagged.', 'tags': [f'tag{i}' for i in range(11)]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_tags_are_normalized_and_deduped(self):
        response = self.client.post(
            self.list_url,
            {'body': 'Tagged.', 'tags': [' React ', 'react', 'REACT', 'hooks', '']},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['tags'], ['react', 'hooks'])

    def test_invalid_color_is_rejected(self):
        response = self.client.post(
            self.list_url, {'body': 'Coloured.', 'color': 'chartreuse'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_detail_get_patch_delete_on_own_note(self):
        note = LearnerNote.objects.create(user=self.learner, body='Mine.')

        self.assertEqual(self.client.get(self.detail_url(note.pk)).status_code, status.HTTP_200_OK)

        patched = self.client.patch(
            self.detail_url(note.pk), {'is_pinned': True}, format='json',
        )
        self.assertEqual(patched.status_code, status.HTTP_200_OK)
        self.assertTrue(patched.data['data']['is_pinned'])
        self.assertEqual(patched.data['data']['body'], 'Mine.')

        deleted = self.client.delete(self.detail_url(note.pk))
        self.assertEqual(deleted.status_code, status.HTTP_200_OK)
        self.assertFalse(LearnerNote.objects.filter(pk=note.pk).exists())

    def test_another_learners_note_is_404_on_every_verb(self):
        note = LearnerNote.objects.create(user=self.other_learner, body='Theirs.')

        self.assertEqual(
            self.client.get(self.detail_url(note.pk)).status_code, status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.patch(self.detail_url(note.pk), {'body': 'x'}, format='json').status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.delete(self.detail_url(note.pk)).status_code, status.HTTP_404_NOT_FOUND,
        )

    def test_patch_bumps_updated_at(self):
        note = LearnerNote.objects.create(user=self.learner, body='Mine.')
        before = note.updated_at

        self.client.patch(self.detail_url(note.pk), {'body': 'Edited.'}, format='json')
        note.refresh_from_db()

        self.assertGreater(note.updated_at, before)


class LearnerNoteFilterTests(_NoteFixtureMixin, APITestCase):
    def setUp(self):
        self.client.force_authenticate(user=self.learner)
        self.anchored = LearnerNote.objects.create(
            user=self.learner,
            course=self.course,
            lecture=self.lecture,
            title='Closures',
            body='Scope chains explained.',
            tags=['react', 'hooks'],
        )
        self.loose = LearnerNote.objects.create(
            user=self.learner, title='Errand', body='Buy milk.', tags=['react'],
        )
        self.pinned = LearnerNote.objects.create(
            user=self.learner, title='Pinned', body='Top of the list.', is_pinned=True,
        )
        LearnerNote.objects.create(user=self.other_learner, body='Not mine.', tags=['react'])

    def _slugs(self, response):
        return [row['id'] for row in response.data['data']['results']]

    def test_list_is_scoped_to_the_caller(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.data['data']['count'], 3)

    def test_filter_by_course(self):
        response = self.client.get(self.list_url, {'course': self.course.slug})
        self.assertEqual(self._slugs(response), [self.anchored.pk])

    def test_filter_by_lecture(self):
        response = self.client.get(self.list_url, {'lecture_id': self.lecture.pk})
        self.assertEqual(self._slugs(response), [self.anchored.pk])

    def test_single_tag_matches_and_multiple_tags_and_together(self):
        one = self.client.get(self.list_url, {'tag': 'react'})
        self.assertCountEqual(self._slugs(one), [self.anchored.pk, self.loose.pk])

        both = self.client.get(self.list_url, {'tag': 'react,hooks'})
        self.assertEqual(self._slugs(both), [self.anchored.pk])

    def test_search_hits_title_and_body_case_insensitively(self):
        by_title = self.client.get(self.list_url, {'search': 'closur'})
        self.assertEqual(self._slugs(by_title), [self.anchored.pk])

        by_body = self.client.get(self.list_url, {'search': 'MILK'})
        self.assertEqual(self._slugs(by_body), [self.loose.pk])

    def test_filter_by_is_pinned(self):
        response = self.client.get(self.list_url, {'is_pinned': 'true'})
        self.assertEqual(self._slugs(response), [self.pinned.pk])

    def test_pinned_sorts_first_even_with_explicit_ordering(self):
        response = self.client.get(self.list_url, {'ordering': 'created_at'})
        self.assertEqual(self._slugs(response)[0], self.pinned.pk)

    def test_invalid_ordering_returns_400(self):
        response = self.client.get(self.list_url, {'ordering': 'bogus'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('ordering', response.data['errors'])

    def test_two_bad_params_return_one_400_carrying_both(self):
        response = self.client.get(self.list_url, {'ordering': 'bogus', 'is_pinned': 'maybe'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('ordering', response.data['errors'])
        self.assertIn('is_pinned', response.data['errors'])

    def test_list_has_no_n_plus_one_on_anchored_notes(self):
        for index in range(10):
            LearnerNote.objects.create(
                user=self.learner,
                course=self.course,
                lecture=self.lecture,
                body=f'Anchored {index}.',
            )
        with self.assertNumQueries(2):
            self.client.get(self.list_url)
