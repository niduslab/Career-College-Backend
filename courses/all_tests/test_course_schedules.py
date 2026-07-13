"""
Course schedules (cohorts) — docs/future_implementations/SCHEDULED_COURSES.md.

Phase 1: CourseSchedule state machine, schedule CRUD ownership gating
(institution-only for institution courses, creator-only for solo courses,
roster experts read-only), the Enrollment partial-unique constraint swap,
and the beat task that auto-advances schedules past their dates.

Phase 2: cohort enrollment (window + capacity via /enroll/ schedule_id) and
the guard_editable carve-out that keeps a published course content-editable
while a cohort is ongoing.

Phase 3: learner release gates — curriculum lock markers, 422 on locked
content detail/submit endpoints, instructor preview bypass.
"""
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, PartnerInstitutionProfile, User
from courses.models import (
    CourseSchedule,
    CourseSection,
    Enrollment,
    Lecture,
    NidusCourse,
    SectionContent,
)
from courses.tasks import advance_course_schedules_task


def _dates(days_from_now=1, run_days=30):
    """Valid date payload: opens now-ish, closes before start, optional end."""
    now = timezone.now()
    start = now + timedelta(days=days_from_now)
    return {
        'enrollment_opens_at': now,
        'enrollment_closes_at': start - timedelta(hours=1),
        'start_date': start,
        'end_date': start + timedelta(days=run_days),
    }


class CourseScheduleTestBase(APITestCase):
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

        cls.other_inst_user = User.objects.create_user(
            email='other-inst@example.com', password='pw12345!',
            full_name='Other Institute', user_type='partner_institution',
            is_email_verified=True,
        )
        PartnerInstitutionProfile.objects.filter(user=cls.other_inst_user).update(
            institution_name='Other Institute', is_verified=True, is_active=True,
        )

        cls.expert = User.objects.create_user(
            email='expert@example.com', password='pw12345!',
            full_name='Dr Expert', user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.expert).update(
            is_verified=True, affiliated_institution=cls.institution,
            affiliation_status='active', onboarding_source='institution',
        )

        cls.foreign_expert = User.objects.create_user(
            email='foreign@example.com', password='pw12345!',
            full_name='Foreign Expert', user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.foreign_expert).update(is_verified=True)

        cls.solo = User.objects.create_user(
            email='solo@example.com', password='pw12345!',
            full_name='Solo Instr', user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.solo).update(is_verified=True)

        cls.learner = User.objects.create_user(
            email='learner@example.com', password='pw12345!',
            full_name='Learner', user_type='learner', is_email_verified=True,
        )

    def setUp(self):
        self.inst_course = NidusCourse.objects.create(
            created_by=self.institution_user, partner_institution=self.institution,
            title='Institution Course', description='Described.',
        )
        self.inst_course.instructors.add(self.expert)
        NidusCourse.objects.filter(pk=self.inst_course.pk).update(
            status='published', is_published=True, published_at=timezone.now(),
            delivery_mode=NidusCourse.DeliveryMode.SCHEDULED,
        )
        self.inst_course.refresh_from_db()

        self.solo_course = NidusCourse.objects.create(
            created_by=self.solo, title='Solo Course', description='Described.',
        )
        self.solo_course.instructors.add(self.solo)
        NidusCourse.objects.filter(pk=self.solo_course.pk).update(
            status='published', is_published=True, published_at=timezone.now(),
            delivery_mode=NidusCourse.DeliveryMode.SCHEDULED,
        )
        self.solo_course.refresh_from_db()

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _make_schedule(course, status_value='draft', **overrides):
        fields = _dates()
        fields.update(overrides)
        schedule = CourseSchedule.objects.create(course=course, **fields)
        if status_value != 'draft':
            CourseSchedule.objects.filter(pk=schedule.pk).update(status=status_value)
            schedule.refresh_from_db()
        return schedule

    def _list_url(self, course):
        return reverse('courses:course-schedule-list-create', kwargs={'pk': course.pk})

    def _detail_url(self, course, schedule):
        return reverse('courses:course-schedule-detail',
                       kwargs={'pk': course.pk, 'schedule_id': schedule.pk})

    def _action_url(self, course, schedule, action):
        return reverse(f'courses:course-schedule-{action}',
                       kwargs={'pk': course.pk, 'schedule_id': schedule.pk})


