from django import template
import urllib.parse

register = template.Library()

@register.simple_tag(takes_context=True)
def whatsapp_link(context, transaction, role_or_key):
    """
    Generates a WhatsApp link for a transaction targeting a specific role or context key.
    Roles: 'supplier', 'buyer', 'owner', 'accountant'
    """
    if not transaction:
        return "#"
        
    request = context.get('request')
    phone = ""
    
    if role_or_key == 'supplier':
        phone = getattr(transaction, 'supplier_contact', '')
    elif role_or_key == 'buyer':
        phone = getattr(transaction, 'buyer_contact', '')
    elif role_or_key == 'owner':
        phone = context.get('wa_owner_phone', '')
    elif role_or_key == 'accountant':
        phone = context.get('wa_accountant_phone', '')
    else:
        # Fallback to direct context lookup if it's a custom key
        phone = context.get(role_or_key, '')
        
    return transaction.get_whatsapp_url(request, target_phone=phone)
