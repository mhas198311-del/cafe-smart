release: PYTHONPATH=. python manage.py migrate --noinput && PYTHONPATH=. python seed.py
web: PYTHONPATH=. daphne -b 0.0.0.0 -p ${PORT:-8000} asgi:application
