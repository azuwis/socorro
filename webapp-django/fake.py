from django.contrib.auth.models import AnonymousUser

class FakeUser(AnonymousUser):
    username = 'Admin'
    is_staff = True
    is_active = True
    is_superuser = True
    def is_anonymous(self):
        return False

    def is_authenticated(self):
        return True

class AuthenticationMiddleware:
    def process_request(self, request):
        request.user = FakeUser()
