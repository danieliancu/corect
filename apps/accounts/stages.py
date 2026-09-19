from allauth.account.stages import EmailVerificationStage as AllauthEmailVerificationStage


class EmailVerificationStage(AllauthEmailVerificationStage):
    """Mandatory verification, except for an account created before addresses were required that has none at all: it
    may sign in, and EmailRequiredMiddleware keeps it on the account page until an address is added and confirmed."""

    def handle(self):
        user = self.login.user
        if not self.login.signup and not user.email and not user.emailaddress_set.exists():
            return None, True
        return super().handle()
