"""Manual account suspension (Terms, "Suspendare pentru abuz sau securitate"). Staff suspend accounts from the Users list
in /admin/; a suspended account can still sign in and see its history, but every AI feature is refused."""

SUSPENDED_GROUP = "Suspended"
SUSPENDED_MESSAGE = ("Contul tău este suspendat pentru încălcarea Termenilor. Dacă crezi că este o greșeală, "
                     "scrie-ne folosind datele de pe pagina Contact.")


def is_suspended(user):
    return user.is_authenticated and user.groups.filter(name=SUSPENDED_GROUP).exists()
