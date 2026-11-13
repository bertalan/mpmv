"""A URLconf with a `sitemap` route, until the sitemaps (SEO.2) add the real one."""

from django.http import HttpRequest, HttpResponse
from django.urls import path

from mpmv.urls import urlpatterns as project_urlpatterns


def sitemap(request: HttpRequest) -> HttpResponse:
    return HttpResponse("<urlset/>", content_type="application/xml")


urlpatterns = [*project_urlpatterns, path("sitemap.xml", sitemap, name="sitemap")]
