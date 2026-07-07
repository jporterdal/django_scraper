from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="tracking_index"),
    path("add_term/", views.SearchableCreateView.as_view(), name="add_term"),
    path("edit_term/<int:pk>/", views.SearchableUpdateView.as_view(), name="edit_term"),
    path("view_terms/", views.SearchableListView.as_view(), name="view_terms"),
    path("tags/", views.TagListView.as_view(), name="view_tags"),
    path("tags/add/", views.TagCreateView.as_view(), name="add_tag"),
    path("tags/<int:pk>/edit/", views.TagUpdateView.as_view(), name="edit_tag"),
    path("tags/<int:pk>/delete/", views.TagDeleteView.as_view(), name="delete_tag"),
    path("add_update/", views.UpdateScheduleCreateView.as_view(), name="add_update"),
    path("view_updates/", views.UpdateScheduleListView.as_view(), name="view_updates"),
    path("update/", views.UpdateFromWebView.as_view(), name="update"),
]