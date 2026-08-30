"""Two-step lecture authoring, and creator-inclusive content ownership.

Step 1 creates the lecture from a title alone; step 2 supplies the payload
(article text, or a video upload) via PATCH. Between the two the lecture is
"awaiting content": invisible to learners, absent from the progress
denominator, and a hard block on leaving draft.

Also covers the ownership fix that ships with it — a partner institution owns
its courses through `created_by` and is never in `course.instructors`, so the
content endpoints resolve ownership creator-inclusively.
"""
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, PartnerInstitutionProfile, User
from courses.models import (
    Assignment,
    CodingExercise,
    CourseSection,
    Enrollment,
    Lecture,
    NidusCourse,
    Quiz,
    SectionContent,
    VideoAsset,
)
from courses.services.enrollment_service import recalculate_progress


class TwoStepLectureTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='alice@example.com', password='pw12345!',
            full_name='Alice Instructor', user_type='instructor',
            is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.instructor).update(is_verified=True)

        cls.other_instructor = User.objects.create_user(
            email='bob@example.com', password='pw12345!',
            full_name='Bob Instructor', user_type='instructor',
            is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.other_instructor).update(is_verified=True)

        cls.learner = User.objects.create_user(
            email='dave@example.com', password='pw12345!',
            full_name='Dave Learner', user_type='learner', is_email_verified=True,
        )

    def setUp(self):
        self.course = NidusCourse.objects.create(
            created_by=self.instructor, title='Test Course',
            description='A well-described course.',
        )
        self.course.instructors.add(self.instructor)
        self.section = CourseSection.objects.create(
            course=self.course, title='Section 1', position=1,
        )

    # ---- helpers -----------------------------------------------------------

    def contents_url(self, section=None):
        return reverse(
            'courses:section-content-list-create',
            kwargs={'section_id': (section or self.section).id},
        )

    def lecture_url(self, lecture):
        return reverse('courses:lecture-detail', kwargs={'lecture_id': lecture.id})

    def make_lecture(self, section=None, title='L1', with_video=True, position=1):
        """Create a lecture + its curriculum slot directly (no HTTP)."""
        lecture = Lecture.objects.create(
            section=section or self.section, title=title,
            lecture_type=Lecture.LectureType.VIDEO,
        )
        SectionContent.objects.create(
            section=lecture.section, item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lecture.pk, position=position,
        )
        if with_video:
            self.attach_ready_video(lecture)
        return lecture

    @staticmethod
    def attach_ready_video(lecture, duration=120):
        return VideoAsset.objects.create(
            lecture=lecture, video_file='courses/x/raw/v.mp4',
            duration_seconds=duration, is_active=True,
            status=VideoAsset.Status.READY,
        )


