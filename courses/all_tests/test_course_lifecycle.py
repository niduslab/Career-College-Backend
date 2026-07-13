from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, User
from courses.models import (
    Assignment,
    AssignmentQuestion,
    CourseSchedule,
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


def _sane_schedule_dates(days_from_now=30, run_days=60):
    """Structurally valid dates that may be past or future depending on days_from_now."""
    now = timezone.now()
    start = now + timedelta(days=days_from_now)
    return {
        'enrollment_opens_at': now - timedelta(days=1),
        'enrollment_closes_at': start - timedelta(days=1),
        'start_date': start,
        'end_date': start + timedelta(days=run_days),
    }


# ---------------------------------------------------------------------------
# Existing CodingExercise serializer tests (kept)
# ---------------------------------------------------------------------------

class CourseLifecycleTestBase(APITestCase):
    """
    Shared fixtures for status-transition and edit-lock tests.

    Creates:
      - instructor      : verified instructor who owns the course
      - other_instructor: verified instructor NOT on the course
      - admin           : is_staff=True, user_type='admin'
      - course          : complete (title + description + section + article lecture)
    """

    @classmethod
    def setUpTestData(cls):
        cls.instructor = cls._make_instructor(
            email='lc_alice@example.com', full_name='Alice Lifecycle', verified=True
        )
        cls.other_instructor = cls._make_instructor(
            email='lc_bob@example.com', full_name='Bob Other', verified=True
        )
        cls.admin = User.objects.create_user(
            email='lc_admin@example.com',
            password='pw12345!',
            full_name='Admin User',
            user_type='admin',
            is_email_verified=True,
            is_staff=True,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Complete Course',
            description='A well-described course.',
        )
        cls.course.instructors.add(cls.instructor)

        cls.section = CourseSection.objects.create(
            course=cls.course, title='Section One', position=1
        )
        cls.lecture = Lecture.objects.create(
            section=cls.section,
            title='Article Lecture',
            lecture_type=Lecture.LectureType.ARTICLE,
            article_content='Enough content to pass validation.',
        )
        SectionContent.objects.create(
            section=cls.section,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=cls.lecture.pk,
            position=1,
        )

    @staticmethod
    def _make_instructor(email, full_name, verified):
        user = User.objects.create_user(
            email=email,
            password='pw12345!',
            full_name=full_name,
            user_type='instructor',
            is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=user).update(is_verified=verified)
        return user

    def setUp(self):
        self.course.refresh_from_db()
        self.client.force_authenticate(user=self.instructor)

    def _set_status(self, s):
        NidusCourse.objects.filter(pk=self.course.pk).update(status=s)
        self.course.refresh_from_db()

    # ---- URL helpers --------------------------------------------------------

    def _submit_url(self):
        return reverse('courses:course-submit', kwargs={'pk': self.course.pk})

    def _review_url(self):
        return reverse('courses:course-review', kwargs={'pk': self.course.pk})

    def _rework_url(self):
        return reverse('courses:course-rework', kwargs={'pk': self.course.pk})

    def _archive_url(self):
        return reverse('courses:course-archive', kwargs={'pk': self.course.pk})

    def _restore_url(self):
        return reverse('courses:course-restore', kwargs={'pk': self.course.pk})

    def _detail_url(self):
        return reverse('courses:course-detail', kwargs={'pk': self.course.pk})

    def _section_create_url(self):
        return reverse('courses:section-create', kwargs={'course_id': self.course.pk})

    def _contents_url(self):
        return reverse('courses:section-content-list-create', kwargs={'section_id': self.section.pk})


# ---------------------------------------------------------------------------
# CourseSubmitTests
# ---------------------------------------------------------------------------

class CourseSectionAccessTests(APITestCase):
    def test_course_creator_can_list_sections_without_instructor_membership(self):
        user = User.objects.create_user(
            email='creator@example.com',
            password='pw12345!',
            full_name='Creator User',
            user_type='instructor',
            is_email_verified=True,
        )
        course = NidusCourse.objects.create(created_by=user, title='Owned Course', description='A course owned by creator.')

        self.client.force_authenticate(user=user)
        url = reverse('courses:section-list', kwargs={'course_id': course.pk})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data'], [])

    def test_course_creator_can_get_patch_delete_own_section_without_instructor_membership(self):
        user = User.objects.create_user(
            email='creator2@example.com',
            password='pw12345!',
            full_name='Creator User 2',
            user_type='instructor',
            is_email_verified=True,
        )
        course = NidusCourse.objects.create(created_by=user, title='Owned Course 2', description='A course owned by creator.')
        section = CourseSection.objects.create(course=course, title='Intro', position=1)

        self.client.force_authenticate(user=user)
        detail_url = reverse('courses:section-detail', kwargs={'section_id': section.pk})

        get_response = self.client.get(detail_url)
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)

        patch_response = self.client.patch(detail_url, {'title': 'Intro (updated)'}, format='json')
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)

        delete_response = self.client.delete(detail_url)
        self.assertEqual(delete_response.status_code, status.HTTP_200_OK)
        self.assertFalse(CourseSection.objects.filter(pk=section.pk).exists())


