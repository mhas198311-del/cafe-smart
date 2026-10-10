from django.urls import path
from . import views

app_name = 'marketing'

urlpatterns = [
    path('api/marketing/raffle/calculate/', views.calculate_raffle_tickets, name='calculate_tickets'),
    path('api/marketing/raffle/draw/', views.draw_winner, name='draw_winner'),
    path('api/marketing/raffle/tickets/', views.get_all_tickets, name='all_tickets'),
]
