"""Reusable DRF pagination classes used across apps."""

from rest_framework.pagination import PageNumberPagination


class StandardResultsSetPagination(PageNumberPagination):
    """Default pagination settings for API list endpoints."""

    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100
