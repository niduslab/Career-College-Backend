from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, User
from courses.models import (
    Assignment,
    AssignmentQuestion,
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


# ---------------------------------------------------------------------------
# Existing CodingExercise serializer tests (kept)
# ---------------------------------------------------------------------------

class AssignmentTestBase(APITestCase):
    """Builds: verified instructor, second verified instructor, unverified instructor,
    learner; one course owned by instructor with a single section."""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = cls._make_instructor(
            email='alice@example.com', full_name='Alice Instructor', verified=True
        )
        cls.other_instructor = cls._make_instructor(
            email='bob@example.com', full_name='Bob Instructor', verified=True
        )
        cls.unverified_instructor = cls._make_instructor(
            email='carol@example.com', full_name='Carol Instructor', verified=False
        )
        cls.learner = User.objects.create_user(
            email='dave@example.com',
            password='pw12345!',
            full_name='Dave Learner',
            user_type='learner',
            is_email_verified=True,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Test Course',
            description='Just a test course.',
        )
        cls.course.instructors.add(cls.instructor)

        cls.section = CourseSection.objects.create(
            course=cls.course, title='Section 1', position=1
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
        # post_save signal already created an InstructorProfile; toggle is_verified.
        InstructorProfile.objects.filter(user=user).update(is_verified=verified)
        return user

    def auth(self, user):
        self.client.force_authenticate(user=user)


# ---------------------------------------------------------------------------
# Assignment read / update / delete via the dedicated endpoints.
# (Creation is exercised exclusively through AssignmentCurriculumFlowTests
#  because the dedicated assignment endpoint no longer accepts POST.)
# ---------------------------------------------------------------------------

class AssignmentCRUDTests(AssignmentTestBase):
    def setUp(self):
        self.auth(self.instructor)
        self.list_url = reverse(
            'courses:assignment-list-create', kwargs={'section_id': self.section.id}
        )

    def test_list_assignments_returns_only_assignments_for_section(self):
        Assignment.objects.create(section=self.section, title='A1')
        Assignment.objects.create(section=self.section, title='A2')

        # Assignment in a different section must not appear.
        other_section = CourseSection.objects.create(
            course=self.course, title='Section 2', position=2
        )
        Assignment.objects.create(section=other_section, title='Other')

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = {row['title'] for row in response.data['data']}
        self.assertEqual(titles, {'A1', 'A2'})

    def test_dedicated_endpoint_does_not_accept_post(self):
        # Creation now happens only via the section-content endpoint.
        response = self.client.post(self.list_url, {'title': 'Nope'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_get_assignment_detail(self):
        a = Assignment.objects.create(section=self.section, title='Detail Test')
        url = reverse('courses:assignment-detail', kwargs={'assignment_id': a.id})

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['title'], 'Detail Test')

    def test_patch_assignment_updates_allowed_fields(self):
        a = Assignment.objects.create(
            section=self.section, title='Original',
            total_score=100, passing_score=10,
        )
        url = reverse('courses:assignment-detail', kwargs={'assignment_id': a.id})

        response = self.client.patch(
            url,
            {'title': 'Updated Title', 'passing_score': 80, 'instructions': 'do this'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        a.refresh_from_db()
        self.assertEqual(a.title, 'Updated Title')
        self.assertEqual(a.passing_score, 80)
        self.assertEqual(a.instructions, 'do this')

    def test_patch_assignment_persists_total_score(self):
        a = Assignment.objects.create(
            section=self.section, title='Initial', total_score=10, passing_score=0,
        )
        AssignmentQuestion.objects.create(
            assignment=a, question_text='Q1', points=10, position=1,
        )
        url = reverse('courses:assignment-detail', kwargs={'assignment_id': a.id})

        response = self.client.patch(url, {'total_score': 120}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        a.refresh_from_db()
        self.assertEqual(a.total_score, 120)
        # max_score (computed sum of question.points) stays at 10 — it is
        # intentionally distinct from the declared total.
        self.assertEqual(response.data['data']['total_score'], 120)
        self.assertEqual(response.data['data']['max_score'], 10)

    def test_patch_assignment_question_persists_rubric(self):
        # Regression test for the matching bug on the question update path:
        # serializer accepted rubric but the service's allow-list dropped it.
        a = Assignment.objects.create(
            section=self.section, title='Rubric Drift Repro',
            total_score=10, passing_score=0,
        )
        q = AssignmentQuestion.objects.create(
            assignment=a, question_text='Q?', points=5, position=1, rubric=[],
        )
        url = reverse('courses:assignment-question-detail', kwargs={'question_id': q.id})

        new_rubric = [
            {'type': 'keyword', 'value': 'API', 'points': 3, 'case_sensitive': True},
            {'type': 'min_length', 'value': 50, 'points': 2},
        ]
        response = self.client.patch(url, {'rubric': new_rubric}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        q.refresh_from_db()
        self.assertEqual(len(q.rubric), 2)
        self.assertEqual(q.rubric[0]['value'], 'API')

    def test_delete_assignment_cascades_questions_and_section_content(self):
        a = Assignment.objects.create(section=self.section, title='Doomed')
        AssignmentQuestion.objects.create(
            assignment=a, question_text='Q1', position=1
        )
        SectionContent.objects.create(
            section=self.section,
            item_type=SectionContent.ItemType.ASSIGNMENT,
            content_type=ContentType.objects.get_for_model(Assignment),
            object_id=a.pk,
            position=1,
        )

        url = reverse('courses:assignment-detail', kwargs={'assignment_id': a.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])

        self.assertFalse(Assignment.objects.filter(pk=a.pk).exists())
        self.assertFalse(AssignmentQuestion.objects.filter(assignment_id=a.pk).exists())
        # GenericRelation should have cascaded the SectionContent slot away.
        self.assertFalse(
            SectionContent.objects.filter(
                item_type=SectionContent.ItemType.ASSIGNMENT, object_id=a.pk
            ).exists()
        )


# ---------------------------------------------------------------------------
# Assignment detail-endpoint error cases (auth/permissions/not-found).
# Create-flow error cases live in AssignmentCurriculumFlowTests because the
# section-content endpoint is now the only path that creates assignments.
# ---------------------------------------------------------------------------

class AssignmentErrorCaseTests(AssignmentTestBase):
    def test_detail_not_found_returns_404(self):
        self.auth(self.instructor)
        url = reverse('courses:assignment-detail', kwargs={'assignment_id': 9_999_999})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_other_instructor_cannot_see_assignment_detail(self):
        a = Assignment.objects.create(section=self.section, title='Mine')
        self.auth(self.other_instructor)
        url = reverse('courses:assignment-detail', kwargs={'assignment_id': a.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unverified_instructor_can_patch(self):
        a = Assignment.objects.create(section=self.section, title='Mine')
        self.course.instructors.add(self.unverified_instructor)
        self.auth(self.unverified_instructor)
        url = reverse('courses:assignment-detail', kwargs={'assignment_id': a.id})
        response = self.client.patch(url, {'title': 'New'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        a.refresh_from_db()
        self.assertEqual(a.title, 'New')

    def test_unverified_instructor_can_read(self):
        # Unverified instructor on this course can read its content.
        a = Assignment.objects.create(section=self.section, title='Mine')
        self.course.instructors.add(self.unverified_instructor)
        self.auth(self.unverified_instructor)
        url = reverse('courses:assignment-detail', kwargs={'assignment_id': a.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unauthenticated_list_returns_401(self):
        # No force_authenticate call.
        url = reverse(
            'courses:assignment-list-create',
            kwargs={'section_id': self.section.id},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# AssignmentQuestion CRUD + reorder
# ---------------------------------------------------------------------------

class AssignmentQuestionTests(AssignmentTestBase):
    def setUp(self):
        self.auth(self.instructor)
        self.assignment = Assignment.objects.create(
            section=self.section, title='With Questions'
        )
        self.q_list_url = reverse(
            'courses:assignment-question-list-create',
            kwargs={'assignment_id': self.assignment.id},
        )

    def _create_question(self, text='Q?', model_answer='', points=10):
        return AssignmentQuestion.objects.create(
            assignment=self.assignment,
            question_text=text,
            model_answer=model_answer,
            points=points,
            position=AssignmentQuestion.objects.filter(
                assignment=self.assignment
            ).count() + 1,
        )

    def test_add_question_auto_assigns_position(self):
        for expected_position in (1, 2, 3):
            response = self.client.post(
                self.q_list_url,
                {'question_text': f'Q{expected_position}', 'points': 5},
                format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['data']['position'], expected_position)

    def test_add_question_strips_model_answer_for_non_instructor_in_response(self):
        # NOTE: this endpoint requires verified instructor for POST, so the
        # response WILL include model_answer. Direct serializer test below
        # covers the learner-facing case.
        response = self.client.post(
            self.q_list_url,
            {
                'question_text': 'What is X?',
                'model_answer': 'It is X.',
                'points': 5,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['model_answer'], 'It is X.')

    def test_add_question_validation_rejects_blank_question_text(self):
        response = self.client.post(
            self.q_list_url, {'question_text': '   '}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('question_text', response.data['errors'])

    def test_update_question_changes_model_answer(self):
        q = self._create_question(model_answer='old')
        url = reverse(
            'courses:assignment-question-detail', kwargs={'question_id': q.id}
        )
        response = self.client.patch(
            url, {'model_answer': 'new'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        q.refresh_from_db()
        self.assertEqual(q.model_answer, 'new')

    def test_delete_question_compacts_positions(self):
        q1 = self._create_question(text='Q1')
        q2 = self._create_question(text='Q2')
        q3 = self._create_question(text='Q3')
        self.assertEqual([q1.position, q2.position, q3.position], [1, 2, 3])

        url = reverse(
            'courses:assignment-question-detail', kwargs={'question_id': q2.id}
        )
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])

        q1.refresh_from_db()
        q3.refresh_from_db()
        self.assertEqual(q1.position, 1)
        # q3 was at 3 → compacts to 2 after q2 is removed.
        self.assertEqual(q3.position, 2)

    def test_reorder_questions_persists_new_order(self):
        q1 = self._create_question(text='Q1')
        q2 = self._create_question(text='Q2')
        q3 = self._create_question(text='Q3')

        url = reverse(
            'courses:assignment-question-reorder',
            kwargs={'assignment_id': self.assignment.id},
        )
        response = self.client.patch(
            url, {'ordered_ids': [q3.id, q1.id, q2.id]}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        q1.refresh_from_db()
        q2.refresh_from_db()
        q3.refresh_from_db()
        self.assertEqual(q3.position, 1)
        self.assertEqual(q1.position, 2)
        self.assertEqual(q2.position, 3)

    def test_reorder_rejects_id_set_mismatch(self):
        q1 = self._create_question(text='Q1')
        self._create_question(text='Q2')

        url = reverse(
            'courses:assignment-question-reorder',
            kwargs={'assignment_id': self.assignment.id},
        )
        # Missing one ID, plus an ID from a different assignment universe.
        response = self.client.patch(
            url, {'ordered_ids': [q1.id, 9_999_999]}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reorder_rejects_duplicates(self):
        q1 = self._create_question(text='Q1')
        self._create_question(text='Q2')

        url = reverse(
            'courses:assignment-question-reorder',
            kwargs={'assignment_id': self.assignment.id},
        )
        response = self.client.patch(
            url, {'ordered_ids': [q1.id, q1.id]}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Either "duplicates" or count-mismatch message; both are 400.
        self.assertFalse(response.data['success'])

    def test_reorder_rejects_empty_or_missing_ordered_ids(self):
        url = reverse(
            'courses:assignment-question-reorder',
            kwargs={'assignment_id': self.assignment.id},
        )
        response = self.client.patch(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.patch(url, {'ordered_ids': []}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reorder_rejects_non_integer_ids(self):
        url = reverse(
            'courses:assignment-question-reorder',
            kwargs={'assignment_id': self.assignment.id},
        )
        response = self.client.patch(
            url, {'ordered_ids': ['abc', 'def']}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_other_instructor_cannot_modify_questions(self):
        q = self._create_question(text='Mine')
        self.auth(self.other_instructor)
        url = reverse(
            'courses:assignment-question-detail', kwargs={'question_id': q.id}
        )
        response = self.client.patch(url, {'points': 99}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# Serializer-level tests: model_answer hiding + max_score aggregation
# ---------------------------------------------------------------------------

class AssignmentSerializerVisibilityTests(AssignmentTestBase):
    def _request_with_user(self, user):
        # AssignmentQuestionSerializer.to_representation() only does
        # `getattr(request, 'user', None)`, so a plain Django HttpRequest is
        # enough — wrapping in DRF's Request triggers re-auth that wipes .user.
        from rest_framework.test import APIRequestFactory

        request = APIRequestFactory().get('/')
        request.user = user
        return request

    def test_model_answer_hidden_for_learner(self):
        assignment = Assignment.objects.create(
            section=self.section, title='A'
        )
        question = AssignmentQuestion.objects.create(
            assignment=assignment,
            question_text='Q1',
            model_answer='SECRET ANSWER',
            position=1,
        )

        serializer = AssignmentQuestionSerializer(
            question, context={'request': self._request_with_user(self.learner)}
        )
        self.assertNotIn('model_answer', serializer.data)

    def test_model_answer_visible_for_instructor(self):
        assignment = Assignment.objects.create(section=self.section, title='A')
        question = AssignmentQuestion.objects.create(
            assignment=assignment,
            question_text='Q1',
            model_answer='SECRET ANSWER',
            position=1,
        )

        serializer = AssignmentQuestionSerializer(
            question, context={'request': self._request_with_user(self.instructor)}
        )
        self.assertEqual(serializer.data['model_answer'], 'SECRET ANSWER')

    def test_model_answer_hidden_when_no_request_in_context(self):
        # Public/anonymous serialization must not leak the answer.
        assignment = Assignment.objects.create(section=self.section, title='A')
        question = AssignmentQuestion.objects.create(
            assignment=assignment,
            question_text='Q1',
            model_answer='SECRET ANSWER',
            position=1,
        )

        serializer = AssignmentQuestionSerializer(question)
        self.assertNotIn('model_answer', serializer.data)

    def test_max_score_sums_question_points(self):
        assignment = Assignment.objects.create(section=self.section, title='A')
        AssignmentQuestion.objects.create(
            assignment=assignment, question_text='Q1', points=5, position=1
        )
        AssignmentQuestion.objects.create(
            assignment=assignment, question_text='Q2', points=15, position=2
        )

        serializer = AssignmentSerializer(
            assignment, context={'request': self._request_with_user(self.instructor)}
        )
        self.assertEqual(serializer.data['max_score'], 20)

    def test_max_score_is_zero_when_no_questions(self):
        assignment = Assignment.objects.create(section=self.section, title='Empty')

        serializer = AssignmentSerializer(
            assignment, context={'request': self._request_with_user(self.instructor)}
        )
        self.assertEqual(serializer.data['max_score'], 0)


# ---------------------------------------------------------------------------
# Service-level tests: race-safe position allocation in add_question
# ---------------------------------------------------------------------------

class AssignmentServiceTests(AssignmentTestBase):
    def test_add_question_assigns_sequential_positions(self):
        from courses.services.assignment_service import add_question

        assignment = Assignment.objects.create(section=self.section, title='S')

        positions = []
        for n in range(3):
            q = add_question(
                assignment.id,
                self.instructor,
                {'question_text': f'Q{n}', 'points': 1},
            )
            positions.append(q.position)
        self.assertEqual(positions, [1, 2, 3])

# ---------------------------------------------------------------------------
# End-to-end flow via the unified curriculum endpoint
# ("instructor picks 'assignment' while creating a section item; the
# Assignment entity is created at the same time; then they add questions")
# ---------------------------------------------------------------------------

class AssignmentCurriculumFlowTests(AssignmentTestBase):
    def setUp(self):
        self.auth(self.instructor)
        self.contents_url = reverse(
            'courses:section-content-list-create',
            kwargs={'section_id': self.section.id},
        )

    def _post_assignment_via_contents(self, **overrides):
        payload = {
            'item_type': 'assignment',
            'title': 'Reflection Essay',
            'description': 'Reflect on the lecture.',
            'instructions': 'Write 300+ words.',
            'total_score': 100,
            'passing_score': 60,
        }
        payload.update(overrides)
        return self.client.post(self.contents_url, payload, format='json')

    def test_contents_endpoint_creates_assignment_and_slot_together(self):
        response = self._post_assignment_via_contents()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])

        # Response wraps the SectionContent slot with the assignment as content.
        slot_data = response.data['data']
        self.assertEqual(slot_data['item_type'], 'assignment')
        self.assertEqual(slot_data['position'], 1)
        self.assertEqual(slot_data['content']['title'], 'Reflection Essay')
        self.assertEqual(slot_data['content']['passing_score'], 60)

        # Assignment row and slot both exist and reference each other.
        assignment_id = slot_data['content']['id']
        assignment = Assignment.objects.get(pk=assignment_id)
        self.assertEqual(assignment.section_id, self.section.id)
        self.assertEqual(assignment.instructions, 'Write 300+ words.')

        slot = SectionContent.objects.get(pk=slot_data['id'])
        self.assertEqual(slot.item_type, SectionContent.ItemType.ASSIGNMENT)
        self.assertEqual(slot.object_id, assignment_id)
        self.assertEqual(slot.section_id, self.section.id)

    def test_contents_endpoint_then_add_questions_completes_authoring_flow(self):
        # Step 1: instructor creates the assignment via the curriculum endpoint.
        create_response = self._post_assignment_via_contents()
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        assignment_id = create_response.data['data']['content']['id']

        # Step 2: instructor adds questions one by one.
        questions_url = reverse(
            'courses:assignment-question-list-create',
            kwargs={'assignment_id': assignment_id},
        )
        first = self.client.post(
            questions_url,
            {
                'question_text': 'What surprised you?',
                'model_answer': 'Reference reflection.',
                'points': 10,
                'hint': 'Be specific.',
            },
            format='json',
        )
        second = self.client.post(
            questions_url,
            {'question_text': 'What would you change?', 'points': 15},
            format='json',
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(first.data['data']['position'], 1)
        self.assertEqual(second.data['data']['position'], 2)

        # Step 3: GET the assignment detail; it should include both questions
        # and a max_score that sums their points.
        detail_url = reverse(
            'courses:assignment-detail', kwargs={'assignment_id': assignment_id}
        )
        detail = self.client.get(detail_url)
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data['data']['max_score'], 25)
        self.assertEqual(len(detail.data['data']['questions']), 2)

    def test_contents_endpoint_rejects_invalid_item_type(self):
        response = self.client.post(
            self.contents_url,
            {'item_type': 'bogus', 'title': 'X'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('item_type', response.data['message'])

    def test_contents_endpoint_validates_assignment_payload(self):
        response = self.client.post(
            self.contents_url,
            {'item_type': 'assignment', 'title': 'A'},  # too short
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('title', response.data['errors'])

    def test_contents_endpoint_assignment_then_curriculum_listing(self):
        # Create assignment via the curriculum endpoint.
        self._post_assignment_via_contents(title='Listed Essay')

        # GET the curriculum: it must include the assignment slot with the
        # assignment payload bulk-loaded into 'content'.
        listing = self.client.get(self.contents_url)
        self.assertEqual(listing.status_code, status.HTTP_200_OK)

        rows = listing.data['data']
        assignment_rows = [r for r in rows if r['item_type'] == 'assignment']
        self.assertEqual(len(assignment_rows), 1)
        self.assertEqual(assignment_rows[0]['content']['title'], 'Listed Essay')
        self.assertEqual(assignment_rows[0]['content']['passing_score'], 60)

    def test_contents_endpoint_assignment_lands_after_existing_slots(self):
        # Seed an assignment-style slot at position 1 via direct DB so we can
        # verify the next assignment lands at position 2.
        first = Assignment.objects.create(section=self.section, title='Pre-existing')
        SectionContent.objects.create(
            section=self.section,
            item_type=SectionContent.ItemType.ASSIGNMENT,
            content_type=ContentType.objects.get_for_model(Assignment),
            object_id=first.pk,
            position=1,
        )

        response = self._post_assignment_via_contents(title='Second Essay')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['position'], 2)

    # -- auth / permission / ownership / validation error cases ------------

    def test_unauthenticated_create_returns_401(self):
        self.client.force_authenticate(user=None)  # drop instructor auth from setUp
        response = self._post_assignment_via_contents()
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unverified_instructor_can_create(self):
        # Content authoring gates on IsInstructorUser, not IsVerifiedInstructor,
        # so a course can be built before identity verification completes.
        # Verification still gates leaving draft (/finish/, /submit/).
        self.course.instructors.add(self.unverified_instructor)
        self.auth(self.unverified_instructor)
        response = self._post_assignment_via_contents()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_learner_cannot_create(self):
        self.auth(self.learner)
        response = self._post_assignment_via_contents()
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_instructor_gets_404_for_section_they_dont_own(self):
        # Verified instructor but not assigned to this course → 404 (no leak).
        self.auth(self.other_instructor)
        response = self._post_assignment_via_contents(title='Sneaky Essay')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_validation_requires_title(self):
        response = self.client.post(
            self.contents_url,
            {'item_type': 'assignment'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('title', response.data['errors'])

    def test_create_with_invalid_position_returns_400(self):
        response = self.client.post(
            self.contents_url,
            {'item_type': 'assignment', 'title': 'OK Title', 'position': 0},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('position', response.data['message'])


# ---------------------------------------------------------------------------
# Submission gate: an assignment must be gradable
# ---------------------------------------------------------------------------

class AssignmentSubmissionGateTests(AssignmentTestBase):
    """An unfinished assignment used to publish silently and then grade every
    submission to 0 — `RubricGrader` short-circuits on an empty rubric, and
    `_autofill_rubric` only derives one from a model answer. Nothing inspected
    assignments at submission time."""

    def setUp(self):
        self.assignment = Assignment.objects.create(
            section=self.section, title='Reflection Essay', total_score=10, passing_score=5,
        )
        SectionContent.objects.create(
            section=self.section,
            item_type=SectionContent.ItemType.ASSIGNMENT,
            content_type=ContentType.objects.get_for_model(Assignment),
            object_id=self.assignment.pk,
            position=1,
        )

    def _submit(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            self.course.transition_to('under_review')
        return ctx.exception.message_dict

    def test_assignment_with_no_questions_blocks_submission(self):
        errors = self._submit()
        self.assertIn('assignments', errors)
        self.assertIn('Reflection Essay', errors['assignments'][0])
        self.assertIn('has no questions', errors['assignments'][0])

    def test_question_without_model_answer_or_rubric_blocks_submission(self):
        AssignmentQuestion.objects.create(
            assignment=self.assignment, question_text='Why?', position=1, points=10,
        )
        errors = self._submit()
        self.assertIn('assignments', errors)
        self.assertIn('cannot be graded', errors['assignments'][0])

    def test_model_answer_alone_is_enough(self):
        """The rubric auto-generates from it on save, so the question can score."""
        AssignmentQuestion.objects.create(
            assignment=self.assignment, question_text='Why?', position=1, points=10,
            model_answer='Because gradient descent minimises the loss function.',
        )
        self.course.transition_to('under_review')
        self.assertEqual(self.course.status, 'under_review')

    def test_rubric_alone_is_enough(self):
        AssignmentQuestion.objects.create(
            assignment=self.assignment, question_text='Why?', position=1, points=10,
            rubric=[{'type': 'keyword', 'value': 'gradient', 'points': 10}],
        )
        self.course.transition_to('under_review')
        self.assertEqual(self.course.status, 'under_review')

    def test_error_is_separate_from_the_quiz_and_lecture_checks(self):
        """Each incomplete content type reports under its own key, so the
        instructor sees every outstanding item at once rather than one at a
        time."""
        lecture = Lecture.objects.create(
            section=self.section, title='Unfinished lesson',
            lecture_type=Lecture.LectureType.VIDEO,
        )
        SectionContent.objects.create(
            section=self.section,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lecture.pk,
            position=2,
        )
        errors = self._submit()
        self.assertIn('assignments', errors)
        self.assertIn('empty_lectures', errors)


# ---------------------------------------------------------------------------
# Course lifecycle (status transitions + edit lock) — shared base
# ---------------------------------------------------------------------------