class ScheduleStateMachineTests(CourseScheduleTestBase):
    def test_full_valid_transition_chain(self):
        schedule = self._make_schedule(self.solo_course)
        schedule.transition_to('scheduled')
        schedule.transition_to('ongoing')
        schedule.transition_to('completed')
        schedule.transition_to('archived')
        schedule.transition_to('draft')
        self.assertEqual(schedule.status, 'draft')

    def test_scheduled_back_to_draft_safety_valve(self):
        schedule = self._make_schedule(self.solo_course)
        schedule.transition_to('scheduled')
        schedule.transition_to('draft')
        self.assertEqual(schedule.status, 'draft')

    def test_invalid_transitions_raise(self):
        schedule = self._make_schedule(self.solo_course)
        for bad in ('ongoing', 'completed', 'archived'):
            with self.assertRaises(ValidationError):
                schedule.transition_to(bad)

    def test_activation_blocked_when_course_not_published(self):
        NidusCourse.objects.filter(pk=self.solo_course.pk).update(status='draft')
        self.solo_course.refresh_from_db()
        schedule = self._make_schedule(self.solo_course)
        with self.assertRaises(ValidationError) as ctx:
            schedule.transition_to('scheduled')
        self.assertIn('course', ctx.exception.message_dict)

    def test_activation_blocked_on_misordered_dates(self):
        now = timezone.now()
        schedule = self._make_schedule(
            self.solo_course,
            enrollment_opens_at=now + timedelta(days=2),
            enrollment_closes_at=now + timedelta(days=1),
            start_date=now + timedelta(days=3),
            end_date=now + timedelta(days=2),
        )
        with self.assertRaises(ValidationError) as ctx:
            schedule.transition_to('scheduled')
        self.assertIn('enrollment_opens_at', ctx.exception.message_dict)
        self.assertIn('end_date', ctx.exception.message_dict)

    def test_activation_blocked_on_past_start_date(self):
        now = timezone.now()
        schedule = self._make_schedule(
            self.solo_course,
            enrollment_opens_at=now - timedelta(days=10),
            enrollment_closes_at=now - timedelta(days=5),
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=30),
        )
        with self.assertRaises(ValidationError) as ctx:
            schedule.transition_to('scheduled')
        self.assertIn('start_date', ctx.exception.message_dict)


class DateLogicErrorsTests(CourseScheduleTestBase):
    """CourseSchedule.date_logic_errors() — structural ordering only, no 'now' check."""

    def test_no_problems_returns_empty_dict(self):
        schedule = self._make_schedule(self.solo_course)
        self.assertEqual(schedule.date_logic_errors(), {})

    def test_opens_after_closes_flagged(self):
        now = timezone.now()
        schedule = self._make_schedule(
            self.solo_course,
            enrollment_opens_at=now + timedelta(days=2),
            enrollment_closes_at=now + timedelta(days=1),
        )
        self.assertIn('enrollment_opens_at', schedule.date_logic_errors())

    def test_closes_after_start_flagged(self):
        now = timezone.now()
        start = now + timedelta(days=3)
        schedule = self._make_schedule(
            self.solo_course,
            enrollment_opens_at=now,
            enrollment_closes_at=start + timedelta(days=1),
            start_date=start,
        )
        self.assertIn('enrollment_closes_at', schedule.date_logic_errors())

    def test_end_before_start_flagged(self):
        now = timezone.now()
        start = now + timedelta(days=3)
        schedule = self._make_schedule(
            self.solo_course,
            start_date=start,
            end_date=start - timedelta(days=1),
        )
        self.assertIn('end_date', schedule.date_logic_errors())

    def test_past_dates_pass_structural_check(self):
        """No 'is it still in the future' check here — that's _validate_activation's job."""
        now = timezone.now()
        schedule = self._make_schedule(
            self.solo_course,
            enrollment_opens_at=now - timedelta(days=30),
            enrollment_closes_at=now - timedelta(days=10),
            start_date=now - timedelta(days=5),
            end_date=now + timedelta(days=25),
        )
        self.assertEqual(schedule.date_logic_errors(), {})


