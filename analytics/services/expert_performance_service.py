"""Per-expert performance metrics for a partner institution's roster.

Drills below the institution-wide dashboard to per-expert outcomes (ratings,
enrollments, completions, certificates, content output, webinar hosting).

Attribution: a course is credited to every user in `course.instructors` AND to
its `created_by`, when the course's `partner_institution` is this institution.
Co-taught courses therefore count toward each instructor — per-expert sums can
exceed the institution total (surfaced as `attribution` in the payload).

Cost is a fixed ~12 grouped queries, independent of roster size — per-course
aggregates are computed once and summed per expert in Python.
"""

from django.db.models import Count, Max, Q

from authentication.models import InstructorProfile
from courses.all_models.assessment_models import Assignment, CodingExercise, Quiz
from courses.all_models.certificate_models import Certificate
from courses.all_models.content_models import Lecture
from courses.all_models.course_models import CourseSection, NidusCourse
from courses.all_models.enrollment_models import Enrollment
from webinars.all_models.registration_models import WebinarRegistration
from webinars.all_models.webinar_models import Webinar

ATTRIBUTION_NOTE = 'a course is credited to every instructor and its creator'


def _pct(part, whole):
    if not whole:
        return 0.0
    return round(part / whole * 100, 1)


def _count_by_creator(queryset, expert_ids):
    """{creator_user_id: {'n': count, 'last': max_created_at}} over authored rows."""
    rows = (
        queryset
        .filter(created_by_id__in=expert_ids)
        .values('created_by_id')
        .annotate(n=Count('id'), last=Max('created_at'))
    )
    return {r['created_by_id']: {'n': r['n'], 'last': r['last']} for r in rows}


def expert_performance(institution, *, expert_id=None):
    """Per-expert performance rows for the institution's active roster.

    Returns a list of row dicts. When `expert_id` is given, returns the single
    matching row; raises InstructorProfile.DoesNotExist if that user is not an
    active affiliate (numeric id → 404 in the view).
    """
    roster_qs = (
        InstructorProfile.objects
        .filter(affiliated_institution=institution, affiliation_status='active')
        .select_related('user', 'department')
        .order_by('-affiliated_at')
    )
    if expert_id is not None:
        roster_qs = roster_qs.filter(user_id=expert_id)

    roster = list(roster_qs)
    if expert_id is not None and not roster:
        raise InstructorProfile.DoesNotExist

    expert_ids = [p.user_id for p in roster]
    if not expert_ids:
        return []

    expert_id_set = set(expert_ids)

    # ── Course metadata + credited-course sets ──
    course_meta = {
        c['id']: c
        for c in NidusCourse.objects
        .filter(partner_institution=institution)
        .values('id', 'is_published', 'created_by_id', 'avg_rating', 'review_count')
    }
    credited = {uid: set() for uid in expert_ids}
    for cid, meta in course_meta.items():
        if meta['created_by_id'] in expert_id_set:
            credited[meta['created_by_id']].add(cid)
    for row in (
        NidusCourse.objects
        .filter(partner_institution=institution, instructors__in=expert_ids)
        .values('id', 'instructors')
    ):
        # The join reuse guarantees `instructors` is in `expert_id_set`, but guard
        # against a future query change surfacing a non-roster instructor.
        if row['instructors'] in credited:
            credited[row['instructors']].add(row['id'])

    # ── Per-course aggregates (summed per expert below) ──
    enroll = {
        r['course']: r
        for r in Enrollment.objects
        .filter(course__partner_institution=institution, is_active=True)
        .values('course')
        .annotate(active=Count('id'), completed=Count('id', filter=Q(completed_at__isnull=False)))
    }
    certs = {
        r['enrollment__course']: r['n']
        for r in Certificate.objects
        .filter(enrollment__course__partner_institution=institution)
        .values('enrollment__course')
        .annotate(n=Count('id'))
    }

    # ── Content authored, keyed by creator ──
    sections = _count_by_creator(
        CourseSection.objects.filter(course__partner_institution=institution), expert_ids)
    lectures = _count_by_creator(
        Lecture.objects.filter(section__course__partner_institution=institution), expert_ids)
    quizzes = _count_by_creator(
        Quiz.objects.filter(section__course__partner_institution=institution), expert_ids)
    assignments = _count_by_creator(
        Assignment.objects.filter(section__course__partner_institution=institution), expert_ids)
    coding = _count_by_creator(
        CodingExercise.objects.filter(section__course__partner_institution=institution), expert_ids)

    # ── Webinar hosting ──
    webinars_hosted = {
        r['host_expert']: r['n']
        for r in Webinar.objects
        .filter(partner_institution=institution, host_expert_id__in=expert_ids)
        .values('host_expert')
        .annotate(n=Count('id'))
    }
    webinar_regs = {
        r['webinar__host_expert']: r['n']
        for r in WebinarRegistration.objects
        .filter(webinar__partner_institution=institution,
                webinar__host_expert_id__in=expert_ids, is_active=True)
        .values('webinar__host_expert')
        .annotate(n=Count('id'))
    }

    rows = [
        _build_row(p, credited[p.user_id], course_meta, enroll, certs,
                   sections, lectures, quizzes, assignments, coding,
                   webinars_hosted, webinar_regs)
        for p in roster
    ]
    return rows[0] if expert_id is not None else rows


def _build_row(profile, course_ids, course_meta, enroll, certs,
               sections, lectures, quizzes, assignments, coding,
               webinars_hosted, webinar_regs):
    uid = profile.user_id

    published = sum(1 for cid in course_ids if course_meta[cid]['is_published'])

    active = completed = 0
    weighted_rating = review_total = 0
    for cid in course_ids:
        agg = enroll.get(cid)
        if agg:
            active += agg['active']
            completed += agg['completed']
        meta = course_meta[cid]
        if meta['is_published'] and meta['review_count']:
            weighted_rating += float(meta['avg_rating']) * meta['review_count']
            review_total += meta['review_count']

    certificates = sum(certs.get(cid, 0) for cid in course_ids)

    content = {
        'sections': sections.get(uid, {}).get('n', 0),
        'lectures': lectures.get(uid, {}).get('n', 0),
        'quizzes': quizzes.get(uid, {}).get('n', 0),
        'assignments': assignments.get(uid, {}).get('n', 0),
        'coding_exercises': coding.get(uid, {}).get('n', 0),
    }
    last_active = max(
        (d.get(uid, {}).get('last') for d in (sections, lectures, quizzes, assignments, coding)
         if d.get(uid, {}).get('last') is not None),
        default=None,
    )

    return {
        'expert': {
            'id': uid,
            'full_name': profile.user.full_name,
            'email': profile.user.email,
        },
        'department': profile.department.name if profile.department_id else None,
        'affiliation_status': profile.affiliation_status,
        'affiliated_at': profile.affiliated_at,
        'courses_credited': len(course_ids),
        'published_courses': published,
        'content': content,
        'avg_rating': round(weighted_rating / review_total, 2) if review_total else 0.0,
        'total_reviews': review_total,
        'enrollments': active,
        'completion_rate': _pct(completed, active),
        'certificates': certificates,
        'webinars_hosted': webinars_hosted.get(uid, 0),
        'webinar_registrations': webinar_regs.get(uid, 0),
        'last_active': last_active,
    }