class LectureStepOneTests(TwoStepLectureTestBase):
    """Step 1 — a lecture is creatable from a title alone."""

    def test_create_without_type_or_payload(self):
        self.client.force_authenticate(self.instructor)
        response = self.client.post(
            self.contents_url(), {'item_type': 'lecture', 'title': 'Untitled lesson'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        lecture = Lecture.objects.get(pk=response.data['data']['object_id'])
        self.assertEqual(lecture.lecture_type, Lecture.LectureType.VIDEO)
        self.assertEqual(lecture.article_content, '')
        self.assertTrue(lecture.is_awaiting_content)
        self.assertTrue(response.data['data']['content']['is_awaiting_content'])

    def test_create_still_accepts_an_explicit_video_type(self):
        self.client.force_authenticate(self.instructor)
        response = self.client.post(
            self.contents_url(),
            {'item_type': 'lecture', 'title': 'Lesson', 'lecture_type': 'video'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_one_shot_article_create_still_works(self):
        """The old single-step path is unchanged."""
        self.client.force_authenticate(self.instructor)
        response = self.client.post(
            self.contents_url(),
            {
                'item_type': 'lecture', 'title': 'Reading',
                'lecture_type': 'article', 'article_content': 'Body text.',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        lecture = Lecture.objects.get(pk=response.data['data']['object_id'])
        self.assertFalse(lecture.is_awaiting_content)

    def test_article_type_without_body_is_still_rejected(self):
        self.client.force_authenticate(self.instructor)
        response = self.client.post(
            self.contents_url(),
            {'item_type': 'lecture', 'title': 'Reading', 'lecture_type': 'article'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('article_content', response.data['errors'])


class LectureStepTwoTests(TwoStepLectureTestBase):
    """Step 2 — the payload arrives by PATCH."""

    def test_patch_sets_article_content_and_type(self):
        lecture = self.make_lecture(with_video=False)
        self.client.force_authenticate(self.instructor)
        response = self.client.patch(
            self.lecture_url(lecture),
            {'lecture_type': 'article', 'article_content': 'The body.'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        lecture.refresh_from_db()
        self.assertEqual(lecture.lecture_type, Lecture.LectureType.ARTICLE)
        self.assertEqual(lecture.article_content, 'The body.')
        self.assertFalse(lecture.is_awaiting_content)

    def test_patch_uploads_video_and_enqueues_transcoding(self):
        lecture = self.make_lecture(with_video=False)
        self.client.force_authenticate(self.instructor)
        upload = SimpleUploadedFile('lesson.mp4', b'\x00' * 32, content_type='video/mp4')

        # The task is imported inside the service function, so patch it at
        # its definition site rather than on the service module.
        with patch('courses.tasks.transcode_video_asset_task.delay') as delay:
            response = self.client.patch(
                self.lecture_url(lecture),
                {'lecture_type': 'video', 'video_file': upload},
                format='multipart',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        delay.assert_called_once()
        asset = VideoAsset.objects.get(lecture=lecture, is_active=True)
        self.assertEqual(asset.original_filename, 'lesson.mp4')
        lecture.refresh_from_db()
        self.assertFalse(lecture.is_awaiting_content)

    def test_switching_article_back_to_video_clears_the_body(self):
        lecture = Lecture.objects.create(
            section=self.section, title='Reading',
            lecture_type=Lecture.LectureType.ARTICLE, article_content='Body.',
        )
        self.client.force_authenticate(self.instructor)
        response = self.client.patch(
            self.lecture_url(lecture), {'lecture_type': 'video'}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        lecture.refresh_from_db()
        self.assertEqual(lecture.lecture_type, Lecture.LectureType.VIDEO)
        self.assertEqual(lecture.article_content, '')

    def test_video_type_with_an_explicit_body_is_rejected(self):
        lecture = self.make_lecture(with_video=False)
        self.client.force_authenticate(self.instructor)
        response = self.client.patch(
            self.lecture_url(lecture),
            {'lecture_type': 'video', 'article_content': 'Nope.'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('article_content', response.data['errors'])

    def test_renaming_an_article_lecture_keeps_its_body(self):
        """A partial update that omits article_content must not blank it."""
        lecture = Lecture.objects.create(
            section=self.section, title='Reading',
            lecture_type=Lecture.LectureType.ARTICLE, article_content='Body.',
        )
        self.client.force_authenticate(self.instructor)
        response = self.client.patch(
            self.lecture_url(lecture), {'title': 'Renamed'}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        lecture.refresh_from_db()
        self.assertEqual(lecture.article_content, 'Body.')


class EmptyLectureBlocksSubmissionTests(TwoStepLectureTestBase):
    def test_submission_lists_lectures_awaiting_content(self):
        self.make_lecture(title='Half-built lesson', with_video=False)

        with self.assertRaises(ValidationError) as ctx:
            self.course.transition_to('under_review')

        self.assertIn('empty_lectures', ctx.exception.message_dict)
        self.assertIn(
            'Half-built lesson', ctx.exception.message_dict['empty_lectures'][0]
        )

    def test_submission_passes_once_content_is_added(self):
        lecture = self.make_lecture(with_video=False)
        self.attach_ready_video(lecture)

        self.course.transition_to('under_review')
        self.assertEqual(self.course.status, 'under_review')

    def test_article_lecture_never_counts_as_empty(self):
        lecture = Lecture.objects.create(
            section=self.section, title='Reading',
            lecture_type=Lecture.LectureType.ARTICLE, article_content='Body.',
        )
        SectionContent.objects.create(
            section=self.section, item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lecture.pk, position=1,
        )
        self.course.transition_to('under_review')
        self.assertEqual(self.course.status, 'under_review')

    def test_processing_video_is_reported_separately(self):
        """An uploaded-but-not-ready video is `video_processing`, not `empty_lectures`."""
        lecture = self.make_lecture(with_video=False)
        VideoAsset.objects.create(
            lecture=lecture, video_file='courses/x/raw/v.mp4', is_active=True,
            status=VideoAsset.Status.PROCESSING,
        )

        with self.assertRaises(ValidationError) as ctx:
            self.course.transition_to('under_review')

        errors = ctx.exception.message_dict
        self.assertIn('video_processing', errors)
        self.assertNotIn('empty_lectures', errors)


class AwaitingLectureIsHiddenFromLearnersTests(TwoStepLectureTestBase):
    def setUp(self):
        super().setUp()
        self.ready = self.make_lecture(title='Ready lesson', position=1)
        self.awaiting = self.make_lecture(
            title='Awaiting lesson', with_video=False, position=2,
        )
        self.course.status = 'published'
        self.course.is_published = True
        self.course.save(update_fields=['status', 'is_published'])
        self.enrollment = Enrollment.objects.create(
            user=self.learner, course=self.course,
        )

    def curriculum_url(self):
        return reverse(
            'courses:learner-curriculum', kwargs={'slug': self.course.slug},
        )

    def test_learner_curriculum_omits_it(self):
        self.client.force_authenticate(self.learner)
        response = self.client.get(self.curriculum_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = [
            item['title']
            for section in response.data['data']['sections']
            for item in section['items']
        ]
        self.assertIn('Ready lesson', titles)
        self.assertNotIn('Awaiting lesson', titles)

    def test_instructor_preview_still_shows_it(self):
        self.client.force_authenticate(self.instructor)
        response = self.client.get(self.curriculum_url())

        titles = [
            item['title']
            for section in response.data['data']['sections']
            for item in section['items']
        ]
        self.assertIn('Awaiting lesson', titles)

    def test_progress_reaches_100_without_it(self):
        """The denominator must exclude it — otherwise no learner can finish."""
        from courses.models import WatchProgress

        WatchProgress.objects.create(
            user=self.learner, lecture=self.ready,
            watched_seconds=120, is_completed=True,
        )
        recalculate_progress(self.enrollment)

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress_percent, 100)

    def test_catalog_outline_omits_it(self):
        url = reverse('courses:catalog-detail', kwargs={'slug': self.course.slug})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['total_content_items'], 1)
        titles = [
            item['content']['title']
            for section in response.data['data']['sections']
            for item in section['contents']
            if item['content']
        ]
        self.assertEqual(titles, ['Ready lesson'])


class AwaitingContentFlagTests(TwoStepLectureTestBase):
    """`is_awaiting_content` on every content type.

    The AI apply uses this to decide which rows it may replace when a
    regenerated outline lands on a section that already has lessons, so the
    flag must mean "nothing would be lost", not merely "incomplete".
    """

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.instructor)
        self.url = reverse(
            'courses:section-content-list-create',
            kwargs={'section_id': self.section.id},
        )

    def _flags(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {
            row['content']['title']: row['content']['is_awaiting_content']
            for row in response.data['data']
        }

    def _add(self, payload):
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return response.data['data']['content']['id']

    def test_fresh_shells_are_all_awaiting_content(self):
        self._add({'item_type': 'lecture', 'title': 'Lesson'})
        self._add({'item_type': 'quiz', 'title': 'Quiz 1'})
        self._add({'item_type': 'coding', 'title': 'Exercise 1'})
        self._add({'item_type': 'assignment', 'title': 'Essay 1'})

        self.assertEqual(
            self._flags(),
            {'Lesson': True, 'Quiz 1': True, 'Exercise 1': True, 'Essay 1': True},
        )

    def test_quiz_with_a_question_is_not_awaiting(self):
        quiz_id = self._add({'item_type': 'quiz', 'title': 'Quiz 1'})
        Quiz.objects.get(pk=quiz_id).questions.create(question_text='Why?', position=1)
        self.assertFalse(self._flags()['Quiz 1'])

    def test_assignment_with_a_question_is_not_awaiting(self):
        """Only 'no questions' counts — a question that cannot yet be graded is
        still authored work, and deleting it would lose that work even though
        it blocks submission."""
        assignment_id = self._add({'item_type': 'assignment', 'title': 'Essay 1'})
        Assignment.objects.get(pk=assignment_id).questions.create(
            question_text='Discuss.', position=1,
        )
        self.assertFalse(self._flags()['Essay 1'])

    def test_coding_exercise_with_only_starter_code_is_not_awaiting(self):
        """Starter code is authored work even though a blank evaluation script
        still blocks submission."""
        exercise_id = self._add({'item_type': 'coding', 'title': 'Exercise 1'})
        CodingExercise.objects.filter(pk=exercise_id).update(starter_code='def solve():\n    pass')
        self.assertFalse(self._flags()['Exercise 1'])

    def test_lecture_with_a_video_is_not_awaiting(self):
        lecture_id = self._add({'item_type': 'lecture', 'title': 'Lesson'})
        self.attach_ready_video(Lecture.objects.get(pk=lecture_id))
        self.assertFalse(self._flags()['Lesson'])

    def test_listing_does_not_n_plus_one_on_the_flag(self):
        """The flag reads each row's questions, so the view prefetches them.

        Six queries — section, contents, then one fetch plus one prefetch for
        quizzes and the same for assignments. The number must stay CONSTANT as
        rows are added; without the prefetch it grows with the row count.
        """
        for index in range(4):
            quiz_id = self._add({'item_type': 'quiz', 'title': f'Quiz {index}'})
            Quiz.objects.get(pk=quiz_id).questions.create(question_text='Q', position=1)
            assignment_id = self._add({'item_type': 'assignment', 'title': f'Essay {index}'})
            Assignment.objects.get(pk=assignment_id).questions.create(
                question_text='Q', position=1,
            )

        with self.assertNumQueries(6):
            self.client.get(self.url)

        # Double the rows; the query count must not move.
        for index in range(4, 8):
            quiz_id = self._add({'item_type': 'quiz', 'title': f'Quiz {index}'})
            Quiz.objects.get(pk=quiz_id).questions.create(question_text='Q', position=1)

        with self.assertNumQueries(6):
            self.client.get(self.url)


class InstitutionContentOwnershipTests(APITestCase):
    """A partner institution owns its courses via `created_by`, never through
    `course.instructors` — the content endpoints must resolve ownership that
    way or the institution cannot author in its own course."""

    @classmethod
    def setUpTestData(cls):
        cls.institution_user = User.objects.create_user(
            email='inst@example.com', password='pw12345!',
            full_name='Acme Institute', user_type='partner_institution',
            is_email_verified=True,
        )
        PartnerInstitutionProfile.objects.filter(user=cls.institution_user).update(
            institution_name='Acme Institute', is_verified=True, is_active=True,
        )
        cls.institution = cls.institution_user.partner_institution_profile

        cls.outsider = User.objects.create_user(
            email='outsider@example.com', password='pw12345!',
            full_name='Outsider', user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.outsider).update(is_verified=True)

    def setUp(self):
        self.course = NidusCourse.objects.create(
            created_by=self.institution_user, partner_institution=self.institution,
            title='Institution Course', description='A well-described course.',
        )
        self.section = CourseSection.objects.create(
            course=self.course, title='S1', position=1,
        )
        self.url = reverse(
            'courses:section-content-list-create',
            kwargs={'section_id': self.section.id},
        )

    def test_institution_can_create_a_lecture(self):
        self.client.force_authenticate(self.institution_user)
        response = self.client.post(
            self.url, {'item_type': 'lecture', 'title': 'Lesson'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_institution_can_create_a_quiz(self):
        self.client.force_authenticate(self.institution_user)
        response = self.client.post(
            self.url, {'item_type': 'quiz', 'title': 'Quiz 1'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_institution_can_list_section_contents(self):
        self.client.force_authenticate(self.institution_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unrelated_instructor_still_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            self.url, {'item_type': 'lecture', 'title': 'Lesson'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_learner_is_rejected_by_the_permission_gate(self):
        learner = User.objects.create_user(
            email='learner2@example.com', password='pw12345!',
            full_name='Learner', user_type='learner', is_email_verified=True,
        )
        self.client.force_authenticate(learner)
        response = self.client.post(
            self.url, {'item_type': 'lecture', 'title': 'Lesson'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
