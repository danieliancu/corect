import re
import uuid

from django.db.models import CharField, Q
from django.db.models.functions import Cast

from .models import AnonymousVisitor


def visitor_lookup(term, field="pk"):
    """Q matching a full visitor UUID or its short "anon-xxxxxx" form, or None when the term is neither."""
    term = term.strip().lower().removeprefix("anon-")
    try:
        return Q(**{field: uuid.UUID(term)})
    except ValueError:
        pass
    if re.fullmatch(r"[0-9a-f]{4,8}", term):
        matches = AnonymousVisitor.objects.annotate(id_text=Cast("id", CharField())).filter(id_text__startswith=term)
        return Q(**{f"{field}__in": matches.values("id")})
    return None
