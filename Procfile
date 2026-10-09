release: python manage.py migrate --noinput && python seed.py
web: daphne -b 0.0.0.0 -p ${PORT:-8000} cafe_smart.asgi:application