class SoloInstructorScheduleCrudTests(CourseScheduleTestBase):
    def test_create_list_get_patch_delete_happy_path(self):
        self.client.force_authenticate(self.solo)

        r = self.client.post(self._list_url(self.solo_course), _dates(), format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertTrue(r.data['success'])
        schedule_id = r.data['data']['id']
        self.assertEqual(r.data['data']['status'], 'draft')
        self.assertEqual(r.data['data']['created_by']['id'], self.solo.pk)

        r = self.client.get(self._list_url(self.solo_course))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.data['success'])
        self.assertEqual(r.data['data']['count'], 1)

        schedule = CourseSchedule.objects.get(pk=schedule_id)
        r = self.client.get(self._detail_url(self.solo_course, schedule))
        self.assertEqual(r.status_code, status.HTTP_200_OK)

        r = self.client.patch(
            self._detail_url(self.solo_course, schedule),
            {'cohort_label': 'Fall 2026'}, format='json',
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['data']['cohort_label'], 'Fall 2026')

        r = self.client.delete(self._detail_url(self.solo_course, schedule))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(CourseSchedule.objects.filter(pk=schedule_id).exists())

    def test_activate_then_rework(self):
        self.client.force_authenticate(self.solo)
        schedule = self._make_schedule(self.solo_course)

        r = self.client.post(self._action_url(self.solo_course, schedule, 'activate'))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['data']['status'], 'scheduled')

        r = self.client.post(self._action_url(self.solo_course, schedule, 'rework'))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['data']['status'], 'draft')

    def test_activate_unpublished_course_returns_400_with_errors(self):
        NidusCourse.objects.filter(pk=self.solo_course.pk).update(status='draft')
        schedule = self._make_schedule(self.solo_course)
        self.client.force_authenticate(self.solo)
        r = self.client.post(self._action_url(self.solo_course, schedule, 'activate'))
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('course', r.data['errors'])

    def test_archive_from_draft_returns_422(self):
        schedule = self._make_schedule(self.solo_course)
        self.client.force_authenticate(self.solo)
        r = self.client.post(self._action_url(self.solo_course, schedule, 'archive'))
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_self_paced_course_rejects_schedule_creation(self):
        NidusCourse.objects.filter(pk=self.solo_course.pk).update(
            delivery_mode=NidusCourse.DeliveryMode.SELF_PACED,
        )
        self.client.force_authenticate(self.solo)
        r = self.client.post(self._list_url(self.solo_course), _dates(), format='json')
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(CourseSchedule.objects.filter(course=self.solo_course).count(), 0)


