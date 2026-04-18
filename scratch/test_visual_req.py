import os
import sys
import django

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nrs_project.settings')
django.setup()

from documents.models import VisualRequisition
from accounts.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

def test_visual_requisition():
    print("--- Starting Visual Requisitions Test ---")
    
    # 1. Setup Users
    owner, _ = User.objects.get_or_create(username='owner_test', defaults={'role': 'OWNER'})
    buyer_a, _ = User.objects.get_or_create(username='buyer_a', defaults={'role': 'BUYER', 'whatsapp_number': '8801711111111'})
    buyer_b, _ = User.objects.get_or_create(username='buyer_b', defaults={'role': 'BUYER'})
    
    print(f"Users: {owner.username}({owner.role}), {buyer_a.username}, {buyer_b.username}")

    # 2. Create Requisition
    # Mock an image file
    small_gif = (
        b'\x47\x49\x46\x20\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x05\x04\x03'
        b'\x00\x00\x00\x21\xf9\x04\x01\x0e\x00\x1f\x00\x2c\x00\x00\x00\x00\x01'
        b'\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    )
    image = SimpleUploadedFile("test_req.gif", small_gif, content_type="image/gif")
    
    req = VisualRequisition.objects.create(
        uploader=owner,
        image=image,
        description="Test Requisition Image"
    )
    req.authorized_users.add(buyer_a)
    print(f"Requisition Created and Shared with {buyer_a.username}")

    # 3. Test Permissions
    # Owner sees everything
    all_reqs = VisualRequisition.objects.all()
    print(f"Owner total viewable: {all_reqs.count()}")
    assert all_reqs.count() >= 1

    # Buyer A should see it
    buyer_a_reqs = buyer_a.authorized_requisitions.all()
    print(f"Buyer A viewable: {buyer_a_reqs.count()}")
    assert buyer_a_reqs.filter(id=req.id).exists()

    # Buyer B should NOT see it
    buyer_b_reqs = buyer_b.authorized_requisitions.all()
    print(f"Buyer B viewable: {buyer_b_reqs.count()}")
    assert not buyer_b_reqs.filter(id=req.id).exists()

    # 4. Cleanup (optional)
    # req.delete()
    
    print("\n--- Test Passed Successfully! ---")

if __name__ == "__main__":
    try:
        test_visual_requisition()
    except Exception as e:
        print(f"Error during test: {e}")
        import traceback
        traceback.print_exc()
