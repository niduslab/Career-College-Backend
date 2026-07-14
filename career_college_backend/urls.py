from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/auth/', include('authentication.urls', namespace='authentication')),
    path('api/v1/verification/', include('id_verification.urls', namespace='id_verification')),
    path('api/v1/courses/', include('courses.urls', namespace='courses')),
    path('api/v1/notifications/', include('notifications.urls', namespace='notifications')),
    path('api/v1/messaging/', include('messaging.urls', namespace='messaging')),
    path('api/v1/webinars/', include('webinars.urls', namespace='webinars')),
    path('api/v1/analytics/', include('analytics.urls', namespace='analytics')),
    path('api/v1/payments/', include('payments.urls', namespace='payments')),
    path('api/v1/admin-console/', include('admin_console.urls', namespace='admin_console')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
