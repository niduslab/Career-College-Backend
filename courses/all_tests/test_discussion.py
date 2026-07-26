from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.all_views.discussion_views import DiscussionUpvoteThrottle
from courses.models import (
    CourseSection,
    Enrollment,
    Lecture,
    NidusCourse,
    SectionContent,
)
from courses.all_models.discussion_models import (
    CourseQuestion,
    QuestionReply,
)


class DiscussionAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='disc_instructor@example.com', password='pw12345!',
            full_name='Disc Instructor', user_type='instructor', is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='disc_learner@example.com', password='pw12345!',
            full_name='Disc Learner', user_type='learner', is_email_verified=True,
        )
        cls.other_learner = User.objects.create_user(
            email='disc_other@example.com', password='pw12345!',
            full_name='Disc Other', user_type='learner', is_email_verified=True,
        )
        cls.stranger = User.objects.create_user(
            email='disc_stranger@example.com', password='pw12345!',
            full_name='Disc Stranger', user_type='learner', is_email_verified=True,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Discussion Course',
            slug='discussion-course',
            description='Course used by discussion tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
            price='0.00',
        )
        cls.course.instructors.add(cls.instructor)

        cls.section = CourseSection.objects.create(course=cls.course, title='Intro')
        cls.lecture = Lecture.objects.create(
            section=cls.section, title='Lecture 1', lecture_type=Lecture.LectureType.VIDEO,
        )
        cls.content = SectionContent.objects.create(
            section=cls.section,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=cls.lecture.pk,
            position=1,
        )

        # learner and other_learner enrolled; stranger not.
        Enrollment.objects.create(user=cls.learner, course=cls.course, is_active=True)
        Enrollment.objects.create(user=cls.other_learner, course=cls.course, is_active=True)

    def setUp(self):
        # Throttle counters live in the default cache and would otherwise
        # leak between tests in the same process.
        cache.clear()

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def _create_question(self, user=None, **extra):
        return CourseQuestion.objects.create(
            course=self.course, author=user or self.learner,
            title=extra.get('title', 'How does X work?'),
            body=extra.get('body', 'Please explain X.'),
            **{k: v for k, v in extra.items() if k not in ('title', 'body')},
        )

    # ------------------------------------------------------------------ access

    def test_enrolled_learner_can_post_question(self):
        self.auth(self.learner)
        resp = self.client.post(
            reverse('courses:course-question-list', kwargs={'slug': self.course.slug}),
            {'title': 'Question one', 'body': 'Body of question one.', 'related_content_id': self.content.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(CourseQuestion.objects.filter(course=self.course, author=self.learner).exists())

    def test_unenrolled_user_gets_403_on_slug_list(self):
        self.auth(self.stranger)
        resp = self.client.get(
            reverse('courses:course-question-list', kwargs={'slug': self.course.slug})
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unenrolled_user_gets_404_on_numeric_detail(self):
        question = self._create_question()
        self.auth(self.stranger)
        resp = self.client.get(
            reverse('courses:course-question-detail', kwargs={'question_id': question.pk})
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_related_content_from_other_course_rejected(self):
        other = NidusCourse.objects.create(
            created_by=self.instructor, title='Other', slug='other-course',
            description='x', status=NidusCourse.CourseStatus.PUBLISHED,
        )
        other_section = CourseSection.objects.create(course=other, title='S')
        other_lecture = Lecture.objects.create(
            section=other_section, title='L', lecture_type=Lecture.LectureType.VIDEO,
        )
        other_content = SectionContent.objects.create(
            section=other_section, item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=other_lecture.pk, position=1,
        )
        self.auth(self.learner)
        resp = self.client.post(
            reverse('courses:course-question-list', kwargs={'slug': self.course.slug}),
            {'title': 'Q', 'body': 'B', 'related_content_id': other_content.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------------------------------------------------------ replies

    def test_instructor_reply_is_badged(self):
        question = self._create_question()
        self.auth(self.instructor)
        resp = self.client.post(
            reverse('courses:question-reply-create', kwargs={'question_id': question.pk}),
            {'body': 'Here is the answer.'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['data']['is_instructor_reply'])
        question.refresh_from_db()
        self.assertEqual(question.reply_count, 1)

    def test_learner_reply_not_badged(self):
        question = self._create_question()
        self.auth(self.other_learner)
        resp = self.client.post(
            reverse('courses:question-reply-create', kwargs={'question_id': question.pk}),
            {'body': 'I have the same question.'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertFalse(resp.data['data']['is_instructor_reply'])

    # ------------------------------------------------------------------ upvotes

    def test_upvote_question_increments_counter(self):
        question = self._create_question(user=self.other_learner)
        url = reverse('courses:question-upvote', kwargs={'question_id': question.pk})
        self.auth(self.learner)

        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['data']['upvote_count'], 1)

        # counter-only: a second call increments again (no dedup, no toggle)
        resp = self.client.post(url)
        self.assertEqual(resp.data['data']['upvote_count'], 2)

    def test_upvote_is_throttled(self):
        question = self._create_question(user=self.other_learner)
        url = reverse('courses:question-upvote', kwargs={'question_id': question.pk})
        self.auth(self.learner)

        # `rate` is read at class-definition time, so override_settings can't
        # reach it — patch the parsed limit on the throttle class instead.
        with patch.object(DiscussionUpvoteThrottle, 'rate', '2/min'):
            self.assertEqual(self.client.post(url).status_code, status.HTTP_200_OK)
            self.assertEqual(self.client.post(url).status_code, status.HTTP_200_OK)
            self.assertEqual(
                self.client.post(url).status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
            )

        question.refresh_from_db()
        self.assertEqual(question.upvote_count, 2)

    def test_upvote_on_reply_increments_counter(self):
        question = self._create_question()
        reply = QuestionReply.objects.create(question=question, author=self.other_learner, body='ans')
        self.auth(self.learner)
        resp = self.client.post(reverse('courses:reply-upvote', kwargs={'reply_id': reply.pk}))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        reply.refresh_from_db()
        self.assertEqual(reply.upvote_count, 1)

    # ------------------------------------------------------------------ pin / delete

    def test_only_instructor_can_pin(self):
        question = self._create_question()
        url = reverse('courses:question-pin', kwargs={'question_id': question.pk})

        self.auth(self.learner)
        self.assertEqual(self.client.post(url).status_code, status.HTTP_403_FORBIDDEN)

        self.auth(self.instructor)
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['data']['is_pinned'])

    def test_author_can_soft_delete_own_question(self):
        question = self._create_question()
        self.auth(self.learner)
        resp = self.client.delete(
            reverse('courses:course-question-detail', kwargs={'question_id': question.pk})
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        question.refresh_from_db()
        self.assertTrue(question.is_deleted)

    def test_learner_cannot_delete_others_question(self):
        question = self._create_question(user=self.other_learner)
        self.auth(self.learner)
        resp = self.client.delete(
            reverse('courses:course-question-detail', kwargs={'question_id': question.pk})
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_instructor_can_delete_any_question(self):
        question = self._create_question(user=self.learner)
        self.auth(self.instructor)
        resp = self.client.delete(
            reverse('courses:course-question-detail', kwargs={'question_id': question.pk})
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_soft_deleted_question_excluded_from_list(self):
        self._create_question(title='visible')
        self._create_question(title='gone', is_deleted=True)
        self.auth(self.learner)
        resp = self.client.get(
            reverse('courses:course-question-list', kwargs={'slug': self.course.slug})
        )
        titles = {row['title'] for row in resp.data['data']['results']}
        self.assertIn('visible', titles)
        self.assertNotIn('gone', titles)
