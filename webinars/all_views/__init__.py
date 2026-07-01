from .catalog_views import CatalogWebinarDetailView, CatalogWebinarListView
from .host_views import WebinarHostView
from .registration_views import (
    MyWebinarDetailView,
    MyWebinarsListView,
    WebinarRegisterView,
)
from .status_views import (
    WebinarArchiveView,
    WebinarPublishView,
    WebinarReworkView,
)
from .webinar_views import (
    WebinarCreateAPIView,
    WebinarDetailView,
    WebinarListAPIView,
)

__all__ = [
    'CatalogWebinarDetailView',
    'CatalogWebinarListView',
    'MyWebinarDetailView',
    'MyWebinarsListView',
    'WebinarArchiveView',
    'WebinarCreateAPIView',
    'WebinarDetailView',
    'WebinarHostView',
    'WebinarListAPIView',
    'WebinarPublishView',
    'WebinarRegisterView',
    'WebinarReworkView',
]
