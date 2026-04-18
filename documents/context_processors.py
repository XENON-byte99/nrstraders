from accounts.models import User

def whatsapp_contacts(request):
    """Provides default WhatsApp numbers for various roles globally."""
    if not request.user.is_authenticated:
        return {}
    
    # Cache these or optimize if many users, but fine for small business
    accountant = User.objects.filter(role='ACCOUNTANT').first()
    owner = User.objects.filter(role='OWNER').first()
    
    return {
        'wa_accountant_phone': accountant.whatsapp_number if accountant else '',
        'wa_owner_phone': owner.whatsapp_number if owner else '',
    }
