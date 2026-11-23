from django.contrib import admin
from django.urls import path, include
from documents import views as doc_views
from documents import app_views, inbox_views, live_sync, mobile_views
from documents import whatsapp as whatsapp_views
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
    path('bills/print-bundle/', doc_views.print_bundle, name='print_bundle'),
    path('bills/print-received-summary/', doc_views.print_received_summary, name='print_received_summary'),
    
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
    path('api/print-layouts/<int:pk>/<str:doc_type>/', doc_views.print_layout_api, name='print_layout_api'),
    path('api/quick-bill/parse/', doc_views.quick_bill_parse, name='quick_bill_parse'),
    path('api/quick-bill/ai-parse/', doc_views.quick_bill_ai_parse, name='quick_bill_ai_parse'),
    path('api/bills/<int:pk>/inline-save/', doc_views.api_inline_save_transaction, name='api_inline_save_transaction'),
    path('api/quick-bill/smart-parse/', inbox_views.quick_bill_smart_parse, name='quick_bill_smart_parse'),

    # Message Inbox (WhatsApp / text messages -> bills)
    path('messages/', inbox_views.inbox_list, name='inbox_list'),
    path('messages/import/', inbox_views.inbox_import, name='inbox_import'),
    path('messages/action/', inbox_views.inbox_action, name='inbox_action'),
    path('api/inbox/status/', inbox_views.inbox_status_api, name='inbox_status_api'),

    # The phone app's own screens
    path('app/', app_views.app_home, name='app_home'),
    path('app/bills/', app_views.app_bills, name='app_bills'),
    path('app/bills/new/', app_views.app_bill_new, name='app_bill_new'),
    path('app/bills/<int:pk>/', app_views.app_bill_detail, name='app_bill_detail'),
    path('app/api/bill/', app_views.app_bill_save, name='app_bill_save'),
    path('app/inbox/', app_views.app_inbox, name='app_inbox'),
    path('app/orders/<int:pk>/action/', app_views.app_order_action, name='app_order_action'),
    path('app/orders/<int:pk>/record/', app_views.app_order_record, name='app_order_record'),
    path('app/ai-costs/', app_views.app_ai_costs, name='app_ai_costs'),
    path('app/setup/', app_views.app_setup, name='app_setup'),
    path('api/whatsapp/webhook/', whatsapp_views.webhook, name='whatsapp_webhook'),
    path('app/products/', app_views.app_products, name='app_products'),
    path('app/contacts/', app_views.app_contacts, name='app_contacts'),
    path('app/more/', app_views.app_more, name='app_more'),
    path('app/sync/', app_views.app_sync, name='app_sync'),
    path('app/api/sync/status/', app_views.app_sync_status, name='app_sync_status'),

    # Live-server sync API (called by phones; token-authenticated)
    path('api/sync/login/', live_sync.api_login, name='sync_login'),
    path('api/sync/snapshot/', live_sync.api_snapshot, name='sync_snapshot'),
    path('api/sync/exchange/', live_sync.api_exchange, name='sync_exchange'),
    path('api/sync/media/', live_sync.api_media, name='sync_media'),

    # First-run setup (mobile app) and backup / restore
    path('setup/', mobile_views.mobile_setup, name='mobile_setup'),
    path('backup/', mobile_views.backup_page, name='backup_page'),
    path('backup/download/', mobile_views.backup_download, name='backup_download'),
    path('backup/restore/', mobile_views.backup_restore, name='backup_restore'),
    path('sync/', doc_views.sync_db_view, name='sync_db'),
    path('api/sync/status/', doc_views.sync_status_api, name='sync_status_api'),
]

from django.conf import settings
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif getattr(settings, 'MOBILE_APP', False):
    # On the phone there is no web server in front of Django to serve uploads.
    from django.urls import re_path
    from django.views.static import serve
    urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]
