from django.urls import path
from . import views

urlpatterns = [
    path('users/', views.user_list, name='user_list'),
    path('users/new/', views.user_create, name='user_create'),
    path('users/<int:pk>/update/', views.user_update, name='user_update'),
    path('users/<int:pk>/delete/', views.user_delete, name='user_delete'),
    path('users/<int:pk>/password/', views.user_password_override, name='user_password_override'),
    path('permissions/', views.permission_dashboard, name='permission_dashboard'),
    path('api/permissions/update/', views.api_update_permission, name='api_update_permission'),
    
    # Peer Transactions
    path('p2p/', views.peer_transaction_list, name='peer_transaction_list'),
    path('p2p/add/', views.peer_transaction_create, name='peer_transaction_create'),
    path('p2p/<int:pk>/confirm/', views.peer_transaction_confirm, name='peer_transaction_confirm'),
    path('p2p/<int:pk>/delete/', views.peer_transaction_delete, name='peer_transaction_delete'),
]