class ScheduleEditPolicyTests(CourseScheduleTestBase):
    def test_patch_allowed_while_scheduled(self):
        schedule = self._make_schedule(self.solo_course, status_value='scheduled')
        self.client.force_authenticate(self.solo)
        r = self.client.patch(
            self._detail_url(self.solo_course, schedule),
            {'max_seats': 50}, format='json',
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['data']['max_seats'], 50)

    def test_patch_blocked_while_ongoing(self):
        schedule = self._make_schedule(self.solo_course, status_value='ongoing')
        self.client.force_authenticate(self.solo)
        r = self.client.patch(
            self._detail_url(self.solo_course, schedule),
            {'max_seats': 50}, format='json',
        )
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_delete_blocked_when_not_draft(self):
        schedule = self._make_schedule(self.solo_course, status_value='scheduled')
        self.client.force_authenticate(self.solo)
        r = self.client.delete(self._detail_url(self.solo_course, schedule))
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertTrue(CourseSchedule.objects.filter(pk=schedule.pk).exists())

    def test_patch_date_ordering_rejected_400(self):
        schedule = self._make_schedule(self.solo_course)
        self.client.force_authenticate(self.solo)
        now = timezone.now()
        r = self.client.patch(
            self._detail_url(self.solo_course, schedule),
            {'enrollment_opens_at': now + timedelta(days=10)}, format='json',
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('enrollment_opens_at', r.data['errors'])


class InstitutionOwnershipTests(CourseScheduleTestBase):
    def test_institution_full_crud_on_own_course(self):
        self.client.force_authenticate(self.institution_user)

        r = self.client.post(self._list_url(self.inst_course), _dates(), format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        schedule = CourseSchedule.objects.get(pk=r.data['data']['id'])

        r = self.client.patch(
            self._detail_url(self.inst_course, schedule),
            {'cohort_label': 'Batch 1'}, format='json',
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)

        r = self.client.post(self._action_url(self.inst_course, schedule, 'activate'))
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_roster_expert_can_read_but_not_mutate(self):
        schedule = self._make_schedule(self.inst_course)
        self.client.force_authenticate(self.expert)

        r = self.client.get(self._list_url(self.inst_course))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        r = self.client.get(self._detail_url(self.inst_course, schedule))
        self.assertEqual(r.status_code, status.HTTP_200_OK)

        r = self.client.post(self._list_url(self.inst_course), _dates(), format='json')
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)
        r = self.client.patch(self._detail_url(self.inst_course, schedule),
                              {'cohort_label': 'X'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)
        r = self.client.delete(self._detail_url(self.inst_course, schedule))
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)
        r = self.client.post(self._action_url(self.inst_course, schedule, 'activate'))
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_roster_expert_gets_404_on_read(self):
        self._make_schedule(self.inst_course)
        self.client.force_authenticate(self.foreign_expert)
        r = self.client.get(self._list_url(self.inst_course))
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_cross_institution_404_no_existence_leak(self):
        schedule = self._make_schedule(self.inst_course)
        self.client.force_authenticate(self.other_inst_user)

        for method, url in (
            ('get', self._list_url(self.inst_course)),
            ('post', self._list_url(self.inst_course)),
            ('get', self._detail_url(self.inst_course, schedule)),
            ('patch', self._detail_url(self.inst_course, schedule)),
            ('delete', self._detail_url(self.inst_course, schedule)),
            ('post', self._action_url(self.inst_course, schedule, 'activate')),
        ):
            r = getattr(self.client, method)(url, {}, format='json')
            self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND, f'{method} {url}')
            self.assertEqual(r.data['message'], 'Course not found.')

    def test_institution_cannot_manage_solo_course(self):
        schedule = self._make_schedule(self.solo_course)
        self.client.force_authenticate(self.institution_user)
        r = self.client.get(self._detail_url(self.solo_course, schedule))
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_wrong_course_schedule_id_404(self):
        other_schedule = self._make_schedule(self.solo_course)
        self.client.force_authenticate(self.institution_user)
        r = self.client.get(self._detail_url(self.inst_course, other_schedule))
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_learner_gets_403(self):
        self.client.force_authenticate(self.learner)
        r = self.client.get(self._list_url(self.inst_course))
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_unverified_instructor_gets_403(self):
        unverified = User.objects.create_user(
            email='unverified@example.com', password='pw12345!',
            full_name='Unverified', user_type='instructor', is_email_verified=True,
        )
        self.client.force_authenticate(unverified)
        r = self.client.get(self._list_url(self.inst_course))
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)


