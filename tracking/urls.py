from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="tracking_index"),
    path("add_term/", views.SearchableCreateView.as_view(), name="add_term"),
    path("edit_term/<int:pk>/", views.SearchableUpdateView.as_view(), name="edit_term"),
    path("view_terms/", views.SearchableListView.as_view(), name="view_terms"),
    path("item/<int:pk>/", views.SearchableItemDetailView.as_view(), name="item_detail"),
    # Phase 3 Step 6 — per-item price-history export.
    path("item/<int:pk>/export.csv", views.export_item_csv, name="export_item_csv"),
    path("item/<int:pk>/export.json", views.export_item_json, name="export_item_json"),
    path("tags/", views.TagListView.as_view(), name="view_tags"),
    path("tags/add/", views.TagCreateView.as_view(), name="add_tag"),
    path("tags/<int:pk>/edit/", views.TagUpdateView.as_view(), name="edit_tag"),
    path("tags/<int:pk>/delete/", views.TagDeleteView.as_view(), name="delete_tag"),
    path("sources/", views.SourceListView.as_view(), name="view_sources"),
    path("sources/add/", views.SourceCreateView.as_view(), name="add_source"),
    path("sources/<str:pk>/edit/", views.SourceUpdateView.as_view(), name="edit_source"),
    path("sources/<str:pk>/delete/", views.SourceDeleteView.as_view(), name="delete_source"),
    path("item/<int:pk>/sources/", views.ItemSourceListView.as_view(), name="item_sources"),
    path("item/<int:pk>/sources/add/", views.ItemSourceCreateView.as_view(), name="add_item_source"),
    path("item_source/<int:pk>/edit/", views.ItemSourceUpdateView.as_view(), name="edit_item_source"),
    path("item_source/<int:pk>/delete/", views.ItemSourceDeleteView.as_view(), name="delete_item_source"),
    # Scrape-run history (unchanged name). Schedules live under /schedules/.
    path("view_updates/", views.WebUpdateListView.as_view(), name="view_updates"),
    # Recurring schedules (Phase 3 Step 4). ``add_update`` is kept as an alias
    # for the schedule create view so existing links keep resolving.
    path("add_update/", views.UpdateScheduleCreateView.as_view(), name="add_update"),
    path("schedules/", views.UpdateScheduleListView.as_view(), name="view_schedules"),
    path("schedules/add/", views.UpdateScheduleCreateView.as_view(), name="add_schedule"),
    path("schedules/<int:pk>/edit/", views.UpdateScheduleUpdateView.as_view(), name="edit_schedule"),
    path("schedules/<int:pk>/delete/", views.UpdateScheduleDeleteView.as_view(), name="delete_schedule"),
    path("update/", views.UpdateFromWebView.as_view(), name="update"),
    path("update/<int:pk>/progress/", views.UpdateProgressView.as_view(), name="update_progress"),
]