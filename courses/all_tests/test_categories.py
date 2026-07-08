from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import CourseCategory


class CourseCategoryAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='cat_admin@example.com',
            password='pw12345!',
            full_name='Cat Admin',
            user_type='admin',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='cat_learner@example.com',
            password='pw12345!',
            full_name='Cat Learner',
            user_type='learner',
            is_email_verified=True,
        )

        cls.parent = CourseCategory.objects.create(name='Programming')
        cls.child = CourseCategory.objects.create(name='Python', parent=cls.parent)
        cls.inactive = CourseCategory.objects.create(name='Deprecated', is_active=False)

        cls.list_url = reverse('courses:category-list-create')

    # ---- Public list -------------------------------------------------------

    def test_public_list_returns_active_tree(self):
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data['data']['results']
        # Only active top-level rows.
        names = [c['name'] for c in data]
        self.assertIn('Programming', names)
        self.assertNotIn('Deprecated', names)
        self.assertNotIn('Python', names)  # child, not top-level
        prog = next(c for c in data if c['name'] == 'Programming')
        child_names = [c['name'] for c in prog['children']]
        self.assertEqual(child_names, ['Python'])

    def test_inactive_child_excluded_from_tree(self):
        CourseCategory.objects.create(
            name='Django', parent=self.parent, is_active=False
        )
        resp = self.client.get(self.list_url)
        prog = next(c for c in resp.data['data']['results'] if c['name'] == 'Programming')
        self.assertNotIn('Django', [c['name'] for c in prog['children']])

    # ---- Create ------------------------------------------------------------

    def test_admin_create_auto_slug(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(self.list_url, {'name': 'Data Science'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['data']['slug'], 'data-science')

    def test_create_with_valid_parent_nests(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            self.list_url,
            {'name': 'Go', 'parent': self.parent.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        list_resp = self.client.get(self.list_url)
        prog = next(c for c in list_resp.data['data']['results'] if c['name'] == 'Programming')
        self.assertIn('Go', [c['name'] for c in prog['children']])

    def test_create_rejects_three_level_parent(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            self.list_url,
            {'name': 'Deep', 'parent': self.child.pk},  # child already has a parent
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('parent', resp.data['errors'])

    def test_create_rejects_empty_name(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(self.list_url, {'name': '   '}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_admin_cannot_create(self):
        self.client.force_authenticate(self.learner)
        resp = self.client.post(self.list_url, {'name': 'Hacky'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauth_cannot_create(self):
        resp = self.client.post(self.list_url, {'name': 'Anon'}, format='json')
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    # ---- Update ------------------------------------------------------------

    def test_admin_patch_updates_fields(self):
        self.client.force_authenticate(self.admin)
        url = reverse('courses:category-detail', args=[self.parent.pk])
        resp = self.client.patch(url, {'name': 'Software'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.parent.refresh_from_db()
        self.assertEqual(self.parent.name, 'Software')

    def test_patch_deactivate_hides_from_public(self):
        self.client.force_authenticate(self.admin)
        url = reverse('courses:category-detail', args=[self.parent.pk])
        resp = self.client.patch(url, {'is_active': False}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        list_resp = self.client.get(self.list_url)
        self.assertNotIn(
            'Programming', [c['name'] for c in list_resp.data['data']['results']]
        )

    def test_patch_rejects_self_parent(self):
        self.client.force_authenticate(self.admin)
        url = reverse('courses:category-detail', args=[self.parent.pk])
        resp = self.client.patch(url, {'parent': self.parent.pk}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_rejects_reparenting_category_that_has_children(self):
        self.client.force_authenticate(self.admin)
        other_top = CourseCategory.objects.create(name='Design')
        url = reverse('courses:category-detail', args=[self.parent.pk])  # self.parent has child self.child
        resp = self.client.patch(url, {'parent': other_top.pk}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('parent', resp.data['errors'])

    def test_create_rejects_inactive_parent(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            self.list_url,
            {'name': 'Under Inactive', 'parent': self.inactive.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('parent', resp.data['errors'])

    def test_patch_deactivate_cascades_to_children(self):
        self.client.force_authenticate(self.admin)
        url = reverse('courses:category-detail', args=[self.parent.pk])
        resp = self.client.patch(url, {'is_active': False}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.child.refresh_from_db()
        self.assertFalse(self.child.is_active)

    def test_list_query_count_does_not_scale_with_children(self):
        for i in range(3):
            top = CourseCategory.objects.create(name=f'Topic {i}')
            CourseCategory.objects.create(name=f'Topic {i} Sub A', parent=top)
            CourseCategory.objects.create(name=f'Topic {i} Sub B', parent=top)

        # 1 count query (pagination) + 1 top-level list query + 1 batched children prefetch.
        with self.assertNumQueries(3):
            resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_detail_unknown_pk_404(self):
        self.client.force_authenticate(self.admin)
        url = reverse('courses:category-detail', args=[999999])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ---- Delete (soft) -----------------------------------------------------

    def test_admin_delete_soft_deactivates(self):
        self.client.force_authenticate(self.admin)
        url = reverse('courses:category-detail', args=[self.parent.pk])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.parent.refresh_from_db()
        self.assertFalse(self.parent.is_active)
        # Row still exists.
        self.assertTrue(CourseCategory.objects.filter(pk=self.parent.pk).exists())

    def test_admin_delete_cascades_to_children(self):
        self.client.force_authenticate(self.admin)
        url = reverse('courses:category-detail', args=[self.parent.pk])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.child.refresh_from_db()
        self.assertFalse(self.child.is_active)

    def test_non_admin_cannot_delete(self):
        self.client.force_authenticate(self.learner)
        url = reverse('courses:category-detail', args=[self.parent.pk])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