class EnrollmentConstraintTests(CourseScheduleTestBase):
    def test_duplicate_self_paced_enrollment_rejected(self):
        Enrollment.objects.create(user=self.learner, course=self.solo_course)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Enrollment.objects.create(user=self.learner, course=self.solo_course)

    def test_same_user_two_schedules_of_one_course_allowed(self):
        s1 = self._make_schedule(self.solo_course)
        s2 = self._make_schedule(self.solo_course)
        Enrollment.objects.create(user=self.learner, course=self.solo_course, schedule=s1)
        Enrollment.objects.create(user=self.learner, course=self.solo_course, schedule=s2)
        self.assertEqual(
            Enrollment.objects.filter(user=self.learner, course=self.solo_course).count(), 2,
        )

    def test_duplicate_schedule_enrollment_rejected(self):
        s1 = self._make_schedule(self.solo_course)
        Enrollment.objects.create(user=self.learner, course=self.solo_course, schedule=s1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Enrollment.objects.create(user=self.learner, course=self.solo_course, schedule=s1)

    def test_self_paced_plus_schedule_enrollment_coexist(self):
        s1 = self._make_schedule(self.solo_course)
        Enrollment.objects.create(user=self.learner, course=self.solo_course)
        Enrollment.objects.create(user=self.learner, course=self.solo_course, schedule=s1)
        self.assertEqual(
            Enrollment.objects.filter(user=self.learner, course=self.solo_course).count(), 2,
        )


class AdvanceSchedulesTaskTests(CourseScheduleTestBase):
    def test_flips_past_start_scheduled_to_ongoing(self):
        now = timezone.now()
        schedule = self._make_schedule(
            self.solo_course, status_value='scheduled',
            enrollment_opens_at=now - timedelta(days=10),
            enrollment_closes_at=now - timedelta(days=5),
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(days=30),
        )
        result = advance_course_schedules_task()
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, 'ongoing')
        self.assertEqual(result, {'started': 1, 'completed': 0})

    def test_flips_past_end_ongoing_to_completed(self):
        now = timezone.now()
        schedule = self._make_schedule(
            self.solo_course, status_value='ongoing',
            enrollment_opens_at=now - timedelta(days=40),
            enrollment_closes_at=now - timedelta(days=35),
            start_date=now - timedelta(days=30),
            end_date=now - timedelta(hours=1),
        )
        result = advance_course_schedules_task()
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, 'completed')
        self.assertEqual(result, {'started': 0, 'completed': 1})

    def test_leaves_future_and_open_ended_rows_alone(self):
        now = timezone.now()
        future = self._make_schedule(self.solo_course, status_value='scheduled')
        open_ended = self._make_schedule(
            self.solo_course, status_value='ongoing',
            enrollment_opens_at=now - timedelta(days=40),
            enrollment_closes_at=now - timedelta(days=35),
            start_date=now - timedelta(days=30),
            end_date=None,
        )
        result = advance_course_schedules_task()
        future.refresh_from_db()
        open_ended.refresh_from_db()
        self.assertEqual(future.status, 'scheduled')
        self.assertEqual(open_ended.status, 'ongoing')
        self.assertEqual(result, {'started': 0, 'completed': 0})


# =============================================================================
# Phase 2 — cohort enrollment + drip authoring
# =============================================================================

class ScheduleEnrollmentApiTests(CourseScheduleTestBase):
    def _enroll_url(self, course):
        return reverse('courses:course-enroll', kwargs={'slug': course.slug})

    def test_enroll_into_open_schedule(self):
        schedule = self._make_schedule(self.solo_course, status_value='scheduled')
        self.client.force_authenticate(self.learner)
        r = self.client.post(self._enroll_url(self.solo_course),
                             {'schedule_id': schedule.pk}, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['data']['schedule'], schedule.pk)
        enrollment = Enrollment.objects.get(user=self.learner, schedule=schedule)
        self.assertTrue(enrollment.is_active)

    def test_self_paced_enroll_unchanged(self):
        self.client.force_authenticate(self.learner)
        r = self.client.post(self._enroll_url(self.solo_course), {}, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(r.data['data']['schedule'])

    def test_unknown_schedule_id_404(self):
        self.client.force_authenticate(self.learner)
        r = self.client.post(self._enroll_url(self.solo_course),
                             {'schedule_id': 999999}, format='json')
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_draft_schedule_not_enrollable(self):
        schedule = self._make_schedule(self.solo_course)  # draft
        self.client.force_authenticate(self.learner)
        r = self.client.post(self._enroll_url(self.solo_course),
                             {'schedule_id': schedule.pk}, format='json')
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_window_not_open_yet_422(self):
        now = timezone.now()
        schedule = self._make_schedule(
            self.solo_course, status_value='scheduled',
            enrollment_opens_at=now + timedelta(days=1),
            enrollment_closes_at=now + timedelta(days=5),
            start_date=now + timedelta(days=6),
            end_date=now + timedelta(days=30),
        )
        self.client.force_authenticate(self.learner)
        r = self.client.post(self._enroll_url(self.solo_course),
                             {'schedule_id': schedule.pk}, format='json')
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_window_closed_422(self):
        now = timezone.now()
        schedule = self._make_schedule(
            self.solo_course, status_value='scheduled',
            enrollment_opens_at=now - timedelta(days=10),
            enrollment_closes_at=now - timedelta(hours=1),
            start_date=now + timedelta(days=1),
            end_date=now + timedelta(days=30),
        )
        self.client.force_authenticate(self.learner)
        r = self.client.post(self._enroll_url(self.solo_course),
                             {'schedule_id': schedule.pk}, format='json')
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_capacity_full_422(self):
        schedule = self._make_schedule(self.solo_course, status_value='scheduled', max_seats=1)
        other = User.objects.create_user(
            email='learner2@example.com', password='pw12345!',
            full_name='Learner Two', user_type='learner', is_email_verified=True,
        )
        Enrollment.objects.create(user=other, course=self.solo_course, schedule=schedule)
        self.client.force_authenticate(self.learner)
        r = self.client.post(self._enroll_url(self.solo_course),
                             {'schedule_id': schedule.pk}, format='json')
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(r.data['message'], 'This cohort is full.')

    def test_duplicate_cohort_enroll_422(self):
        schedule = self._make_schedule(self.solo_course, status_value='scheduled')
        Enrollment.objects.create(user=self.learner, course=self.solo_course, schedule=schedule)
        self.client.force_authenticate(self.learner)
        r = self.client.post(self._enroll_url(self.solo_course),
                             {'schedule_id': schedule.pk}, format='json')
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)


