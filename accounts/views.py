from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import User as CustomUser, PeerTransaction
from .forms import CustomUserCreationForm, UserUpdateForm, AdminPasswordChangeForm, PeerTransactionForm
from .decorators import permission_required

@login_required
@permission_required('p_manage_users')
def user_list(request):
    users = CustomUser.objects.all().order_by('username')
    return render(request, 'accounts/user_list.html', {'users': users})

@login_required
@permission_required('p_manage_users')
def user_create(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f"User '{user.username}' created successfully.")
            return redirect('user_list')
    else:
        form = CustomUserCreationForm()
    return render(request, 'accounts/user_form.html', {'form': form})

@login_required
@permission_required('p_manage_users')
def user_update(request, pk):
    user_obj = get_object_or_404(CustomUser, pk=pk)
    if request.method == 'POST':
        form = UserUpdateForm(request.POST, instance=user_obj)
        if form.is_valid():
            form.save()
            messages.success(request, f"User '{user_obj.username}' updated.")
            return redirect('user_list')
    else:
        form = UserUpdateForm(instance=user_obj)
    return render(request, 'accounts/user_form.html', {'form': form, 'is_update': True})

@login_required
@permission_required('p_manage_users')
def user_delete(request, pk):
    user_obj = get_object_or_404(CustomUser, pk=pk)
    if user_obj == request.user:
        messages.error(request, "You cannot delete yourself.")
        return redirect('user_list')
    
    if request.method == 'POST':
        username = user_obj.username
        user_obj.delete()
        messages.success(request, f"User '{username}' deleted.")
        return redirect('user_list')
    return render(request, 'documents/confirm_delete.html', {
        'object_name': f"User: {user_obj.username}",
        'cancel_url': 'user_list'
    })

@login_required
@permission_required('p_manage_users')
def user_password_override(request, pk):
    user_obj = get_object_or_404(CustomUser, pk=pk)
    if request.method == 'POST':
        form = AdminPasswordChangeForm(request.POST)
        if form.is_valid():
            user_obj.set_password(form.cleaned_data['new_password'])
            user_obj.save()
            messages.success(request, f"Password overridden for '{user_obj.username}'.")
            return redirect('user_list')
    else:
        form = AdminPasswordChangeForm()
    return render(request, 'accounts/user_password_override.html', {
        'form': form, 'target_user': user_obj
    })

@login_required
@permission_required('p_manage_users')
def permission_dashboard(request):
    users = CustomUser.objects.exclude(is_superuser=True).order_by('username')
    permission_fields = [
        ('p_view_bill_summary', 'Bill Summary'),
        ('p_view_bills', 'View Bills'),
        ('p_create_bill', 'Create Bill'),
        ('p_edit_bill', 'Edit Bill'),
        ('p_delete_bill', 'Delete Bill'),
        ('p_price_bill', 'Pricing'),
        ('p_approve_bill', 'Approval'),
        ('p_manage_inventory', 'Inventory'),
        ('p_manage_contacts', 'Contacts'),
        ('p_manage_categories', 'Categories'),
        ('p_view_gate_entry', 'Gate Entry'),
        ('p_manage_visual_requisitions', 'Visual Requisitions'),
        ('p_manage_requisitions', 'Buyer Requisitions'),
        ('p_manage_authorizations', 'Check Authorizations'),
        ('p_manage_peer_transactions', 'Money Tracker (Initiate)'),
        ('p_print_documents', 'Print Documents'),
        ('p_view_audit_logs', 'Audit Logs'),
        ('p_manage_users', 'User Mgmt'),
    ]
    return render(request, 'accounts/permission_dashboard.html', {
        'target_users': users,
        'permission_fields': permission_fields
    })

