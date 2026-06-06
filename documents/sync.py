import os
import uuid
import dj_database_url
from django.conf import settings
from django.db import transaction, connections
from django.contrib.auth import get_user_model
from documents.models import (
    TransactionCategory, BusinessParty, Product, 
    Transaction, TransactionItem, VisualRequisition, CheckAuthorization
)

User = get_user_model()

# Order is important: dependencies must be synced first
# e.g., categories and parties first, then products and transactions, then transaction items
MODELS_TO_SYNC = [
    (TransactionCategory, []),
    (BusinessParty, []),
    (Product, [('category', TransactionCategory)]),
    (Transaction, [('transaction_category', TransactionCategory)]),
    (TransactionItem, [('transaction', Transaction)]),
    (VisualRequisition, []),
    (CheckAuthorization, [])
]

def get_remote_user(local_user):
    if not local_user:
        return None
    # Match user by username
    return User.objects.using('supabase').filter(username=local_user.username).first()

def sync_model_instances(model, fk_mappings):
    print(f"Syncing model: {model.__name__}...")
    
    # --- PHASE 1: PUSH LOCAL DIRTY CHANGES TO SUPABASE ---
    local_dirty = model.objects.using('default').filter(is_dirty=True)
    for local_obj in local_dirty:
        # Check if record already exists in Supabase by UUID
        remote_obj = model.objects.using('supabase').filter(uuid=local_obj.uuid).first()
        
        # Build field dictionary to copy
        fields_to_copy = {}
        for field in local_obj._meta.fields:
            # Skip primary key ID and sync helper fields
            if field.name in ['id', 'is_dirty', 'updated_at']:
                continue
            
            # Handle Foreign Keys to Syncable Models
            fk_target = next((target for name, target in fk_mappings if name == field.name), None)
            if fk_target:
                local_fk_val = getattr(local_obj, field.name)
                if local_fk_val:
                    # Look up corresponding remote FK id by matching UUID
                    remote_fk_obj = fk_target.objects.using('supabase').filter(uuid=local_fk_val.uuid).first()
                    fields_to_copy[field.attname] = remote_fk_obj.id if remote_fk_obj else None
                else:
                    fields_to_copy[field.attname] = None
            
            # Handle Foreign Keys to User Model
            elif field.name in ['creator', 'added_by', 'uploader', 'nominee', 'authorized_by']:
                local_user = getattr(local_obj, field.name)
                remote_user = get_remote_user(local_user)
                fields_to_copy[field.attname] = remote_user.id if remote_user else None
            
            # Copy standard fields
            else:
                fields_to_copy[field.name] = getattr(local_obj, field.name)

        if remote_obj:
            # Record exists, update it
            if local_obj.is_deleted:
                # If deleted locally, soft delete on Supabase
                remote_obj.is_deleted = True
            else:
                for k, v in fields_to_copy.items():
                    setattr(remote_obj, k, v)
                remote_obj.is_deleted = local_obj.is_deleted
            
            # Save remote copy with skip_dirty to avoid infinite loops
            remote_obj.save(using='supabase', skip_dirty=True)
        else:
            # Record does not exist, create it in Supabase
            if not local_obj.is_deleted: # Don't sync deleted records that never went to cloud
                new_remote = model(**fields_to_copy)
                new_remote.is_deleted = False
                new_remote.save(using='supabase', skip_dirty=True)
        
        # Mark local record as clean (not dirty anymore)
        local_obj.is_dirty = False
        local_obj.save(using='default', skip_dirty=True)

    # --- PHASE 2: PULL REMOTE CHANGES TO LOCAL SQLITE ---
    # Fetch all records from Supabase
    remote_all = model.objects.using('supabase').all()
    for remote_obj in remote_all:
        local_obj = model.objects.using('default').filter(uuid=remote_obj.uuid).first()
        
        # Build field dictionary to copy
        fields_to_copy = {}
        for field in remote_obj._meta.fields:
            if field.name in ['id', 'is_dirty', 'updated_at']:
                continue
            
            # Handle Foreign Keys to Syncable Models
            fk_target = next((target for name, target in fk_mappings if name == field.name), None)
            if fk_target:
                remote_fk_val = getattr(remote_obj, field.name)
                if remote_fk_val:
                    # Look up local FK id by matching UUID
                    local_fk_obj = fk_target.objects.using('default').filter(uuid=remote_fk_val.uuid).first()
                    fields_to_copy[field.attname] = local_fk_obj.id if local_fk_obj else None
                else:
                    fields_to_copy[field.attname] = None
            
            # Handle Foreign Keys to User Model
            elif field.name in ['creator', 'added_by', 'uploader', 'nominee', 'authorized_by']:
                remote_user = getattr(remote_obj, field.name)
                if remote_user:
                    local_user = User.objects.using('default').filter(username=remote_user.username).first()
                    fields_to_copy[field.attname] = local_user.id if local_user else None
                else:
                    fields_to_copy[field.attname] = None
            
            # Copy standard fields
            else:
                fields_to_copy[field.name] = getattr(remote_obj, field.name)

        if local_obj:
            # Record exists locally. Only update if remote is newer and local is not dirty (to avoid overwriting user edits)
            if not local_obj.is_dirty and remote_obj.updated_at > local_obj.updated_at:
                for k, v in fields_to_copy.items():
                    setattr(local_obj, k, v)
                local_obj.is_deleted = remote_obj.is_deleted
                local_obj.is_dirty = False
                local_obj.save(using='default', skip_dirty=True)
        else:
            # Record does not exist locally, create it (unless soft-deleted)
            if not remote_obj.is_deleted:
                new_local = model(**fields_to_copy)
                new_local.is_dirty = False
                new_local.is_deleted = False
                new_local.save(using='default', skip_dirty=True)

def run_synchronization():
    url = os.environ.get('SUPABASE_DATABASE_URL')
    if not url:
        return {"status": "error", "message": "SUPABASE_DATABASE_URL environment variable is not configured."}

    if 'supabase' not in settings.DATABASES:
        settings.DATABASES['supabase'] = dj_database_url.parse(url)

    try:
        connections['supabase'].ensure_connection()
    except Exception as e:
        return {"status": "error", "message": f"Could not connect to Supabase: {e}"}

    try:
        # Run sync within database transactions to ensure data consistency
        with transaction.atomic(using='default'):
            with transaction.atomic(using='supabase'):
                # Sync Users first (ensure users exist on both sides)
                # Note: We assume users are created online and synced down
                local_users = User.objects.using('default').all()
                for u in local_users:
                    if not User.objects.using('supabase').filter(username=u.username).exists():
                        # Copy user to Supabase
                        fields = {f.name: getattr(u, f.name) for f in u._meta.fields if f.name != 'id'}
                        User.objects.using('supabase').create(**fields)
                
                remote_users = User.objects.using('supabase').all()
                for u in remote_users:
                    if not User.objects.using('default').filter(username=u.username).exists():
                        fields = {f.name: getattr(u, f.name) for f in u._meta.fields if f.name != 'id'}
                        User.objects.using('default').create(**fields)

                # Sync all syncable models in order
                for model, fk_mappings in MODELS_TO_SYNC:
                    sync_model_instances(model, fk_mappings)
                    
        return {"status": "success", "message": "Sync completed successfully."}
    except Exception as e:
        return {"status": "error", "message": f"Sync failed: {str(e)}"}