class DripAuthoringTests(CourseScheduleTestBase):
    def _section_create_url(self, course):
        return reverse('courses:section-create', kwargs={'course_id': course.pk})

    def test_published_course_with_ongoing_schedule_is_editable(self):
        now = timezone.now()
        self._make_schedule(
            self.solo_course, status_value='ongoing',
            enrollment_opens_at=now - timedelta(days=10),
            enrollment_closes_at=now - timedelta(days=5),
            start_date=now - timedelta(days=4),
            end_date=now + timedelta(days=30),
        )
        self.client.force_authenticate(self.solo)
        unlock = (now + timedelta(days=7)).isoformat()
        r = self.client.post(
            self._section_create_url(self.solo_course),
            {'title': 'Week 2', 'position': 1, 'unlocks_at': unlock},
            format='json',
        )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        section = CourseSection.objects.get(course=self.solo_course, title='Week 2')
        self.assertIsNotNone(section.unlocks_at)

    def test_published_course_with_scheduled_not_yet_started_is_editable(self):
        # Carve-out also covers `scheduled` (pre-start), not just `ongoing` —
        # instructors can author ahead of the cohort's start_date.
        self._make_schedule(self.solo_course, status_value='scheduled')
        self.client.force_authenticate(self.solo)
        r = self.client.post(
            self._section_create_url(self.solo_course),
            {'title': 'Week 2', 'position': 1},
            format='json',
        )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)

    def test_published_course_without_any_live_schedule_still_locked(self):
        self._make_schedule(
            self.solo_course, status_value='completed',
            start_date=timezone.now() - timedelta(days=60),
            end_date=timezone.now() - timedelta(days=10),
        )
        self.client.force_authenticate(self.solo)
        r = self.client.post(
            self._section_create_url(self.solo_course),
            {'title': 'Week 2', 'position': 1},
            format='json',
        )
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_edit_of_already_released_section_blocked(self):
        self._make_schedule(
            self.solo_course, status_value='ongoing',
            enrollment_opens_at=timezone.now() - timedelta(days=10),
            enrollment_closes_at=timezone.now() - timedelta(days=5),
            start_date=timezone.now() - timedelta(days=4),
            end_date=timezone.now() + timedelta(days=30),
        )
        section = CourseSection.objects.create(
            course=self.solo_course, title='Week 1', position=1,
        )
        self.client.force_authenticate(self.solo)
        url = reverse('courses:section-detail', kwargs={'section_id': section.pk})
        r = self.client.patch(url, {'title': 'Week 1 (revised)'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(
            r.data['message'],
            'This content has already been released to learners and cannot be edited.',
        )

    def test_edit_of_not_yet_released_section_allowed(self):
        self._make_schedule(
            self.solo_course, status_value='ongoing',
            enrollment_opens_at=timezone.now() - timedelta(days=10),
            enrollment_closes_at=timezone.now() - timedelta(days=5),
            start_date=timezone.now() - timedelta(days=4),
            end_date=timezone.now() + timedelta(days=30),
        )
        section = CourseSection.objects.create(
            course=self.solo_course, title='Week 2', position=2,
            unlocks_at=timezone.now() + timedelta(days=3),
        )
        self.client.force_authenticate(self.solo)
        url = reverse('courses:section-detail', kwargs={'section_id': section.pk})
        r = self.client.patch(url, {'title': 'Week 2 (revised)'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)


# =============================================================================
# Phase 3 — learner release gates
# =============================================================================

class LearnerReleaseGateTests(CourseScheduleTestBase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        # An ongoing cohort the learner belongs to.
        self.schedule = self._make_schedule(
            self.solo_course, status_value='ongoing',
            enrollment_opens_at=now - timedelta(days=10),
            enrollment_closes_at=now - timedelta(days=5),
            start_date=now - timedelta(days=4),
            end_date=now + timedelta(days=30),
        )
        self.enrollment = Enrollment.objects.create(
            user=self.learner, course=self.solo_course, schedule=self.schedule,
        )
        # Week 1 released, week 2 drip-locked.
        self.open_section = CourseSection.objects.create(
            course=self.solo_course, title='Week 1', position=1,
        )
        self.locked_section = CourseSection.objects.create(
            course=self.solo_course, title='Week 2', position=2,
            unlocks_at=now + timedelta(days=3),
        )
        self.open_lecture = self._make_lecture(self.open_section, 'L1')
        self.locked_lecture = self._make_lecture(self.locked_section, 'L2')

    @staticmethod
    def _make_lecture(section, title):
        lecture = Lecture.objects.create(
            section=section, title=title,
            lecture_type=Lecture.LectureType.ARTICLE,
            article_content='Content long enough to read.',
        )
        SectionContent.objects.create(
            section=section, item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lecture.pk, position=1,
        )
        return lecture

    def _curriculum_url(self):
        return reverse('courses:learner-curriculum', kwargs={'slug': self.solo_course.slug})

    def _lecture_url(self, lecture):
        return reverse('courses:learner-lecture-detail', kwargs={'lecture_id': lecture.pk})

    def _progress_url(self, lecture):
        return reverse('courses:learner-lecture-progress', kwargs={'lecture_id': lecture.pk})

    def test_curriculum_marks_locked_sections(self):
        self.client.force_authenticate(self.learner)
        r = self.client.get(self._curriculum_url())
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        by_title = {s['title']: s for s in r.data['data']['sections']}
        self.assertFalse(by_title['Week 1']['is_locked'])
        self.assertTrue(by_title['Week 2']['is_locked'])
        self.assertIsNotNone(by_title['Week 2']['unlocks_at'])

    def test_open_lecture_accessible(self):
        self.client.force_authenticate(self.learner)
        r = self.client.get(self._lecture_url(self.open_lecture))
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_locked_lecture_detail_422(self):
        self.client.force_authenticate(self.learner)
        r = self.client.get(self._lecture_url(self.locked_lecture))
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(r.data['message'], 'This content has not been released yet.')

    def test_locked_lecture_progress_write_422(self):
        self.client.force_authenticate(self.learner)
        r = self.client.post(
            self._progress_url(self.locked_lecture),
            {'watched_seconds': 0, 'is_completed': True},
            format='json',
        )
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_pre_start_cohort_blocks_all_content(self):
        future_start = timezone.now() + timedelta(days=2)
        CourseSchedule.objects.filter(pk=self.schedule.pk).update(
            status='scheduled', start_date=future_start,
        )
        self.client.force_authenticate(self.learner)

        r = self.client.get(self._curriculum_url())
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(all(s['is_locked'] for s in r.data['data']['sections']))

        r = self.client.get(self._lecture_url(self.open_lecture))
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(r.data['message'], 'This course has not started yet.')

    def test_instructor_bypasses_all_locks(self):
        self.client.force_authenticate(self.solo)
        r = self.client.get(self._lecture_url(self.locked_lecture))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        r = self.client.get(self._curriculum_url())
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(any(s['is_locked'] for s in r.data['data']['sections']))

    def test_self_paced_learner_still_drip_locked(self):
        selfpaced = User.objects.create_user(
            email='sp-learner@example.com', password='pw12345!',
            full_name='SP Learner', user_type='learner', is_email_verified=True,
        )
        Enrollment.objects.create(user=selfpaced, course=self.solo_course)
        self.client.force_authenticate(selfpaced)

        r = self.client.get(self._lecture_url(self.open_lecture))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        r = self.client.get(self._lecture_url(self.locked_lecture))
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
