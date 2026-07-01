from django.urls import path

from analytics.views import (
    InstitutionAnalyticsSummaryView,
    InstitutionCertificateTrendView,
    InstitutionEnrollmentTrendView,
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
]
