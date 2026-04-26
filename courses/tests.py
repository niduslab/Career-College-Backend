from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from auth.models import InstructorProfile, PartnerInstitutionProfile, User
from courses.models import NidusCourse


class CourseFlowTests(APITestCase):
    def setUp(self):
        self.verified_instructor = User.objects.create_user(
            email='verified_instructor@example.com',
            password='Password123!',
            full_name='Verified Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        profile, _ = InstructorProfile.objects.get_or_create(user=self.verified_instructor)
        profile.is_verified = True
        profile.save(update_fields=['is_verified', 'updated_at'])

        self.unverified_instructor = User.objects.create_user(
            email='unverified_instructor@example.com',
            password='Password123!',
            full_name='Unverified Instructor',
            user_type='instructor',
            is_email_verified=True,
        )

        self.partner_user = User.objects.create_user(
            email='partner@example.com',
            password='Password123!',
            full_name='Partner Institution',
            user_type='partner_institution',
            is_email_verified=True,
        )
        self.partner = PartnerInstitutionProfile.objects.create(
            user=self.partner_user,
            institution_name='Global Tech College',
            is_active=True,
        )

    def test_unverified_instructor_cannot_create_course(self):
        self.client.force_authenticate(self.unverified_instructor)
        response = self.client.post(
            reverse('courses:course-create'),
            {
                'title': 'Django Masterclass',
                'description': 'Comprehensive DRF course',
                'price': '79.00',
                'language': 'English',
                'level': 'intermediate',
                'duration_minutes': 120,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_verified_instructor_can_create_course(self):
        self.client.force_authenticate(self.verified_instructor)
        response = self.client.post(
            reverse('courses:course-create'),
            {
                'title': 'Django Masterclass',
                'description': 'Comprehensive DRF course',
                'price': '79.00',
                'language': 'English',
                'level': 'intermediate',
                'duration_minutes': 120,
                'partner_institutions': [self.partner.id],
                'learning_objectives': [
                    {'text': 'Build REST APIs with DRF', 'display_order': 1},
                    {'text': 'Design scalable backend services', 'display_order': 2},
                ],
                'prerequisites': [
                    {'text': 'Basic Python knowledge', 'display_order': 1},
                ],
                'audiences': [
                    {'text': 'Backend developers', 'display_order': 1},
                ],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])
        self.assertEqual(NidusCourse.objects.count(), 1)

    def test_verified_instructor_can_update_existing_course(self):
        course = NidusCourse.objects.create(
            created_by=self.verified_instructor,
            title='Data Science Basics',
            slug='data-science-basics',
            description='Description',
            language='English',
            level='beginner',
            duration_minutes=45,
        )
        course.instructors.add(self.verified_instructor)

        self.client.force_authenticate(self.verified_instructor)
        response = self.client.patch(
            reverse('courses:course-detail', kwargs={'pk': course.id}),
            {
                'status': 'under_review',
                'duration_minutes': 90,
                'audiences': [{'text': 'Career switchers', 'display_order': 1}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        course.refresh_from_db()
        self.assertEqual(course.status, NidusCourse.CourseStatus.UNDER_REVIEW)
        self.assertEqual(course.duration_minutes, 90)
        self.assertEqual(course.audiences.count(), 1)
