from django.urls import path

from datum.api.router import api

urlpatterns = [path("api/", api.urls)]
