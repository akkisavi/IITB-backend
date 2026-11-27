from django.urls import path

from accounts import views as acc
from warehouse import views as wh

urlpatterns = [
    path("", wh.RootView.as_view(), name="root"),
    path("health/", wh.HealthView.as_view(), name="health"),
    path("api/auth/login/", acc.LoginView.as_view(), name="login"),
    path("api/auth/logout/", acc.LogoutView.as_view(), name="logout"),
    path("api/auth/me/", acc.MeView.as_view(), name="me"),
    path("api/auth/profile/", acc.ProfileView.as_view(), name="profile"),
    path("api/auth/password-reset/", acc.PasswordResetRequestView.as_view(), name="password-reset"),
    path("api/auth/password-reset/confirm/", acc.PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("api/auth/change-password/", acc.ChangePasswordView.as_view(), name="change-password"),
    path("api/locations/", wh.LocationListView.as_view(), name="locations"),
    path("api/locations/<str:code>/", wh.LocationDetailView.as_view(), name="location-detail"),
    path("api/operations/", wh.OperationListView.as_view(), name="operations"),
    path("api/operations/load/", wh.LoadView.as_view(), name="load"),
    path("api/operations/unload/", wh.UnloadView.as_view(), name="unload"),
    path("api/operations/bulk-unload/", wh.BulkUnloadView.as_view(), name="bulk-unload"),
    path("api/operations/<int:pk>/undo/", wh.UndoView.as_view(), name="undo"),
    path("api/pallets/", wh.PalletListView.as_view(), name="pallets"),
    path("api/pallets/<int:pk>/", wh.PalletDetailView.as_view(), name="pallet-detail"),
    path("api/pallets/<int:pk>/history/", wh.PalletHistoryView.as_view(), name="pallet-history"),
    path("api/shuttle/", wh.ShuttleView.as_view(), name="shuttle"),
    path("api/shuttle/maintenance/", wh.ShuttleMaintenanceView.as_view(), name="shuttle-maintenance"),
    path("api/reports/aging/", wh.AgingReportView.as_view(), name="report-aging"),
    path("api/reports/<str:name>.csv", wh.ReportView.as_view(), name="report-csv"),
    path("api/dashboard/", wh.DashboardView.as_view(), name="dashboard"),
    path("api/users/", acc.UserListCreateView.as_view(), name="users"),
    path("api/users/<int:pk>/", acc.UserDetailView.as_view(), name="user-detail"),
    path("api/users/<int:pk>/set-password/", acc.UserSetPasswordView.as_view(), name="user-set-password"),
]
