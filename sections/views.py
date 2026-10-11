from django.contrib.auth.decorators import login_required
from django.http import request
from django.shortcuts import render
from .models import Section


@login_required
def section_list(request):
    sections = Section.objects.all()
    return render(
        request,
        "sections/section_list.html",
        {"sections": sections},
    )


def home(request):
    return render(request, "home.html")
