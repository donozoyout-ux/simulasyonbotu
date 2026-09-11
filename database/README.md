# Database

PostgreSQL şeması SQLAlchemy modellerinden yönetilir. Sürümlenmiş başlangıç migration'ı `backend/alembic/versions/0001_initial.py` dosyasındadır.

Yerel Docker volume adı `postgres_data` olup uygulama parolası yalnızca yerel geliştirme varsayımıdır; farklı bir ortamda `.env` üzerinden değiştirilmelidir.

