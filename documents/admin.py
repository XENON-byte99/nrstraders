from django.contrib import admin
from .models import Transaction, TransactionItem, BusinessParty, TransactionCategory, TransactionSnapshot

@admin.register(BusinessParty)
class BusinessPartyAdmin(admin.ModelAdmin):
    list_display = ('name', 'party_type', 'whatsapp_number', 'contact')
    search_fields = ('name', 'whatsapp_number')

@admin.register(TransactionCategory)
class TransactionCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'default_supplier', 'default_buyer')

class NoDeleteAdminMixin:
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

class TransactionItemInline(admin.TabularInline):
    model = TransactionItem
    extra = 1
    
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

@admin.register(Transaction)
class TransactionAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    inlines = [TransactionItemInline]
    list_display = ('invoice_number', 'supplier_name', 'buyer_name', 'status', 'created_at')
    
    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions and not request.user.is_superuser:
            del actions['delete_selected']
        return actions

@admin.register(TransactionSnapshot)
class TransactionSnapshotAdmin(admin.ModelAdmin):
    list_display = ('transaction', 'created_at', 'created_by')
    readonly_fields = ('transaction', 'created_at', 'created_by', 'snapshot_data')
    search_fields = ('transaction__invoice_number',)
