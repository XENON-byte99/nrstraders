from django.contrib import admin
from django.urls import path, include
from documents import views as doc_views
from django.contrib.auth import views as auth_views
from django.contrib.auth import logout
from django.shortcuts import redirect

def custom_logout(request):
    logout(request)
    return redirect('/login/')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', custom_logout, name='logout'),
    path('password_change/', auth_views.PasswordChangeView.as_view(), name='password_change'),
    path('password_change/done/', auth_views.PasswordChangeDoneView.as_view(), name='password_change_done'),
    
    path('', doc_views.home, name='home'),
    path('summary/', doc_views.bill_summary, name='bill_summary'),
    path('bills/', doc_views.transaction_list, name='transaction_list'),
    path('bills/new/', doc_views.transaction_create, name='transaction_create'),
    path('bills/<int:pk>/pricing/', doc_views.transaction_pricing, name='transaction_pricing'),
    path('bills/<int:pk>/approve/', doc_views.transaction_approve, name='transaction_approve'),
    path('bills/<int:pk>/status/', doc_views.transaction_update_status, name='transaction_update_status'),
    
    path('bills/<int:pk>/print/invoice/', doc_views.print_invoice, name='print_invoice'),
    path('bills/<int:pk>/print/quotation/', doc_views.print_quotation, name='print_quotation'),
    path('bills/<int:pk>/print/challan/', doc_views.print_challan, name='print_challan'),
    path('bills/<int:pk>/print/challan/<int:item_pk>/', doc_views.print_single_challan, name='print_single_challan'),
    path('bills/<int:pk>/print/lunch-challan/<str:date_str>/', doc_views.print_lunch_daily_challan, name='print_lunch_daily_challan'),
    path('bills/<int:pk>/print/mushok/', doc_views.print_mushok, name='print_mushok'),
    path('bills/print-multiple/<str:doc_type>/', doc_views.print_multiple, name='print_multiple'),
    
    path('bills/<int:pk>/edit/', doc_views.transaction_update, name='transaction_update'),
    path('bills/<int:pk>/delete/', doc_views.transaction_delete, name='transaction_delete'),

    path('audit/', doc_views.audit_log_view, name='audit_logs'),
    
    path('contacts/', doc_views.party_list, name='party_list'),
    path('contacts/new/', doc_views.party_create, name='party_create'),
    path('contacts/<int:pk>/edit/', doc_views.party_update, name='party_update'),
    path('contacts/<int:pk>/delete/', doc_views.party_delete, name='party_delete'),

    path('categories/', doc_views.category_list, name='category_list'),
    path('categories/new/', doc_views.category_create, name='category_create'),
    path('categories/<int:pk>/edit/', doc_views.category_update, name='category_update'),
    path('categories/<int:pk>/delete/', doc_views.category_delete, name='category_delete'),
    path('api/category/<int:pk>/defaults/', doc_views.category_defaults_api, name='category_defaults_api'),
    path('gate-entry/', doc_views.gate_entry, name='gate_entry'),

    # Products
    path('products/', doc_views.product_list, name='product_list'),
    path('products/new/', doc_views.product_create, name='product_create'),
    path('products/<int:pk>/edit/', doc_views.product_update, name='product_update'),
    path('products/<int:pk>/delete/', doc_views.product_delete, name='product_delete'),
    path('products/<int:pk>/approve/', doc_views.product_approve, name='product_approve'),
    path('api/products/search/', doc_views.product_search_api, name='product_search_api'),
    path('requisitions/', doc_views.requisition_list, name='requisition_list'),
    path('requisitions/new/', doc_views.requisition_create, name='requisition_create'),
    path('requisitions/<int:pk>/edit/', doc_views.requisition_update, name='requisition_update'),
    path('requisitions/<int:pk>/delete/', doc_views.requisition_delete, name='requisition_delete'),

    # Visual/Image Requisitions
    path('visual-requisitions/', doc_views.visual_requisition_list, name='visual_requisition_list'),
    path('visual-requisitions/new/', doc_views.visual_requisition_create, name='visual_requisition_create'),
    path('visual-requisitions/<int:pk>/success/', doc_views.visual_requisition_success, name='visual_requisition_success'),
    path('visual-requisitions/<int:pk>/delete/', doc_views.visual_requisition_delete, name='visual_requisition_delete'),

    # Check Authorizations
    path('authorizations/', doc_views.authorization_list, name='authorization_list'),
    path('authorizations/new/', doc_views.authorization_create, name='authorization_create'),
    path('authorizations/<int:pk>/edit/', doc_views.authorization_update, name='authorization_update'),
    path('authorizations/<int:pk>/print/', doc_views.authorization_print, name='authorization_print'),
    path('api/users/<int:user_id>/nid/', doc_views.api_get_user_nid, name='api_get_user_nid'),
    path('api/parties/<int:party_id>/details/', doc_views.api_get_party_details, name='api_get_party_details'),
    path('sync/', doc_views.sync_db_view, name='sync_db'),
    path('api/sync/status/', doc_views.sync_status_api, name='sync_status_api'),
]

from django.conf import settings
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
