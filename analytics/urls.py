from django.urls import path

from analytics.views import (
    InstitutionAnalyticsSummaryView,
    InstitutionCertificateTrendView,
    InstitutionEnrollmentTrendView,
    InstitutionExpertPerformanceDetailView,
    InstitutionExpertPerformanceView,
    InstitutionTopCoursesView,
    InstitutionWebinarTrendView,
)

app_name = 'analytics'

urlpatterns = [
    # Partner institution analytics dashboard
    path('partner/summary/', InstitutionAnalyticsSummaryView.as_view(), name='partner-summary'),
    path('partner/enrollments/trend/', InstitutionEnrollmentTrendView.as_view(), name='partner-enrollment-trend'),
    path('partner/webinars/trend/', InstitutionWebinarTrendView.as_view(), name='partner-webinar-trend'),
    path('partner/certificates/trend/', InstitutionCertificateTrendView.as_view(), name='partner-certificate-trend'),
    path('partner/top-courses/', InstitutionTopCoursesView.as_view(), name='partner-top-courses'),
    # Per-expert performance (literal path before the <int:expert_id> route).
    path('partner/experts/performance/', InstitutionExpertPerformanceView.as_view(), name='partner-expert-performance'),
    path('partner/experts/<int:expert_id>/performance/', InstitutionExpertPerformanceDetailView.as_view(), name='partner-expert-performance-detail'),
]
