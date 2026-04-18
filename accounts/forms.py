from django import forms
from .models import User as CustomUser

class CustomUserCreationForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)

    class Meta:
        model = CustomUser
        fields = ['username', 'email', 'role', 'whatsapp_number', 'nid_number']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['nid_number'].required = True
        self.fields['nid_number'].label = "National ID (NID)"

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user

class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ['username', 'email', 'role', 'whatsapp_number', 'nid_number', 'google_drive_json_key', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['nid_number'].required = True
        self.fields['nid_number'].label = "National ID (NID)"

class AdminPasswordChangeForm(forms.Form):
    new_password = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': 'Enter new password'}))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': 'Confirm new password'}))

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('new_password')
        p2 = cleaned_data.get('confirm_password')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned_data

from .models import PeerTransaction

class PeerTransactionForm(forms.ModelForm):
    class Meta:
        model = PeerTransaction
        fields = ['sender', 'receiver', 'amount', 'description']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'What is this money for?'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Sort users by username for easier selection
        self.fields['sender'].queryset = CustomUser.objects.filter(is_active=True).order_by('username')
        self.fields['receiver'].queryset = CustomUser.objects.filter(is_active=True).exclude(pk=self.instance.sender.pk if self.instance.pk else None).order_by('username')
