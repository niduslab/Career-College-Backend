from datetime import timedelta
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.services.enrollment_service import recalculate_progress
from courses.models import (
    CourseCategory,
    CourseSection,
    Enrollment,
    Lecture,
    NidusCourse,
    SectionContent,
    WatchProgress,
)


class EnrollmentAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='enroll_instructor@example.com',
            password='pw12345!',
            full_name='Enrollment Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='enroll_learner@example.com',
            password='pw12345!',
            full_name='Enrollment Learner',
            user_type='learner',
            is_email_verified=True,
        )
        cls.other_learner = User.objects.create_user(
            email='other_learner@example.com',
            password='pw12345!',
            full_name='Other Learner',
            user_type='learner',
            is_email_verified=True,
        )
        cls.unverified_learner = User.objects.create_user(
            email='unverified_learner@example.com',
            password='pw12345!',
            full_name='Unverified Learner',
            user_type='learner',
            is_email_verified=False,
        )

        cls.published_course = cls._make_course(
            title='Published Enrollment Course',
            slug='published-enrollment-course',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        cls.draft_course = cls._make_course(
            title='Draft Enrollment Course',
            slug='draft-enrollment-course',
            status=NidusCourse.CourseStatus.DRAFT,
        )
        cls.paid_course = cls._make_course(
            title='Paid MVP Course',
            slug='paid-mvp-course',
            status=NidusCourse.CourseStatus.PUBLISHED,
            price='49.00',
        )

    @classmethod
    def _make_course(cls, title, slug, status, price='0.00'):
        course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title=title,
            slug=slug,
            description='A course used by enrollment tests.',
            status=status,
            price=price,
        )
        course.instructors.add(cls.instructor)
        return course

    def auth(self, user=None):
        self.client.force_authenticate(user=user or self.learner)

    def test_catalog_lists_only_published_courses(self):
        response = self.client.get(reverse('courses:catalog-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = {row['title'] for row in response.data['data']['results']}
        self.assertIn(self.published_course.title, titles)
        self.assertIn(self.paid_course.title, titles)
        self.assertNotIn(self.draft_course.title, titles)

    def test_learner_can_enroll_in_published_course(self):
        self.auth()

        response = self.client.post(
            reverse('courses:course-enroll', kwargs={'slug': self.published_course.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])
        self.assertTrue(
            Enrollment.objects.filter(
                user=self.learner,
                course=self.published_course,
                is_active=True,
            ).exists()
        )

    def test_paid_course_free_enroll_is_rejected_without_purchase(self):
        self.auth()

        response = self.client.post(
            reverse('courses:course-enroll', kwargs={'slug': self.paid_course.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertFalse(response.data['success'])
        self.assertFalse(
            Enrollment.objects.filter(user=self.learner, course=self.paid_course).exists()
        )

    def test_paid_course_enroll_succeeds_with_paid_order(self):
        from payments.models import Order

        Order.objects.create(
            user=self.learner,
            course=self.paid_course,
            amount=Decimal('49.00'),
            tran_id='CCTESTPAIDORDER0000000001',
            status=Order.Status.PAID,
            paid_at=timezone.now(),
        )
        self.auth()

        response = self.client.post(
            reverse('courses:course-enroll', kwargs={'slug': self.paid_course.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['enrollment_type'], Enrollment.EnrollmentType.PAID)

    def test_paid_course_reenroll_after_unenroll_reactivates_without_second_charge(self):
        from payments.models import Order

        Order.objects.create(
            user=self.learner,
            course=self.paid_course,
            amount=Decimal('49.00'),
            tran_id='CCTESTPAIDORDER0000000002',
            status=Order.Status.PAID,
            paid_at=timezone.now(),
        )
        self.auth()
        enroll_url = reverse('courses:course-enroll', kwargs={'slug': self.paid_course.slug})
        self.client.post(enroll_url)
        self.client.post(reverse('courses:course-unenroll', kwargs={'slug': self.paid_course.slug}))

        response = self.client.post(enroll_url)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        enrollment = Enrollment.objects.get(user=self.learner, course=self.paid_course)
        self.assertTrue(enrollment.is_active)
        self.assertEqual(enrollment.enrollment_type, Enrollment.EnrollmentType.PAID)
        # Still exactly one order — reactivation never creates a new charge.
        self.assertEqual(Order.objects.filter(user=self.learner, course=self.paid_course).count(), 1)

    def test_duplicate_enroll_returns_422(self):
        self.auth()
        url = reverse('courses:course-enroll', kwargs={'slug': self.published_course.slug})
        self.client.post(url)

        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertFalse(response.data['success'])

    def test_unenroll_soft_deactivates_and_reenroll_reactivates_same_row(self):
        self.auth()
        enroll_url = reverse('courses:course-enroll', kwargs={'slug': self.published_course.slug})
        unenroll_url = reverse('courses:course-unenroll', kwargs={'slug': self.published_course.slug})
        self.client.post(enroll_url)
        enrollment = Enrollment.objects.get(user=self.learner, course=self.published_course)

        response = self.client.post(unenroll_url)
        enrollment.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(enrollment.is_active)

        response = self.client.post(enroll_url)
        enrollment.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(enrollment.is_active)
        self.assertEqual(
            Enrollment.objects.filter(user=self.learner, course=self.published_course).count(),
            1,
        )

    def test_non_learner_cannot_enroll(self):
        self.auth(self.instructor)

        response = self.client.post(
            reverse('courses:course-enroll', kwargs={'slug': self.published_course.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unverified_learner_cannot_enroll(self):
        self.auth(self.unverified_learner)

        response = self.client.post(
            reverse('courses:course-enroll', kwargs={'slug': self.published_course.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_my_courses_lists_only_current_users_active_enrollments(self):
        Enrollment.objects.create(user=self.learner, course=self.published_course)
        Enrollment.objects.create(user=self.learner, course=self.paid_course, is_active=False)
        Enrollment.objects.create(user=self.other_learner, course=self.paid_course)
        self.auth()

        response = self.client.get(reverse('courses:my-courses-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = {row['course']['title'] for row in response.data['data']['results']}
        self.assertEqual(titles, {self.published_course.title})

    def test_my_course_detail_updates_last_accessed_in_response_and_database(self):
        Enrollment.objects.create(user=self.learner, course=self.published_course)
        self.auth()

        response = self.client.get(
            reverse('courses:my-courses-detail', kwargs={'slug': self.published_course.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['data']['enrollment']['last_accessed_at'])
        enrollment = Enrollment.objects.get(user=self.learner, course=self.published_course)
        self.assertIsNotNone(enrollment.last_accessed_at)

    def test_update_last_accessed_is_debounced_within_5_minutes(self):
        from datetime import timedelta
        from django.utils import timezone

        from courses.services.enrollment_service import (
            LAST_ACCESSED_DEBOUNCE,
            update_last_accessed,
        )

        # Fresh row (last_accessed_at=None) → first call writes.
        enrollment = Enrollment.objects.create(user=self.learner, course=self.published_course)
        first = update_last_accessed(enrollment)
        self.assertIsNotNone(first)

        # Immediate second call should be debounced — no write, returns the
        # original timestamp.
        second = update_last_accessed(enrollment)
        self.assertEqual(second, first)

        # Simulate a touch older than the debounce window → next call writes.
        stale = timezone.now() - LAST_ACCESSED_DEBOUNCE - timedelta(seconds=1)
        Enrollment.objects.filter(pk=enrollment.pk).update(last_accessed_at=stale)
        enrollment.refresh_from_db()
        third = update_last_accessed(enrollment)
        self.assertGreater(third, stale)

    def test_my_course_detail_returns_slim_metadata_only(self):
        # The detail endpoint must not return the curriculum tree; the
        # frontend pairs it with /learn/<slug>/curriculum/ for the sidebar.
        Enrollment.objects.create(user=self.learner, course=self.published_course)
        self.auth()

        response = self.client.get(
            reverse('courses:my-courses-detail', kwargs={'slug': self.published_course.slug})
        )

        data = response.data['data']
        self.assertNotIn('sections', data)
        self.assertEqual(set(data.keys()), {'is_instructor', 'enrollment', 'course'})
        # Course meta block contains the header fields the frontend needs.
        course = data['course']
        self.assertIn('total_sections', course)
        self.assertIn('total_content_items', course)
        self.assertIn('instructors', course)
        self.assertIn('learning_objectives', course)
        self.assertFalse(data['is_instructor'])

    def test_watch_progress_recalculates_active_enrollment_progress(self):
        enrollment = Enrollment.objects.create(user=self.learner, course=self.published_course)
        section = CourseSection.objects.create(
            course=self.published_course,
            title='Progress Section',
            position=1,
        )
        lecture = Lecture.objects.create(
            section=section,
            title='Progress Lecture',
            lecture_type=Lecture.LectureType.ARTICLE,
            article_content='Complete me.',
        )
        SectionContent.objects.create(
            section=section,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lecture.pk,
            position=1,
        )

        WatchProgress.objects.create(
            user=self.learner,
            lecture=lecture,
            watched_seconds=10,
            is_completed=True,
        )

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.progress_percent, 100)
        self.assertIsNotNone(enrollment.completed_at)

    def test_adding_content_after_completion_does_not_uncomplete(self):
        """Regression: `completed_at` is sticky.

        It used to be cleared whenever progress fell below 100, so an
        instructor adding a lecture silently un-completed everyone who had
        already finished — the course vanished from the My Courses
        "Completed" tab and from the dashboard's completed count, while the
        already-issued certificate stayed put.
        """
        enrollment = Enrollment.objects.create(
            user=self.learner, course=self.published_course,
        )
        section = CourseSection.objects.create(
            course=self.published_course, title='Sticky Section', position=1,
        )

        def _add_lecture(title, position):
            lecture = Lecture.objects.create(
                section=section,
                title=title,
                lecture_type=Lecture.LectureType.ARTICLE,
                article_content='Body.',
            )
            SectionContent.objects.create(
                section=section,
                item_type=SectionContent.ItemType.LECTURE,
                content_type=ContentType.objects.get_for_model(Lecture),
                object_id=lecture.pk,
                position=position,
            )
            return lecture

        first = _add_lecture('Only Lecture', 1)
        WatchProgress.objects.create(
            user=self.learner, lecture=first, watched_seconds=10, is_completed=True,
        )
        enrollment.refresh_from_db()
        completed_at = enrollment.completed_at
        self.assertIsNotNone(completed_at)

        # Instructor adds a second lecture, then any learner action triggers
        # a recalculation.
        _add_lecture('Added Later', 2)
        WatchProgress.objects.filter(user=self.learner, lecture=first).update(
            watched_seconds=11,
        )
        recalculate_progress(enrollment)

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.progress_percent, 50)
        self.assertEqual(enrollment.completed_at, completed_at)


class MyCoursesStatusFilterTests(APITestCase):
    """`?status=` + `status_counts` on the My Courses list.

    Both exist because the tab counts describe the whole enrollment set. The
    page previously counted rows client-side over an unpaginated fetch, so any
    learner past `page_size` courses saw wrong counts and lost the overflow
    entirely — a finished course sinks first, because the list orders by
    `last_accessed_at` descending.
    """

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='status_instructor@example.com',
            password='pw12345!',
            full_name='Status Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='status_learner@example.com',
            password='pw12345!',
            full_name='Status Learner',
            user_type='learner',
            is_email_verified=True,
        )

        cls.done_course = cls._make_course('Finished', 'status-finished')
        cls.doing_course = cls._make_course('Ongoing', 'status-ongoing')
        cls.dropped_course = cls._make_course('Dropped', 'status-dropped')

        cls.completed = Enrollment.objects.create(
            user=cls.learner,
            course=cls.done_course,
            progress_percent=100,
            completed_at=timezone.now(),
        )
        cls.in_progress = Enrollment.objects.create(
            user=cls.learner, course=cls.doing_course, progress_percent=30,
        )
        # Unenrolled *and* unfinished — the only case still excluded from the
        # list and the counts. An unenrolled row that was completed stays
        # visible; see test_completed_then_unenrolled_course_still_appears.
        Enrollment.objects.create(
            user=cls.learner,
            course=cls.dropped_course,
            progress_percent=20,
            completed_at=None,
            is_active=False,
        )

    @classmethod
    def _make_course(cls, title, slug):
        course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title=title,
            slug=slug,
            description='A course used by status-filter tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        course.instructors.add(cls.instructor)
        return course

    @property
    def url(self):
        return reverse('courses:my-courses-list')

    def setUp(self):
        self.client.force_authenticate(user=self.learner)

    def test_default_returns_every_active_enrollment(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['count'], 2)

    def test_completed_course_is_listed_by_default(self):
        """The reported bug: a finished course must still appear."""
        slugs = [
            row['course']['slug']
            for row in self.client.get(self.url).data['data']['results']
        ]
        self.assertIn(self.done_course.slug, slugs)

    def test_status_completed_filters(self):
        results = self.client.get(self.url, {'status': 'completed'}).data['data']['results']

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['course']['slug'], self.done_course.slug)

    def test_status_in_progress_filters(self):
        results = self.client.get(self.url, {'status': 'in_progress'}).data['data']['results']

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['course']['slug'], self.doing_course.slug)

    def test_status_all_is_the_same_as_omitting_it(self):
        self.assertEqual(
            self.client.get(self.url, {'status': 'all'}).data['data']['count'],
            self.client.get(self.url).data['data']['count'],
        )

    def test_invalid_status_returns_400(self):
        response = self.client.get(self.url, {'status': 'bogus'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('status', response.data['errors'])

    def test_status_counts_are_exact_and_exclude_unenrolled_unfinished(self):
        counts = self.client.get(self.url).data['data']['status_counts']

        self.assertEqual(counts, {'all': 2, 'in_progress': 1, 'completed': 1})

    def test_unenrolled_unfinished_course_is_hidden(self):
        slugs = [
            row['course']['slug']
            for row in self.client.get(self.url).data['data']['results']
        ]
        self.assertNotIn(self.dropped_course.slug, slugs)

    def test_completed_then_unenrolled_course_still_appears(self):
        """The reported bug.

        `unenroll_learner` is a soft revoke that preserves `completed_at` and
        never touches the issued certificate, so a learner who finished a
        course and then unenrolled kept the certificate while My Courses
        reported 0 completed — and the dashboard summary, which has never
        filtered on `is_active`, reported 1.
        """
        Enrollment.objects.filter(pk=self.completed.pk).update(is_active=False)

        payload = self.client.get(self.url).data['data']
        slugs = [row['course']['slug'] for row in payload['results']]

        self.assertIn(self.done_course.slug, slugs)
        self.assertEqual(payload['status_counts']['completed'], 1)

    def test_unenrolled_completed_course_shows_only_under_completed(self):
        Enrollment.objects.filter(pk=self.completed.pk).update(is_active=False)

        in_progress = self.client.get(self.url, {'status': 'in_progress'}).data['data']
        completed = self.client.get(self.url, {'status': 'completed'}).data['data']

        self.assertNotIn(
            self.done_course.slug,
            [row['course']['slug'] for row in in_progress['results']],
        )
        self.assertEqual(
            [row['course']['slug'] for row in completed['results']],
            [self.done_course.slug],
        )

    def test_counts_agree_with_the_dashboard_summary(self):
        """The two surfaces disagreeing is what the learner actually saw."""
        from courses.services.dashboard_service import get_learner_summary

        Enrollment.objects.filter(pk=self.completed.pk).update(is_active=False)

        counts = self.client.get(self.url).data['data']['status_counts']
        summary = get_learner_summary(self.learner)

        self.assertEqual(counts['completed'], summary['courses_completed'])

    def test_unenrolled_course_is_never_the_resume_target(self):
        """My Courses lists it; "continue learning" must not send the learner
        into content they no longer have access to."""
        from courses.services.dashboard_service import get_continue_target

        Enrollment.objects.filter(pk=self.completed.pk).update(is_active=False)

        target = get_continue_target(self.learner)

        self.assertIsNotNone(target)
        self.assertEqual(target['course']['slug'], self.doing_course.slug)

    def test_status_counts_describe_the_whole_set_not_the_page(self):
        for index in range(12):
            course = self._make_course(f'Bulk {index}', f'status-bulk-{index}')
            Enrollment.objects.create(user=self.learner, course=course)

        payload = self.client.get(self.url, {'page_size': 5}).data['data']

        self.assertEqual(len(payload['results']), 5)
        self.assertEqual(payload['count'], 14)
        self.assertEqual(payload['status_counts']['all'], 14)
        self.assertEqual(payload['status_counts']['completed'], 1)


class CatalogFilterTests(APITestCase):
    """End-to-end coverage of the multi-criteria catalog filter/sort API.

    Touches: C1 (search hits full_name), H1/H2 (category/subcategory tree),
    M4/M5/M6 (validator 400s), and sort ordering (newest, popularity,
    price_asc, relevance).
    """

    @classmethod
    def setUpTestData(cls):
        # Two instructors with non-overlapping name tokens — used to assert
        # that ?search= hits instructor full_name (C1) and that other
        # search terms don't accidentally match instructor names.
        cls.alice = User.objects.create_user(
            email='catalog_alice@example.com', password='pw12345!',
            full_name='Alice Smith', user_type='instructor',
            is_email_verified=True,
        )
        cls.bob = User.objects.create_user(
            email='catalog_bob@example.com', password='pw12345!',
            full_name='Bob Carpenter', user_type='instructor',
            is_email_verified=True,
        )

        # Two-level category tree:
        #   Programming → Python, Rust
        #   Cooking → Italian
        cls.prog = CourseCategory.objects.create(name='Programming', slug='programming')
        cls.python_cat = CourseCategory.objects.create(name='Python', slug='python', parent=cls.prog)
        cls.rust_cat = CourseCategory.objects.create(name='Rust', slug='rust', parent=cls.prog)
        cls.cooking = CourseCategory.objects.create(name='Cooking', slug='cooking')
        cls.italian = CourseCategory.objects.create(name='Italian', slug='italian', parent=cls.cooking)

        cls.c_python_intro = cls._make_course(
            title='Intro to Python', slug='intro-to-python',
            category=cls.python_cat, level=NidusCourse.CourseLevel.BEGINNER,
            language='English', price=Decimal('0'), duration_minutes=60,
        )
        cls.c_python_adv = cls._make_course(
            title='Advanced Python Tricks', slug='advanced-python-tricks',
            category=cls.python_cat, level=NidusCourse.CourseLevel.ADVANCED,
            language='English', price=Decimal('49.99'), duration_minutes=600,
        )
        cls.c_python_start = cls._make_course(
            title='Python for Beginners', slug='python-for-beginners',
            category=cls.python_cat, level=NidusCourse.CourseLevel.BEGINNER,
            language='English', price=Decimal('19.99'), duration_minutes=120,
        )
        cls.c_rust = cls._make_course(
            title='Learning Rust', slug='learning-rust',
            category=cls.rust_cat, level=NidusCourse.CourseLevel.INTERMEDIATE,
            language='English', price=Decimal('29.99'), duration_minutes=240,
        )
        cls.c_italian = cls._make_course(
            title='Italian Pasta Mastery', slug='italian-pasta-mastery',
            category=cls.italian, level=NidusCourse.CourseLevel.BEGINNER,
            language='English', price=Decimal('19.99'), duration_minutes=180,
            instructors=[cls.bob],
        )
        cls.c_bangla = cls._make_course(
            title='Bangla Calligraphy', slug='bangla-calligraphy',
            category=None, level=NidusCourse.CourseLevel.BEGINNER,
            language='Bangla', price=Decimal('0'), duration_minutes=90,
        )
        cls.c_draft = cls._make_course(
            title='Unpublished Stuff', slug='unpublished-stuff',
            status=NidusCourse.CourseStatus.DRAFT,
        )

        # Three enrollments staged for the popularity sort assertion:
        # adv:2 > rust:1 > everyone else:0.
        l1 = User.objects.create_user(
            email='catalog_l1@example.com', password='pw',
            full_name='L1', user_type='learner', is_email_verified=True,
        )
        l2 = User.objects.create_user(
            email='catalog_l2@example.com', password='pw',
            full_name='L2', user_type='learner', is_email_verified=True,
        )
        Enrollment.objects.create(user=l1, course=cls.c_python_adv)
        Enrollment.objects.create(user=l2, course=cls.c_python_adv)
        Enrollment.objects.create(user=l1, course=cls.c_rust)

    @classmethod
    def _make_course(cls, *, title, slug, instructors=None, **kwargs):
        defaults = dict(
            description='A course used by catalog filter tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
            price=Decimal('0'),
            language='English',
            level=NidusCourse.CourseLevel.BEGINNER,
            duration_minutes=120,
        )
        defaults.update(kwargs)
        course = NidusCourse.objects.create(
            created_by=cls.alice, title=title, slug=slug, **defaults
        )
        for inst in (instructors or [cls.alice]):
            course.instructors.add(inst)
        return course

    def _slugs(self, response):
        return [row['slug'] for row in response.data['data']['results']]

    # ── C1: search must hit instructors.full_name ────────────────────────

    def test_search_matches_instructor_full_name(self):
        # 'Carpenter' lives only in Bob's full_name; Bob teaches just c_italian.
        # No course title or description contains 'Carpenter' → only c_italian
        # can satisfy this search, and only via the full_name join.
        response = self.client.get(
            reverse('courses:catalog-list'), {'search': 'Carpenter'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(self._slugs(response)), {self.c_italian.slug})

    # ── H1: category + subcategory must validate the parent/child pair ──

    def test_category_plus_matching_subcategory_returns_python_rows(self):
        response = self.client.get(
            reverse('courses:catalog-list'),
            {'category': 'programming', 'subcategory': 'python'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(self._slugs(response)), {
            self.c_python_intro.slug,
            self.c_python_adv.slug,
            self.c_python_start.slug,
        })

    def test_category_plus_mismatched_subcategory_returns_empty(self):
        # python's parent is 'programming', not 'cooking' → no rows can match.
        response = self.client.get(
            reverse('courses:catalog-list'),
            {'category': 'cooking', 'subcategory': 'python'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['count'], 0)

    def test_subcategory_only_returns_exact_subcategory_rows(self):
        response = self.client.get(
            reverse('courses:catalog-list'), {'subcategory': 'italian'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(self._slugs(response)), {self.c_italian.slug})

    # ── H2: parent category must roll up to subcategory rows ─────────────

    def test_parent_category_rolls_up_to_subcategory_rows(self):
        response = self.client.get(
            reverse('courses:catalog-list'), {'category': 'programming'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slugs = set(self._slugs(response))
        self.assertEqual(slugs, {
            self.c_python_intro.slug,
            self.c_python_adv.slug,
            self.c_python_start.slug,
            self.c_rust.slug,
        })
        # Cooking + Bangla + draft must not leak in.
        self.assertNotIn(self.c_italian.slug, slugs)
        self.assertNotIn(self.c_bangla.slug, slugs)
        self.assertNotIn(self.c_draft.slug, slugs)

    # ── M4: unknown sort returns 400 ─────────────────────────────────────

    def test_unknown_sort_returns_400(self):
        response = self.client.get(
            reverse('courses:catalog-list'), {'sort': 'cheapest'}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])
        self.assertIn('sort', response.data['errors'])

    # ── M5: numeric range params must validate ───────────────────────────

    def test_negative_price_min_returns_400(self):
        response = self.client.get(
            reverse('courses:catalog-list'), {'price_min': '-10'}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('price_min', response.data['errors'])

    def test_non_numeric_price_max_returns_400(self):
        response = self.client.get(
            reverse('courses:catalog-list'), {'price_max': 'abc'}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('price_max', response.data['errors'])

    def test_non_integer_duration_max_returns_400(self):
        response = self.client.get(
            reverse('courses:catalog-list'), {'duration_max': '3.5'}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('duration_max', response.data['errors'])

    # ── M6: invalid level returns 400 ────────────────────────────────────

    def test_invalid_level_returns_400(self):
        response = self.client.get(
            reverse('courses:catalog-list'), {'level': 'expert'}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('level', response.data['errors'])

    # ── Validator collects every bad field in one response ───────────────

    def test_combined_bad_inputs_returns_all_errors_together(self):
        response = self.client.get(reverse('courses:catalog-list'), {
            'sort': 'foo',
            'level': 'wizard',
            'price_min': '-1',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            set(response.data['errors'].keys()),
            {'sort', 'level', 'price_min'},
        )

    # ── Sort ordering ────────────────────────────────────────────────────

    def test_sort_newest_orders_by_published_at_desc(self):
        # All catalog courses share roughly the same published_at from save().
        # Force a known order by updating published_at directly.
        now = timezone.now()
        NidusCourse.objects.filter(pk=self.c_python_intro.pk).update(
            published_at=now - timedelta(days=5)
        )
        NidusCourse.objects.filter(pk=self.c_python_adv.pk).update(
            published_at=now - timedelta(days=1)
        )

        response = self.client.get(
            reverse('courses:catalog-list'), {'sort': 'newest'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slugs = self._slugs(response)
        self.assertLess(
            slugs.index(self.c_python_adv.slug),
            slugs.index(self.c_python_intro.slug),
        )

    def test_sort_price_asc_orders_by_price_ascending(self):
        response = self.client.get(
            reverse('courses:catalog-list'), {'sort': 'price_asc'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        prices = [Decimal(row['price']) for row in response.data['data']['results']]
        self.assertEqual(prices, sorted(prices))

    def test_sort_popularity_orders_by_active_enrollment_count_desc(self):
        # Staged enrollments: adv:2, rust:1, others:0 → adv first, rust before zeros.
        response = self.client.get(
            reverse('courses:catalog-list'), {'sort': 'popularity'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slugs = self._slugs(response)
        self.assertEqual(slugs[0], self.c_python_adv.slug)
        rust_idx = slugs.index(self.c_rust.slug)
        zero_count_slugs = {
            self.c_python_intro.slug, self.c_python_start.slug,
            self.c_italian.slug, self.c_bangla.slug,
        }
        for s in zero_count_slugs:
            self.assertLess(rust_idx, slugs.index(s))

    def test_sort_relevance_ranks_title_startswith_above_contains(self):
        # 'Python for Beginners' (rank 2 — starts with 'Python') must come
        # before 'Intro to Python' / 'Advanced Python Tricks' (rank 1 — contains).
        response = self.client.get(
            reverse('courses:catalog-list'),
            {'search': 'Python', 'sort': 'relevance'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slugs = self._slugs(response)
        self.assertEqual(slugs[0], self.c_python_start.slug)
