from django.contrib.auth.views import LoginView
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST


class UserLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True


@login_required
@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")