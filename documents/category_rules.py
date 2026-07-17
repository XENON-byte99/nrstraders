"""
Per-category rules, isolated.

Each transaction category (Room reservation, Lunch/meal, or General) has its own
class here holding EVERYTHING that is special about it:

  * which formset to use for full item entry (create/edit)
  * which formset to use for the supplier-pricing step
  * how a line item must be normalized before saving (its "rules")

The views (create / pricing / approve) delegate to these classes instead of
carrying scattered ``if is_room / elif is_lunch / else`` chains. That means:

  - To change how ROOM bills behave, edit ``RoomCategoryRules`` only.
  - To change how LUNCH bills behave, edit ``LunchCategoryRules`` only.
  - One category can no longer accidentally hamper another.

Combined bills (a primary category + an optional secondary category) are handled
correctly because each *item* is normalized by the rules of ITS OWN category:
primary items use the primary category's rules, secondary items use the
secondary category's rules. A room+lunch bill therefore never applies room's
"quantity = 1" rule to a lunch line, etc.
"""

from .forms import (
    LunchItemFormSet,
    RoomItemFormSet,
    TransactionItemFormSet,
    SupplierPricingFormSet,
)


class BaseCategoryRules:
    """General / uncategorized items. The other categories override as needed."""

    #: Human label, handy for audit messages / debugging.
    name = "General"

    # ── Which formset each workflow step uses ──────────────────────────────
    def entry_formset(self):
        """Formset for full item entry (the create / edit form)."""
        return TransactionItemFormSet

    def pricing_formset(self):
        """Formset for the supplier-pricing step."""
        return SupplierPricingFormSet

    # ── The category's line-item rules ─────────────────────────────────────
    def normalize_item(self, item, transaction):
        """
        Enforce this category's rules on a single line item, in place, right
        before it is saved. Base categories impose nothing.
        """
        return item

    # ── Audit wording for the pricing step ─────────────────────────────────
    def pricing_change_note(self, item_form):
        """
        Return a short audit string for one changed pricing row, or None.
        Base category only logs an actual price change.
        """
        if 'base_price' in item_form.changed_data:
            old = item_form.initial.get('base_price', '0')
            new = item_form.cleaned_data.get('base_price', '0')
            desc = item_form.instance.description or 'Item'
            return f"{desc}: ৳{old} -> ৳{new}"
        return None


class RoomCategoryRules(BaseCategoryRules):
    """Hotel / room-reservation bills."""

    name = "Room"

    def entry_formset(self):
        # Unified: every category now uses the same item table for entry/edit.
        return TransactionItemFormSet

    def pricing_formset(self):
        # Unified: the supplier-pricing step is a uniform price-only table.
        return SupplierPricingFormSet

    def normalize_item(self, item, transaction):
        # Room bills expose Unit & Qty just like every other category, so the
        # quantity the user typed is always respected (in both daily-basis and
        # range mode). A blank quantity falls back to 1 via ``normalize_items``.
        # checkout_date is left exactly as submitted.
        return item

    def pricing_change_note(self, item_form):
        desc = item_form.cleaned_data.get('description', 'Item')
        return f"Updated {desc}"


class LunchCategoryRules(BaseCategoryRules):
    """Lunch / tiffin / meal-supply bills."""

    name = "Lunch"

    def entry_formset(self):
        # Unified: every category now uses the same item table for entry/edit.
        return TransactionItemFormSet

    def pricing_formset(self):
        # Unified: the supplier-pricing step is a uniform price-only table.
        return SupplierPricingFormSet

    def normalize_item(self, item, transaction):
        # A lunch line is identified by its date; if no free-text description was
        # given, fall back to a descriptive category/restaurant name.
        import re
        is_date = re.match(r'^\d{4}-\d{2}-\d{2}$', str(item.description or "").strip())
        if not item.description or is_date:
            cat = transaction.secondary_category if (item.is_secondary and transaction.secondary_category) else transaction.transaction_category
            cat_name = cat.name if cat else "Lunch"
            if item.restaurant_name:
                item.description = f"{cat_name} from {item.restaurant_name}"
            else:
                item.description = f"{cat_name} Supply"
        return item

    def pricing_change_note(self, item_form):
        rest = item_form.cleaned_data.get('restaurant_name', 'Item')
        edate = item_form.cleaned_data.get('entry_date', '')
        return f"Updated {rest} ({edate})"


def rules_for_category(category):
    """Return the rules object for a single category (or the general base)."""
    if category is not None:
        if category.is_lunch:
            return LunchCategoryRules()
        if category.is_room_reservation:
            return RoomCategoryRules()
    return BaseCategoryRules()


def rules_for_transaction(transaction):
    """Rules for the transaction's PRIMARY category (drives formset selection)."""
    return rules_for_category(transaction.transaction_category)


def normalize_items(transaction, items):
    """
    Normalize every saved item by the rules of the category it belongs to:
    secondary items follow the secondary category, everything else follows the
    primary category. Call after ``formset.save(commit=False)`` and before
    ``item.save()`` (this helper does not save).
    """
    primary = rules_for_category(transaction.transaction_category)
    secondary = rules_for_category(transaction.secondary_category)
    for item in items:
        if item.quantity is None:
            item.quantity = 1
        rules = secondary if item.is_secondary else primary
        rules.normalize_item(item, transaction)
    return items
