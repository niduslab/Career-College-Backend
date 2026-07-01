from django.urls import path

from webinars.views import (
    CatalogWebinarDetailView,
    CatalogWebinarListView,
    MyWebinarDetailView,
    MyWebinarsListView,
    WebinarArchiveView,
    WebinarCreateAPIView,
    WebinarDetailView,
    WebinarHostView,
    WebinarListAPIView,
    WebinarPublishView,
    WebinarRegisterView,
    WebinarReworkView,
)

app_name = 'webinars'

urlpatterns = [
    # -------------------------------------------------------------------------
    # Public catalog (no auth required)
    # -------------------------------------------------------------------------
    path('catalog/', CatalogWebinarListView.as_view(), name='catalog-list'),
    path('catalog/<slug:slug>/', CatalogWebinarDetailView.as_view(), name='catalog-detail'),

    # -------------------------------------------------------------------------
    # Learner registration + my-webinars
    # -------------------------------------------------------------------------
    path('my-webinars/', MyWebinarsListView.as_view(), name='my-webinars-list'),
    path('my-webinars/<slug:slug>/', MyWebinarDetailView.as_view(), name='my-webinars-detail'),

    # -------------------------------------------------------------------------
    # Authoring (institution-owned)
    # -------------------------------------------------------------------------
    path('create/', WebinarCreateAPIView.as_view(), name='webinar-create'),

    # Slug-based learner action (declared before the int-pk authoring routes).
    path('<slug:slug>/register/', WebinarRegisterView.as_view(), name='webinar-register'),

    path('', WebinarListAPIView.as_view(), name='webinar-list'),
    path('<int:pk>/', WebinarDetailView.as_view(), name='webinar-detail'),

    # Status transitions — host publishes directly; no review gates.
    path('<int:pk>/publish/', WebinarPublishView.as_view(), name='webinar-publish'),
    path('<int:pk>/rework/', WebinarReworkView.as_view(), name='webinar-rework'),
    path('<int:pk>/archive/', WebinarArchiveView.as_view(), name='webinar-archive'),

    # Host assignment
    path('<int:pk>/host/', WebinarHostView.as_view(), name='webinar-host'),
]
