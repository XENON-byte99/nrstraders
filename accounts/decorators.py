from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps

def permission_required(perm_name):
    """Decorator for views that checks if a user has a specific granular permission."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            
            # Superusers always have permission
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            
            # Check specific permission OR if user is OWNER (Master Access)
            if request.user.role == 'OWNER' or getattr(request.user, perm_name, False):
                return view_func(request, *args, **kwargs)
            
            messages.error(request, f"Permission Denied: You do not have the required access ({perm_name.replace('p_', '').replace('_', ' ').title()}).")
            return redirect('home')
        return _wrapped_view
    return decorator

# Legacy/Shortcut Decorators (Updated to use permissions)
def accountant_required(view_func):
    return permission_required('p_manage_users')(view_func)

def supplier_required(view_func):
    return permission_required('p_price_bill')(view_func)

def buyer_required(view_func):
    return permission_required('p_manage_requisitions')(view_func)

def owner_required(view_func):
    # Specifically for the permission panel and high-level owner tasks
    return permission_required('p_approve_bill')(view_func)

def owner_or_accountant_required(view_func):
    def check_logic(user):
        # Allow if Superuser, Owner role, Accountant role, or has key billing permissions
        return (user.is_superuser or 
                user.role in ['OWNER', 'ACCOUNTANT'] or 
                user.p_edit_bill or 
                user.p_approve_bill or 
                user.p_create_bill)
    return user_passes_test(check_logic)(view_func)
