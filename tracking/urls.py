from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="tracking_index"),
    path("add_term/", views.SearchableCreateView.as_view(), name="add_term"),
    path("view_terms/", views.SearchableListView.as_view(), name="view_terms"),
    path("add_update/", views.UpdateScheduleCreateView.as_view(), name="add_update"),
    path("view_updates/", views.UpdateScheduleListView.as_view(), name="view_updates"),
    path("update/", views.UpdateFromWebView.as_view(), name="update"),
]