from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import (
    CourseSection,
    Enrollment,
    Lecture,
    NidusCourse,
    SectionContent,
    VideoAsset,
    WatchProgress,
)


class LearnerConsumptionAPITests(APITestCase):
    """Phase-1 learner consumption endpoints — curriculum, lecture detail, progress."""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='lc_instructor@example.com',
            password='pw12345!',
            full_name='LC Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='lc_learner@example.com',
            password='pw12345!',
            full_name='LC Learner',
            user_type='learner',
            is_email_verified=True,
        )
        cls.outsider = User.objects.create_user(
            email='lc_outsider@example.com',
            password='pw12345!',
            full_name='LC Outsider',
            user_type='learner',
            is_email_verified=True,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Consumable Course',
            slug='consumable-course',
            description='A course used by learner-consumption tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        cls.course.instructors.add(cls.instructor)

        cls.section = CourseSection.objects.create(
            course=cls.course, title='Section One', position=1
        )
        cls.article_lecture = cls._add_lecture(
            cls.section, 'Article Lecture', Lecture.LectureType.ARTICLE,
            article_content='Hello, learner.', position=1,
        )
        cls.video_lecture = cls._add_lecture(
            cls.section, 'Video Lecture', Lecture.LectureType.VIDEO,
            article_content='', position=2,
        )
        VideoAsset.objects.create(
            lecture=cls.video_lecture,
            video_file='courses/consumable-course/lectures/2/raw/raw.mp4',
            original_filename='raw.mp4',
            mime_type='video/mp4',
            file_size=1024,
            duration_seconds=600,
            is_active=True,
            status=VideoAsset.Status.READY,
        )

        cls.enrollment = Enrollment.objects.create(
            user=cls.learner, course=cls.course, is_active=True,
        )

    @classmethod
    def _add_lecture(cls, section, title, lecture_type, article_content, position):
        lecture = Lecture.objects.create(
            section=section,
            title=title,
            lecture_type=lecture_type,
            article_content=article_content,
        )
        SectionContent.objects.create(
            section=section,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lecture.pk,
            position=position,
        )
        return lecture

    def auth(self, user=None):
        self.client.force_authenticate(user=user or self.learner)

    # -------------------------------------------------------------------------
    # Curriculum endpoint
    # -------------------------------------------------------------------------

    def test_curriculum_returns_section_items_in_position_order(self):
        self.auth()
        url = reverse('courses:learner-curriculum', kwargs={'slug': self.course.slug})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sections = response.data['data']['sections']
        self.assertEqual(len(sections), 1)
        items = sections[0]['items']
        self.assertEqual([i['position'] for i in items], [1, 2])
        self.assertEqual(items[0]['title'], 'Article Lecture')
        self.assertEqual(items[0]['lecture_type'], 'article')
        self.assertEqual(items[1]['title'], 'Video Lecture')
        self.assertEqual(items[1]['lecture_type'], 'video')
        self.assertEqual(items[1]['duration_seconds'], 600)

    def test_curriculum_includes_is_completed_marker_for_learner(self):
        WatchProgress.objects.create(
            user=self.learner, lecture=self.article_lecture,
            watched_seconds=0, is_completed=True,
        )
        self.auth()
        url = reverse('courses:learner-curriculum', kwargs={'slug': self.course.slug})

        response = self.client.get(url)

        items = response.data['data']['sections'][0]['items']
        self.assertTrue(items[0]['is_completed'])
        self.assertFalse(items[1]['is_completed'])

    def test_curriculum_omits_is_completed_marker_for_instructor_preview(self):
        self.auth(self.instructor)
        url = reverse('courses:learner-curriculum', kwargs={'slug': self.course.slug})

        response = self.client.get(url)

        items = response.data['data']['sections'][0]['items']
        self.assertNotIn('is_completed', items[0])

    def test_curriculum_forbids_unenrolled_learner(self):
        self.auth(self.outsider)
        url = reverse('courses:learner-curriculum', kwargs={'slug': self.course.slug})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_curriculum_returns_404_for_missing_course(self):
        self.auth()
        url = reverse('courses:learner-curriculum', kwargs={'slug': 'does-not-exist'})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_curriculum_requires_authentication(self):
        url = reverse('courses:learner-curriculum', kwargs={'slug': self.course.slug})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # -------------------------------------------------------------------------
    # Lecture detail endpoint
    # -------------------------------------------------------------------------

    def test_lecture_detail_returns_article_content_for_article_lecture(self):
        self.auth()
        url = reverse('courses:learner-lecture-detail', kwargs={'lecture_id': self.article_lecture.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['lecture_type'], 'article')
        self.assertEqual(data['article_content'], 'Hello, learner.')

    def test_lecture_detail_returns_video_fields_for_video_lecture(self):
        self.auth()
        url = reverse('courses:learner-lecture-detail', kwargs={'lecture_id': self.video_lecture.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['lecture_type'], 'video')
        self.assertEqual(data['duration_seconds'], 600)
        self.assertIn('stream_master_playlist', data)
        self.assertIn('stream_renditions', data)
        # Sensitive fields must not leak.
        self.assertNotIn('transcoding_error', data)

    def test_lecture_detail_includes_progress_for_learner(self):
        WatchProgress.objects.create(
            user=self.learner, lecture=self.video_lecture,
            watched_seconds=120, is_completed=False,
        )
        self.auth()
        url = reverse('courses:learner-lecture-detail', kwargs={'lecture_id': self.video_lecture.id})

        response = self.client.get(url)

        self.assertEqual(response.data['data']['progress']['watched_seconds'], 120)
        self.assertFalse(response.data['data']['progress']['is_completed'])

    def test_lecture_detail_returns_404_for_unenrolled_learner(self):
        self.auth(self.outsider)
        url = reverse('courses:learner-lecture-detail', kwargs={'lecture_id': self.article_lecture.id})

        response = self.client.get(url)

        # 404, not 403 — don't leak lecture existence
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_lecture_detail_allows_instructor_preview(self):
        self.auth(self.instructor)
        url = reverse('courses:learner-lecture-detail', kwargs={'lecture_id': self.article_lecture.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # -------------------------------------------------------------------------
    # Progress upsert endpoint
    # -------------------------------------------------------------------------

    def test_progress_upsert_creates_then_updates_idempotently(self):
        self.auth()
        url = reverse('courses:learner-lecture-progress', kwargs={'lecture_id': self.video_lecture.id})

        first = self.client.post(url, {'watched_seconds': 60, 'is_completed': False}, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data['data']['watched_seconds'], 60)

        second = self.client.post(url, {'watched_seconds': 180, 'is_completed': False}, format='json')
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data['data']['watched_seconds'], 180)
        self.assertEqual(
            WatchProgress.objects.filter(user=self.learner, lecture=self.video_lecture).count(),
            1,
        )

    def test_progress_completion_recalculates_enrollment_progress(self):
        self.auth()
        url_article = reverse(
            'courses:learner-lecture-progress', kwargs={'lecture_id': self.article_lecture.id}
        )
        url_video = reverse(
            'courses:learner-lecture-progress', kwargs={'lecture_id': self.video_lecture.id}
        )

        self.client.post(url_article, {'watched_seconds': 0, 'is_completed': True}, format='json')
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress_percent, 50)

        self.client.post(url_video, {'watched_seconds': 600, 'is_completed': True}, format='json')
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress_percent, 100)

    def test_progress_rejects_negative_watched_seconds(self):
        self.auth()
        url = reverse('courses:learner-lecture-progress', kwargs={'lecture_id': self.video_lecture.id})

        response = self.client.post(url, {'watched_seconds': -5, 'is_completed': False}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('watched_seconds', response.data['errors'])

    def test_progress_rejects_unenrolled_learner_with_404(self):
        self.auth(self.outsider)
        url = reverse('courses:learner-lecture-progress', kwargs={'lecture_id': self.video_lecture.id})

        response = self.client.post(url, {'watched_seconds': 10, 'is_completed': False}, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            WatchProgress.objects.filter(user=self.outsider, lecture=self.video_lecture).exists()
        )

    def test_progress_rejects_instructor_preview(self):
        # Instructors are not learners — the IsLearnerUser permission blocks them.
        self.auth(self.instructor)
        url = reverse('courses:learner-lecture-progress', kwargs={'lecture_id': self.video_lecture.id})

        response = self.client.post(url, {'watched_seconds': 10, 'is_completed': False}, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_progress_missing_field_returns_400(self):
        self.auth()
        url = reverse('courses:learner-lecture-progress', kwargs={'lecture_id': self.video_lecture.id})

        response = self.client.post(url, {'watched_seconds': 10}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('is_completed', response.data['errors'])

    def test_progress_clamps_watched_seconds_to_video_duration(self):
        self.auth()
        url = reverse('courses:learner-lecture-progress', kwargs={'lecture_id': self.video_lecture.id})

        response = self.client.post(
            url, {'watched_seconds': 99999, 'is_completed': True}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['watched_seconds'], 600)
        wp = WatchProgress.objects.get(user=self.learner, lecture=self.video_lecture)
        self.assertEqual(wp.watched_seconds, 600)
        self.assertTrue(wp.is_completed)

    def test_progress_forces_completion_when_cursor_reaches_duration(self):
        # Client lies / hasn't fired `ended` yet: cursor at duration but
        # is_completed=false. Server overrides — at duration means done.
        self.auth()
        url = reverse('courses:learner-lecture-progress', kwargs={'lecture_id': self.video_lecture.id})

        response = self.client.post(
            url, {'watched_seconds': 600, 'is_completed': False}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['data']['is_completed'])
        wp = WatchProgress.objects.get(user=self.learner, lecture=self.video_lecture)
        self.assertTrue(wp.is_completed)
        self.assertEqual(wp.watched_seconds, 600)

    def test_progress_does_not_force_completion_below_duration(self):
        # Mid-video heartbeat must not flip is_completed on its own.
        self.auth()
        url = reverse('courses:learner-lecture-progress', kwargs={'lecture_id': self.video_lecture.id})

        response = self.client.post(
            url, {'watched_seconds': 599, 'is_completed': False}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['data']['is_completed'])
        wp = WatchProgress.objects.get(user=self.learner, lecture=self.video_lecture)
        self.assertFalse(wp.is_completed)
        self.assertEqual(wp.watched_seconds, 599)

    def test_progress_forces_watched_seconds_to_zero_for_article(self):
        self.auth()
        url = reverse('courses:learner-lecture-progress', kwargs={'lecture_id': self.article_lecture.id})

        response = self.client.post(
            url, {'watched_seconds': 42, 'is_completed': True}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['watched_seconds'], 0)
        wp = WatchProgress.objects.get(user=self.learner, lecture=self.article_lecture)
        self.assertEqual(wp.watched_seconds, 0)