class CourseSubmitTests(CourseLifecycleTestBase):
    """POST /{pk}/submit/ — draft → under_review."""

    def test_complete_course_transitions_to_under_review(self):
        response = self.client.post(self._submit_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data']['status'], 'under_review')
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, 'under_review')

    def test_missing_description_returns_400(self):
        incomplete = NidusCourse.objects.create(
            created_by=self.instructor,
            title='No Description',
        )
        incomplete.instructors.add(self.instructor)
        sec = CourseSection.objects.create(course=incomplete, title='S', position=1)
        lec = Lecture.objects.create(
            section=sec,
            title='L',
            lecture_type=Lecture.LectureType.ARTICLE,
            article_content='content',
        )
        SectionContent.objects.create(
            section=sec,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lec.pk,
            position=1,
        )
        url = reverse('courses:course-submit', kwargs={'pk': incomplete.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])
        self.assertIn('description', response.data['errors'])

    def test_no_sections_returns_400(self):
        incomplete = NidusCourse.objects.create(
            created_by=self.instructor,
            title='No Sections',
            description='Has description but no sections.',
        )
        incomplete.instructors.add(self.instructor)
        url = reverse('courses:course-submit', kwargs={'pk': incomplete.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('sections', response.data['errors'])

    def test_empty_section_returns_400(self):
        incomplete = NidusCourse.objects.create(
            created_by=self.instructor,
            title='Empty Section',
            description='Has section but no content.',
        )
        incomplete.instructors.add(self.instructor)
        CourseSection.objects.create(course=incomplete, title='Empty', position=1)
        url = reverse('courses:course-submit', kwargs={'pk': incomplete.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('empty_sections', response.data['errors'])

    def test_already_under_review_returns_422(self):
        self._set_status('under_review')
        response = self.client.post(self._submit_url())
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertFalse(response.data['success'])

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(self._submit_url())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_wrong_instructor_gets_404(self):
        self.client.force_authenticate(user=self.other_instructor)
        response = self.client.post(self._submit_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# ScheduledCourseSubmitTests
# ---------------------------------------------------------------------------

class ScheduledCourseSubmitTests(CourseLifecycleTestBase):
    """draft → under_review for delivery_mode='scheduled' courses."""

    def _scheduled_course(self, **schedule_overrides):
        course = NidusCourse.objects.create(
            created_by=self.instructor,
            title='Cohort Course',
            description='A cohort-delivered course.',
            delivery_mode=NidusCourse.DeliveryMode.SCHEDULED,
            course_outline='Week 1: Intro\nWeek 2: Advanced Topics',
        )
        course.instructors.add(self.instructor)
        if schedule_overrides is not None:
            fields = _sane_schedule_dates()
            fields.update(schedule_overrides)
            CourseSchedule.objects.create(course=course, **fields)
        return course

    def test_outline_only_section_passes_when_schedule_attached(self):
        course = self._scheduled_course()
        url = reverse('courses:course-submit', kwargs={'pk': course.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['status'], 'under_review')

    def test_no_schedule_attached_returns_400(self):
        course = NidusCourse.objects.create(
            created_by=self.instructor,
            title='Cohort Course No Schedule',
            description='Missing a schedule.',
            delivery_mode=NidusCourse.DeliveryMode.SCHEDULED,
            course_outline='Week 1: Intro\nWeek 2: Advanced Topics',
        )
        course.instructors.add(self.instructor)
        url = reverse('courses:course-submit', kwargs={'pk': course.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('schedules', response.data['errors'])
        self.assertNotIn('empty_sections', response.data['errors'])
        self.assertNotIn('sections', response.data['errors'])
        self.assertNotIn('course_outline', response.data['errors'])

    def test_no_course_outline_returns_400(self):
        course = NidusCourse.objects.create(
            created_by=self.instructor,
            title='Cohort Course No Outline',
            description='Missing a course outline.',
            delivery_mode=NidusCourse.DeliveryMode.SCHEDULED,
        )
        course.instructors.add(self.instructor)
        CourseSchedule.objects.create(course=course, **_sane_schedule_dates())
        url = reverse('courses:course-submit', kwargs={'pk': course.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('course_outline', response.data['errors'])

    def test_scheduled_course_with_zero_sections_submits(self):
        """Scheduled courses need no sections at all — an outline stands in."""
        course = NidusCourse.objects.create(
            created_by=self.instructor,
            title='Sectionless Cohort Course',
            description='No curriculum yet, just a plan.',
            delivery_mode=NidusCourse.DeliveryMode.SCHEDULED,
            course_outline='Week 1: Intro\nWeek 2: Advanced Topics',
        )
        course.instructors.add(self.instructor)
        CourseSchedule.objects.create(course=course, **_sane_schedule_dates())
        url = reverse('courses:course-submit', kwargs={'pk': course.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['status'], 'under_review')

    def test_bad_schedule_dates_returns_400(self):
        now = timezone.now()
        course = self._scheduled_course(
            enrollment_opens_at=now,
            enrollment_closes_at=now - timedelta(days=1),
        )
        url = reverse('courses:course-submit', kwargs={'pk': course.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('schedule_dates', response.data['errors'])

    def test_sane_but_past_schedule_dates_still_submits(self):
        """Submit doesn't reuse _validate_activation's 'must be in the future' check."""
        now = timezone.now()
        course = self._scheduled_course(
            enrollment_opens_at=now - timedelta(days=30),
            enrollment_closes_at=now - timedelta(days=10),
            start_date=now - timedelta(days=5),
            end_date=now + timedelta(days=25),
        )
        url = reverse('courses:course-submit', kwargs={'pk': course.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['status'], 'under_review')


# ---------------------------------------------------------------------------
# CourseAdminReviewTests
# ---------------------------------------------------------------------------

class CourseAdminReviewTests(CourseLifecycleTestBase):
    """POST /{pk}/review/ — under_review → published | rejected."""

    def setUp(self):
        super().setUp()
        self._set_status('under_review')
        self.client.force_authenticate(user=self.admin)

    def test_admin_approves_course(self):
        response = self.client.post(self._review_url(), {'action': 'approve'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data']['status'], 'published')

    def test_admin_rejects_course_with_reason(self):
        response = self.client.post(
            self._review_url(),
            {'action': 'reject', 'rejection_reason': 'Too many errors.'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['status'], 'rejected')

    def test_admin_rejects_without_reason_returns_400(self):
        response = self.client.post(
            self._review_url(),
            {'action': 'reject', 'rejection_reason': ''},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])

    def test_invalid_action_returns_400(self):
        response = self.client.post(self._review_url(), {'action': 'maybe'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_admin_instructor_forbidden(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.post(self._review_url(), {'action': 'approve'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_starting_status_returns_422(self):
        self._set_status('draft')
        response = self.client.post(self._review_url(), {'action': 'approve'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_approve_activates_valid_draft_schedule(self):
        self.course.delivery_mode = NidusCourse.DeliveryMode.SCHEDULED
        self.course.save(update_fields=['delivery_mode'])
        schedule = CourseSchedule.objects.create(course=self.course, **_sane_schedule_dates())

        response = self.client.post(self._review_url(), {'action': 'approve'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, 'scheduled')

    def test_approve_leaves_stale_schedule_draft_but_still_publishes(self):
        self.course.delivery_mode = NidusCourse.DeliveryMode.SCHEDULED
        self.course.save(update_fields=['delivery_mode'])
        now = timezone.now()
        schedule = CourseSchedule.objects.create(
            course=self.course,
            enrollment_opens_at=now - timedelta(days=30),
            enrollment_closes_at=now - timedelta(days=10),
            start_date=now - timedelta(days=5),
            end_date=now + timedelta(days=25),
        )

        response = self.client.post(self._review_url(), {'action': 'approve'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['status'], 'published')
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, 'draft')


# ---------------------------------------------------------------------------
# CourseAdminPendingReviewListTests
# ---------------------------------------------------------------------------

class CourseAdminPendingReviewListTests(CourseLifecycleTestBase):
    """GET /admin/pending-review/ — admin queue of status=under_review courses."""

    def _url(self):
        return reverse('courses:course-admin-pending-review')

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.admin)

    def test_admin_sees_only_under_review_courses(self):
        self._set_status('under_review')
        other = NidusCourse.objects.create(
            created_by=self.other_instructor,
            title='Draft Course',
            description='Still being written.',
        )

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [row['id'] for row in response.data['data']['results']]
        self.assertIn(self.course.pk, ids)
        self.assertNotIn(other.pk, ids)

    def test_non_admin_forbidden(self):
        self._set_status('under_review')
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delivery_mode_filter(self):
        self._set_status('under_review')
        scheduled_course = self._scheduled_course_under_review()

        response = self.client.get(self._url(), {'delivery_mode': 'scheduled'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [row['id'] for row in response.data['data']['results']]
        self.assertIn(scheduled_course.pk, ids)
        self.assertNotIn(self.course.pk, ids)

    def test_invalid_delivery_mode_returns_400(self):
        response = self.client.get(self._url(), {'delivery_mode': 'bogus'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def _scheduled_course_under_review(self):
        course = NidusCourse.objects.create(
            created_by=self.instructor,
            title='Cohort Course For Queue',
            description='A cohort-delivered course.',
            delivery_mode=NidusCourse.DeliveryMode.SCHEDULED,
            course_outline='Week 1: Intro',
            status='under_review',
        )
        course.instructors.add(self.instructor)
        return course


# ---------------------------------------------------------------------------
# CourseReworkTests
# ---------------------------------------------------------------------------

class CourseReworkTests(CourseLifecycleTestBase):
    """POST /{pk}/rework/ — rejected → draft."""

    def setUp(self):
        super().setUp()
        self._set_status('rejected')

    def test_instructor_reworks_rejected_course(self):
        response = self.client.post(self._rework_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data']['status'], 'draft')

    def test_wrong_starting_status_returns_422(self):
        self._set_status('draft')
        response = self.client.post(self._rework_url())
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_wrong_instructor_gets_404(self):
        self.client.force_authenticate(user=self.other_instructor)
        response = self.client.post(self._rework_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_cannot_use_rework_endpoint(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self._rework_url())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# CourseArchiveTests
# ---------------------------------------------------------------------------

class CourseArchiveTests(CourseLifecycleTestBase):
    """POST /{pk}/archive/ — published → archived."""

    def setUp(self):
        super().setUp()
        self._set_status('published')

    def test_instructor_archives_own_course(self):
        response = self.client.post(self._archive_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['status'], 'archived')

    def test_admin_archives_any_course(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self._archive_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['status'], 'archived')

    def test_wrong_starting_status_returns_422(self):
        self._set_status('draft')
        response = self.client.post(self._archive_url())
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_wrong_instructor_gets_404(self):
        self.client.force_authenticate(user=self.other_instructor)
        response = self.client.post(self._archive_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# CourseRestoreTests
# ---------------------------------------------------------------------------

class CourseRestoreTests(CourseLifecycleTestBase):
    """POST /{pk}/restore/ — archived → draft."""

    def setUp(self):
        super().setUp()
        self._set_status('archived')

    def test_instructor_restores_own_course(self):
        response = self.client.post(self._restore_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['status'], 'draft')

    def test_admin_restores_any_course(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self._restore_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['status'], 'draft')

    def test_wrong_starting_status_returns_422(self):
        self._set_status('draft')
        response = self.client.post(self._restore_url())
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_wrong_instructor_gets_404(self):
        self.client.force_authenticate(user=self.other_instructor)
        response = self.client.post(self._restore_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# CourseEditLockTests
# ---------------------------------------------------------------------------

class CourseEditLockTests(CourseLifecycleTestBase):
    """Edit-lock guard returns 422 for non-editable statuses; reads are unaffected."""

    def _patch_course(self):
        return self.client.patch(
            self._detail_url(), {'title': 'Updated Title'}, format='json'
        )

    def _create_section(self):
        return self.client.post(
            self._section_create_url(), {'title': 'New Section'}, format='json'
        )

    def _create_content(self):
        return self.client.post(
            self._contents_url(),
            {
                'item_type': 'lecture',
                'title': 'New Lecture',
                'lecture_type': 'article',
                'article_content': 'Some content here.',
            },
            format='json',
        )

    # editable statuses must not block writes
    def test_draft_course_patch_is_allowed(self):
        response = self._patch_course()
        self.assertNotEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_rejected_course_patch_is_allowed(self):
        self._set_status('rejected')
        response = self._patch_course()
        self.assertNotEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # non-editable statuses must block ALL writes with 422
    def test_under_review_course_patch_blocked(self):
        self._set_status('under_review')
        response = self._patch_course()
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertFalse(response.data['success'])

    def test_published_course_patch_blocked(self):
        self._set_status('published')
        response = self._patch_course()
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_archived_course_patch_blocked(self):
        self._set_status('archived')
        response = self._patch_course()
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_under_review_section_create_blocked(self):
        self._set_status('under_review')
        response = self._create_section()
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_published_content_create_blocked(self):
        self._set_status('published')
        response = self._create_content()
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # reads must never be blocked by the edit lock
    def test_course_detail_get_never_blocked(self):
        self._set_status('under_review')
        response = self.client.get(self._detail_url())
        self.assertNotEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)


# ---------------------------------------------------------------------------
# CourseOwnerProtectionTests
# ---------------------------------------------------------------------------

class CourseOwnerProtectionTests(CourseLifecycleTestBase):
    """Roster is immutable via PATCH. Only the invitation flow can add co-instructors."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.third_instructor = cls._make_instructor(
            email='lc_carol@example.com', full_name='Carol Third', verified=True
        )

    def setUp(self):
        super().setUp()
        # give other_instructor access to the course so _get_course resolves for them
        self.course.instructors.add(self.other_instructor)

    # ---- instructors field ignored in PATCH (all callers) -------------------

    def test_patch_instructors_field_ignored_for_coinstructor(self):
        """Roster unchanged when co-instructor passes instructors in body."""
        self.client.force_authenticate(user=self.other_instructor)
        response = self.client.patch(
            self._detail_url(),
            {'instructors': [self.other_instructor.pk]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        instructor_ids = {i['id'] for i in response.data['data']['instructors']}
        self.assertIn(self.instructor.pk, instructor_ids)
        self.assertIn(self.other_instructor.pk, instructor_ids)

    def test_patch_instructors_field_ignored_for_owner(self):
        """Roster unchanged when owner passes instructors in body."""
        response = self.client.patch(
            self._detail_url(),
            {'instructors': [self.instructor.pk, self.third_instructor.pk]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        instructor_ids = {i['id'] for i in response.data['data']['instructors']}
        self.assertNotIn(self.third_instructor.pk, instructor_ids)

    def test_coinstructor_title_update_with_instructors_field_in_body(self):
        """Co-instructor can edit content fields; instructors field in payload is silently dropped."""
        self.client.force_authenticate(user=self.other_instructor)
        response = self.client.patch(
            self._detail_url(),
            {'title': 'Co-instructor Title Update', 'instructors': [self.other_instructor.pk]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['title'], 'Co-instructor Title Update')
        instructor_ids = {i['id'] for i in response.data['data']['instructors']}
        self.assertIn(self.instructor.pk, instructor_ids)

    def test_patch_with_unknown_fields_does_not_raise(self):
        """Unrecognised fields like instructors are silently dropped, patch succeeds."""
        response = self.client.patch(
            self._detail_url(),
            {'title': 'New Title', 'instructors': [self.third_instructor.pk]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['title'], 'New Title')

    # ---- created_by immutability --------------------------------------------

    def test_created_by_is_immutable_via_patch(self):
        original_owner_id = self.course.created_by_id
        response = self.client.patch(
            self._detail_url(),
            {'created_by': self.other_instructor.pk},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.course.refresh_from_db()
        self.assertEqual(self.course.created_by_id, original_owner_id)