@login_required
@permission_required('p_manage_users')
def api_update_permission(request):
    from django.http import JsonResponse
    import json
    # Only real permission flags may be toggled — never arbitrary model
    # attributes (is_superuser, role, password, ...).
    ALLOWED_PERMS = {
        f.name for f in CustomUser._meta.get_fields()
        if f.name.startswith('p_') and getattr(f, 'get_internal_type', lambda: '')() == 'BooleanField'
    }
    if request.method == 'POST':
        data = json.loads(request.body)
        user_id = data.get('user_id')
        perm_name = data.get('perm_name')
        value = bool(data.get('value'))

        if perm_name not in ALLOWED_PERMS:
            return JsonResponse({'success': False, 'error': 'Invalid permission'}, status=400)

        user_obj = get_object_or_404(CustomUser, pk=user_id)
        if user_obj.is_superuser and not request.user.is_superuser:
            return JsonResponse({'success': False, 'error': 'Cannot modify a superuser'}, status=403)
        setattr(user_obj, perm_name, value)
        user_obj.save(update_fields=[perm_name])
        return JsonResponse({'success': True})
    return JsonResponse({'success': False}, status=400)

@login_required
def peer_transaction_list(request):
    """
    Shows list of peer-to-peer money transfers.
    - Managers see everything.
    - Others see only transactions where they are sender or receiver.
    """
    if request.user.p_manage_peer_transactions or request.user.is_superuser:
        transactions = PeerTransaction.objects.all()
    else:
        from django.db.models import Q
        transactions = PeerTransaction.objects.filter(Q(sender=request.user) | Q(receiver=request.user))
    
    return render(request, 'accounts/peer_transaction_list.html', {
        'transactions': transactions
    })

@login_required
@permission_required('p_manage_peer_transactions')
def peer_transaction_create(request):
    """Initiate a new money record (authorized only)."""
    if request.method == 'POST':
        form = PeerTransactionForm(request.POST)
        if form.is_valid():
            trans = form.save(commit=False)
            trans.initiator = request.user
            trans.status = 'PENDING'
            
            # Auto-confirm side for initiator
            if request.user == trans.sender:
                trans.sender_confirmed = True
            if request.user == trans.receiver:
                trans.receiver_confirmed = True
                
            trans.save()
            messages.success(request, f"Record for BDT {trans.amount} initiated. Pending confirmation from counterparts.")
            return redirect('peer_transaction_list')
    else:
        form = PeerTransactionForm()
    
    return render(request, 'accounts/peer_transaction_form.html', {'form': form})

@login_required
def peer_transaction_confirm(request, pk):
    """Allows the counterpart to confirm receiving/sending the money."""
    trans = get_object_or_404(PeerTransaction, pk=pk)
    
    # Check if the user is the 'other side' vs initiator
    # Logic: If I am the counterpart (not initiator), I can confirm.
    if request.user == trans.initiator:
        messages.error(request, "You cannot confirm a transaction that you initiated.")
        return redirect('peer_transaction_list')
    
    if request.user not in [trans.sender, trans.receiver]:
        messages.error(request, "You are not involved in this transaction.")
        return redirect('peer_transaction_list')
    
    if trans.status != 'PENDING':
        messages.warning(request, "This transaction is already processed.")
        return redirect('peer_transaction_list')

    if request.method == 'POST':
        import datetime
        if request.user == trans.sender:
            trans.sender_confirmed = True
        if request.user == trans.receiver:
            trans.receiver_confirmed = True
        
        # If both sides confirmed, mark as CONFIRMED overall
        if trans.sender_confirmed and trans.receiver_confirmed:
            trans.status = 'CONFIRMED'
            trans.confirmed_at = datetime.datetime.now()
            
        trans.save()
        messages.success(request, "Your confirmation has been recorded.")
        return redirect('peer_transaction_list')
    
    return render(request, 'documents/confirm_delete.html', {
        'object_name': f"Transaction: BDT {trans.amount} (from {trans.sender.username} to {trans.receiver.username})",
        'cancel_url': 'peer_transaction_list',
        'confirm_btn_text': 'Confirm Record',
        'confirm_btn_color': 'bg-emerald-600'
    })

@login_required
@permission_required('p_manage_peer_transactions')
def peer_transaction_delete(request, pk):
    trans = get_object_or_404(PeerTransaction, pk=pk)
    if request.method == 'POST':
        trans.delete()
        messages.success(request, "Transaction record deleted.")
        return redirect('peer_transaction_list')
    return render(request, 'documents/confirm_delete.html', {
        'object_name': f"Transaction Record: BDT {trans.amount}",
        'cancel_url': 'peer_transaction_list'
    })
