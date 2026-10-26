"""Other dashboards and Docker front-ends: people run them side by side."""
from __future__ import annotations

from .base import App, register, title, title_is

register(
    App("homarr", "Homarr", "Dashboards", [7575], match=title("Homarr"), images=["homarr"]),
    App("dashy", "Dashy", "Dashboards", [4000, 8080], match=title("Dashy"), images=["dashy"]),
    App("heimdall", "Heimdall", "Dashboards", [80], match=title("Heimdall"), images=["heimdall"]),
    App("organizr", "Organizr", "Dashboards", [80], match=title("Organizr"), images=["organizr"]),
    App("flame", "Flame", "Dashboards", [5005], match=title_is("Flame"), images=["flame"]),
    App("glance", "Glance", "Dashboards", [8080], match=title_is("Glance"), images=["glanceapp/glance"]),
    App("dockge", "Dockge", "Infrastructure", [5001], match=title_is("Dockge"), images=["dockge"]),
    App("yacht", "Yacht", "Infrastructure", [8000], match=title_is("Yacht"), images=["yacht"]),
)
