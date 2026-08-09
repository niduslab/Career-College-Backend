from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import Enrollment, NidusCourse, Wishlist


class _WishlistFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='wishlist_instructor@example.com',
            password='pw12345!',
            full_name='Wishlist Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='wishlist_learner@example.com',
            password='pw12345!',
            full_name='Wishlist Learner',
            user_type='learner',
            is_email_verified=True,
        )
        cls.other_learner = User.objects.create_user(
            email='wishlist_other@example.com',
            password='pw12345!',
            full_name='Wishlist Other',
            user_type='learner',
            is_email_verified=True,
        )
        cls.unverified_learner = User.objects.create_user(
            email='wishlist_unverified@example.com',
            password='pw12345!',
            full_name='Wishlist Unverified',
            user_type='learner',
            is_email_verified=False,
        )

        cls.course_a = cls._make_course('Wishlist Course A', 'wishlist-course-a')
        cls.course_b = cls._make_course('Wishlist Course B', 'wishlist-course-b')
        cls.course_c = cls._make_course('Wishlist Course C', 'wishlist-course-c')
        cls.draft_course = cls._make_course(
            'Wishlist Draft', 'wishlist-draft', status=NidusCourse.CourseStatus.DRAFT,
        )

    @classmethod
    def _make_course(cls, title, slug, status=NidusCourse.CourseStatus.PUBLISHED):
        course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title=title,
            slug=slug,
            description='A course used by wishlist tests.',
            status=status,
        )
        course.instructors.add(cls.instructor)
        return course

    def toggle_url(self, slug):
        return reverse('courses:course-wishlist', kwargs={'slug': slug})


class WishlistTests(_WishlistFixtureMixin, APITestCase):
    def test_requires_authentication(self):
        response = self.client.get(reverse('courses:wishlist-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_instructor_is_forbidden(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(reverse('courses:wishlist-list'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unverified_learner_is_forbidden(self):
        self.client.force_authenticate(user=self.unverified_learner)
        response = self.client.get(reverse('courses:wishlist-list'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_add_returns_201_and_creates_row(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.post(self.toggle_url(self.course_a.slug))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Wishlist.objects.filter(user=self.learner, course=self.course_a).exists())
        self.assertEqual(response.data['data']['course']['slug'], self.course_a.slug)
        self.assertTrue(response.data['data']['course']['is_wishlisted'])

    def test_add_twice_is_idempotent(self):
        self.client.force_authenticate(user=self.learner)
        self.client.post(self.toggle_url(self.course_a.slug))
        response = self.client.post(self.toggle_url(self.course_a.slug))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Wishlist.objects.filter(user=self.learner).count(), 1)

    def test_duplicate_row_violates_db_constraint(self):
        Wishlist.objects.create(user=self.learner, course=self.course_a)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Wishlist.objects.create(user=self.learner, course=self.course_a)

    def test_remove_deletes_row(self):
        Wishlist.objects.create(user=self.learner, course=self.course_a)
        self.client.force_authenticate(user=self.learner)
        response = self.client.delete(self.toggle_url(self.course_a.slug))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Wishlist.objects.filter(user=self.learner).exists())

    def test_remove_when_not_wishlisted_returns_404(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.delete(self.toggle_url(self.course_a.slug))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unpublished_course_returns_404(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.post(self.toggle_url(self.draft_course.slug))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_slug_returns_404(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.post(self.toggle_url('no-such-course'))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_is_scoped_to_the_caller(self):
        Wishlist.objects.create(user=self.learner, course=self.course_a)
        Wishlist.objects.create(user=self.other_learner, course=self.course_b)

        self.client.force_authenticate(user=self.learner)
        response = self.client.get(reverse('courses:wishlist-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['course']['slug'], self.course_a.slug)

    def test_list_is_newest_first_and_nests_the_catalog_card(self):
        Wishlist.objects.create(user=self.learner, course=self.course_a)
        Wishlist.objects.create(user=self.learner, course=self.course_b)

        self.client.force_authenticate(user=self.learner)
        response = self.client.get(reverse('courses:wishlist-list'))

        results = response.data['data']['results']
        self.assertEqual(
            [row['course']['slug'] for row in results],
            [self.course_b.slug, self.course_a.slug],
        )
        self.assertIn('instructors', results[0]['course'])
        self.assertIn('created_at', results[0])


class CatalogWishlistFlagTests(_WishlistFixtureMixin, APITestCase):
    def test_anonymous_catalog_flags_are_false(self):
        response = self.client.get(reverse('courses:catalog-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['data']['results']
        self.assertTrue(results)
        self.assertTrue(all(row['is_wishlisted'] is False for row in results))

    def test_flag_costs_one_query_per_page_and_none_when_anonymous(self):
        """The id set is resolved once per page, never per row, and
        short-circuits entirely for anonymous callers."""
        url = reverse('courses:catalog-list')

        with self.assertNumQueries(3) as anonymous:
            # count + page + instructors prefetch. No wishlist lookup.
            self.client.get(url)

        self.client.force_authenticate(user=self.learner)
        with self.assertNumQueries(len(anonymous.captured_queries) + 1):
            self.client.get(url)

    def test_learner_catalog_flags_only_wishlisted_courses(self):
        Wishlist.objects.create(user=self.learner, course=self.course_b)
        self.client.force_authenticate(user=self.learner)

        response = self.client.get(reverse('courses:catalog-list'))
        flags = {row['slug']: row['is_wishlisted'] for row in response.data['data']['results']}

        self.assertTrue(flags[self.course_b.slug])
        self.assertFalse(flags[self.course_a.slug])
        self.assertFalse(flags[self.course_c.slug])

    def test_catalog_detail_reflects_the_flag(self):
        Wishlist.objects.create(user=self.learner, course=self.course_a)
        self.client.force_authenticate(user=self.learner)

        response = self.client.get(
            reverse('courses:catalog-detail', kwargs={'slug': self.course_a.slug})
        )
        self.assertTrue(response.data['data']['is_wishlisted'])

    def test_my_courses_still_works_without_wishlist_context(self):
        """Regression: EnrollmentSerializer nests the catalog card with no
        wishlisted_course_ids in context — the flag must default to False
        rather than querying or raising."""
        Enrollment.objects.create(user=self.learner, course=self.course_a)
        self.client.force_authenticate(user=self.learner)

        response = self.client.get(reverse('courses:my-courses-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]['course']['is_wishlisted'])
