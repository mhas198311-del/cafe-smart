web: sh -c "python manage.py migrate --noinput && python seed.py && daphne -b 0.0.0.0 -p $PORT cafe_smart.asgi:application"
