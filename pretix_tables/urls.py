from django.urls import path

from . import views

urlpatterns = [
    path("control/event/<str:organizer>/<str:event>/numbered-spaces/", views.OverviewView.as_view(),
         name="overview"),
    path("control/event/<str:organizer>/<str:event>/numbered-spaces/wording/", views.WordingView.as_view(),
         name="wording"),
    path("control/event/<str:organizer>/<str:event>/numbered-spaces/<int:item>/", views.TablesView.as_view(),
         name="tables"),
]

event_patterns = [
    path("numbered-spaces/status/", views.status_view, name="status"),
]
