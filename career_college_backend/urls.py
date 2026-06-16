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
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
