from django.urls import path

from analytics.views import (
    AdminAnalyticsSummaryView,
    AdminCertificateTrendView,
    AdminEnrollmentTrendView,
    AdminFunnelView,
    AdminRevenueTrendView,
    AdminTopCoursesView,
    AdminUserTrendView,
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

    # Platform-wide analytics dashboard (admin only)
    path('admin/summary/', AdminAnalyticsSummaryView.as_view(), name='admin-summary'),
    path('admin/users/trend/', AdminUserTrendView.as_view(), name='admin-users-trend'),
    path('admin/enrollments/trend/', AdminEnrollmentTrendView.as_view(), name='admin-enrollments-trend'),
    path('admin/certificates/trend/', AdminCertificateTrendView.as_view(), name='admin-certificates-trend'),
    path('admin/revenue/trend/', AdminRevenueTrendView.as_view(), name='admin-revenue-trend'),
    path('admin/top-courses/', AdminTopCoursesView.as_view(), name='admin-top-courses'),
    path('admin/funnel/', AdminFunnelView.as_view(), name='admin-funnel'),
]
