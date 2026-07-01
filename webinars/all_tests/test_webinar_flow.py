"""
Core webinar slice: institution authoring → host assignment → host direct publish
→ catalog → learner registration, plus the access-denied policy.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, PartnerInstitutionProfile, User
from webinars.models import Webinar, WebinarRegistration


class WebinarFlowTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        # Verified partner institution (profile auto-created by signal).
        cls.institution_user = User.objects.create_user(
            email='inst@example.com', password='pw12345!',
            full_name='Acme Institute', user_type='partner_institution',
            is_email_verified=True,
        )
        PartnerInstitutionProfile.objects.filter(user=cls.institution_user).update(
            institution_name='Acme Institute', is_verified=True, is_active=True,
        )
        cls.institution = cls.institution_user.partner_institution_profile

        # A second institution (cross-institution 404 checks).
        cls.other_inst_user = User.objects.create_user(
            email='other-inst@example.com', password='pw12345!',
            full_name='Other Institute', user_type='partner_institution',
            is_email_verified=True,
        )
        PartnerInstitutionProfile.objects.filter(user=cls.other_inst_user).update(
            institution_name='Other Institute', is_verified=True, is_active=True,
        )
        cls.other_institution = cls.other_inst_user.partner_institution_profile

        # Institution-onboarded expert (active affiliated, verified instructor).
        cls.expert = User.objects.create_user(
            email='expert@example.com', password='pw12345!',
            full_name='Dr Expert', user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.expert).update(
            is_verified=True, affiliated_institution=cls.institution,
            affiliation_status='active', onboarding_source='institution',
        )

        # An expert that is NOT affiliated with cls.institution.
        cls.foreign_expert = User.objects.create_user(
            email='foreign@example.com', password='pw12345!',
            full_name='Foreign Expert', user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.foreign_expert).update(
            is_verified=True, affiliated_institution=cls.other_institution,
            affiliation_status='active', onboarding_source='institution',
        )

        cls.learner = User.objects.create_user(
            email='learner@example.com', password='pw12345!',
            full_name='Lana Learner', user_type='learner', is_email_verified=True,
        )
        cls.learner2 = User.objects.create_user(
            email='learner2@example.com', password='pw12345!',
            full_name='Other Learner', user_type='learner', is_email_verified=True,
        )

        cls.admin = User.objects.create_user(
            email='admin@example.com', password='pw12345!', full_name='Admin',
            user_type='admin', is_email_verified=True, is_staff=True,
        )

    # ---- helpers -----------------------------------------------------------

    def _make_webinar(self, title='Live AI Workshop', *, institution=None,
                      with_host=True, meeting_url='https://meet.example.com/abc',
                      status_value='draft', max_capacity=None):
        institution = institution or self.institution
        webinar = Webinar.objects.create(
            created_by=institution.user,
            partner_institution=institution,
            title=title,
            description='A well-described live session.',
            scheduled_at=timezone.now() + timedelta(days=7),
            duration_minutes=60,
            meeting_url=meeting_url,
            host_expert=self.expert if with_host else None,
            max_capacity=max_capacity,
        )
        if status_value != 'draft':
            webinar.status = status_value
            webinar.save()
        return webinar

    def _create_url(self):
        return reverse('webinars:webinar-create')

    def _host_url(self, pk):
        return reverse('webinars:webinar-host', kwargs={'pk': pk})

    def _publish_url(self, pk):
        return reverse('webinars:webinar-publish', kwargs={'pk': pk})

    # ---- 1. creation -------------------------------------------------------

    def test_institution_creates_webinar_with_guests(self):
        self.client.force_authenticate(self.institution_user)
        payload = {
            'title': 'Intro to Live AI',
            'description': 'A live session.',
            'scheduled_at': (timezone.now() + timedelta(days=3)).isoformat(),
            'duration_minutes': 90,
            'meeting_url': 'https://zoom.example.com/xyz',
            'meeting_provider': 'zoom',
            'guest_speakers': [
                {'full_name': 'Jane Guest', 'title': 'CTO, Acme', 'bio': 'Expert in X.'},
            ],
        }
        r = self.client.post(self._create_url(), payload, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        data = r.data['data']
        self.assertEqual(data['status'], 'draft')
        self.assertIsNone(data['host_expert'])
        self.assertEqual(len(data['guest_speakers']), 1)
        self.assertEqual(data['guest_speakers'][0]['full_name'], 'Jane Guest')

        webinar = Webinar.objects.get(pk=data['id'])
        self.assertEqual(webinar.partner_institution_id, self.institution.id)
        self.assertEqual(webinar.created_by_id, self.institution_user.id)

    def test_create_with_malformed_guest_returns_400(self):
        self.client.force_authenticate(self.institution_user)
        payload = {
            'title': 'Bad Guest Webinar',
            'description': 'desc',
            'guest_speakers': [{'title': 'No name here'}],
        }
        r = self.client.post(self._create_url(), payload, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_institution_cannot_create(self):
        self.client.force_authenticate(self.expert)
        r = self.client.post(self._create_url(), {'title': 'Nope', 'description': 'd'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    # ---- 2. host assignment ------------------------------------------------

    def test_assign_host_success(self):
        webinar = self._make_webinar(with_host=False)
        self.client.force_authenticate(self.institution_user)
        r = self.client.post(self._host_url(webinar.pk), {'expert_user_id': self.expert.id}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        webinar.refresh_from_db()
        self.assertEqual(webinar.host_expert_id, self.expert.id)

    def test_assign_non_affiliated_expert_returns_422(self):
        webinar = self._make_webinar(with_host=False)
        self.client.force_authenticate(self.institution_user)
        r = self.client.post(self._host_url(webinar.pk), {'expert_user_id': self.foreign_expert.id}, format='json')
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_assign_host_foreign_webinar_404(self):
        webinar = self._make_webinar(with_host=False)
        self.client.force_authenticate(self.other_inst_user)
        r = self.client.post(self._host_url(webinar.pk), {'expert_user_id': self.foreign_expert.id}, format='json')
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    # ---- 3. host direct publish (completeness + scoping) -------------------

    def test_publish_incomplete_returns_400(self):
        # Has a host (so the endpoint scope passes) but no meeting_url.
        webinar = self._make_webinar(meeting_url='')
        self.client.force_authenticate(self.expert)
        r = self.client.post(self._publish_url(webinar.pk))
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_host_publishes_directly(self):
        webinar = self._make_webinar()
        self.client.force_authenticate(self.expert)
        r = self.client.post(self._publish_url(webinar.pk))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        webinar.refresh_from_db()
        self.assertEqual(webinar.status, 'published')
        self.assertTrue(webinar.is_published)
        self.assertIsNotNone(webinar.published_at)

    def test_institution_user_cannot_publish_404(self):
        # The institution is created_by, not the host → outside the publish scope.
        webinar = self._make_webinar()
        self.client.force_authenticate(self.institution_user)
        r = self.client.post(self._publish_url(webinar.pk))
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    # ---- 5. catalog --------------------------------------------------------

    def test_catalog_hides_meeting_url(self):
        webinar = self._make_webinar(status_value='published')
        url = reverse('webinars:catalog-detail', kwargs={'slug': webinar.slug})
        r = self.client.get(url)  # AllowAny
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertNotIn('meeting_url', r.data['data'])

    def test_catalog_list_returns_published(self):
        self._make_webinar(title='Pub One', status_value='published')
        self._make_webinar(title='Draft One', with_host=False)  # draft, excluded
        r = self.client.get(reverse('webinars:catalog-list'))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        titles = [w['title'] for w in r.data['data']['results']]
        self.assertIn('Pub One', titles)
        self.assertNotIn('Draft One', titles)

    # ---- 6. registration ---------------------------------------------------

    def test_learner_registers_and_sees_meeting_url(self):
        webinar = self._make_webinar(status_value='published')
        reg_url = reverse('webinars:webinar-register', kwargs={'slug': webinar.slug})

        self.client.force_authenticate(self.learner)
        r = self.client.post(reg_url)
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)

        # Duplicate registration → 422.
        r = self.client.post(reg_url)
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

        # my-webinars detail exposes meeting_url.
        detail_url = reverse('webinars:my-webinars-detail', kwargs={'slug': webinar.slug})
        r = self.client.get(detail_url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['data']['webinar']['meeting_url'], webinar.meeting_url)

    def test_capacity_enforced(self):
        webinar = self._make_webinar(status_value='published', max_capacity=1)
        reg_url = reverse('webinars:webinar-register', kwargs={'slug': webinar.slug})

        self.client.force_authenticate(self.learner)
        self.assertEqual(self.client.post(reg_url).status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(self.learner2)
        r = self.client.post(reg_url)
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # ---- 7. access policy --------------------------------------------------

    def test_unpublished_catalog_slug_404(self):
        webinar = self._make_webinar()  # draft
        url = reverse('webinars:catalog-detail', kwargs={'slug': webinar.slug})
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_unregistered_my_webinar_slug_403(self):
        webinar = self._make_webinar(status_value='published')
        url = reverse('webinars:my-webinars-detail', kwargs={'slug': webinar.slug})
        self.client.force_authenticate(self.learner)
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_foreign_webinar_authoring_pk_404(self):
        webinar = self._make_webinar()  # owned by self.institution
        detail_url = reverse('webinars:webinar-detail', kwargs={'pk': webinar.pk})
        self.client.force_authenticate(self.other_inst_user)
        r = self.client.get(detail_url)
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    # ---- 8. institutional speakers -----------------------------------------

    def _detail_url(self, pk):
        return reverse('webinars:webinar-detail', kwargs={'pk': pk})

    def test_create_with_institutional_speakers(self):
        self.client.force_authenticate(self.institution_user)
        payload = {
            'title': 'Speakers Webinar',
            'description': 'desc',
            'scheduled_at': (timezone.now() + timedelta(days=3)).isoformat(),
            'duration_minutes': 60,
            'meeting_url': 'https://zoom.example.com/s',
            'institutional_speaker_ids': [self.expert.id],
        }
        r = self.client.post(self._create_url(), payload, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        speaker_ids = [s['id'] for s in r.data['data']['institutional_speakers']]
        self.assertEqual(speaker_ids, [self.expert.id])

    def test_create_with_foreign_speaker_returns_422(self):
        self.client.force_authenticate(self.institution_user)
        payload = {
            'title': 'Foreign Speaker Webinar',
            'description': 'desc',
            'scheduled_at': (timezone.now() + timedelta(days=3)).isoformat(),
            'duration_minutes': 60,
            'meeting_url': 'https://zoom.example.com/s',
            'institutional_speaker_ids': [self.foreign_expert.id],
        }
        r = self.client.post(self._create_url(), payload, format='json')
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_patch_clears_institutional_speakers(self):
        webinar = self._make_webinar()
        webinar.institutional_speakers.set([self.expert])
        self.client.force_authenticate(self.institution_user)
        r = self.client.patch(self._detail_url(webinar.pk), {'institutional_speaker_ids': []}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(webinar.institutional_speakers.count(), 0)

    # ---- 9. editing scope: host reads, institution edits -------------------

    def test_host_cannot_patch_webinar(self):
        webinar = self._make_webinar()  # host = self.expert
        self.client.force_authenticate(self.expert)
        r = self.client.patch(self._detail_url(webinar.pk), {'title': 'Hijacked Title'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_host_can_still_get_webinar(self):
        webinar = self._make_webinar()
        self.client.force_authenticate(self.expert)
        r = self.client.get(self._detail_url(webinar.pk))
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_institution_can_patch_webinar(self):
        webinar = self._make_webinar()
        self.client.force_authenticate(self.institution_user)
        r = self.client.patch(self._detail_url(webinar.pk), {'title': 'Renamed Webinar'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        webinar.refresh_from_db()
        self.assertEqual(webinar.title, 'Renamed Webinar')

    # ---- 10. host clear ----------------------------------------------------

    def test_clear_host(self):
        webinar = self._make_webinar()  # host assigned
        self.client.force_authenticate(self.institution_user)
        r = self.client.delete(self._host_url(webinar.pk))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        webinar.refresh_from_db()
        self.assertIsNone(webinar.host_expert_id)

    def test_clear_host_when_none_returns_422(self):
        webinar = self._make_webinar(with_host=False)
        self.client.force_authenticate(self.institution_user)
        r = self.client.delete(self._host_url(webinar.pk))
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # ---- 11. archive + rework transitions ----------------------------------

    def test_archive_then_rework(self):
        webinar = self._make_webinar(status_value='published')
        self.client.force_authenticate(self.institution_user)

        r = self.client.post(reverse('webinars:webinar-archive', kwargs={'pk': webinar.pk}))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        webinar.refresh_from_db()
        self.assertEqual(webinar.status, 'archived')
        self.assertFalse(webinar.is_published)

        r = self.client.post(reverse('webinars:webinar-rework', kwargs={'pk': webinar.pk}))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        webinar.refresh_from_db()
        self.assertEqual(webinar.status, 'draft')

    def test_admin_can_archive(self):
        webinar = self._make_webinar(status_value='published')
        self.client.force_authenticate(self.admin)
        r = self.client.post(reverse('webinars:webinar-archive', kwargs={'pk': webinar.pk}))
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    # ---- 12. registration reactivation + notifications ---------------------

    def test_cancelled_registration_reactivates(self):
        webinar = self._make_webinar(status_value='published')
        reg = WebinarRegistration.objects.create(user=self.learner, webinar=webinar, is_active=False)
        reg_url = reverse('webinars:webinar-register', kwargs={'slug': webinar.slug})
        self.client.force_authenticate(self.learner)
        r = self.client.post(reg_url)
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        reg.refresh_from_db()
        self.assertTrue(reg.is_active)
        self.assertEqual(
            WebinarRegistration.objects.filter(user=self.learner, webinar=webinar).count(), 1
        )

    def test_registration_dispatches_notification(self):
        from notifications.models import Notification
        webinar = self._make_webinar(status_value='published')
        reg_url = reverse('webinars:webinar-register', kwargs={'slug': webinar.slug})
        self.client.force_authenticate(self.learner)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(reg_url)
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.learner, event_type='webinar.registered'
            ).exists()
        )

    def test_publish_dispatches_notification_to_institution_and_host(self):
        from notifications.models import Notification
        webinar = self._make_webinar()
        self.client.force_authenticate(self.expert)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(self._publish_url(webinar.pk))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(
            Notification.objects.filter(
                event_type='webinar.published', recipient=self.institution_user
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event_type='webinar.published', recipient=self.expert
            ).exists()
        )
