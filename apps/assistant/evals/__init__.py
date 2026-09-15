"""Language-quality evaluation of "Vreau să sune natural!" against the live model (opt-in, never run by CI).

The unit tests only check that the dataset is well formed and that scoring works: mocked fixtures cannot prove live
linguistic quality. Run `python manage.py eval_naturalize --live` for that.
"""
