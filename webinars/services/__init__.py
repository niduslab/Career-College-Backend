from .registration_service import register_for_webinar
from .webinar_service import (
    WebinarError,
    assign_webinar_host,
    clear_webinar_host,
    filter_catalog_webinars,
    get_catalog_webinars,
    set_institutional_speakers,
)

__all__ = [
    'WebinarError',
    'assign_webinar_host',
    'clear_webinar_host',
    'filter_catalog_webinars',
    'get_catalog_webinars',
    'set_institutional_speakers',
    'register_for_webinar',
]
