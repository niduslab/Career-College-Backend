"""
Tests for the co-instructor invitation pipeline.

Covers: create, list, revoke, my-invites, accept, decline, expiry task,
403/404 policy, editability guard, race-condition guards, token visibility.
"""
import uuid
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, User
from courses.models import CourseInstructorInvite, NidusCourse
from courses.tasks import expire_instructor_invites_task


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class InviteTestBase(APITestCase):
    """
    Shared fixtures for all invite tests.

    Creates:
      - owner         : verified instructor who owns the course
      - invitee       : verified instructor who will be invited
      - third         : verified instructor not involved
      - course        : draft course owned by owner
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner = cls._make_instructor('inv_owner@example.com', 'Owner User', verified=True)
        cls.invitee = cls._make_instructor('inv_invitee@example.com', 'Invitee User', verified=True)
        cls.third = cls._make_instructor('inv_third@example.com', 'Third User', verified=True)

        cls.course = NidusCourse.objects.create(
            created_by=cls.owner,
            title='Invite Test Course',
            description='A course for testing invitations.',
        )
        cls.course.instructors.add(cls.owner)

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
        self.client.force_authenticate(user=self.owner)

    def _set_status(self, s):
        NidusCourse.objects.filter(pk=self.course.pk).update(status=s)
        self.course.refresh_from_db()

    def _create_url(self):
        return reverse('courses:course-instructor-invite-create', kwargs={'pk': self.course.pk})

    def _list_url(self):
        return reverse('courses:course-instructor-invite-list', kwargs={'pk': self.course.pk})

    def _revoke_url(self, invite_id):
        return reverse('courses:course-instructor-invite-revoke', kwargs={
            'pk': self.course.pk, 'invite_id': invite_id,
        })

    def _my_url(self):
        return reverse('courses:my-invite-list')

    def _accept_url(self, token):
        return reverse('courses:invite-accept', kwargs={'token': token})

    def _decline_url(self, token):
        return reverse('courses:invite-decline', kwargs={'token': token})

    def _make_invite(self, invitee=None, status_val=CourseInstructorInvite.STATUS_PENDING,
                     expires_days=7):
        invitee = invitee or self.invitee
        return CourseInstructorInvite.objects.create(
            course=self.course,
            invited_by=self.owner,
            invited_user=invitee,
            expires_at=timezone.now() + timedelta(days=expires_days),
            status=status_val,
        )


# ---------------------------------------------------------------------------
# Create invite
# ---------------------------------------------------------------------------

class CreateInviteTests(InviteTestBase):

    def test_owner_can_send_invite(self):
        # Invite email is sent via dispatch(INVITE_SENT) on transaction.on_commit,
        # which never fires inside this TestCase's outer transaction — no patch needed.
        response = self.client.post(self._create_url(), {'email': self.invitee.email}, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data']['status'], 'pending')
        # token must NOT appear in owner-facing response (finding #7)
        self.assertNotIn('token', response.data['data'])
        self.assertTrue(CourseInstructorInvite.objects.filter(
            course=self.course, invited_user=self.invitee, status='pending'
        ).exists())

    def test_co_instructor_gets_404_not_403(self):
        """Numeric-ID endpoint: co-instructor cannot send invites, gets 404 per policy."""
        self.course.instructors.add(self.invitee)
        self.client.force_authenticate(user=self.invitee)
        response = self.client.post(self._create_url(), {'email': self.third.email}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.course.instructors.remove(self.invitee)

    def test_self_invite_blocked(self):
        response = self.client.post(self._create_url(), {'email': self.owner.email}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('cannot invite yourself', response.data['message'])

    def test_already_instructor_blocked(self):
        self.course.instructors.add(self.third)
        response = self.client.post(self._create_url(), {'email': self.third.email}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already an instructor', response.data['message'])
        self.course.instructors.remove(self.third)

    def test_duplicate_pending_invite_blocked(self):
        self._make_invite()
        response = self.client.post(self._create_url(), {'email': self.invitee.email}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('pending invite already exists', response.data['message'])

    def test_non_instructor_email_blocked(self):
        learner = User.objects.create_user(
            email='learner@example.com',
            password='pw12345!',
            full_name='Learner',
            user_type='learner',
            is_email_verified=True,
        )
        response = self.client.post(self._create_url(), {'email': learner.email}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('No verified instructor found', response.data['message'])

    def test_non_editable_course_blocked(self):
        self._set_status('published')
        response = self.client.post(self._create_url(), {'email': self.invitee.email}, format='json')
        self.assertIn(response.status_code, [status.HTTP_422_UNPROCESSABLE_ENTITY, status.HTTP_403_FORBIDDEN])

    def test_unknown_course_returns_404(self):
        url = reverse('courses:course-instructor-invite-create', kwargs={'pk': 99999})
        response = self.client.post(url, {'email': self.invitee.email}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# List invites
# ---------------------------------------------------------------------------

class ListInvitesTests(InviteTestBase):

    def setUp(self):
        super().setUp()
        self.invite = self._make_invite()

    def tearDown(self):
        CourseInstructorInvite.objects.all().delete()

    def test_owner_sees_invite_list(self):
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        results = response.data['data']['results']
        self.assertEqual(len(results), 1)

    def test_token_not_in_owner_list_response(self):
        """Owner list must never expose the token (finding #7)."""
        response = self.client.get(self._list_url())
        result = response.data['data']['results'][0]
        self.assertNotIn('token', result)

    def test_co_instructor_gets_404(self):
        """Numeric-ID endpoint: co-instructor sees 404, not 403."""
        self.course.instructors.add(self.invitee)
        self.client.force_authenticate(user=self.invitee)
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.course.instructors.remove(self.invitee)

    def test_status_filter_pending(self):
        response = self.client.get(self._list_url() + '?status=pending')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']['results']), 1)

    def test_status_filter_accepted_returns_empty(self):
        response = self.client.get(self._list_url() + '?status=accepted')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']['results']), 0)

    def test_invalid_status_filter_returns_400(self):
        response = self.client.get(self._list_url() + '?status=bogus')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Revoke invite
# ---------------------------------------------------------------------------

class RevokeInviteTests(InviteTestBase):

    def setUp(self):
        super().setUp()
        self.invite = self._make_invite()

    def tearDown(self):
        CourseInstructorInvite.objects.all().delete()

    def test_owner_can_revoke_pending_invite(self):
        response = self.client.delete(self._revoke_url(self.invite.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, CourseInstructorInvite.STATUS_REVOKED)

    def test_co_instructor_gets_404(self):
        """Numeric-ID endpoint: co-instructor gets 404, not 403."""
        self.course.instructors.add(self.invitee)
        self.client.force_authenticate(user=self.invitee)
        response = self.client.delete(self._revoke_url(self.invite.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.course.instructors.remove(self.invitee)

    def test_non_pending_invite_cannot_be_revoked(self):
        self.invite.status = CourseInstructorInvite.STATUS_ACCEPTED
        self.invite.save(update_fields=['status', 'updated_at'])
        response = self.client.delete(self._revoke_url(self.invite.pk))
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_unknown_invite_returns_404(self):
        response = self.client.delete(self._revoke_url(99999))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# My received invites
# ---------------------------------------------------------------------------

class MyInvitesTests(InviteTestBase):

    def setUp(self):
        super().setUp()
        self.invite = self._make_invite()
        self.client.force_authenticate(user=self.invitee)

    def tearDown(self):
        CourseInstructorInvite.objects.all().delete()

    def test_invitee_sees_pending_by_default(self):
        response = self.client.get(self._my_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'pending')

    def test_token_visible_to_invitee(self):
        """Invitee-facing endpoint must include token."""
        response = self.client.get(self._my_url())
        result = response.data['data']['results'][0]
        self.assertIn('token', result)
        self.assertEqual(str(self.invite.token), result['token'])

    def test_status_filter_accepted_returns_empty(self):
        response = self.client.get(self._my_url() + '?status=accepted')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']['results']), 0)

    def test_invalid_status_returns_400(self):
        response = self.client.get(self._my_url() + '?status=nope')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_other_instructors_invites_not_visible(self):
        """Invitee only sees their own invites."""
        self.client.force_authenticate(user=self.third)
        response = self.client.get(self._my_url())
        self.assertEqual(len(response.data['data']['results']), 0)


# ---------------------------------------------------------------------------
# Accept invite
# ---------------------------------------------------------------------------

class AcceptInviteTests(InviteTestBase):

    def setUp(self):
        super().setUp()
        self.invite = self._make_invite()
        self.client.force_authenticate(user=self.invitee)

    def tearDown(self):
        CourseInstructorInvite.objects.all().delete()
        self.course.instructors.set([self.owner])

    def test_invitee_can_accept(self):
        response = self.client.post(self._accept_url(self.invite.token))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, CourseInstructorInvite.STATUS_ACCEPTED)
        self.assertIsNotNone(self.invite.responded_at)
        self.assertIn(self.invitee, self.course.instructors.all())

    def test_wrong_user_gets_404(self):
        self.client.force_authenticate(user=self.third)
        response = self.client.post(self._accept_url(self.invite.token))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_token_returns_404(self):
        response = self.client.post(self._accept_url(uuid.uuid4()))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_revoked_invite_cannot_be_accepted(self):
        self.invite.status = CourseInstructorInvite.STATUS_REVOKED
        self.invite.save(update_fields=['status', 'updated_at'])
        response = self.client.post(self._accept_url(self.invite.token))
        self.assertEqual(response.status_code, status.HTTP_410_GONE)

    def test_already_accepted_invite_cannot_be_re_accepted(self):
        self.invite.status = CourseInstructorInvite.STATUS_ACCEPTED
        self.invite.save(update_fields=['status', 'updated_at'])
        response = self.client.post(self._accept_url(self.invite.token))
        self.assertEqual(response.status_code, status.HTTP_410_GONE)

    def test_expired_invite_cannot_be_accepted(self):
        self.invite.expires_at = timezone.now() - timedelta(days=1)
        self.invite.save(update_fields=['expires_at', 'updated_at'])
        response = self.client.post(self._accept_url(self.invite.token))
        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        # DB status is swept to 'expired' by expire_instructor_invites_task, not inline.
        # Confirm the invitee was NOT added to instructors.
        self.assertNotIn(self.invitee, self.course.instructors.all())

    def test_accept_on_published_course_blocked(self):
        """Finding #3: accept must be blocked if course is no longer editable."""
        self._set_status('published')
        response = self.client.post(self._accept_url(self.invite.token))
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertNotIn(self.invitee, self.course.instructors.all())

    def test_accept_response_includes_token(self):
        """Invitee accept response includes token."""
        response = self.client.post(self._accept_url(self.invite.token))
        self.assertIn('token', response.data['data'])

    def test_owner_cannot_accept_invite_intended_for_invitee(self):
        """Owner is not the invited_user, so they get 404."""
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(self._accept_url(self.invite.token))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# Decline invite
# ---------------------------------------------------------------------------

class DeclineInviteTests(InviteTestBase):

    def setUp(self):
        super().setUp()
        self.invite = self._make_invite()
        self.client.force_authenticate(user=self.invitee)

    def tearDown(self):
        CourseInstructorInvite.objects.all().delete()

    def test_invitee_can_decline(self):
        response = self.client.post(self._decline_url(self.invite.token))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, CourseInstructorInvite.STATUS_DECLINED)
        self.assertNotIn(self.invitee, self.course.instructors.all())

    def test_wrong_user_gets_404(self):
        self.client.force_authenticate(user=self.third)
        response = self.client.post(self._decline_url(self.invite.token))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_already_declined_cannot_be_declined_again(self):
        self.invite.status = CourseInstructorInvite.STATUS_DECLINED
        self.invite.save(update_fields=['status', 'updated_at'])
        response = self.client.post(self._decline_url(self.invite.token))
        self.assertEqual(response.status_code, status.HTTP_410_GONE)

    def test_revoked_invite_cannot_be_declined(self):
        self.invite.status = CourseInstructorInvite.STATUS_REVOKED
        self.invite.save(update_fields=['status', 'updated_at'])
        response = self.client.post(self._decline_url(self.invite.token))
        self.assertEqual(response.status_code, status.HTTP_410_GONE)

    def test_declined_invite_allows_new_invite(self):
        """After decline, owner can re-invite the same user."""
        self.invite.status = CourseInstructorInvite.STATUS_DECLINED
        self.invite.save(update_fields=['status', 'updated_at'])
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(self._create_url(), {'email': self.invitee.email}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Expiry task
# ---------------------------------------------------------------------------

class ExpireInvitesTaskTests(InviteTestBase):

    def tearDown(self):
        CourseInstructorInvite.objects.all().delete()

    def test_bulk_expiry_marks_past_due_invites(self):
        past_invite = CourseInstructorInvite.objects.create(
            course=self.course,
            invited_by=self.owner,
            invited_user=self.invitee,
            expires_at=timezone.now() - timedelta(hours=1),
            status=CourseInstructorInvite.STATUS_PENDING,
        )
        future_invite = CourseInstructorInvite.objects.create(
            course=self.course,
            invited_by=self.owner,
            invited_user=self.third,
            expires_at=timezone.now() + timedelta(days=7),
            status=CourseInstructorInvite.STATUS_PENDING,
        )

        result = expire_instructor_invites_task()

        self.assertEqual(result['expired'], 1)
        past_invite.refresh_from_db()
        self.assertEqual(past_invite.status, CourseInstructorInvite.STATUS_EXPIRED)
        future_invite.refresh_from_db()
        self.assertEqual(future_invite.status, CourseInstructorInvite.STATUS_PENDING)

    def test_non_pending_invites_not_affected(self):
        CourseInstructorInvite.objects.create(
            course=self.course,
            invited_by=self.owner,
            invited_user=self.invitee,
            expires_at=timezone.now() - timedelta(hours=1),
            status=CourseInstructorInvite.STATUS_ACCEPTED,
        )
        result = expire_instructor_invites_task()
        self.assertEqual(result['expired'], 0)
