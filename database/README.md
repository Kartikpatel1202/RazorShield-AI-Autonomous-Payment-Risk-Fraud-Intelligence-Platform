# database/

| Directory     | Contents                                                          |
| ------------- | ----------------------------------------------------------------- |
| `migrations/` | Alembic environment and revision scripts (`versions/`)             |
| `seed/`       | Seed data scripts - empty until the business schema exists         |

Alembic is driven from `backend/alembic.ini`, which points `script_location` here.
Run migrations from the `backend/` directory so the `app` package is importable:

```
alembic upgrade head
alembic revision --autogenerate -m "add transactions table"
```

The connection URL is read from `DATABASE_URL`; it is never stored in `alembic.ini`.
