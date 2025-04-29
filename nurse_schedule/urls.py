"""
URL configuration for nurse_schedule project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from scheduler import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.view_schedule, name='view_schedule'),
    path('generate/', views.generate_schedule, name='generate_schedule'),
    path('staffing/<int:pk>/', views.update_staffing, name='update_staffing'),
    path('regenerate/', views.regenerate_schedule, name='regenerate_schedule'),
    path('delete/', views.delete_schedule, name='delete_schedule'),
]
