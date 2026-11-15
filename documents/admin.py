from django.contrib import admin
from .models import (Transaction, TransactionItem, BusinessParty, TransactionCategory,
                     TransactionSnapshot, Note, DriveFolder, DriveFile, TableTemplate,
                     TransactionAttachment)

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

@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'color', 'is_pinned', 'created_at', 'updated_at')
    list_filter = ('color', 'is_pinned', 'created_at')
    search_fields = ('title', 'content', 'user__username')

@admin.register(DriveFolder)
class DriveFolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'created_by', 'created_at')
    search_fields = ('name',)

@admin.register(DriveFile)
class DriveFileAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'folder', 'size', 'uploaded_by', 'uploaded_at')
    search_fields = ('original_name',)

@admin.register(TableTemplate)
class TableTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'created_at', 'updated_at')
    search_fields = ('name', 'description')

@admin.register(TransactionAttachment)
class TransactionAttachmentAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'transaction', 'size', 'uploaded_by', 'uploaded_at')
    search_fields = ('original_name', 'transaction__invoice_number')

