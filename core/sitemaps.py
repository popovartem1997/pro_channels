from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return ["home", "core:offer", "core:privacy", "advertisers:catalog", "core:quickstart"]

    def location(self, item):
        return reverse(item)

