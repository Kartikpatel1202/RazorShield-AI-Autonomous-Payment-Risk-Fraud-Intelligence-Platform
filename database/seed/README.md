# database/seed/

The seed generator is Python, not SQL. It lives in `backend/app/seed/` so it can
import the ORM models and be covered by the test suite, and it is driven by the
CLI at `backend/scripts/seed_data.py`.

Run it from `backend/`:

```
python scripts/seed_data.py
```

See `docs/dataset.md` for the dataset's composition, the fraud distribution and
the three deterministic demo scenarios.
