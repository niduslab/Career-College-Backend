from django.test import RequestFactory
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import CourseCategory, NidusCourse
from courses.serializers import (
    NidusCourseCreateUpdateSerializer,
    NidusCourseSerializer,
)


class CourseMetadataTextFieldTests(APITestCase):
    """learning_objectives / prerequisites / audiences are now plain text fields."""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='meta_instructor@example.com',
            password='pw12345!',
            full_name='Meta Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.category = CourseCategory.objects.create(name='Programming')

    def _request(self):
        req = RequestFactory().post('/')
        req.user = self.instructor
        return req

    def test_create_persists_text_fields(self):
        serializer = NidusCourseCreateUpdateSerializer(
            data={
                'title': 'Metadata Course',
                'description': 'Course for metadata text fields.',
                'category': self.category.pk,
                'learning_objectives': 'Build APIs\nShip it',
                'prerequisites': 'Know Python',
                'audiences': 'Backend devs\nStudents',
            },
            context={'request': self._request()},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        course = serializer.save()

        course.refresh_from_db()
        self.assertEqual(course.learning_objectives, 'Build APIs\nShip it')
        self.assertEqual(course.prerequisites, 'Know Python')
        self.assertEqual(course.audiences, 'Backend devs\nStudents')

    def test_category_required_on_create(self):
        serializer = NidusCourseCreateUpdateSerializer(
            data={
                'title': 'No Category Course',
                'description': 'Missing category.',
            },
            context={'request': self._request()},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('category', serializer.errors)

    def test_fields_default_to_empty_string(self):
        course = NidusCourse.objects.create(
            created_by=self.instructor,
            title='Empty Meta Course',
            slug='empty-meta-course',
            description='No metadata set.',
        )
        self.assertEqual(course.learning_objectives, '')
        self.assertEqual(course.prerequisites, '')
        self.assertEqual(course.audiences, '')

    def test_read_serializer_returns_strings(self):
        course = NidusCourse.objects.create(
            created_by=self.instructor,
            title='Read Meta Course',
            slug='read-meta-course',
            description='Has metadata.',
            learning_objectives='A\nB',
            prerequisites='C',
            audiences='D',
        )
        data = NidusCourseSerializer(course).data
        self.assertEqual(data['learning_objectives'], 'A\nB')
        self.assertEqual(data['prerequisites'], 'C')
        self.assertEqual(data['audiences'], 'D')

    def test_whitespace_only_lines_are_stripped(self):
        serializer = NidusCourseCreateUpdateSerializer(
            data={
                'title': 'Whitespace Course',
                'description': 'Normalization check.',
                'category': self.category.pk,
                'learning_objectives': '  A  \n\n  B  \n   \n',
            },
            context={'request': self._request()},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        course = serializer.save()
        course.refresh_from_db()
        self.assertEqual(course.learning_objectives, 'A\nB')

    def test_all_whitespace_normalizes_to_empty_string(self):
        serializer = NidusCourseCreateUpdateSerializer(
            data={
                'title': 'All Whitespace Course',
                'description': 'Normalization check.',
                'category': self.category.pk,
                'prerequisites': '   \n   \n',
            },
            context={'request': self._request()},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        course = serializer.save()
        course.refresh_from_db()
        self.assertEqual(course.prerequisites, '')

    def test_update_replaces_text_field(self):
        course = NidusCourse.objects.create(
            created_by=self.instructor,
            title='Update Meta Course',
            slug='update-meta-course',
            description='Will change.',
            prerequisites='Old',
        )
        serializer = NidusCourseCreateUpdateSerializer(
            course,
            data={'prerequisites': 'New\nStuff'},
            partial=True,
            context={'request': self._request()},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        course.refresh_from_db()
        self.assertEqual(course.prerequisites, 'New\nStuff')


class CourseDeliveryModeTests(APITestCase):
    """NidusCourse.delivery_mode — set at creation, immutable afterward."""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='delivery_instructor@example.com',
            password='pw12345!',
            full_name='Delivery Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.category = CourseCategory.objects.create(name='Delivery Mode Category')

    def _request(self):
        req = RequestFactory().post('/')
        req.user = self.instructor
        return req

    def test_create_defaults_to_self_paced(self):
        serializer = NidusCourseCreateUpdateSerializer(
            data={
                'title': 'Default Delivery Course',
                'description': 'No delivery_mode passed.',
                'category': self.category.pk,
            },
            context={'request': self._request()},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        course = serializer.save()
        self.assertEqual(course.delivery_mode, NidusCourse.DeliveryMode.SELF_PACED)

    def test_create_with_scheduled_persists(self):
        serializer = NidusCourseCreateUpdateSerializer(
            data={
                'title': 'Cohort Course',
                'description': 'A cohort-delivered course.',
                'category': self.category.pk,
                'delivery_mode': 'scheduled',
            },
            context={'request': self._request()},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        course = serializer.save()
        self.assertEqual(course.delivery_mode, NidusCourse.DeliveryMode.SCHEDULED)

    def test_invalid_delivery_mode_returns_error(self):
        serializer = NidusCourseCreateUpdateSerializer(
            data={
                'title': 'Bad Delivery Course',
                'description': 'Invalid delivery_mode value.',
                'category': self.category.pk,
                'delivery_mode': 'weekly',
            },
            context={'request': self._request()},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('delivery_mode', serializer.errors)

    def test_patch_cannot_change_delivery_mode(self):
        course = NidusCourse.objects.create(
            created_by=self.instructor,
            title='Immutable Delivery Course',
            slug='immutable-delivery-course',
            description='Starts self-paced.',
        )
        serializer = NidusCourseCreateUpdateSerializer(
            course,
            data={'delivery_mode': 'scheduled'},
            partial=True,
            context={'request': self._request()},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('delivery_mode', serializer.errors)
        course.refresh_from_db()
        self.assertEqual(course.delivery_mode, NidusCourse.DeliveryMode.SELF_PACED)

    def test_patch_without_delivery_mode_is_unaffected(self):
        course = NidusCourse.objects.create(
            created_by=self.instructor,
            title='Untouched Delivery Course',
            slug='untouched-delivery-course',
            description='Starts self-paced.',
        )
        serializer = NidusCourseCreateUpdateSerializer(
            course,
            data={'title': 'Renamed Course'},
            partial=True,
            context={'request': self._request()},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        course.refresh_from_db()
        self.assertEqual(course.delivery_mode, NidusCourse.DeliveryMode.SELF_PACED)
        self.assertEqual(course.title, 'Renamed Course')
